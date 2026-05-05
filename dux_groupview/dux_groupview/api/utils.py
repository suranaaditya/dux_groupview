"""Shared helpers used across the cockpit's API surface.

Functions here must NOT read from `tabGL Entry` directly -- they
support cockpit reads, which per the Phase 4 amendment in CLAUDE.md
is restricted to `_drill`-suffixed APIs satisfying conditions (a)-(g).
The helpers here read from `tabAccount` (metadata) and the snapshot
tables only.

Phase 4 commit 1 introduces `_walk_subtree_leaves`. The cockpit's
existing `_resolve_scope` helper continues to live in `api/pivot.py`
for now -- moving it would be a "while I'm here" cleanup outside the
scope of this commit. A future commit can centralise both helpers in
this module if it becomes useful.
"""

import frappe


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
