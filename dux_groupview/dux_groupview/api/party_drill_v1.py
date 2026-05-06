"""Party drill APIs.

Per Phase 4 spec §4.2. Two whitelisted endpoints share SQL shape and
differ only in GROUP BY:

  - `get_party_breakdown`         -- GROUP BY g.party_type, g.party
                                     (used by the account drill panel)
  - `get_party_company_breakdown` -- GROUP BY g.company
                                     (used by the disambiguation popover
                                      in commit 5)

Plus one non-whitelisted bench-only audit:

  - `audit_group_co_name_match`   -- production rollout audit (Q19).
                                     Reports each tabCompany row with
                                     whether a matching Customer/Supplier
                                     record exists. Run via
                                     `bench --site <site> execute
                                       dux_groupview.dux_groupview.api.party_drill_v1.audit_group_co_name_match`.

The architecture rule amended in CLAUDE.md (Phase 4 amendment) permits
the direct `tabGL Entry` reads in `_drill`-suffixed APIs because:
  (a) module name suffix `_drill`,
  (b) `_require_cockpit_role()` + `_resolve_scope()` enforce User
      Permissions on Company at API entry,
  (c) uses Phase 3 covering index `(is_cancelled, docstatus, company,
      account, posting_date)` plus the `dgv_party_drill` supplementary
      index added in commit 2's patch (see `patches/add_party_drill_index.py`),
  (d) results paginate at >100 rows (configurable page_size, max 200),
  (e) scope is bounded: a resolved leaf-account list AND a bounded
      company set (the user's allowed companies),
  (f) read-only -- only SELECT against `tabGL Entry`, no writes,
  (g) every returned row's company is in the allowed set (enforced
      via `WHERE g.company IN (...)`).

Sign convention parity (spec §4.2): the party drill SQL uses
`SUM(CASE WHEN a.root_type IN (FLIP_ROOT_TYPES) THEN g.credit - g.debit
ELSE g.debit - g.credit END)`, which is algebraically equivalent to
spotlight cache aggregation's `SUM(CASE WHEN root_type IN (...) THEN
-balance ELSE balance END)` against the snapshot's raw `Dr - Cr`
storage. Pinned by the parity tests in `test_party_drill.py`.
"""

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api.utils import (
	FLIP_ROOT_TYPES,
	_named_in,
	_resolve_scope_to_leaves,
)


DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 200
ALLOWED_SORTS = ("balance_desc", "balance_asc", "name_asc")


# ---------------------------------------------------------------------------
# get_party_breakdown -- group by party across companies
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_party_breakdown(scope=None, accounts=None, as_of_date=None,
                        companies=None, page=1, page_size=None,
                        sort="balance_desc"):
	"""Group GL entries by party across companies in scope.

	Output shape per spec §4.2.1.
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)

	page = _coerce_int(page, default=1, minimum=1)
	page_size = _coerce_int(page_size, default=DEFAULT_PAGE_SIZE,
	                        minimum=1, maximum=MAX_PAGE_SIZE)
	sort = sort if sort in ALLOWED_SORTS else "balance_desc"

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	leaves = _leaves_from_input(scope, accounts, allowed)
	if not leaves or not allowed:
		return _empty_party_breakdown(page, page_size)

	common_where, common_params, flip_ph = _common_where_clause(
		leaves, allowed, target_date,
	)

	# Total row count -- HAVING balance != 0 to drop net-zero parties
	# (a party with balanced rows that net to zero is uninteresting).
	total = _count_parties(common_where, common_params, flip_ph)
	if total == 0:
		return _empty_party_breakdown(page, page_size)

	# Page query.
	sort_clause = {
		"balance_desc": "balance DESC, g.party ASC",
		"balance_asc":  "balance ASC, g.party ASC",
		"name_asc":     "g.party ASC, g.party_type ASC",
	}[sort]
	offset = (page - 1) * page_size

	rows = frappe.db.sql(
		f"""
		SELECT
		  g.party_type,
		  g.party,
		  SUM(CASE WHEN a.root_type IN ({flip_ph})
		           THEN g.credit - g.debit
		           ELSE g.debit - g.credit END) AS balance,
		  COUNT(DISTINCT g.company) AS company_count
		FROM `tabGL Entry` g
		JOIN `tabAccount` a ON a.name = g.account
		{common_where}
		GROUP BY g.party_type, g.party
		HAVING balance != 0
		ORDER BY {sort_clause}
		LIMIT %(page_size)s OFFSET %(offset)s
		""",
		{**common_params, "page_size": page_size, "offset": offset},
		as_dict=True,
	)

	group_co_names = _group_company_names()
	parties = [
		{
			"party_type": r["party_type"],
			"party": r["party"],
			"balance": round(flt(r["balance"]), 2),
			"company_count": int(r["company_count"]),
			"is_group_company": r["party"] in group_co_names,
		}
		for r in rows
	]

	return {
		"total_parties": total,
		"page": page,
		"page_size": page_size,
		"parties": parties,
	}


# ---------------------------------------------------------------------------
# get_party_company_breakdown -- group by company for one party
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_party_company_breakdown(scope=None, accounts=None, as_of_date=None,
                                companies=None, party=None, party_type=None):
	"""Group GL entries by company for one (party, party_type).

	Used by the disambiguation popover (commit 5) when a party row
	in the account drill panel has `company_count > 1`.

	Output shape per spec §4.2.2.
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	if not party or not party_type:
		frappe.throw(_("party and party_type are required"))

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	leaves = _leaves_from_input(scope, accounts, allowed)
	if not leaves or not allowed:
		return {
			"party": party,
			"party_type": party_type,
			"total_companies": 0,
			"by_company": [],
		}

	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", allowed)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)

	rows = frappe.db.sql(
		f"""
		SELECT
		  g.company,
		  SUM(CASE WHEN a.root_type IN ({f_ph})
		           THEN g.credit - g.debit
		           ELSE g.debit - g.credit END) AS balance
		FROM `tabGL Entry` g
		JOIN `tabAccount` a ON a.name = g.account
		WHERE g.account IN ({a_ph})
		  AND g.company IN ({c_ph})
		  AND g.posting_date <= %(as_of_date)s
		  AND g.is_cancelled = 0
		  AND g.docstatus = 1
		  AND g.party = %(party)s
		  AND g.party_type = %(party_type)s
		GROUP BY g.company
		HAVING balance != 0
		ORDER BY balance DESC
		""",
		{
			**a_params, **c_params, **f_params,
			"as_of_date": target_date,
			"party": party,
			"party_type": party_type,
		},
		as_dict=True,
	)

	by_company = [
		{"company": r["company"], "balance": round(flt(r["balance"]), 2)}
		for r in rows
	]

	return {
		"party": party,
		"party_type": party_type,
		"total_companies": len(by_company),
		"by_company": by_company,
	}


# ---------------------------------------------------------------------------
# Audit -- Q19 production rollout one-shot
# ---------------------------------------------------------------------------

def audit_group_co_name_match():
	"""Q19 production audit. Run via `bench execute`. Not whitelisted intentionally.

	For each `tabCompany` row, report whether a matching Customer or
	Supplier record exists with the exact same name. Used during
	production rollout to validate the `is_group_company` flag's
	exact-match assumption (spec §4.2 / OPEN_QUESTIONS Q19).

	On dev seed, every row will report matched=False -- the synthetic
	seed never creates Customer/Supplier records that mirror group
	companies. Run on production data to validate.

	Returns the list-of-dicts result; also prints a human-readable
	table to stdout for the bench operator.
	"""
	companies = frappe.db.sql(
		"SELECT name FROM `tabCompany` ORDER BY name", as_dict=False,
	)
	results = []
	for (co_name,) in companies:
		has_customer = bool(frappe.db.exists("Customer", co_name))
		has_supplier = bool(frappe.db.exists("Supplier", co_name))
		results.append({
			"company": co_name,
			"has_customer": has_customer,
			"has_supplier": has_supplier,
			"matched": has_customer or has_supplier,
		})

	matched = sum(1 for r in results if r["matched"])
	print(
		f"\nGroup-co name match audit: {matched} / {len(results)} "
		f"companies have a matching Customer or Supplier record.\n"
	)
	header_co = "Company"
	print(f"{header_co:<50s} | Customer | Supplier")
	print("-" * 78)
	for r in results:
		cust = "YES" if r["has_customer"] else "   "
		supp = "YES" if r["has_supplier"] else "   "
		print(f"{r['company']:<50s} | {cust:<8s} | {supp:<8s}")

	if matched == 0:
		print(
			"\nNOTE: zero matches. On synthetic dev seed this is "
			"expected. On production, zero matches means every "
			"`is_group_company` flag will be False -- investigate "
			"before users see Group co pills missing on real parties."
		)
	return results


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


def _coerce_int(value, default, minimum=None, maximum=None):
	try:
		v = int(value) if value is not None else default
	except (ValueError, TypeError):
		v = default
	if minimum is not None and v < minimum:
		v = minimum
	if maximum is not None and v > maximum:
		v = maximum
	return v


def _empty_party_breakdown(page, page_size):
	return {
		"total_parties": 0,
		"page": page,
		"page_size": page_size,
		"parties": [],
	}


def _leaves_from_input(scope, accounts, allowed):
	"""Resolve the leaf account list from either entry shape.

	Returns [] if neither input is usable; callers treat this as
	"empty result" (rather than 400-erroring) so a partially
	configured drill panel just shows nothing.
	"""
	if accounts is not None:
		return [a for a in accounts if isinstance(a, str)]
	if isinstance(scope, dict):
		leaves, _ = _resolve_scope_to_leaves(scope, allowed)
		return leaves
	return []


def _common_where_clause(leaves, allowed, target_date):
	"""Build the shared WHERE clause for both party-drill queries.

	Returns ``(where_sql, params, flip_placeholders)`` so the caller's
	SELECT-side CASE WHEN can reference the same placeholder names.
	"""
	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", allowed)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)
	where = f"""
		WHERE g.account IN ({a_ph})
		  AND g.company IN ({c_ph})
		  AND g.posting_date <= %(as_of_date)s
		  AND g.is_cancelled = 0
		  AND g.docstatus = 1
		  AND g.party IS NOT NULL AND g.party != ''
	"""
	params = {
		**a_params, **c_params, **f_params,
		"as_of_date": target_date,
	}
	return where, params, f_ph


def _count_parties(common_where, common_params, flip_ph):
	row = frappe.db.sql(
		f"""
		SELECT COUNT(*) FROM (
		  SELECT g.party_type, g.party
		  FROM `tabGL Entry` g
		  JOIN `tabAccount` a ON a.name = g.account
		  {common_where}
		  GROUP BY g.party_type, g.party
		  HAVING SUM(CASE WHEN a.root_type IN ({flip_ph})
		                  THEN g.credit - g.debit
		                  ELSE g.debit - g.credit END) != 0
		) AS sub
		""",
		common_params,
	)
	return int(row[0][0]) if row else 0


def _group_company_names():
	"""Set of all `tabCompany.name` values.

	Used for the `is_group_company` flag. Not scoped by the user's
	allowed companies because the flag's meaning is "this party is
	a group company in the org overall," not "this party is a group
	company the current user can see." The flag is just a boolean,
	doesn't reveal company names the user can't already infer.
	"""
	return set(frappe.db.sql_list("SELECT name FROM `tabCompany`"))
