"""Resolve `cards.py` predicates to a leaf account list.

Used by:
  - the spotlight-card-click drill path (commit 7) -- the cockpit JS
    reads a card's `match` predicate and `label`, calls
    `resolve_match_to_accounts`, then calls `account_drill_v1.
    get_account_breakdown` with the resolved `accounts` list.
  - Phase 5's card editor (eventually) -- live preview of which leaf
    accounts a `match` predicate would aggregate.

Reads `tabAccount` only. Mirrors the predicate-translation logic in
`snapshots.spotlight_refresh._match_clause`, but emits a list of leaf
account full names rather than a snapshot-row WHERE clause.

This module is a cockpit read (no `_drill` suffix) -- it does not
touch `tabGL Entry` and does not require the Phase 4 §3 amendment.
"""

import json

import frappe

from dux_groupview.dux_groupview.api.utils import _named_in


@frappe.whitelist()
def resolve_match_to_accounts(match, companies=None, label=None):
	"""Resolve a card `match` predicate to a leaf account list.

	Args:
		match: dict matching the cards.py schema. Either
			{"by_account_type": <str | list[str]>} or
			{"by_root_type_and_name_pattern":
				{"root_type": <str>, "name_pattern": <SQL LIKE pattern>}}.
			Whitelist serialisation may pass this as a JSON string.
		companies: optional explicit company scope (intersected with
			User Permissions server-side). Whitelist serialisation may
			pass this as a JSON string.
		label: optional human-readable label echoed back in the
			response (per spec §4.7 step 3 -- the cockpit JS already
			has the card's label and forwards it as `scope_label` to
			`get_account_breakdown`; echoing it here keeps the helper's
			response self-describing).

	Returns:
		{"accounts": list[str], "label": str}

	The returned account names are full company-suffixed (the same
	form the snapshot row's `account` column and `tabGL Entry.account`
	use), suitable as the `accounts` parameter to
	`account_drill_v1.get_account_breakdown`.
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	if isinstance(match, str):
		try:
			match = json.loads(match)
		except (ValueError, TypeError):
			match = None
	if isinstance(companies, str):
		try:
			companies = json.loads(companies)
		except (ValueError, TypeError):
			companies = None

	# Distinguish "valid request, empty result" from "predicate is
	# malformed / refers to nothing". A well-formed predicate that
	# matches zero leaves stays a 200 + empty list (legitimate today
	# for cards like Inter-co receivable on dev seed). A predicate
	# that's None / missing required keys is a stale-deep-link case
	# and surfaces as 404 with malformed_scope:true so the gl-drill
	# / party-list pages can show the targeted "this link is no
	# longer valid" tile instead of a generic 500. (Phase 4 commit 6
	# HALT 6.2 carryover.)
	if not isinstance(match, dict) or not match:
		frappe.local.response["malformed_scope"] = True
		frappe.throw(
			frappe._("Match predicate is missing or malformed."),
			frappe.DoesNotExistError,
		)

	allowed = _resolve_scope(companies)
	accounts = _resolve_match(match, allowed) if allowed else []
	return {"accounts": sorted(accounts), "label": label or ""}


def _resolve_match(match: dict, companies: list) -> list:
	"""Translate a `match` predicate to leaf account full names.

	Mirror of `spotlight_refresh._match_clause` but against
	`tabAccount` (returns names) instead of `tabDGV TB Snapshot Row`
	(returns aggregated balances).
	"""
	if not isinstance(match, dict) or not companies:
		return []

	co_ph, co_params = _named_in("co", companies)

	if "by_account_type" in match:
		v = match["by_account_type"]
		if isinstance(v, (list, tuple)):
			at_ph, at_params = _named_in("at", v)
			return frappe.db.sql_list(
				f"""
				SELECT name FROM `tabAccount`
				WHERE is_group = 0
				  AND account_type IN ({at_ph})
				  AND company IN ({co_ph})
				""",
				{**at_params, **co_params},
			)
		return frappe.db.sql_list(
			f"""
			SELECT name FROM `tabAccount`
			WHERE is_group = 0
			  AND account_type = %(account_type)s
			  AND company IN ({co_ph})
			""",
			{"account_type": v, **co_params},
		)

	if "by_root_type_and_name_pattern" in match:
		conf = match["by_root_type_and_name_pattern"]
		if not isinstance(conf, dict):
			return []
		return frappe.db.sql_list(
			f"""
			SELECT name FROM `tabAccount`
			WHERE is_group = 0
			  AND root_type = %(root_type)s
			  AND name LIKE %(name_pattern)s
			  AND company IN ({co_ph})
			""",
			{
				"root_type": conf.get("root_type"),
				"name_pattern": conf.get("name_pattern"),
				**co_params,
			},
		)

	return []
