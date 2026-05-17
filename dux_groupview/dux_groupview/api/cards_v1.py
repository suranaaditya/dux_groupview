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
		# `by_account_type` value shapes (three forms; first two are
		# shortcuts for the canonical dict form):
		#   "Payable"                                            -> single account_type
		#   ["Bank", "Cash"]                                     -> multiple account_types
		#   {"account_type": ...,                                -> canonical
		#    "balance_sign": "negative",                             optional sign filter
		#    "exclude_parent_stems": ["Unsecured Loans"]}            optional exclusion
		# `exclude_parent_stems` is accepted only in the canonical
		# dict form (shortcut shapes have no place to express it).
		if isinstance(v, str):
			account_types, balance_sign = [v], "any"
			exclude_parent_stems = None
		elif isinstance(v, (list, tuple)):
			# Strict: every element must be a string. Mixed-type lists
			# (e.g. `["Payable", {"balance_sign": "positive"}]`) are
			# NOT supported -- a card author wanting sign-filter +
			# multi-type must use the canonical dict shape:
			#   {"account_type": ["Payable", ...], "balance_sign": ...}
			# Defensive: any non-string element -> empty result.
			if not all(isinstance(x, str) for x in v):
				return []
			account_types, balance_sign = list(v), "any"
			exclude_parent_stems = None
		elif isinstance(v, dict):
			at = v.get("account_type")
			if isinstance(at, str):
				account_types = [at]
			elif isinstance(at, (list, tuple)):
				if not all(isinstance(x, str) for x in at):
					return []
				account_types = list(at)
			else:
				return []
			balance_sign = v.get("balance_sign", "any")
			exclude_parent_stems = v.get("exclude_parent_stems")
		else:
			return []
		if not account_types:
			return []
		if balance_sign not in ("positive", "negative", "any"):
			# Defensive: unrecognised sign -> empty (matches the
			# defensive shape used elsewhere in this resolver).
			return []
		at_ph, at_params = _named_in("at", account_types)
		ex_clause_unaliased, ex_clause_aliased, ex_params = (
			_exclude_parent_stems_clauses(exclude_parent_stems)
		)
		return _resolve_with_optional_sign_filter(
			tabaccount_only_sql=f"""
				SELECT name FROM `tabAccount`
				WHERE is_group = 0
				  AND account_type IN ({at_ph})
				  AND company IN ({co_ph})
				  {ex_clause_unaliased}
			""",
			tabaccount_sign_filtered_sql=f"""
				SELECT DISTINCT s.account
				FROM `tabDGV TB Snapshot Row` s
				JOIN `tabAccount` a ON a.name = s.account
				WHERE s.snapshot_date = (
				    SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
				    WHERE status = 'Complete'
				)
				  AND a.is_group = 0
				  AND a.account_type IN ({at_ph})
				  AND s.company IN ({co_ph})
				  AND s.balance {{sign_op}} 0
				  {ex_clause_aliased}
			""",
			params={**at_params, **co_params, **ex_params},
			balance_sign=balance_sign,
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

	if "by_parent_account_stem_in" in match:
		# Per spec `specs/cash-bank-card-split.md` §4: matches leaves
		# whose immediate parent group's stripped account_name (the
		# part BEFORE the first ` - ` separator in `parent_account`)
		# is in `stems`. Defensive: empty stems, missing root_type,
		# or wrong types -> empty.
		#
		# Optional `balance_sign` (added in spec/supplier-advances-split):
		# "positive" / "negative" / "any". When set, filters leaves to
		# those whose latest-snapshot balance has the matching sign.
		# Predicate types other than this one don't currently consume
		# the key but are designed to accept it for forward
		# consistency.
		conf = match["by_parent_account_stem_in"]
		if not isinstance(conf, dict):
			return []
		stems = conf.get("stems")
		root_type = conf.get("root_type")
		if not isinstance(stems, (list, tuple)) or not stems:
			return []
		if not isinstance(root_type, str) or not root_type:
			return []
		balance_sign = conf.get("balance_sign", "any")
		if balance_sign not in ("positive", "negative", "any"):
			return []
		exclude_parent_stems = conf.get("exclude_parent_stems")
		st_ph, st_params = _named_in("st", stems)
		ex_clause_unaliased, ex_clause_aliased, ex_params = (
			_exclude_parent_stems_clauses(exclude_parent_stems)
		)
		return _resolve_with_optional_sign_filter(
			tabaccount_only_sql=f"""
				SELECT name FROM `tabAccount`
				WHERE is_group = 0
				  AND root_type = %(root_type)s
				  AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN ({st_ph})
				  AND company IN ({co_ph})
				  {ex_clause_unaliased}
			""",
			tabaccount_sign_filtered_sql=f"""
				SELECT DISTINCT s.account
				FROM `tabDGV TB Snapshot Row` s
				JOIN `tabAccount` a ON a.name = s.account
				WHERE s.snapshot_date = (
				    SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
				    WHERE status = 'Complete'
				)
				  AND a.is_group = 0
				  AND a.root_type = %(root_type)s
				  AND SUBSTRING_INDEX(a.parent_account, ' - ', 1) IN ({st_ph})
				  AND s.company IN ({co_ph})
				  AND s.balance {{sign_op}} 0
				  {ex_clause_aliased}
			""",
			params={"root_type": root_type, **st_params, **co_params, **ex_params},
			balance_sign=balance_sign,
		)

	return []


def _exclude_parent_stems_clauses(value):
	"""Build the unaliased + aliased `NOT IN` exclusion clauses.

	Returns a 3-tuple `(unaliased_clause, aliased_clause, params)`.
	Used by both `by_account_type` and `by_parent_account_stem_in`
	branches of `_resolve_match`. The two clauses differ only in
	whether `parent_account` carries the `a.` alias prefix -- the
	`tabaccount_only_sql` template has no alias; the
	`tabaccount_sign_filtered_sql` template joins tabAccount as `a`.

	Defensive: missing / empty / non-list / non-string elements ->
	returns `("", "", {})` so the surrounding SQL template renders
	with no additional WHERE clause and no spurious params (no-op
	shape per cards.py docstring).
	"""
	if not isinstance(value, list) or not value:
		return ("", "", {})
	if not all(isinstance(x, str) for x in value):
		return ("", "", {})
	ex_ph, ex_params = _named_in("ex", value)
	unaliased = f"AND SUBSTRING_INDEX(parent_account, ' - ', 1) NOT IN ({ex_ph})"
	aliased = f"AND SUBSTRING_INDEX(a.parent_account, ' - ', 1) NOT IN ({ex_ph})"
	return (unaliased, aliased, ex_params)


def _resolve_with_optional_sign_filter(
	tabaccount_only_sql: str,
	tabaccount_sign_filtered_sql: str,
	params: dict,
	balance_sign: str,
) -> list:
	"""Run the right resolution query based on `balance_sign`.

	Per spec/supplier-advances-split §1 + HALT 1 decision point (a):

	  - `balance_sign == "any"`: query `tabAccount` only (default,
	    backward-compatible). No snapshot dependency -- safe for
	    fresh installs / mid-test environments where the snapshot
	    cache may not exist yet.

	  - `balance_sign in ("positive", "negative")`: JOIN
	    `tabDGV TB Snapshot Row` against `tabAccount` at the latest
	    `Complete` snapshot, filter leaves to those whose snapshot
	    `balance` has the matching sign. Drill panel sees the same
	    leaf set the card aggregated -- consistency between card
	    surface and drill expansion. Architectural rule 1 compliant
	    -- the snapshot row table is the cache layer, fair game from
	    UI code paths.

	The `tabaccount_sign_filtered_sql` argument carries a literal
	`{sign_op}` placeholder that we replace here with `>` or `<`.
	Sign comparison runs against the snapshot row's RAW `balance`
	column (debit-credit, pre-natural-side-flip). Balance > 0 = debit
	balance (advance-paid for Payable accounts); balance < 0 = credit
	balance (owed for Payable accounts).
	"""
	if balance_sign == "any":
		return frappe.db.sql_list(tabaccount_only_sql, params)
	sign_op = ">" if balance_sign == "positive" else "<"
	return frappe.db.sql_list(
		tabaccount_sign_filtered_sql.replace("{sign_op}", sign_op),
		params,
	)
