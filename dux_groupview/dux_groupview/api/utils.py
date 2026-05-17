"""Shared helpers used across the cockpit's API surface.

Functions here must NOT read from `tabGL Entry` directly -- they
support cockpit reads, which per the Phase 4 amendment in CLAUDE.md
is restricted to `_drill`-suffixed APIs satisfying conditions (a)-(g).
The helpers here read from `tabAccount` (metadata) and the snapshot
tables only.

Phase 4 commit 1 introduced `_walk_subtree_leaves`. Commit 2 adds the
`FLIP_ROOT_TYPES` and `PARTY_TRACKABLE_ACCOUNT_TYPES` constants plus
the `_resolve_scope_to_leaves` helper used by both drill APIs. The
cockpit's existing `_resolve_scope` helper continues to live in
`api/pivot.py` -- moving it would be a "while I'm here" cleanup
outside the scope of this commit.
"""

import frappe


# ---------------------------------------------------------------------------
# Sign convention
# ---------------------------------------------------------------------------
# The TB snapshot stores raw `SUM(debit) - SUM(credit)` (Phase 1
# decision -- preserves the invariant `balance = debit_total -
# credit_total`). UI readers apply a sign flip on read for these root
# types so values display positive on their natural side.
#
# Every cockpit reader -- spotlight aggregation, account drill, pivot
# group totals, focus mode tiles, party drill -- must use this exact
# tuple. Drift here causes silent disagreement between cockpit panels.
# Pinned by tests in tests/test_party_drill.py:
#   - test_flip_root_types_literal       -- catches accidental edits
#   - test_sign_convention_parity_*      -- catches semantic divergence
#     between spotlight aggregation and party drill aggregation
FLIP_ROOT_TYPES = ("Liability", "Equity", "Income")


# ---------------------------------------------------------------------------
# Party-trackable account_type allow-list (Q17, locked at default)
# ---------------------------------------------------------------------------
# Account types whose presence on a resolved leaf list causes
# `is_party_trackable=True` in account drill responses. Locked at the
# convention-based default per commit 1 finding 2; the dev seed has
# essentially no party data so empirical validation is deferred to
# production rollout (Q19, audit_group_co_name_match in
# api/party_drill_v1.py).
PARTY_TRACKABLE_ACCOUNT_TYPES = ("Receivable", "Payable", "Loan")


def _walk_subtree_leaves(parent_account_name: str, company: str) -> list[str]:
	"""Return all leaf account names under `parent_account_name` in `company`.

	A leaf account is one with `is_group = 0`. Uses tabAccount's
	nested-set `lft` / `rgt` columns for efficient subtree query
	(O(log N) lookup of the parent's range, O(K) for K leaves in the
	subtree -- both index-backed).

	`parent_account_name` accepts either:
	  - the full company-suffixed name (e.g. "Sundry Creditors - GHRCE"), or
	  - the stripped `account_name` value (e.g. "Sundry Creditors").

	Returns an empty list when:
	  - the parent does not exist in this company, OR
	  - the parent exists but is not a group account (is_group = 0,
	    i.e. already a leaf -- no descendants), OR
	  - the parent is a group with no leaf descendants in this company.

	The function intentionally takes a `company` argument and never
	walks across companies, because ERPNext maintains a separate
	tabAccount tree per Company; lft/rgt are only meaningful within
	one company's tree.
	"""
	# Resolve parent. Match either full name or stripped account_name.
	# ORDER BY lft + LIMIT 1 disambiguates the unlikely case of
	# multiple matches (e.g. two groups with the same account_name in
	# the same company); the highest in the tree wins.
	parent = frappe.db.sql(
		"""
		SELECT name, lft, rgt
		FROM `tabAccount`
		WHERE company = %(company)s
		  AND is_group = 1
		  AND (name = %(name)s OR account_name = %(name)s)
		ORDER BY lft
		LIMIT 1
		""",
		{"company": company, "name": parent_account_name},
		as_dict=True,
	)
	if not parent:
		return []
	p = parent[0]

	# All leaf descendants in this subtree.
	return frappe.db.sql_list(
		"""
		SELECT name
		FROM `tabAccount`
		WHERE company = %(company)s
		  AND is_group = 0
		  AND lft > %(lft)s AND rgt < %(rgt)s
		ORDER BY lft
		""",
		{"company": company, "lft": p["lft"], "rgt": p["rgt"]},
	)


def _apply_display_sign(value, display_sign):
	"""Apply a card's `display_sign` transform to an aggregated value.

	Mirror of `spotlight_refresh._apply_display_sign` but at the drill
	layer. Per spec/supplier-advances-display-and-exclude-fixes Fix 1,
	the spotlight cache writes display-corrected values. The drill APIs
	bypass the cache and aggregate live, so they need the same
	transform applied at the response boundary so the drill panel hero,
	by-company breakdown, by-account breakdown, and by-party breakdown
	render with the same sign convention as the card surface.

	Three values: "natural" (default, passthrough), "absolute"
	(`abs(value)`), "negated" (`-value`). Invalid -> passthrough
	(silent in the drill path; the spotlight refresh path logs a
	warning, which is enough surfaces).

	`value` of `None` or non-numeric returns unchanged (callers pass
	`None` for missing sparkline points; the transform must preserve
	that signal).
	"""
	if value is None:
		return None
	try:
		v = float(value)
	except (TypeError, ValueError):
		return value
	if display_sign == "absolute":
		return abs(v)
	if display_sign == "negated":
		return -v
	return v


def _named_in(prefix: str, values):
	"""Build named placeholders for a SQL IN clause.

	Returns (placeholders_string, params_dict). Use as:

	    ph, params = _named_in("co", companies)
	    sql = f"... WHERE company IN ({ph})"
	    rows = frappe.db.sql(sql, {**params, ...})

	Single-element IN tuples need no special casing -- MariaDB through
	the pyformat driver accepts `IN ('x')` as valid SQL.
	"""
	values = list(values)
	placeholders = ", ".join(f"%({prefix}_{i})s" for i in range(len(values)))
	params = {f"{prefix}_{i}": v for i, v in enumerate(values)}
	return placeholders, params


def _resolve_scope_to_leaves(scope: dict, companies: list) -> tuple:
	"""Translate a Phase 4 ScopeSpec to (leaf_full_names, default_label).

	`scope` shapes (per spec §4.6):

	    {"type": "account",      "value": "<account_name>"}
	    {"type": "subtree",      "value": "<parent_account_name>"}
	    {"type": "name_pattern", "value": "<SQL LIKE pattern>"}

	`companies` MUST be a permission-resolved list (intersection with
	User Permissions on Company already applied -- typically the
	output of `pivot._resolve_scope`). This helper does NOT enforce
	permissions; callers do.

	Returns (list_of_leaf_account_full_names, default_label). The
	leaf names are full company-suffixed (e.g. "Sundry Creditors - GHRCE")
	suitable for `account` filter on `tabDGV TB Snapshot Row` or
	`tabGL Entry`.
	"""
	if not isinstance(scope, dict):
		frappe.throw(frappe._("scope must be a dict"))
	type_ = scope.get("type")
	value = scope.get("value")
	if not isinstance(value, str) or not value:
		frappe.throw(frappe._("scope.value is required"))

	if not companies:
		return [], value

	co_ph, co_params = _named_in("co", companies)

	if type_ == "account":
		rows = frappe.db.sql_list(
			f"""
			SELECT name FROM `tabAccount`
			WHERE is_group = 0
			  AND account_name = %(value)s
			  AND company IN ({co_ph})
			""",
			{"value": value, **co_params},
		)
		return rows, value

	if type_ == "subtree":
		leaves = []
		for c in companies:
			leaves.extend(_walk_subtree_leaves(value, c))
		# Per-company walks emit per-company unique leaves; sorted +
		# deduplicated for determinism.
		return sorted(set(leaves)), value

	if type_ == "name_pattern":
		rows = frappe.db.sql_list(
			f"""
			SELECT name FROM `tabAccount`
			WHERE is_group = 0
			  AND name LIKE %(value)s
			  AND company IN ({co_ph})
			""",
			{"value": value, **co_params},
		)
		return rows, value

	frappe.throw(frappe._("Unknown scope.type: {0}").format(type_))
