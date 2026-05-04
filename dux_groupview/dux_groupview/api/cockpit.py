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
	cache_by_id = {r["card_id"]: r for r in cache_rows}

	out = []
	for card in CARDS:
		cache = cache_by_id.get(card["id"])
		value = flt(cache["value"]) if cache else 0.0
		delta = flt(cache["delta"]) if cache else 0.0
		delta_percent = flt(cache["delta_percent"]) if cache else 0.0
		sparkline = _parse_sparkline(cache["sparkline_data"] if cache else None)

		out.append({
			"card_id": card["id"],
			"label": card["label"],
			"polarity": card["polarity"],
			"format": card["format"],
			"color": card["color"],
			"value": value,
			"delta": delta,
			"delta_percent": delta_percent,
			"sparkline_data": sparkline,
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
	if not allowed:
		# No accessible companies after intersection -- return zeroed
		# cards in the same shape as get_spotlight_cards.
		return [_zero_card_payload(card) for card in CARDS]

	prior_month_date = prior_month_snapshot_date(snapshot_date)
	hist_dates = historical_month_end_dates(snapshot_date, SPARKLINE_LENGTH)

	out = []
	for card in CARDS:
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

		out.append({
			"card_id": card["id"],
			"label": card["label"],
			"polarity": card["polarity"],
			"format": card["format"],
			"color": card["color"],
			"value": value,
			"delta": delta,
			"delta_percent": delta_percent,
			"sparkline_data": sparkline,
			"formatted_value": _format_value(value, card["format"]),
			"formatted_delta": _format_delta(delta, card["format"]),
		})
	return out


def _zero_card_payload(card):
	return {
		"card_id": card["id"],
		"label": card["label"],
		"polarity": card["polarity"],
		"format": card["format"],
		"color": card["color"],
		"value": 0.0,
		"delta": 0.0,
		"delta_percent": 0.0,
		"sparkline_data": [],
		"formatted_value": _format_value(0.0, card["format"]),
		"formatted_delta": _format_delta(0.0, card["format"]),
	}


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
