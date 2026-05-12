"""Spotlight cache refresh.

Reads only `tabDGV TB Snapshot Row` and `tabDGV TB Snapshot`. Never
`tabGL Entry`. Phase 2 hard rule -- two layers of cache, both layers
read-only from the cockpit code path.

For each card:
  1. Aggregate matching accounts in the target snapshot (sign-corrected
     so positive = the natural / healthy direction).
  2. Compute delta vs the latest snapshot strictly before the first day
     of this snapshot's calendar month.
  3. Compute a 6-point sparkline from the 6 most recent month-end
     snapshots whose date <= target snapshot date. Pad with `null` if
     fewer than 6 historical month-ends exist.
  4. Upsert the row in `tabDGV Spotlight Cache`.

Wraps all 6 cards in one transaction. On failure: rollback so we never
end with a partial cache. Idempotent on (card_id, snapshot_date).
"""

import json
import time
from calendar import monthrange
from datetime import date

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, today

from dux_groupview.dux_groupview.api.utils import FLIP_ROOT_TYPES
from dux_groupview.dux_groupview.spotlight.cards import CARDS


CARD_DEFINITION_HASH = "phase2-hardcoded"

# Phase 4 commit 2 bumped 6 -> 12 to match account drill's by-company
# sparkline length (spec §4.1, "Sparkline length: 12 points"). After
# merge, regenerate the cache via `bench --site <site> execute
# dux_groupview.dux_groupview.snapshots.spotlight_refresh.refresh_spotlight_cache`
# so existing rows pick up the new length; otherwise cards keep
# rendering 6 points until the next scheduled refresh.
SPARKLINE_LENGTH = 12

# Sign convention: see api/utils.FLIP_ROOT_TYPES. Imported so this
# module and the drill modules share a single source of truth -- party
# drill aggregation in api/party_drill_v1.py and snapshot aggregation
# here must agree per spec §4.2 sign-convention parity.


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def refresh_spotlight_cache(snapshot_date=None):
	"""Refresh spotlight cache for one date.

	Args:
		snapshot_date: Date or string. Defaults to today (site timezone).

	Returns:
		dict with keys: snapshot_date, cards_processed, duration_seconds.

	Raises on aggregation failure; the entire transaction rolls back so
	the cache is never partially written.
	"""
	snapshot_date = getdate(snapshot_date) if snapshot_date else getdate(today())
	t_start = time.time()

	# Pre-fetch the historical date pool so each card's sparkline is built
	# from the same set of dates (avoids drift if a snapshot finishes mid-loop).
	historical_dates = _historical_month_end_dates(snapshot_date, SPARKLINE_LENGTH)
	prior_month_date = _prior_month_snapshot_date(snapshot_date)

	now = now_datetime()
	processed = []

	try:
		for card in CARDS:
			value = _aggregate(card, snapshot_date)
			prior_value = (
				_aggregate(card, prior_month_date)
				if prior_month_date is not None else 0.0
			)
			delta = round(value - prior_value, 2)
			delta_percent = (
				round((delta / prior_value) * 100, 2)
				if prior_value not in (0, 0.0) else 0.0
			)

			sparkline = []
			for d in historical_dates:
				sparkline.append(_aggregate(card, d) if d is not None else None)

			_upsert(
				card_id=card["id"],
				snapshot_date=snapshot_date,
				value=value,
				delta=delta,
				delta_percent=delta_percent,
				sparkline_data=json.dumps(sparkline),
				computed_at=now,
			)
			processed.append(card["id"])

		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise

	duration = round(time.time() - t_start, 3)
	print(
		f"Refreshed {len(processed)} spotlight cards for "
		f"{snapshot_date} in {duration} sec"
	)
	return {
		"snapshot_date": str(snapshot_date),
		"cards_processed": processed,
		"duration_seconds": duration,
	}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate(card, snapshot_date, companies=None):
	"""Sum matching accounts for one card on one snapshot date.

	Returns 0.0 if snapshot_date is None, no matching rows exist, or
	the snapshot itself does not exist.

	`companies` is an optional iterable of company names. When supplied
	(and non-empty), restricts the aggregation to those companies via
	an additional `WHERE company IN (...)` clause. Used by the cockpit's
	scope-filter endpoint; the standard cache refresh always passes None
	(i.e. all companies the snapshot already covers).
	"""
	if snapshot_date is None:
		return 0.0

	clause, params = _match_clause(card)
	if clause is None:
		return 0.0
	params["snapshot_date"] = snapshot_date

	company_clause = ""
	if companies is not None:
		companies = [c for c in companies if c]
		if not companies:
			return 0.0
		# Single-element IN tuples are fine on MariaDB through the
		# pyformat driver (`IN ('x')` is valid SQL); no need to duplicate.
		placeholders = ", ".join(
			f"%(co_{i})s" for i in range(len(companies))
		)
		for i, c in enumerate(companies):
			params[f"co_{i}"] = c
		company_clause = f" AND company IN ({placeholders})"

	# CASE flips sign for natural-credit root types so the stored value
	# is positive on the natural side.
	rows = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(
			CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
			     THEN -balance
			     ELSE balance
			END
		), 0) AS total
		FROM `tabDGV TB Snapshot Row`
		WHERE snapshot_date = %(snapshot_date)s
		  AND ({clause})
		  {company_clause}
		""",
		params,
	)
	return round(flt(rows[0][0]), 2) if rows else 0.0




def _match_clause(card):
	"""Translate a card's `match` config into (where_clause, params).

	Returns (None, {}) if the strategy is unrecognised.
	"""
	match = card.get("match", {})

	if "by_account_type" in match:
		v = match["by_account_type"]
		if isinstance(v, (list, tuple)):
			placeholders = ", ".join(
				f"%(at_{i})s" for i in range(len(v))
			)
			params = {f"at_{i}": x for i, x in enumerate(v)}
			return (f"account_type IN ({placeholders})", params)
		return ("account_type = %(account_type)s", {"account_type": v})

	if "by_root_type_and_name_pattern" in match:
		conf = match["by_root_type_and_name_pattern"]
		return (
			"root_type = %(root_type)s AND account LIKE %(name_pattern)s",
			{
				"root_type": conf["root_type"],
				"name_pattern": conf["name_pattern"],
			},
		)

	return (None, {})


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _prior_month_snapshot_date(target):
	"""Latest snapshot_date strictly before the first day of target's month.

	Returns None if no such snapshot exists.
	"""
	target = getdate(target)
	first_of_month = date(target.year, target.month, 1)
	row = frappe.db.sql(
		"""
		SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
		WHERE snapshot_date < %s AND status = 'Complete'
		""",
		(first_of_month,),
	)
	return row[0][0] if row and row[0][0] else None


def _historical_month_end_dates(target, n):
	"""Return up to n month-end snapshot_dates <= target.

	Pads the front with None if fewer than n exist.
	Result is oldest-first (left-to-right time axis for a sparkline).
	"""
	target = getdate(target)
	rows = frappe.db.sql(
		"""
		SELECT snapshot_date FROM `tabDGV TB Snapshot`
		WHERE status = 'Complete'
		  AND snapshot_date <= %s
		  AND snapshot_date = LAST_DAY(snapshot_date)
		ORDER BY snapshot_date DESC
		LIMIT %s
		""",
		(target, n),
	)
	dates = [r[0] for r in rows]
	# Oldest-first.
	dates.reverse()
	# Pad front with None.
	while len(dates) < n:
		dates.insert(0, None)
	return dates


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def _upsert(card_id, snapshot_date, value, delta, delta_percent,
            sparkline_data, computed_at):
	name = f"{card_id}-{snapshot_date}"
	if frappe.db.exists("DGV Spotlight Cache", name):
		frappe.db.set_value(
			"DGV Spotlight Cache", name,
			{
				"value": value,
				"delta": delta,
				"delta_percent": delta_percent,
				"sparkline_data": sparkline_data,
				"computed_at": computed_at,
				"card_definition_hash": CARD_DEFINITION_HASH,
			},
		)
		return

	doc = frappe.new_doc("DGV Spotlight Cache")
	doc.card_id = card_id
	doc.snapshot_date = snapshot_date
	doc.value = value
	doc.delta = delta
	doc.delta_percent = delta_percent
	doc.sparkline_data = sparkline_data
	doc.computed_at = computed_at
	doc.card_definition_hash = CARD_DEFINITION_HASH
	doc.flags.ignore_permissions = True
	doc.insert()


# ---------------------------------------------------------------------------
# Public aliases for cross-module reuse (cockpit's filtered-spotlight path
# imports these). The underscored names remain canonical inside this module.
# ---------------------------------------------------------------------------

aggregate_card_value = _aggregate
prior_month_snapshot_date = _prior_month_snapshot_date
historical_month_end_dates = _historical_month_end_dates
