"""Account drill API.

Per Phase 4 spec §4.1. Reads `tabDGV TB Snapshot Row` only -- this is
a cockpit read in the amended CLAUDE.md rule 1, NOT a `_drill`-suffixed
GL reader. (The `_v1` suffix is a versioning convention; the `_drill`
keyword applies only to APIs that read `tabGL Entry`. See
`api/party_drill_v1.py` for the GL-reading drill.)

Two entry shapes (per spec §4.1 "Two entry shapes"):
  - `scope`:    pivot row click. ScopeSpec resolved to a leaf list
                server-side via `utils._resolve_scope_to_leaves`.
  - `accounts`: card click. Pre-resolved leaf list passed directly.
                Caller (cockpit JS, via `cards_v1.resolve_match_to_accounts`)
                has already translated the card's `match` predicate.
                When `accounts` is given, `scope` is ignored.

Sign convention: snapshot stores raw `Dr - Cr`; this reader applies
the natural-side flip via `FLIP_ROOT_TYPES` so values display
positive on their natural side. Parity with party drill SQL is
asserted by tests in `test_party_drill.py`.
"""

import json
from calendar import monthrange
from datetime import date

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api.utils import (
	FLIP_ROOT_TYPES,
	PARTY_TRACKABLE_ACCOUNT_TYPES,
	_named_in,
	_resolve_scope_to_leaves,
)
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	SPARKLINE_LENGTH,
)


@frappe.whitelist()
def get_account_breakdown(scope=None, accounts=None, scope_label=None,
                          as_of_date=None, companies=None):
	"""Return the account drill panel data for one scope.

	Output shape per spec §4.1:

	    {
	      "scope_label": str,
	      "group_total": float,           # natural-side, raw rupees
	      "is_party_trackable": bool,
	      "trend_12mo": [{"month": "YYYY-MM", "value": float|None}, ...],
	      "by_company": [
	        {"company": str, "value": float, "sparkline": [12 vals]},
	        ...
	      ]
	    }
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	# --- Resolve leaves + label ---
	if accounts is not None:
		if not scope_label:
			frappe.throw(_("scope_label is required when accounts is provided"))
		leaves = [a for a in accounts if isinstance(a, str)]
	else:
		if not isinstance(scope, dict):
			frappe.throw(_("scope or accounts is required"))
		leaves, default_label = _resolve_scope_to_leaves(scope, allowed)
		if not scope_label:
			scope_label = default_label

	if not allowed or not leaves:
		return _empty_response(scope_label, target_date)

	# --- 12-month trend dates (label, snapshot_date or None) ---
	trend = _trend_dates(target_date, SPARKLINE_LENGTH)
	known_dates = sorted({snap for _label, snap in trend if snap is not None})

	if not known_dates:
		return _empty_response(scope_label, target_date)

	# --- is_party_trackable ---
	is_party_trackable = _is_party_trackable(leaves)

	# --- Aggregate snapshot rows for all (date, company) pairs ---
	agg = _aggregate_by_date_company(leaves, allowed, known_dates)

	# --- Build response ---
	# `current_date` is the rightmost trend date (== latest snapshot
	# <= target_date). `group_total` and `by_company[].value` are taken
	# at this date.
	current_date = trend[-1][1]

	# by_company: one entry per company that has any non-zero value
	# in any of the trend dates. Prevents empty rows cluttering the
	# panel for companies whose scope has no balance.
	by_company = []
	for c in sorted(allowed):
		current_value = (
			agg.get((current_date, c), 0.0) if current_date else 0.0
		)
		sparkline = []
		any_nonzero = False
		for _label, snap in trend:
			if snap is None:
				sparkline.append(None)
			else:
				v = round(flt(agg.get((snap, c), 0.0)), 2)
				sparkline.append(v)
				if v != 0:
					any_nonzero = True
		if not any_nonzero and round(flt(current_value), 2) == 0:
			continue
		by_company.append({
			"company": c,
			"value": round(flt(current_value), 2),
			"sparkline": sparkline,
		})

	# Sort by_company by absolute value desc (largest contributors first).
	by_company.sort(key=lambda r: abs(r["value"]), reverse=True)

	# trend_12mo: sum across companies for each trend date
	trend_12mo = []
	for label, snap in trend:
		if snap is None:
			trend_12mo.append({"month": label, "value": None})
		else:
			value = sum(
				flt(agg.get((snap, c), 0.0)) for c in allowed
			)
			trend_12mo.append({
				"month": label,
				"value": round(value, 2),
			})

	group_total = sum(r["value"] for r in by_company)

	return {
		"scope_label": scope_label,
		"group_total": round(group_total, 2),
		"is_party_trackable": is_party_trackable,
		"trend_12mo": trend_12mo,
		"by_company": by_company,
	}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _ensure_dict(value):
	if value is None:
		return None
	if isinstance(value, dict):
		return value
	if isinstance(value, str):
		try:
			parsed = json.loads(value)
			return parsed if isinstance(parsed, dict) else None
		except (ValueError, TypeError):
			return None
	return None


def _ensure_list(value):
	if value is None:
		return None
	if isinstance(value, list):
		return value
	if isinstance(value, str):
		try:
			parsed = json.loads(value)
			return parsed if isinstance(parsed, list) else None
		except (ValueError, TypeError):
			return None
	return None


def _empty_response(scope_label, target_date):
	# Pad an all-None trend so the panel still renders the 12-slot axis.
	trend = _trend_dates(target_date, SPARKLINE_LENGTH)
	return {
		"scope_label": scope_label or "",
		"group_total": 0.0,
		"is_party_trackable": False,
		"trend_12mo": [{"month": label, "value": None} for label, _ in trend],
		"by_company": [],
	}


def _is_party_trackable(leaves: list) -> bool:
	"""True if any resolved leaf has account_type in PARTY_TRACKABLE_ACCOUNT_TYPES."""
	if not leaves:
		return False
	a_ph, a_params = _named_in("a", leaves)
	t_ph, t_params = _named_in("t", PARTY_TRACKABLE_ACCOUNT_TYPES)
	row = frappe.db.sql(
		f"""
		SELECT 1 FROM `tabAccount`
		WHERE name IN ({a_ph})
		  AND account_type IN ({t_ph})
		LIMIT 1
		""",
		{**a_params, **t_params},
	)
	return bool(row)


def _aggregate_by_date_company(leaves: list, companies: list, dates: list) -> dict:
	"""Return {(snapshot_date, company): natural_side_value} for the given inputs.

	One round-trip; in-Python pivoting after.
	"""
	if not leaves or not companies or not dates:
		return {}
	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", companies)
	d_ph, d_params = _named_in("d", dates)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)
	rows = frappe.db.sql(
		f"""
		SELECT snapshot_date, company,
		       COALESCE(SUM(
		         CASE WHEN root_type IN ({f_ph})
		              THEN -balance
		              ELSE balance
		         END
		       ), 0) AS value
		FROM `tabDGV TB Snapshot Row`
		WHERE account IN ({a_ph})
		  AND company IN ({c_ph})
		  AND snapshot_date IN ({d_ph})
		GROUP BY snapshot_date, company
		""",
		{**a_params, **c_params, **d_params, **f_params},
		as_dict=True,
	)
	return {
		(getdate(r["snapshot_date"]), r["company"]): flt(r["value"])
		for r in rows
	}


def _trend_dates(target, n):
	"""Return [(month_label, snapshot_date_or_None)] for the last `n` calendar months.

	Each month's snapshot is the latest `DGV TB Snapshot` whose date
	is <= the lesser of the month-end and the caller's `target` date.
	A month with no snapshot yields a None snapshot_date but keeps its
	calendar label so the trend visualisation renders evenly-spaced
	slots.

	Order: oldest-first (left-to-right time axis).
	"""
	target = getdate(target)
	out = []
	for i in range(n - 1, -1, -1):
		mm = target.month - i
		yy = target.year
		while mm <= 0:
			mm += 12
			yy -= 1
		last_day = monthrange(yy, mm)[1]
		month_end = date(yy, mm, last_day)
		cap = min(month_end, target)
		snap = _latest_snapshot_le(cap)
		out.append((f"{yy:04d}-{mm:02d}", snap))
	return out


def _latest_snapshot_le(target):
	"""Latest `DGV TB Snapshot.snapshot_date` (status='Complete') <= target."""
	target = getdate(target)
	row = frappe.db.sql(
		"""
		SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
		WHERE snapshot_date <= %s AND status = 'Complete'
		""",
		(target,),
	)
	if not row or not row[0][0]:
		return None
	return getdate(row[0][0])
