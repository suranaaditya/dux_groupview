"""Whitelisted endpoints for the /groupview cockpit page.

All endpoints require GroupView Viewer or higher (System Manager,
GroupView Owner, GroupView Viewer all qualify).

Reads only `tabDGV TB Snapshot`, `tabDGV TB Snapshot Row`, and
`tabDGV Spotlight Cache`. Never reads `tabGL Entry`. The cockpit JS in
turn reads only the API responses, never the database directly --
two-layer cache, both layers read-only at this phase.
"""

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime

from dux_groupview.dux_groupview.api.pivot import _resolve_scope
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	SPARKLINE_LENGTH,
	aggregate_card_value,
	historical_month_end_dates,
	prior_month_snapshot_date,
)
from dux_groupview.dux_groupview.spotlight.cards import CARDS, by_id


# ---------------------------------------------------------------------------
# Permission helper
# ---------------------------------------------------------------------------

ALLOWED_ROLES = {"System Manager", "GroupView Owner", "GroupView Viewer"}


def _require_cockpit_role():
	if not (ALLOWED_ROLES & set(frappe.get_roles())):
		frappe.throw(
			_("/groupview is restricted to GroupView roles."),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_available_snapshot_dates():
	"""Return up to 30 most-recent Complete snapshot dates (newest first)."""
	_require_cockpit_role()
	rows = frappe.db.sql(
		"""
		SELECT snapshot_date
		FROM `tabDGV TB Snapshot`
		WHERE status = 'Complete'
		ORDER BY snapshot_date DESC
		LIMIT 30
		""",
		as_dict=False,
	)
	return [_iso_date(r[0]) for r in rows]


@frappe.whitelist()
def get_spotlight_cards(snapshot_date):
	"""Return all 6 spotlight cards for the requested date.

	Each card is enriched with definition metadata (label, polarity,
	format, color) and server-rendered formatted strings for the value
	and delta. Cards with no cache row (zero-match accounts on dev seed)
	still appear with value=0 / delta=0 / sparkline_data=[].

	Cache fallback (commit 7 F-3): scheduler-driven runs go through
	`_refresh_with_spotlight()` (refresh.py) which chains the spotlight
	cache after the TB snapshot, so under normal operation the cache
	rows for a fresh snapshot are present. If the chained refresh
	silently fails (caught and logged in `_refresh_with_spotlight`) OR
	someone runs `refresh_tb_snapshot` manually without the wrapper,
	the cache lags and this endpoint would return all-zero cards --
	indistinguishable from a "no activity" cockpit. Defensive read-side
	fallback: if no cache rows exist for `snapshot_date`, fall through
	to the same live recomputation path that
	`get_spotlight_cards_filtered` uses, scoped to the user's full
	allowed company set. Logs to `frappe.log_error` so production
	observability flags every fallback fire.
	"""
	_require_cockpit_role()
	snapshot_date = getdate(snapshot_date)

	cache_rows = frappe.db.sql(
		"""
		SELECT card_id, value, delta, delta_percent, sparkline_data, computed_at
		FROM `tabDGV Spotlight Cache`
		WHERE snapshot_date = %s
		""",
		(snapshot_date,),
		as_dict=True,
	)

	# Defensive fallback when the cache is empty for this date.
	if not cache_rows:
		frappe.log_error(
			message=(
				f"Spotlight cache empty for snapshot_date={snapshot_date}; "
				"falling back to live recomputation. Either the chained "
				"_refresh_with_spotlight failed silently for this date, or "
				"refresh_tb_snapshot was run without the wrapper. Cockpit "
				"is showing correct numbers via fallback."
			),
			title="Spotlight cache empty fallback",
		)
		# Live recompute scoped to user's full allowed companies, mirroring
		# the no-explicit-scope path that `get_spotlight_cards_filtered`
		# uses when companies=null. Reuses the same code path; no new
		# helper needed.
		allowed = _resolve_scope(None)
		return _build_filtered_cards_payload(snapshot_date, allowed)

	cache_by_id = {r["card_id"]: r for r in cache_rows}

	# Phase 4 commit 2.5 fix 3: per-card has_prior_baseline. A card has
	# a prior baseline iff a cache row exists for it at the prior month's
	# snapshot date. Used by JS to render "First reported this month"
	# instead of "Up ₹X Cr from <Month>" on cards with no MoM context.
	prior_month_date = prior_month_snapshot_date(snapshot_date)
	prior_card_ids = set()
	if prior_month_date is not None:
		prior_rows = frappe.db.sql(
			"""
			SELECT card_id FROM `tabDGV Spotlight Cache`
			WHERE snapshot_date = %s
			""",
			(prior_month_date,),
		)
		prior_card_ids = {r[0] for r in prior_rows}

	# Per spec `specs/cash-bank-card-split.md` §5.3: disabled cards are
	# hidden from the cockpit grid but their cache rows remain so the
	# sparkline history survives a future re-enable. The filter is
	# applied at the iteration boundary -- cache lookups, prior-baseline
	# checks, and response assembly all run against `visible_cards`.
	visible_cards = [c for c in CARDS if not c.get("disabled")]

	out = []
	for card in visible_cards:
		cache = cache_by_id.get(card["id"])
		value = flt(cache["value"]) if cache else 0.0
		delta = flt(cache["delta"]) if cache else 0.0
		delta_percent = flt(cache["delta_percent"]) if cache else 0.0
		sparkline = _parse_sparkline(cache["sparkline_data"] if cache else None)

		out.append({
			"card_id": card["id"],
			"label": card["label"],
			# `match` exposes the card's predicate dict so the cockpit JS
			# can hand it to `cards_v1.resolve_match_to_accounts` when
			# opening the drill panel (Phase 4 commit 3). It carries no
			# user-data sensitivity -- the predicate is a server-side
			# constant from spotlight/cards.py.
			"match": card["match"],
			"polarity": card["polarity"],
			"format": card["format"],
			"color": card["color"],
			"value": value,
			"delta": delta,
			"delta_percent": delta_percent,
			"sparkline_data": sparkline,
			"has_prior_baseline": card["id"] in prior_card_ids,
			"formatted_value": _format_value(value, card["format"]),
			"formatted_delta": _format_delta(delta, card["format"]),
		})
	return out


@frappe.whitelist()
def get_spotlight_cards_filtered(snapshot_date, companies):
	"""Return spotlight cards aggregated for an explicit subset of companies.

	Mirrors the shape of get_spotlight_cards but bypasses
	`tabDGV Spotlight Cache` (which stores all-company aggregations) and
	re-computes each card's value, delta, and 6-point sparkline directly
	from `tabDGV TB Snapshot Row`. Use only when scope is non-empty
	and narrower than the user's full allowed set; for the default "all
	companies" scope the cached endpoint is faster and equally correct.

	`companies` is intersected with the user's User-Permission-allowed
	set before any aggregation -- a user cannot widen their visibility.

	Note on hierarchy: this endpoint aggregates by `account_type` or
	name pattern at the leaf level (per the card definitions in
	spotlight/cards.py). It deliberately does NOT do the
	descendant-group aggregation that `get_pivot_data` does, because
	spotlight matches are predicate-based (e.g. "all accounts with
	account_type = Receivable") rather than hierarchy-based. A leaf
	account with the matching predicate already produces the correct
	value for its card; rolling that value up to a parent group would
	double-count once the parent group has its own predicate match
	(rare but not impossible). The pivot grid -- which IS hierarchy-
	based -- is the right place for tree aggregation; spotlight cards
	are flat sums.
	"""
	_require_cockpit_role()
	snapshot_date = getdate(snapshot_date)

	allowed = _resolve_scope(companies)
	return _build_filtered_cards_payload(snapshot_date, allowed)


def _build_filtered_cards_payload(snapshot_date, allowed):
	"""Live-recompute the 6 cards for an explicit company list.

	Shared between `get_spotlight_cards_filtered` (always called) and
	`get_spotlight_cards`'s defensive fallback (called when the cache
	is empty for the requested date — commit 7 F-3 fix).

	`allowed` is already permission-intersected (caller invokes
	`_resolve_scope`).
	"""
	# Per spec `specs/cash-bank-card-split.md` §5.3: filter disabled
	# cards here too -- the live-recompute path mirrors the cache path's
	# response shape. Disabled cards never appear in the cockpit
	# regardless of which read path is used.
	visible_cards = [c for c in CARDS if not c.get("disabled")]

	if not allowed:
		# No accessible companies after intersection -- return zeroed
		# cards in the same shape as get_spotlight_cards.
		return [_zero_card_payload(card) for card in visible_cards]

	prior_month_date = prior_month_snapshot_date(snapshot_date)
	hist_dates = historical_month_end_dates(snapshot_date, SPARKLINE_LENGTH)

	out = []
	for card in visible_cards:
		value = aggregate_card_value(card, snapshot_date, companies=allowed)
		prior_value = (
			aggregate_card_value(card, prior_month_date, companies=allowed)
			if prior_month_date is not None else 0.0
		)
		delta = round(value - prior_value, 2)
		delta_percent = (
			round((delta / prior_value) * 100, 2)
			if prior_value not in (0, 0.0) else 0.0
		)
		sparkline = []
		for d in hist_dates:
			sparkline.append(
				aggregate_card_value(card, d, companies=allowed) if d is not None
				else None
			)

		# Phase 4 commit 2.5 fix 3: per-card has_prior_baseline. The
		# cache doesn't apply when scope is filtered, so we check
		# whether this card's predicate matches any snapshot rows in
		# the requested companies at the prior month's date.
		has_prior_baseline = _card_has_prior_baseline(
			card, prior_month_date, allowed,
		)

		out.append({
			"card_id": card["id"],
			"label": card["label"],
			"match": card["match"],
			"polarity": card["polarity"],
			"format": card["format"],
			"color": card["color"],
			"value": value,
			"delta": delta,
			"delta_percent": delta_percent,
			"sparkline_data": sparkline,
			"has_prior_baseline": has_prior_baseline,
			"formatted_value": _format_value(value, card["format"]),
			"formatted_delta": _format_delta(delta, card["format"]),
		})
	return out


def _zero_card_payload(card):
	return {
		"card_id": card["id"],
		"label": card["label"],
		"match": card["match"],
		"polarity": card["polarity"],
		"format": card["format"],
		"color": card["color"],
		"value": 0.0,
		"delta": 0.0,
		"delta_percent": 0.0,
		"sparkline_data": [],
		"has_prior_baseline": False,
		"formatted_value": _format_value(0.0, card["format"]),
		"formatted_delta": _format_delta(0.0, card["format"]),
	}


def _card_has_prior_baseline(card, prior_month_date, allowed):
	"""Did this card's leaves have any snapshot rows at prior_month_date
	for the given company scope?

	Used by the filtered spotlight endpoint and the cockpit headline,
	which both bypass the cache. Returns False when prior_month_date
	is None (no prior snapshot at all) or when the card's predicate
	matches zero snapshot rows in the requested scope.
	"""
	from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
		_match_clause,
	)
	if prior_month_date is None or not allowed:
		return False
	clause, params = _match_clause(card)
	if clause is None:
		return False
	params["snapshot_date"] = prior_month_date
	co_clause = ""
	co_list = list(allowed)
	if co_list:
		ph = ", ".join(f"%(co_{i})s" for i in range(len(co_list)))
		for i, c in enumerate(co_list):
			params[f"co_{i}"] = c
		co_clause = f" AND company IN ({ph})"
	rows = frappe.db.sql(
		f"""
		SELECT 1 FROM `tabDGV TB Snapshot Row`
		WHERE snapshot_date = %(snapshot_date)s
		  AND ({clause}){co_clause}
		LIMIT 1
		""",
		params,
	)
	return bool(rows)


@frappe.whitelist()
def get_seed_state():
	"""Detect whether the cockpit is currently rendering synthetic preview data.

	Used by the cockpit page to render a "SYNTHETIC PREVIEW DATA" banner
	when the seed_rgi_named_data fixture is loaded (voucher_no LIKE
	RGI-DEMO-%). Banner disappears automatically when the seed is torn
	down -- no UI state to reset.
	"""
	_require_cockpit_role()
	count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` "
		"WHERE voucher_no LIKE 'RGI-DEMO-%' LIMIT 1"
	)[0][0]
	return {
		"is_synthetic_preview": int(count) > 0,
		"synthetic_entry_count": int(count),
	}


@frappe.whitelist()
def get_snapshot_age(snapshot_date):
	"""Return generated_at + age_seconds for the snapshot age pill."""
	_require_cockpit_role()
	snapshot_date = getdate(snapshot_date)
	row = frappe.db.get_value(
		"DGV TB Snapshot",
		{"snapshot_date": snapshot_date},
		["generated_at", "status"],
		as_dict=True,
	)
	if not row:
		return {"generated_at": None, "age_seconds": None, "status": None}

	now = now_datetime()
	age = now - get_datetime(row["generated_at"])
	return {
		"generated_at": row["generated_at"].isoformat() if row["generated_at"] else None,
		"age_seconds": int(age.total_seconds()),
		"status": row["status"],
	}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_date(d):
	return d.isoformat() if d else None


def _parse_sparkline(raw):
	if not raw:
		return []
	try:
		parsed = json.loads(raw)
	except Exception:
		return []
	return parsed if isinstance(parsed, list) else []


def _format_value(value, fmt):
	"""Server-side formatting so JS doesn't drift on rounding."""
	value = flt(value)
	if fmt == "lakh":
		return f"{value / 100000:,.1f} L"
	if fmt == "auto":
		if abs(value) >= 10000000:
			return f"{value / 10000000:,.1f} Cr"
		return f"{value / 100000:,.1f} L"
	# default: crore
	return f"{value / 10000000:,.1f} Cr"


def _format_delta(delta, fmt):
	"""Format delta with sign prefix."""
	delta = flt(delta)
	sign = "+" if delta > 0 else ("" if delta == 0 else "-")
	formatted = _format_value(abs(delta), fmt)
	return f"{sign}{formatted}" if delta != 0 else formatted


# ---------------------------------------------------------------------------
# Cockpit headline (Phase 4 commit 2.5)
# ---------------------------------------------------------------------------
#
# Generates the one-sentence summary that sits above the spotlight grid
# in the executive-briefing layout. Reads the same data as the spotlight
# endpoints (cache when scope is full, recomputed when scoped) and
# composes natural English from the largest month-over-month deltas.

# Friendly card names for headline copy. Keys must match cards.py ids.
HEADLINE_CARD_NAMES = {
	"sundry_creditors":       "Sundry creditors",
	"sundry_debtors":         "Sundry debtors",
	"unsecured_loans":        "Unsecured loans",
	"cash_and_bank":          "Cash position",
	"inter_co_receivable":    "Inter-company receivables",
	"fixed_deposits":         "Fixed deposits",
	"financial_exp_to_bank":  "Bank finance costs",
	"financial_exp_to_other": "Other finance costs",
	# Per spec `specs/cash-bank-card-split.md` §6.2: labels match
	# headline copy directly for these two -- they read naturally
	# inline ("Liquid cash up Rs 2.3 Cr from last month"). Entries for
	# disabled cards (sundry_debtors / cash_and_bank /
	# inter_co_receivable) stay in this map so flipping `disabled`
	# back later doesn't require a re-add; the visible-cards filter
	# at the read sites is the source of truth for headline inclusion.
	"liquid_cash":            "Liquid cash",
	"secured_loans":          "Secured loans",
	# Companion to `sundry_creditors`. Same label as the card
	# (matches the liquid_cash / secured_loans pattern -- label and
	# headline copy converge for cards where the label is already
	# prose-friendly inline).
	"supplier_advances":      "Supplier advances",
}

# Significance threshold for inclusion in the headline.
HEADLINE_DELTA_THRESHOLD_RUPEES = 1_000_000.0  # ₹0.10 Cr


@frappe.whitelist()
def get_cockpit_headline(as_of_date, companies=None):
	"""One-sentence MoM summary for the cockpit's headline section.

	Reads spotlight values for `as_of_date` and the prior month-end,
	picks the most significant deltas (abs >= 0.1 Cr) on cards that
	have a real prior baseline, and composes a plain-English sentence.
	Seven template branches:

	  - All cards lack baseline (first snapshot for this scope):
	    "First snapshot for this scope — no prior month to compare."
	  - 0 significant deltas: "All key metrics held steady from <PrevMonth>."
	  - 1 significant delta:  "<Name> [up|down] by ₹X.X Cr since <PrevMonth>."
	  - 2 same sign:          "<Name1> and <Name2> both [rose|fell] over the
	                           month, by ₹X1 Cr and ₹X2 Cr respectively."
	  - 2 opposite signs:     "<UpName> strengthened by ₹X1 Cr; <DownName>
	                           declined by ₹X2 Cr over the month."
	  - 3+ deltas:            Falls into either "same sign" or "opposite
	                           signs" template using the top two by
	                           abs(delta).

	Returns: { "headline": "<sentence>" } or { "headline": "" } when
	the user has no companies in scope.
	"""
	_require_cockpit_role()
	snapshot_date = getdate(as_of_date)

	allowed = _resolve_scope(companies)
	if not allowed:
		return {"headline": ""}

	prior_month_date = prior_month_snapshot_date(snapshot_date)

	# Phase 4 commit 2.5 fix 3: filter out cards without a real prior
	# baseline before significance evaluation. Cards where the prior
	# month had no matching snapshot rows for this scope are excluded.
	# If ALL cards lack baseline, emit the dedicated "first snapshot"
	# template.
	#
	# Per spec `specs/cash-bank-card-split.md` §5.3 + Q2: disabled cards
	# are also excluded from headline narration. Narrating a delta for
	# a hidden card would be confusing ("Sundry debtors up Rs X Cr"
	# with no visible card to read) and would leak a value the user
	# was meant to not see.
	visible_cards = [c for c in CARDS if not c.get("disabled")]
	deltas = []
	for card in visible_cards:
		has_baseline = _card_has_prior_baseline(card, prior_month_date, allowed)
		if not has_baseline:
			continue
		current = aggregate_card_value(card, snapshot_date, companies=allowed)
		prior = aggregate_card_value(card, prior_month_date, companies=allowed)
		delta = round(flt(current) - flt(prior), 2)
		deltas.append({
			"card_id": card["id"],
			"name": HEADLINE_CARD_NAMES.get(card["id"], card["label"]),
			"delta": delta,
		})

	if not deltas:
		# No card has a prior-month baseline in this scope. Cleanest:
		# tell the operator that and skip the misleading per-card MoM
		# template. (Distinct from "0 significant deltas" — there
		# IS a prior month, just no movement worth reporting.)
		return {"headline": "First snapshot for this scope — no prior month to compare."}

	significant = [d for d in deltas if abs(d["delta"]) >= HEADLINE_DELTA_THRESHOLD_RUPEES]
	significant.sort(key=lambda d: abs(d["delta"]), reverse=True)

	prev_month_label = _month_long_name(prior_month_date)
	headline = _compose_headline(significant, prev_month_label)
	return {"headline": headline}


def _compose_headline(significant, prev_month_label):
	"""Render one of the six "real-comparison" template branches.

	Caller (`get_cockpit_headline`) handles the seventh branch — "First
	snapshot for this scope" — when no card has a prior baseline.
	"""
	if not significant:
		return f"All key metrics held steady from {prev_month_label}."

	if len(significant) == 1:
		d = significant[0]
		direction = "up" if d["delta"] > 0 else "down"
		return (
			f"{d['name']} {direction} by {_cr(d['delta'])} "
			f"since {prev_month_label}."
		)

	# 2 or more — take top two by abs(delta).
	first, second = significant[0], significant[1]
	same_sign = (first["delta"] > 0) == (second["delta"] > 0)

	if same_sign:
		direction = "rose" if first["delta"] > 0 else "fell"
		return (
			f"{first['name']} and {second['name']} both {direction} "
			f"over the month, by {_cr(first['delta'])} and "
			f"{_cr(second['delta'])} respectively."
		)

	# Opposite signs.
	up_card = first if first["delta"] > 0 else second
	down_card = second if first["delta"] > 0 else first
	return (
		f"{up_card['name']} strengthened by {_cr(up_card['delta'])}; "
		f"{down_card['name']} declined by {_cr(down_card['delta'])} "
		f"over the month."
	)


def _cr(rupees):
	"""Format an absolute rupee amount as ₹X.X Cr."""
	cr = abs(flt(rupees)) / 10_000_000.0
	return f"₹{cr:.1f} Cr"


def _month_long_name(d):
	"""Return the long English month name for a date (e.g. 'April')."""
	return getdate(d).strftime("%B")
