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

import csv
import io
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

# HALT 4 -- mode='page' tier (per spec v0.6 §5.4 "Mode args contract"):
# higher max_page_size + extra `name_desc` sort + `total_pages` /
# `scope` echo in response. Existing `card` mode (panel + account-drill
# page) is byte-identical to its HALT 1+2 behavior.
ALLOWED_MODES = ("card", "page")
PAGE_MODE_DEFAULT_PAGE_SIZE = 50
PAGE_MODE_MAX_PAGE_SIZE = 500
PAGE_MODE_ALLOWED_SORTS = (
	"balance_desc", "balance_asc", "name_asc", "name_desc",
)


# ---------------------------------------------------------------------------
# get_party_breakdown -- group by party across companies
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_party_breakdown(scope=None, accounts=None, as_of_date=None,
                        companies=None, page=1, page_size=None,
                        sort="balance_desc", mode="card"):
	"""Group GL entries by party across companies in scope.

	Two modes (per spec v0.6 §5.4):

	  - `card` (default; HALT 1 + HALT 2 behavior, unchanged): smaller
	    DEFAULT_PAGE_SIZE / MAX_PAGE_SIZE; sort allow-list excludes
	    `name_desc`; response shape has no `total_pages` / `scope`
	    echo. Used by the panel and the account-drill full page.
	  - `page` (HALT 4, new): for the /app/party-list page. Higher
	    page_size cap (500), extra `name_desc` sort, `total_pages` and
	    echoed `scope` in the response so the page can render
	    pagination + show what the server resolved.

	Output shape per spec §4.2.1 (card) and §5.4 / §6.2 (page).

	`mode` is normalised to a known value via `_normalise_mode`; an
	invalid mode raises ValidationError so a typo from a hand-crafted
	URL gets a clear error rather than silently degrading.
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)

	mode = _normalise_mode(mode)
	default_size, max_size, allowed_sorts = _mode_knobs(mode)

	page = _coerce_int(page, default=1, minimum=1)
	page_size = _coerce_int(page_size, default=default_size,
	                        minimum=1, maximum=max_size)
	sort = sort if sort in allowed_sorts else "balance_desc"

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	leaves = _leaves_from_input(scope, accounts, allowed)
	if not leaves or not allowed:
		return _empty_party_breakdown(page, page_size, mode=mode,
		                              scope=scope, accounts=accounts)

	common_where, common_params, flip_ph = _common_where_clause(
		leaves, allowed, target_date,
	)

	# Total row count -- HAVING ABS(balance) >= 1 to drop net-zero AND
	# sub-rupee residual parties (commit 3.1). The original threshold of
	# `balance != 0` admitted ~Rs 0.50 rounding residuals from the
	# augmented AP/AR seed (side PR #11) which then rendered as "Rs 0"
	# rows in the panel. Threshold of one rupee is conservative -- it
	# only drops what's clearly noise, never real small balances.
	total = _count_parties(common_where, common_params, flip_ph)
	if total == 0:
		return _empty_party_breakdown(page, page_size, mode=mode,
		                              scope=scope, accounts=accounts)

	# Page query.
	# `balance_desc` / `balance_asc` sort by ABSOLUTE balance so a panel
	# scoped where some parties have net-debit positions (we are owed
	# by the supplier — advances, refunds, overpayments) still surfaces
	# the biggest exposures first. Without ABS, raw `ORDER BY balance
	# DESC` would put a -Rs 1,00,000 party AFTER a +Rs 100 one, which
	# isn't what "Top N by balance" means in the panel UI.
	#
	# MariaDB doesn't allow ABS() applied to an aggregate alias in
	# ORDER BY (only HAVING resolves aggregate aliases that way), so the
	# SUM(CASE...) expression is inlined here. flip_ph is already
	# substituted by `_common_where_clause` so the params bind once.
	abs_balance_expr = (
		f"ABS(SUM(CASE WHEN a.root_type IN ({flip_ph}) "
		"THEN g.credit - g.debit "
		"ELSE g.debit - g.credit END))"
	)
	sort_clause = {
		"balance_desc": f"{abs_balance_expr} DESC, g.party ASC",
		"balance_asc":  f"{abs_balance_expr} ASC, g.party ASC",
		"name_asc":     "g.party ASC, g.party_type ASC",
		"name_desc":    "g.party DESC, g.party_type ASC",
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
		HAVING ABS(balance) >= 1
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

	response = {
		"total_parties": total,
		"page": page,
		"page_size": page_size,
		"parties": parties,
	}
	# HALT 4: page mode adds total_pages (math) + scope echo (the
	# resolved scope shape so the page can confirm what the server
	# interpreted). Card mode keeps its byte-identical shape so
	# existing panel + account-drill page callers don't break.
	if mode == "page":
		response["total_pages"] = max(
			1, (total + page_size - 1) // page_size
		)
		response["scope"] = _scope_echo(scope, accounts, leaves)
	return response


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


def _empty_party_breakdown(page, page_size, mode="card", scope=None,
                           accounts=None):
	out = {
		"total_parties": 0,
		"page": page,
		"page_size": page_size,
		"parties": [],
	}
	if mode == "page":
		out["total_pages"] = 1
		out["scope"] = _scope_echo(scope, accounts, [])
	return out


def _normalise_mode(mode):
	"""Return a known mode value or raise ValidationError.

	Accepts None / empty string as 'card' (HALT 1+2 callers don't
	pass a mode). Any other unknown value raises -- spec v0.6 §5.4
	says invalid mode is a hard error so a typo from a hand-crafted
	URL gets a clear failure.
	"""
	if not mode:
		return "card"
	if mode in ALLOWED_MODES:
		return mode
	frappe.throw(
		_("Invalid mode '{0}'. Allowed: {1}").format(
			mode, ", ".join(ALLOWED_MODES),
		),
		title=_("Invalid mode"),
	)


def _mode_knobs(mode):
	"""Return (default_page_size, max_page_size, allowed_sorts) for a mode."""
	if mode == "page":
		return (
			PAGE_MODE_DEFAULT_PAGE_SIZE,
			PAGE_MODE_MAX_PAGE_SIZE,
			PAGE_MODE_ALLOWED_SORTS,
		)
	return (DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ALLOWED_SORTS)


def _scope_echo(scope, accounts, leaves):
	"""Return the resolved scope shape for the response echo.

	`scope` (dict) and `accounts` (list) are the inputs the caller
	provided; `leaves` is the post-resolution leaf list. Echo
	includes the leaf count so the page can show "N accounts in
	scope" without re-fetching.
	"""
	echo = {
		"n_leaves": len(leaves) if leaves else 0,
	}
	if isinstance(scope, dict):
		echo["scope"] = {
			"type": scope.get("type"),
			"value": scope.get("value"),
		}
	elif accounts is not None:
		echo["accounts"] = list(accounts)[:50]  # cap to keep echo small
		echo["accounts_truncated"] = len(accounts) > 50
	return echo


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
		  HAVING ABS(SUM(CASE WHEN a.root_type IN ({flip_ph})
		                      THEN g.credit - g.debit
		                      ELSE g.debit - g.credit END)) >= 1
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


# ---------------------------------------------------------------------------
# CSV export -- export_party_list_csv (HALT 4)
# ---------------------------------------------------------------------------

PARTY_CSV_HEADERS = ("Party", "Party Type", "Balance", "Company Count")
PARTY_EXPORT_TOO_LARGE_MSG = (
	"Scope too large for CSV export ({n} parties). Narrow the scope "
	"or use the party list page with pagination."
)
PARTY_CSV_HARD_TRUNCATE_AT = 50_000


@frappe.whitelist()
def export_party_list_csv(scope=None, accounts=None, scope_label=None,
                          as_of_date=None, companies=None,
                          sort="balance_desc"):
	"""Stream the party list for one drill scope as a CSV download.

	Honors the same scope/companies/sort args as `get_party_breakdown`
	mode='page'. NO pagination -- all parties up to the 50K hard cap
	are exported.

	Cell format (locked at HALT 2 + carried to HALT 4):
	  - Balance: raw decimal like "4500000.00" -- NO Indian grouping,
	    NO currency symbol. Spreadsheet apps reformat per locale on
	    import; pre-formatting breaks numerical typing.
	  - Sub-rupee filter (ABS(balance) >= 1) applies same as the
	    on-screen list, so file matches what user sees.

	Filename: party_list_<scope-slug>_<as_of>_<HHMMSS>.csv per
	HALT 4 instruction. (No `_filtered` infix because party list
	doesn't have HALT 2.5-style filters yet.)
	"""
	# Reuse the shared CSV helpers from gl_drill_v1 to keep filename
	# slugification + response-setting consistent across all three
	# CSV endpoints.
	from dux_groupview.dux_groupview.api.gl_drill_v1 import (
		_csv_filename, _set_csv_response,
	)
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)
	# Sort allow-list matches mode='page' (4 sorts incl name_desc).
	sort = sort if sort in PAGE_MODE_ALLOWED_SORTS else "balance_desc"

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	# Resolve label for filename slug.
	resolved_label = scope_label or ""
	if accounts is not None:
		leaves = [a for a in accounts if isinstance(a, str)]
	elif isinstance(scope, dict):
		leaves, default_label = _resolve_scope_to_leaves(scope, allowed)
		if not resolved_label:
			resolved_label = default_label
	else:
		leaves = []

	filename = _csv_filename("party_list", resolved_label, target_date)

	if not allowed or not leaves:
		_set_csv_response(filename, _build_party_csv([]))
		return

	common_where, common_params, flip_ph = _common_where_clause(
		leaves, allowed, target_date,
	)

	# Cap check before the expensive query.
	total = _count_parties(common_where, common_params, flip_ph)
	if total > PARTY_CSV_HARD_TRUNCATE_AT:
		frappe.throw(
			_(PARTY_EXPORT_TOO_LARGE_MSG).format(n=f"{total:,}"),
			title=_("Export too large"),
		)

	if total == 0:
		_set_csv_response(filename, _build_party_csv([]))
		return

	# Same SQL shape as get_party_breakdown's page query, no LIMIT.
	abs_balance_expr = (
		f"ABS(SUM(CASE WHEN a.root_type IN ({flip_ph}) "
		"THEN g.credit - g.debit "
		"ELSE g.debit - g.credit END))"
	)
	sort_clause = {
		"balance_desc": f"{abs_balance_expr} DESC, g.party ASC",
		"balance_asc":  f"{abs_balance_expr} ASC, g.party ASC",
		"name_asc":     "g.party ASC, g.party_type ASC",
		"name_desc":    "g.party DESC, g.party_type ASC",
	}[sort]

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
		HAVING ABS(balance) >= 1
		ORDER BY {sort_clause}
		LIMIT %(cap)s
		""",
		{**common_params, "cap": PARTY_CSV_HARD_TRUNCATE_AT},
		as_dict=True,
	)

	_set_csv_response(filename, _build_party_csv(rows))


def _build_party_csv(rows):
	"""Build the party list CSV body. UTF-8, QUOTE_MINIMAL,
	\\n line endings, raw decimals.
	"""
	buf = io.StringIO()
	w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
	w.writerow(PARTY_CSV_HEADERS)
	for r in rows:
		w.writerow([
			r["party"] or "",
			r["party_type"] or "",
			f"{flt(r['balance']):.2f}",
			int(r["company_count"]),
		])
	return buf.getvalue()
