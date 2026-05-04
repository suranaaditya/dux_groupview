"""Pivot grid data API.

Reads only `tabDGV TB Snapshot Row`, `tabAccount`, `tabCompany`. Never
`tabGL Entry`. Never `tabDGV Spotlight Cache` (separate concern).

The hard rule from CLAUDE.md applies: no UI code path touches
`tabGL Entry`. The pivot is the densest read path in the cockpit and
sits two layers above the source data:

    tabGL Entry  -- only refresh.py reads
        |
    tabDGV TB Snapshot Row  -- this module reads
        |
    pivot data JSON  -- DuxPivotGrid (JS) consumes
"""

import json
from collections import Counter, defaultdict
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime

from dux_groupview.dux_groupview.pivot.trust_groups import (
	DEFAULT_TRUST,
	TRUSTS,
	get_company_trust_assignments,
)


ALLOWED_ROLES = {"System Manager", "GroupView Owner", "GroupView Viewer"}


def _require_cockpit_role():
	if not (ALLOWED_ROLES & set(frappe.get_roles())):
		frappe.throw(
			_("Pivot data is restricted to GroupView roles."),
			frappe.PermissionError,
		)


def _allowed_companies():
	"""Return company names visible to the current user.

	Respects User Permissions on Company. We deliberately do NOT route
	through `frappe.get_list("Company", ...)` because that requires the
	user to have read permission on the Company doctype, and the
	GroupView Viewer / Owner roles do not grant that (Company is a
	stock ERPNext doctype we don't want to modify). Authorization here
	is via User Permissions only -- if the user has User Permissions on
	Company, only those are returned; otherwise all companies are
	visible. System Managers always see everything.
	"""
	user = frappe.session.user
	roles = frappe.get_roles(user)
	if "System Manager" in roles:
		return frappe.db.sql_list("SELECT name FROM tabCompany ORDER BY name")
	user_perms = frappe.permissions.get_user_permissions(user) or {}
	company_perms = user_perms.get("Company") or []
	if company_perms:
		# Each entry is a dict with at least a "doc" key.
		return sorted({p.get("doc") for p in company_perms if p.get("doc")})
	return frappe.db.sql_list("SELECT name FROM tabCompany ORDER BY name")


def _resolve_scope(companies):
	"""Resolve the requested company scope against the user's permissions.

	Returns the sorted list of companies the request may read. The
	intersection rule is the security boundary -- a user can never widen
	their own visibility through the `companies` argument.

	`companies` can be:
	  - None / "" / [] -> use the user's full allowed set.
	  - JSON-stringified list of names (frappe.call serialises arrays).
	  - Python list of names.
	"""
	allowed = set(_allowed_companies())
	if companies in (None, "", "null"):
		return sorted(allowed)
	if isinstance(companies, str):
		try:
			companies = json.loads(companies)
		except (ValueError, TypeError):
			# Treat malformed input as "no scope provided" -- safer than
			# returning an empty set, which would render an empty cockpit
			# silently.
			return sorted(allowed)
	if not isinstance(companies, (list, tuple, set)):
		return sorted(allowed)
	requested = {c for c in companies if isinstance(c, str)}
	if not requested:
		return sorted(allowed)
	return sorted(allowed & requested)


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_pivot_data(snapshot_date, format="crore", companies=None):
	"""Return the full pivot dataset for one snapshot date.

	`companies` is an optional explicit scope (JSON-stringified list of
	company names from the trust selector). When provided, it is
	intersected with the user's User-Permission-allowed set on the
	server -- a user can never widen their own visibility through this
	parameter. When None, falls back to the user's full allowed set.

	Shape:
	  {
	    snapshot_date, snapshot_age_seconds, format,
	    trusts:    [{id, name, abbr, color, companies}, ...],
	    accounts:  [{id, name, parent, depth, is_group, root_type, account_type}, ...],
	    balances:  {account_name: {company_name: raw_balance}}
	  }
	"""
	_require_cockpit_role()
	snapshot_date = getdate(snapshot_date)

	allowed = _resolve_scope(companies)
	if not allowed:
		return _empty_payload(snapshot_date, format)

	rows = frappe.db.sql(
		"""
		SELECT r.company, r.account, r.balance, r.root_type, r.account_type,
		       a.account_name, a.is_group, a.parent_account
		FROM `tabDGV TB Snapshot Row` r
		LEFT JOIN `tabAccount` a ON a.name = r.account
		WHERE r.snapshot_date = %(snapshot_date)s
		  AND r.company IN %(companies)s
		""",
		{
			"snapshot_date": snapshot_date,
			"companies": _sql_in_tuple(allowed),
		},
		as_dict=True,
	)

	trusts = _build_trusts(allowed)
	accounts, balances = _build_accounts_and_balances(rows)

	return {
		"snapshot_date": str(snapshot_date),
		"snapshot_age_seconds": _snapshot_age_seconds(snapshot_date),
		"format": format,
		"trusts": trusts,
		"accounts": accounts,
		"balances": balances,
	}


@frappe.whitelist()
def get_pivot_summary(snapshot_date, companies=None):
	"""Return lightweight metadata about a snapshot.

	`companies` follows the same intersection-with-user-permissions rule
	as get_pivot_data.
	"""
	_require_cockpit_role()
	snapshot_date = getdate(snapshot_date)

	parent = frappe.db.get_value(
		"DGV TB Snapshot",
		{"snapshot_date": snapshot_date},
		["generated_at", "row_count", "status"],
		as_dict=True,
	)
	if not parent:
		return {
			"snapshot_date": str(snapshot_date),
			"generated_at": None,
			"row_count": 0,
			"company_count": 0,
			"trust_count": 0,
		}

	allowed = _resolve_scope(companies)
	if not allowed:
		return {
			"snapshot_date": str(snapshot_date),
			"generated_at": parent["generated_at"].isoformat() if parent["generated_at"] else None,
			"row_count": 0,
			"company_count": 0,
			"trust_count": 0,
		}

	rows = frappe.db.sql(
		"""
		SELECT COUNT(DISTINCT company) AS cc, COUNT(*) AS rc
		FROM `tabDGV TB Snapshot Row`
		WHERE snapshot_date = %(snapshot_date)s
		  AND company IN %(companies)s
		""",
		{
			"snapshot_date": snapshot_date,
			"companies": _sql_in_tuple(allowed),
		},
		as_dict=True,
	)[0]

	trusts = _build_trusts(allowed)

	return {
		"snapshot_date": str(snapshot_date),
		"generated_at": parent["generated_at"].isoformat() if parent["generated_at"] else None,
		"row_count": int(rows["rc"]),
		"company_count": int(rows["cc"]),
		"trust_count": len(trusts),
	}


@frappe.whitelist()
def get_scope_options():
	"""Return the universe of trusts × companies the current user can see.

	Used by the trust-selector popover to render its tree, independent of
	the currently-applied scope. Shape:

	    {trusts: [{id, name, abbr, color, companies}, ...]}
	"""
	_require_cockpit_role()
	allowed = _allowed_companies()
	return {"trusts": _build_trusts(allowed)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sql_in_tuple(values):
	"""Return a tuple usable in a MariaDB `WHERE col IN %(...)s` placeholder.

	MariaDB rejects 1-element tuples with a syntax error after parameter
	substitution (`IN ('x',)` -> `IN ('x', )` which can confuse some
	drivers); we duplicate the lone value to keep the SQL valid. For
	2+ values, `tuple(values)` is fine.
	"""
	values = list(values)
	if len(values) == 1:
		return (values[0], values[0])
	return tuple(values)


def _empty_payload(snapshot_date, format):
	return {
		"snapshot_date": str(snapshot_date),
		"snapshot_age_seconds": None,
		"format": format,
		"trusts": [],
		"accounts": [],
		"balances": {},
	}


def _snapshot_age_seconds(snapshot_date):
	row = frappe.db.get_value(
		"DGV TB Snapshot",
		{"snapshot_date": snapshot_date},
		"generated_at",
	)
	if not row:
		return None
	return int((now_datetime() - get_datetime(row)).total_seconds())


def _build_trusts(allowed_companies):
	"""Return [{id, name, abbr, color, companies}] filtered to allowed.

	Trusts with zero allowed companies are omitted. Companies not in any
	defined trust go under DEFAULT_TRUST. The default trust appears last
	if it has any companies.
	"""
	assignments = get_company_trust_assignments()
	by_trust = defaultdict(list)

	for company in sorted(allowed_companies):
		trust_id = assignments.get(company, "default")
		by_trust[trust_id].append(company)

	out = []
	for trust in TRUSTS:
		members = by_trust.get(trust["id"], [])
		if not members:
			continue
		out.append({
			"id": trust["id"],
			"name": trust["name"],
			"abbr": trust["abbr"],
			"color": trust["color"],
			"companies": members,
		})

	default_members = by_trust.get("default", [])
	if default_members:
		out.append({
			"id": DEFAULT_TRUST["id"],
			"name": DEFAULT_TRUST["name"],
			"abbr": DEFAULT_TRUST["abbr"],
			"color": DEFAULT_TRUST["color"],
			"companies": default_members,
		})

	return out


def _build_accounts_and_balances(rows):
	"""Group snapshot rows by `account_name` and reconstruct hierarchy.

	Each unique `account_name` becomes one pivot row. Group / parent
	accounts that have no snapshot rows are added to the list (with
	empty balances) so the hierarchy is intact and the UI can show
	expandable parents. Hierarchy is taken from the most-common
	parent_account name across all rows for that account (after
	stripping the company-suffix).
	"""
	# Group raw rows by account_name.
	by_acct = defaultdict(list)
	for r in rows:
		key = r.get("account_name") or _strip_company_suffix(r["account"])
		by_acct[key].append(r)

	# Build balances map.
	balances = {}
	for acct_name, group in by_acct.items():
		balances[acct_name] = {}
		for r in group:
			balances[acct_name][r["company"]] = float(flt(r["balance"]))

	# Initial meta for leaves found in the snapshot.
	account_meta = {}
	for acct_name, group in by_acct.items():
		parents = Counter()
		root_types = Counter()
		account_types = Counter()
		is_groups = Counter()
		for r in group:
			parent_raw = r.get("parent_account") or ""
			parent_clean = _strip_company_suffix(parent_raw) if parent_raw else ""
			parents[parent_clean] += 1
			root_types[r.get("root_type") or ""] += 1
			account_types[r.get("account_type") or ""] += 1
			is_groups[bool(r.get("is_group"))] += 1
		account_meta[acct_name] = {
			"name": acct_name,
			"parent": parents.most_common(1)[0][0] if parents else "",
			"is_group": bool(is_groups.most_common(1)[0][0]) if is_groups else False,
			"root_type": root_types.most_common(1)[0][0] if root_types else "",
			"account_type": account_types.most_common(1)[0][0] if account_types else "",
		}

	# Walk up the tabAccount tree to add any ancestor groups that aren't
	# already in account_meta. We resolve each missing parent by querying
	# the most representative tabAccount row whose stripped name equals
	# the parent name.
	pending = {
		meta["parent"] for meta in account_meta.values()
		if meta["parent"] and meta["parent"] not in account_meta
	}
	while pending:
		next_pending = set()
		for parent_name in pending:
			row = _lookup_group_by_stripped_name(parent_name)
			if not row:
				# Parent doesn't exist in tabAccount with that stripped name.
				# Treat it as a true root (parent="").
				account_meta[parent_name] = {
					"name": parent_name,
					"parent": "",
					"is_group": True,
					"root_type": "",
					"account_type": "",
				}
				continue
			grandparent = (
				_strip_company_suffix(row["parent_account"])
				if row.get("parent_account") else ""
			)
			account_meta[parent_name] = {
				"name": parent_name,
				"parent": grandparent,
				"is_group": True,
				"root_type": row.get("root_type") or "",
				"account_type": row.get("account_type") or "",
			}
			if grandparent and grandparent not in account_meta:
				next_pending.add(grandparent)
		pending = next_pending

	# Aggregate every direct snapshot-row contribution upward into its
	# ancestors, so the response carries per-company totals for every
	# non-leaf row. This is a B-lazy approach: cheaper to recompute
	# group totals at request time than to maintain them in the
	# snapshot cache (which would mean rebuilding cache rows for group
	# accounts every refresh, doubling the row count).
	#
	# We bubble up to ALL ancestors regardless of `is_group`, not just
	# is_group=True ones. Real-world charts of accounts contain mid-
	# tree accounts that are flagged is_group=False in the snapshot but
	# still have children in the response (e.g. an "Unsecured Loans"
	# account with its own snapshot row AND child accounts hanging
	# under it). Skipping is_group=False ancestors during aggregation
	# would let descendants jump past them to the next is_group=True
	# ancestor, leaving the intermediate's stored balance as just its
	# own row -- inconsistent with the surrounding subtree. By always
	# aggregating, we preserve the invariant `balance[node] = own_row
	# + sum(balance[child] for child in children(node))` at every level
	# of the response tree.
	#
	# Important: aggregate from a snapshot of the *direct* (own-row)
	# balances captured before any mutation, so a node's own direct
	# value never gets added to its ancestors twice (once as its own
	# contribution, once via the already-bubbled-up descendants).
	#
	# Sign convention: balances are stored as raw `Dr - Cr`, just like
	# leaves. The UI flips sign per root_type for display. Naive
	# summing preserves the descendant-sum invariant in this stored
	# sign.
	direct_balances = {
		name: dict(per_company) for name, per_company in balances.items()
	}
	for direct_name, direct_company_balances in direct_balances.items():
		cur_parent = account_meta[direct_name]["parent"]
		while cur_parent and cur_parent in account_meta:
			bucket = balances.setdefault(cur_parent, {})
			for c, v in direct_company_balances.items():
				bucket[c] = bucket.get(c, 0.0) + v
			cur_parent = account_meta[cur_parent]["parent"]

	# Ensure every account has a balances entry, even if zero, so the
	# frontend renders the row regardless of whether it had any
	# contribution. Zero-balance groups still need to appear so the
	# user can see the structure of the chart of accounts.
	for name in account_meta:
		if name not in balances:
			balances[name] = {}

	# Build the children index, then walk depth-first.
	children = defaultdict(list)
	roots = []
	for name, meta in account_meta.items():
		if meta["parent"] and meta["parent"] in account_meta:
			children[meta["parent"]].append(name)
		else:
			roots.append(name)

	for parent_key in children:
		children[parent_key].sort()
	roots.sort()

	ordered = []

	def _walk(name, depth):
		meta = account_meta[name]
		ordered.append({
			"id": name,
			"name": name,
			"parent": meta["parent"] if meta["parent"] in account_meta else "",
			"depth": depth,
			"is_group": meta["is_group"],
			"root_type": meta["root_type"],
			"account_type": meta["account_type"],
		})
		for child in children.get(name, []):
			_walk(child, depth + 1)

	for root in roots:
		_walk(root, 0)

	return ordered, balances


_GROUP_LOOKUP_CACHE = {}


def _lookup_group_by_stripped_name(stripped_name):
	"""Find one tabAccount row whose stripped name matches stripped_name.

	Used to discover ancestor groups not present in the snapshot. Cached
	per-request.
	"""
	if stripped_name in _GROUP_LOOKUP_CACHE:
		return _GROUP_LOOKUP_CACHE[stripped_name]
	# Try direct match first (account whose name equals the stripped form).
	row = frappe.db.sql(
		"""
		SELECT name, parent_account, root_type, account_type, is_group
		FROM `tabAccount`
		WHERE account_name = %s AND is_group = 1
		LIMIT 1
		""",
		(stripped_name,),
		as_dict=True,
	)
	result = row[0] if row else None
	_GROUP_LOOKUP_CACHE[stripped_name] = result
	return result


_COMPANY_ABBR_CACHE = {}


def _strip_company_suffix(account_or_parent_name):
	"""Strip the trailing ` - {company_abbr}` from a Frappe Account name.

	Accounts in ERPNext are named "Account Name - ABBR". Build a cache
	of all known abbreviations once per request, then strip exactly the
	matching suffix. Falls back to the raw string if no known abbr is a
	suffix.
	"""
	if not account_or_parent_name:
		return account_or_parent_name
	if not _COMPANY_ABBR_CACHE:
		for abbr in frappe.db.sql_list("SELECT DISTINCT abbr FROM `tabCompany`"):
			if abbr:
				_COMPANY_ABBR_CACHE[abbr] = True
	for abbr in _COMPANY_ABBR_CACHE:
		suffix = f" - {abbr}"
		if account_or_parent_name.endswith(suffix):
			return account_or_parent_name[: -len(suffix)]
	return account_or_parent_name
