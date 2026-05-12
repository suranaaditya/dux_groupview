"""GL drill API.

Per Phase 4 commit 4 spec §5.1. Reads `tabGL Entry` directly under the
CLAUDE.md amendment for `_drill`-suffixed APIs (commit 1, lifted in
phase-4-drills branch). The amendment's conditions (a)-(g) all hold:

  (a) module name suffix `_drill`,
  (b) `_require_cockpit_role()` + `_resolve_scope()` enforce User
      Permissions on Company at API entry,
  (c) uses Phase 3 / commit-2 covering indexes on `tabGL Entry`
      (which one the optimizer picks is documented at perf-measurement
      time, not asserted here -- see spec v0.3 §10),
  (d) results paginate; default page_size 100, max 1000; results above
      50,000 trigger `is_truncated=True` and a hard cap on the windowed
      read (spec §5.1 truncation behavior),
  (e) scope is bounded: a resolved leaf-account list AND a bounded
      company set,
  (f) read-only -- only SELECT against `tabGL Entry`,
  (g) every returned row's company is in the allowed set.

Sign convention: `signed_amount` uses the same FLIP_ROOT_TYPES sign
convention as the rest of the cockpit -- positive on the natural side
of the account's root_type.

Running balance (spec v0.9): window function with PARTITION BY
(company, account), ordered (posting_date, name). Under the v0.9
per-company scope constraint, each call sees one company's accounts
each as its own small partition (typically <500 rows). The window
sort aligns with `dgv_party_drill`'s ordering and the outer LIMIT
short-circuits cheaply.

GL drill is per-company by design (spec v0.9). `get_gl_entries`,
`export_gl_entries_csv`, and `get_filter_metadata` all assert
`len(resolved_companies) == 1` at entry. ValidationError otherwise.
This is enforced server-side; the UI presents a company picker for
multi-company scopes before reaching these endpoints. The
per-company constraint bounds row count by construction -- realistic
single-company scopes resolve to <10K rows even at production scale.

Iteration history is in the spec changelog (v0.4 partitioned,
v0.5 unpartitioned, v0.7 re-partitioned, v0.8 conditional running
balance, v0.9 per-company reframing). PHASE_LOG commit 9 entry has
the honest narrative.
"""

import csv
import io
import json
import re
from datetime import datetime
from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api.utils import (
	FLIP_ROOT_TYPES,
	_named_in,
	_resolve_scope_to_leaves,
)


DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000
HARD_TRUNCATE_AT = 50_000
ALLOWED_SORTS = (
	"posting_date_desc",
	"posting_date_asc",
	"amount_desc",
	"amount_asc",
)
REMARKS_TRUNCATE_LEN = 200

# GL drill is per-company by design (spec v0.9). The endpoints assert
# exactly one company in the resolved permission-allowed set. The UI
# presents a company picker for multi-company scopes before calling
# any of these endpoints; the server-side check is a defensive
# guard against direct URL hits with `companies=` lists.
PER_COMPANY_ERROR_MESSAGE = (
	"GL drill is per-company. Open from the cockpit drill panel "
	"and use the company picker."
)


def _check_single_company(allowed):
	"""Raise ValidationError if the resolved allowed-companies set is
	not exactly one company.

	Permission-empty (allowed == []) is handled by the caller's
	existing empty-response path; this helper only fires when there's
	a non-empty multi-company set.

	The verbatim message is the same on every endpoint so the client
	error tile renders identically regardless of which call surfaced
	the error. `scope_multi_company` flag in the response lets the
	classifier route to a dedicated tile category that nudges the user
	toward the company picker. `_server_messages` is cleared so
	Frappe's default popup doesn't fire alongside the targeted tile --
	scope-multi-company is the ONLY known multi-popup overlap surfaced
	in commit 10 smoke; other frappe.throw paths in this module retain
	default popup behavior. The actual exception message is terse
	("requires a single company") for log clarity; the user-facing
	message lives in scope_multi_company_message and is the verbatim
	text the client tile renders.
	"""
	if allowed and len(allowed) > 1:
		frappe.local.response["scope_multi_company"] = True
		frappe.local.response["scope_multi_company_message"] = (
			PER_COMPANY_ERROR_MESSAGE
		)
		frappe.local.response["scope_companies"] = list(allowed)
		# Scoped suppression of Frappe's default popup so the client
		# renders its own classified error tile for this one case.
		frappe.local.response["_server_messages"] = json.dumps([])
		frappe.throw(
			_("GL drill requires a single company in scope."),
			exc=frappe.ValidationError,
		)


# ---------------------------------------------------------------------------
# Filename slugification (shared; also used by account_drill_v1's CSV export)
# ---------------------------------------------------------------------------

def _slugify_for_filename(value: str, max_len: int = 80) -> str:
	"""Make a string safe for use in an HTTP attachment filename.

	Replaces every run of non-alphanumeric chars with a single hyphen
	and trims to `max_len`. Leading/trailing hyphens stripped. An
	empty result becomes "scope" so the filename always has substance.
	"""
	if not value:
		return "scope"
	# Lower-case for predictability across OSes (FAT/NTFS are
	# case-insensitive; macOS HFS+ optionally so).
	slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
	if not slug:
		return "scope"
	return slug[:max_len].strip("-").lower() or "scope"


def _csv_filename(prefix: str, scope_label: str, as_of_date) -> str:
	"""Build a filename matching the spec convention:
	`<prefix>_<slug>_<as_of>_<HHMMSS>.csv`.
	"""
	slug = _slugify_for_filename(scope_label or "scope")
	if hasattr(as_of_date, "isoformat"):
		date_str = as_of_date.isoformat()
	else:
		date_str = str(as_of_date)
	ts = datetime.now().strftime("%H%M%S")
	return f"{prefix}_{slug}_{date_str}_{ts}.csv"


def _set_csv_response(filename: str, csv_string: str) -> None:
	"""Set Frappe's response so the browser triggers a file download.

	Uses `type=binary` because Frappe's csv-typed response handler
	wraps `result` in a JSON envelope -- we want the raw CSV bytes.
	The `filename` field on the response object drives the
	Content-Disposition header; binary type triggers
	application/octet-stream which spreadsheets handle on the .csv
	extension.
	"""
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = csv_string.encode("utf-8")
	frappe.local.response.type = "binary"


@frappe.whitelist()
def get_gl_entries(
	scope=None,
	accounts=None,
	as_of_date=None,
	companies=None,
	party=None,
	party_type=None,
	page=1,
	page_size=None,
	sort="posting_date_asc",
	# HALT 2.5 filter params -- all optional, default no-op so HALT 1
	# callers (which never pass these) get unchanged behavior.
	account_names=None,
	from_date=None,
	to_date=None,
	voucher_types=None,
):
	"""Paginated GL entries for one drill scope.

	Output shape per spec v0.3 §5.1:

	    {
	      "scope_label": <str>,
	      "scope_fanout": {"n_accounts": int, "n_companies": int},
	      "total_entries": int,                # actual count, no cap
	      "is_truncated": bool,                # True iff total > 50K
	      "page": int,
	      "page_size": int,
	      "entries": [
	        {
	          "name", "posting_date", "company", "account",
	          "voucher_type", "voucher_no",
	          "party_type", "party",
	          "debit", "credit",
	          "signed_amount",                 # natural-side
	          "running_balance",               # cumulative natural-side, partitioned by (company, account)
	          "remarks", "remarks_truncated",
	        },
	        ...
	      ],
	    }

	Two entry shapes (mirrors `account_drill_v1` / `party_drill_v1`):

	  - `scope`:    dict (or JSON string) of ScopeSpec form, resolved
	                via `utils._resolve_scope_to_leaves`.
	  - `accounts`: pre-resolved leaf list (used by the spotlight-card
	                path; caller has already translated card.match).
	                When passed, `scope` is ignored.

	Optional `(party, party_type)` filter narrows the result set to one
	party (used by the party-list "view GL for this party" deep-link in
	halt-3).
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)

	page = _coerce_int(page, default=1, minimum=1)
	page_size = _coerce_int(
		page_size, default=DEFAULT_PAGE_SIZE,
		minimum=1, maximum=MAX_PAGE_SIZE,
	)
	sort = sort if sort in ALLOWED_SORTS else "posting_date_asc"

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	# --- Resolve leaves + label ---
	scope_label = ""
	if accounts is not None:
		leaves = [a for a in accounts if isinstance(a, str)]
		scope_label = _short_account_label(leaves)
	elif isinstance(scope, dict):
		leaves, default_label = _resolve_scope_to_leaves(scope, allowed)
		scope_label = default_label
	else:
		leaves = []

	if not allowed or not leaves:
		return _empty_response(
			scope_label, page, page_size,
			n_accounts=len(leaves) if leaves else 0,
			n_companies=len(allowed) if allowed else 0,
		)

	# Spec v0.9: GL drill is per-company by design. Reject multi-company
	# scopes; UI presents a company picker before reaching this endpoint.
	_check_single_company(allowed)

	# Normalise party + HALT 2.5 filter inputs (csv strings -> lists,
	# date strings -> getdate, to_date clamped to as_of_date, etc.).
	party, party_type, fnorm = _normalise_filters(
		party, party_type, account_names, from_date, to_date,
		voucher_types, target_date,
	)
	# After normalisation, the effective upper-bound date used in
	# WHERE is min(as_of_date, to_date). target_date is the as_of
	# envelope; effective_to_date is the value SQL compares against.
	effective_to_date = fnorm["effective_to_date"]
	clamped = fnorm["clamped_to_date"]

	# --- Total count (cheap; no window, no LIMIT) ---
	count_where, count_params = _build_where_clause(
		leaves, allowed, effective_to_date, party, party_type,
		account_names=fnorm["account_names"],
		from_date=fnorm["from_date"],
		voucher_types=fnorm["voucher_types"],
	)
	total_entries = _count_entries(count_where, count_params)
	is_truncated = total_entries > HARD_TRUNCATE_AT

	# Voucher types in the *unfiltered-by-voucher-type* WHERE -- so
	# the dropdown lists every type the user could pick, even when
	# they've narrowed to a subset. Spec §3.5: "available types
	# from distinct voucher_type values in current scope".
	voucher_types_in_scope = _voucher_types_in_scope(
		leaves, allowed, effective_to_date, party, party_type,
		account_names=fnorm["account_names"],
		from_date=fnorm["from_date"],
	)

	filter_state = _filter_state_echo(fnorm)

	if total_entries == 0:
		return _empty_response(
			scope_label, page, page_size,
			n_accounts=len(leaves), n_companies=len(allowed),
			total_entries=0,
			voucher_types_in_scope=voucher_types_in_scope,
			clamped_to_date=clamped,
			filter_state=filter_state,
		)

	# --- Data query (windowed) ---
	# When NOT truncated, the inner subquery selects all rows
	# unordered; the window's PARTITION/ORDER computes running balance;
	# the outer query applies display sort + LIMIT/OFFSET.
	#
	# When truncated, the inner subquery applies the display sort and
	# LIMITs to 50K so the user sees the "first 50K rows in display
	# order"; running balance then runs over those 50K rows (relative
	# to the visible window, not the full N -- the page surfaces a
	# banner explaining this).
	rows = _fetch_windowed_page(
		leaves, allowed, effective_to_date, party, party_type,
		sort, page, page_size, is_truncated,
		account_names=fnorm["account_names"],
		from_date=fnorm["from_date"],
		voucher_types=fnorm["voucher_types"],
	)

	entries = [_row_to_entry(r) for r in rows]

	return {
		"scope_label": scope_label,
		"scope_fanout": {
			"n_accounts": len(leaves),
			"n_companies": len(allowed),
		},
		"total_entries": total_entries,
		"is_truncated": is_truncated,
		"page": page,
		"page_size": page_size,
		"entries": entries,
		# HALT 2.5 additions
		"voucher_types_in_scope": voucher_types_in_scope,
		"clamped_to_date": clamped,
		"filter_state": filter_state,
	}


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

# Maps `sort` arg to (display ORDER BY clause). Window function's
# PARTITION/ORDER is fixed -- running balance always accumulates in
# (posting_date ASC, name ASC) order regardless of display sort, so
# it remains a meaningful per-(company, account) cumulative figure.
DISPLAY_SORTS = {
	"posting_date_desc": "posting_date DESC, name DESC",
	"posting_date_asc":  "posting_date ASC, name ASC",
	"amount_desc":       "ABS(signed_amount) DESC, posting_date DESC, name DESC",
	"amount_asc":        "ABS(signed_amount) ASC, posting_date DESC, name DESC",
}


def _build_where_clause(leaves, allowed, target_date, party, party_type,
                        account_names=None, from_date=None, to_date=None,
                        voucher_types=None):
	"""WHERE clause + params for both the count query and the data
	query's inner subquery. Same predicate, same params; reused twice.

	HALT 2.5 added the four optional filter params (`account_names`,
	`from_date`, `to_date`, `voucher_types`). Each is appended only
	when populated -- a None / empty value is a no-op so HALT 1 / 2
	callers (which never pass these) keep their exact previous WHERE.

	Sign convention: `to_date` is clamped to `target_date` upstream
	(see `_normalise_filters`). The clause here uses the already-
	effective `target_date` (== as_of_date when no to_date given,
	== min(as_of_date, to_date) otherwise).
	"""
	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", allowed)
	where = f"""
		WHERE g.account IN ({a_ph})
		  AND g.company IN ({c_ph})
		  AND g.posting_date <= %(as_of_date)s
		  AND g.is_cancelled = 0
		  AND g.docstatus = 1
	"""
	params = {**a_params, **c_params, "as_of_date": target_date}
	if party and party_type:
		where += " AND g.party = %(party)s AND g.party_type = %(party_type)s"
		params["party"] = party
		params["party_type"] = party_type
	# HALT 2.5 filters -- predicates pushed onto the same JOIN, no
	# new index dimension required (`account_name` is on tabAccount
	# already JOINed for `root_type`; `voucher_type` is on tabGL Entry
	# directly). EXPLAIN at HALT 2.5.3 verifies this.
	if account_names:
		an_ph, an_params = _named_in("an", account_names)
		where += f" AND a.account_name IN ({an_ph})"
		params.update(an_params)
	if from_date:
		where += " AND g.posting_date >= %(from_date)s"
		params["from_date"] = from_date
	if voucher_types:
		vt_ph, vt_params = _named_in("vt", voucher_types)
		where += f" AND g.voucher_type IN ({vt_ph})"
		params.update(vt_params)
	# Note: `to_date` is NOT a separate predicate -- it was folded
	# into `target_date` upstream via `_normalise_filters`. The
	# `g.posting_date <= %(as_of_date)s` clause above handles both
	# the as_of_date envelope and the user's to_date narrowing in
	# one comparison.
	return where, params


def _count_entries(where_clause, params):
	# JOIN tabAccount because the WHERE clause may reference
	# `a.account_name` (HALT 2.5 account_names filter). The JOIN is
	# cheap (PRIMARY-key lookup per row) and matches the data
	# query's shape so EXPLAIN stays consistent.
	# FORCE INDEX: optimizer picks posting_date for large IN-lists at
	# 5M+ scale (cost-model issue, not stats). See commit 9 PHASE_LOG
	# for diagnosis.
	row = frappe.db.sql(
		f"""
		SELECT COUNT(*) FROM `tabGL Entry` g FORCE INDEX (dgv_party_drill)
		JOIN `tabAccount` a ON a.name = g.account
		{where_clause}
		""",
		params,
	)
	return int(row[0][0]) if row else 0


# ---------------------------------------------------------------------------
# HALT 2.5 filter normalisation + metadata helpers
# ---------------------------------------------------------------------------

def _normalise_filters(party, party_type, account_names, from_date,
                       to_date, voucher_types, target_date):
	"""Coerce + validate the filter inputs.

	Returns ``(party, party_type, dict)``. The dict has:

	  - account_names         normalised list[str] or None
	  - from_date             normalised date or None
	  - to_date               normalised date (clamped to target_date) or None
	  - voucher_types         normalised list[str] or None
	  - effective_to_date     date used in WHERE (== min(target, to_date))
	  - clamped_to_date       True iff to_date was supplied AND > target_date

	Empty-string / empty-list inputs collapse to None so the SQL
	branches stay off (matches HALT 1's "no-op when empty" semantics).
	"""
	# Party normalisation (carry-over from HALT 1)
	party = party.strip() if isinstance(party, str) and party.strip() else None
	party_type = (
		party_type.strip()
		if isinstance(party_type, str) and party_type.strip()
		else None
	)
	if party and not party_type:
		party = None

	# account_names: accept JSON string, csv string, list, or None.
	account_names = _coerce_csv_or_list(account_names)
	# voucher_types: same shape.
	voucher_types = _coerce_csv_or_list(voucher_types)

	# Date normalisation. Both are ISO strings, may be None or empty.
	def _to_date(v):
		if not v:
			return None
		if isinstance(v, str) and not v.strip():
			return None
		try:
			return getdate(v)
		except Exception:
			return None

	from_date = _to_date(from_date)
	to_date = _to_date(to_date)

	# to_date clamped to target_date (the cockpit's snapshot envelope).
	clamped = False
	effective_to_date = target_date
	if to_date is not None:
		if to_date > target_date:
			clamped = True
			effective_to_date = target_date
		else:
			effective_to_date = to_date

	# from_date sanity: if from > effective_to, the user gets zero rows
	# and the UI surfaces an inline "From must be on or before To"
	# message. We don't error here -- valid empty result.

	return party, party_type, {
		"account_names": account_names,
		"from_date": from_date,
		"to_date": to_date,
		"voucher_types": voucher_types,
		"effective_to_date": effective_to_date,
		"clamped_to_date": clamped,
	}


def _coerce_csv_or_list(value):
	"""Accept JSON-string / csv-string / list / None -> list[str] or None.

	Empty / whitespace-only collapse to None.
	"""
	if value is None:
		return None
	if isinstance(value, list):
		out = [str(v).strip() for v in value if v is not None and str(v).strip()]
		return out or None
	if isinstance(value, str):
		s = value.strip()
		if not s:
			return None
		# JSON array form (matches the `accounts=` HALT 1 contract)
		if s.startswith("[") and s.endswith("]"):
			try:
				parsed = json.loads(s)
				if isinstance(parsed, list):
					return _coerce_csv_or_list(parsed)
			except (ValueError, TypeError):
				pass
		# Otherwise comma-separated (the spec §5 wire form)
		out = [v.strip() for v in s.split(",") if v.strip()]
		return out or None
	return None


def _voucher_types_in_scope(leaves, allowed, target_date, party, party_type,
                            account_names=None, from_date=None):
	"""DISTINCT voucher_type values from the same WHERE as the data
	query, MINUS the voucher_types filter itself (so the dropdown
	shows everything the user could pick, even when they've narrowed).

	Per-company scope (spec v0.9): caller enforces single-company at
	the endpoint entry, so `allowed` here is one company. Row count
	bounded to that company's tabGL Entry slice (~85K rows max on
	the dev seed), comfortably fast.

	FORCE INDEX (dgv_party_drill) retained as a safety hint -- the
	optimizer's cost-model issue with large IN-lists (commit 9
	PHASE_LOG) is non-load-bearing under the per-company constraint
	but cheap to keep.

	LIMIT 1000 protects against pathological data -- production data
	has <50 distinct voucher_types per scope; cap is a safety, not a
	functional limit.
	"""
	# Build WHERE with the same shape but voucher_types=None to
	# return the full universe.
	where_clause, where_params = _build_where_clause(
		leaves, allowed, target_date, party, party_type,
		account_names=account_names, from_date=from_date,
		voucher_types=None,
	)
	rows = frappe.db.sql_list(
		f"""
		SELECT DISTINCT g.voucher_type
		FROM `tabGL Entry` g FORCE INDEX (dgv_party_drill)
		JOIN `tabAccount` a ON a.name = g.account
		{where_clause}
		ORDER BY g.voucher_type ASC
		LIMIT 1000
		""",
		where_params,
	)
	return [r for r in rows if r]


def _filter_state_echo(fnorm: dict) -> dict:
	"""Echo the normalised filter state back to the client.

	Dates serialised as ISO strings (or None); lists as lists (or
	None). Mirrors the URL-contract shape so the UI can compare its
	local state against the server's interpretation.
	"""
	def _iso(d):
		return d.isoformat() if d else None
	return {
		"account_names": fnorm["account_names"],
		"from_date": _iso(fnorm["from_date"]),
		"to_date": _iso(fnorm["to_date"]),
		"voucher_types": fnorm["voucher_types"],
	}


def _filters_active(fnorm: dict) -> bool:
	"""True iff any of the four HALT 2.5 filter slots is non-default.

	Used by the export endpoint to decide the `_filtered` filename
	segment (party / companies don't count -- they're scope concerns,
	not HALT 2.5 filters; per spec §8).
	"""
	return bool(
		fnorm.get("account_names")
		or fnorm.get("from_date")
		or fnorm.get("to_date")
		or fnorm.get("voucher_types")
	)


def _fetch_windowed_page(
	leaves, allowed, target_date, party, party_type,
	sort, page, page_size, is_truncated,
	account_names=None, from_date=None, voucher_types=None,
):
	"""Run the windowed paginated GL fetch and return page rows.

	Per-company scope (spec v0.9): caller enforces single-company. The
	window function with `PARTITION BY (company, account)` collapses
	to one partition per account within the single company -- each
	partition is small (typically <500 rows), the window's ORDER BY
	aligns with `dgv_party_drill`'s ordering, and the outer LIMIT
	short-circuits cheaply.

	FORCE INDEX (dgv_party_drill) retained as a safety hint -- the
	optimizer's cost-model issue with large IN-lists is non-load-
	bearing under the per-company constraint but cheap to keep.

	When `is_truncated`, the innermost filter step applies the display
	sort and LIMITs to 50K. Under per-company constraint truncation
	rarely fires (one company's full COA × 12 months stays comfortably
	under 50K) but the path is retained for defense.

	HALT 2.5 filters (`account_names`, `from_date`, `voucher_types`)
	pass through to `_build_where_clause`.
	"""
	where_clause, where_params = _build_where_clause(
		leaves, allowed, target_date, party, party_type,
		account_names=account_names, from_date=from_date,
		voucher_types=voucher_types,
	)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)

	display_order = DISPLAY_SORTS[sort]
	# Inner subquery sort applies only when truncated (saves a sort
	# pass at the smaller-data path).
	inner_order_clause = ""
	inner_limit_clause = ""
	if is_truncated:
		# Inner-subquery alias only exposes raw column names; rewrite
		# `signed_amount` references in the display sort to use the
		# CASE expression directly so the LIMIT picks the right 50K.
		inner_display_order = display_order.replace(
			"signed_amount",
			f"(CASE WHEN a.root_type IN ({f_ph}) "
			"THEN g.credit - g.debit "
			"ELSE g.debit - g.credit END)",
		)
		inner_order_clause = f"ORDER BY {inner_display_order}"
		inner_limit_clause = f"LIMIT {HARD_TRUNCATE_AT}"

	offset = (page - 1) * page_size

	sql = f"""
		SELECT * FROM (
			SELECT
				name, posting_date, company, account,
				voucher_type, voucher_no, party_type, party,
				debit, credit, signed_amount, remarks,
				SUM(signed_amount) OVER (
					PARTITION BY company, account
					ORDER BY posting_date ASC, name ASC
					ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
				) AS running_balance
			FROM (
				SELECT
					g.name,
					g.posting_date,
					g.company,
					g.account,
					g.voucher_type,
					g.voucher_no,
					g.party_type,
					g.party,
					g.debit,
					g.credit,
					CASE WHEN a.root_type IN ({f_ph})
					     THEN g.credit - g.debit
					     ELSE g.debit - g.credit
					END AS signed_amount,
					g.remarks
				FROM `tabGL Entry` g FORCE INDEX (dgv_party_drill)
				JOIN `tabAccount` a ON a.name = g.account
				{where_clause}
				{inner_order_clause}
				{inner_limit_clause}
			) AS filtered
		) AS windowed
		ORDER BY {display_order}
		LIMIT %(page_size)s OFFSET %(offset)s
	"""

	params = {
		**where_params, **f_params,
		"page_size": int(page_size), "offset": int(offset),
	}
	return frappe.db.sql(sql, params, as_dict=True)


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _row_to_entry(r):
	remarks = r.get("remarks") or ""
	truncated = False
	if len(remarks) > REMARKS_TRUNCATE_LEN:
		remarks = remarks[: REMARKS_TRUNCATE_LEN - 1] + "…"
		truncated = True
	return {
		"name": r["name"],
		"posting_date": r["posting_date"].isoformat()
			if hasattr(r["posting_date"], "isoformat")
			else str(r["posting_date"]),
		"company": r["company"],
		"account": r["account"],
		"voucher_type": r["voucher_type"],
		"voucher_no": r["voucher_no"],
		"party_type": r.get("party_type") or None,
		"party": r.get("party") or None,
		"debit": round(flt(r["debit"]), 2),
		"credit": round(flt(r["credit"]), 2),
		"signed_amount": round(flt(r["signed_amount"]), 2),
		"running_balance": round(flt(r["running_balance"]), 2),
		"remarks": remarks,
		"remarks_truncated": truncated,
	}


def _empty_response(scope_label, page, page_size, n_accounts=0,
                    n_companies=0, total_entries=0,
                    voucher_types_in_scope=None,
                    clamped_to_date=False, filter_state=None):
	return {
		"scope_label": scope_label or "",
		"scope_fanout": {
			"n_accounts": n_accounts,
			"n_companies": n_companies,
		},
		"total_entries": total_entries,
		"is_truncated": False,
		"page": page,
		"page_size": page_size,
		"entries": [],
		# HALT 2.5 additions -- always present in the response shape
		# (UI relies on them being there even on empty results).
		"voucher_types_in_scope": voucher_types_in_scope or [],
		"clamped_to_date": bool(clamped_to_date),
		"filter_state": filter_state or _empty_filter_state(),
	}


def _empty_filter_state():
	return {
		"account_names": None,
		"from_date": None,
		"to_date": None,
		"voucher_types": None,
	}


# ---------------------------------------------------------------------------
# Coercion helpers (mirror party_drill_v1 conventions)
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


def _short_account_label(leaves):
	"""Best-effort label when caller passed `accounts` without a label.

	Returns the stripped account_name of the first leaf
	(e.g. "Sundry Creditors") if uniform across the leaves; otherwise
	"<n> accounts". Mirrors `account_drill_v1`'s fallback behavior.
	"""
	if not leaves:
		return ""
	# tabAccount.account_name is the stripped form ("Sundry Creditors")
	# stored once per company-suffixed name. Look up the first.
	names = frappe.db.sql_list(
		"SELECT DISTINCT account_name FROM `tabAccount` WHERE name = %s LIMIT 1",
		(leaves[0],),
	)
	if names:
		return names[0]
	return f"{len(leaves)} accounts"


# ---------------------------------------------------------------------------
# CSV export endpoint  (HALT 2)
# ---------------------------------------------------------------------------

GL_CSV_HEADERS = (
	"Posting Date",
	"Voucher Type",
	"Voucher No",
	"Company",
	"Account",
	"Party Type",
	"Party",
	"Debit",
	"Credit",
	"Running Balance",
	"Remarks",
)

EXPORT_TOO_LARGE_MSG = (
	"Scope too large for CSV export ({n} rows). Narrow the scope or "
	"use the GL drill page with pagination."
)


@frappe.whitelist()
def export_gl_entries_csv(
	scope=None,
	accounts=None,
	scope_label=None,
	as_of_date=None,
	companies=None,
	party=None,
	party_type=None,
	sort="posting_date_asc",
	# HALT 2.5 filter params -- match get_gl_entries
	account_names=None,
	from_date=None,
	to_date=None,
	voucher_types=None,
):
	"""Stream the GL entries for one drill scope as a CSV download.

	Honors the same filter args as `get_gl_entries`. NO pagination --
	all matching rows up to the 50K hard cap are exported.

	Behavior at the cap:
	  - `total_entries <= 50,000`: full result returned with running
	    balance computed scope-wide (same window function as
	    `get_gl_entries`).
	  - `total_entries > 50,000`: HTTP 400 via `frappe.throw` with
	    EXPORT_TOO_LARGE_MSG. The CSV download has no in-band channel
	    for an `is_truncated` flag, so capping silently would produce
	    a file that pretends to be complete -- worse UX than failing
	    visibly with a remediation suggestion.

	Cell format (per spec §9, locked at HALT 2):
	  - Numeric columns (debit, credit, running_balance) are raw
	    decimals like ``"4500000.00"`` -- NO Indian comma grouping,
	    NO currency symbol. Indian-grouped pre-formatting would force
	    visual interpretation onto every CSV consumer and would break
	    sum/filter/pivot in spreadsheet apps (Excel/Sheets parse
	    locale-numerics natively when cells are numeric-typed strings).
	  - Posting date is ISO ``"YYYY-MM-DD"``.
	  - Empty party renders as empty string, not "None" / "null".
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)
	sort = sort if sort in ALLOWED_SORTS else "posting_date_asc"

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	# --- Resolve leaves + label ---
	resolved_label = scope_label or ""
	if accounts is not None:
		leaves = [a for a in accounts if isinstance(a, str)]
		if not resolved_label:
			resolved_label = _short_account_label(leaves)
	elif isinstance(scope, dict):
		leaves, default_label = _resolve_scope_to_leaves(scope, allowed)
		if not resolved_label:
			resolved_label = default_label
	else:
		leaves = []

	# Normalise filters early so the filename helper can consult
	# `_filters_active` and the WHERE clause threads correctly.
	party, party_type, fnorm = _normalise_filters(
		party, party_type, account_names, from_date, to_date,
		voucher_types, target_date,
	)
	effective_to_date = fnorm["effective_to_date"]
	filename = _gl_csv_filename(resolved_label, target_date, fnorm)

	if not allowed or not leaves:
		# No data at all -- still send a header-only CSV so the
		# download triggers and the user sees an empty file rather
		# than an opaque error. Mirrors the empty-state response
		# shape from get_gl_entries which returns {entries: []}.
		csv_string = _build_gl_csv([])
		_set_csv_response(filename, csv_string)
		return

	# Spec v0.9: GL drill (and its CSV export) is per-company by design.
	# The CSV download path has no in-band channel for the
	# scope_multi_company flag, but Frappe surfaces frappe.throw as an
	# error response that triggers no download -- correct UX.
	_check_single_company(allowed)

	# --- Cap check before doing the expensive query ---
	count_where, count_params = _build_where_clause(
		leaves, allowed, effective_to_date, party, party_type,
		account_names=fnorm["account_names"],
		from_date=fnorm["from_date"],
		voucher_types=fnorm["voucher_types"],
	)
	total_entries = _count_entries(count_where, count_params)
	if total_entries > HARD_TRUNCATE_AT:
		frappe.throw(
			_(EXPORT_TOO_LARGE_MSG).format(n=f"{total_entries:,}"),
			title=_("Export too large"),
		)

	if total_entries == 0:
		csv_string = _build_gl_csv([])
		_set_csv_response(filename, csv_string)
		return

	# --- Full data fetch (windowed, scope-wide running balance) ---
	# Reuse the page fetcher with the cap as the page size and
	# is_truncated=False (we never reach the inner-LIMIT branch
	# because we throw above the cap).
	rows = _fetch_windowed_page(
		leaves, allowed, effective_to_date, party, party_type,
		sort, page=1, page_size=HARD_TRUNCATE_AT, is_truncated=False,
		account_names=fnorm["account_names"],
		from_date=fnorm["from_date"],
		voucher_types=fnorm["voucher_types"],
	)
	entries = [_row_to_entry(r) for r in rows]

	csv_string = _build_gl_csv(entries)
	_set_csv_response(filename, csv_string)


@frappe.whitelist()
def get_filter_metadata(scope=None, accounts=None, as_of_date=None,
                        companies=None):
	"""Return dropdown metadata for the gl-drill filter UI.

	Output (per spec §6.3):
	  {
	    "account_names": [{"name": "Creditors", "company_count": 13}, ...],
	    "voucher_types": ["Journal Entry", "Payment Entry", ...],
	    "parties": [{"party": "ACME", "party_type": "Customer"}, ...],
	    "scope_fanout": {"n_accounts": ..., "n_companies": ...},
	  }

	Cheap: one query per dimension over the resolved leaf set, all
	on already-indexed columns. UI calls this once on page load and
	caches client-side until the scope changes.

	Parties are returned as a sample (top 50 by row count) -- a full
	list could be huge for a wide scope. The party autocomplete UI
	uses these as suggestions but accepts free-text input for parties
	not in the sample.
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

	if accounts is not None:
		leaves = [a for a in accounts if isinstance(a, str)]
	elif isinstance(scope, dict):
		leaves, _ = _resolve_scope_to_leaves(scope, allowed)
	else:
		leaves = []

	empty = {
		"account_names": [],
		"voucher_types": [],
		"parties": [],
		"scope_fanout": {
			"n_accounts": len(leaves),
			"n_companies": len(allowed),
		},
		"companies_in_scope": list(allowed),
	}
	if not allowed or not leaves:
		return empty

	# Spec v0.9: GL drill is per-company by design. Reject multi-company
	# scopes; UI presents a company picker before reaching this endpoint.
	_check_single_company(allowed)

	# account_names: distinct stripped names + how many companies
	# each appears in.
	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", allowed)

	account_name_rows = frappe.db.sql(
		f"""
		SELECT a.account_name AS name,
		       COUNT(DISTINCT a.company) AS company_count
		FROM `tabAccount` a
		WHERE a.name IN ({a_ph})
		  AND a.company IN ({c_ph})
		GROUP BY a.account_name
		ORDER BY a.account_name ASC
		""",
		{**a_params, **c_params},
		as_dict=True,
	)
	account_names = [
		{"name": r["name"], "company_count": int(r["company_count"])}
		for r in account_name_rows
	]

	# voucher_types: distinct types in scope (no party / filter narrowing
	# -- the filter UI shows the full universe so the user can pick).
	voucher_types = _voucher_types_in_scope(
		leaves, allowed, target_date,
		party=None, party_type=None,
	)

	# parties: top 50 by row count, with their type. Party autocomplete
	# suggestions; not authoritative.
	# FORCE INDEX: optimizer picks posting_date for large IN-lists at
	# 5M+ scale (cost-model issue, not stats). See commit 9 PHASE_LOG
	# for diagnosis.
	party_rows = frappe.db.sql(
		f"""
		SELECT g.party AS party, g.party_type AS party_type, COUNT(*) AS n
		FROM `tabGL Entry` g FORCE INDEX (dgv_party_drill)
		WHERE g.account IN ({a_ph})
		  AND g.company IN ({c_ph})
		  AND g.posting_date <= %(as_of_date)s
		  AND g.is_cancelled = 0
		  AND g.docstatus = 1
		  AND g.party IS NOT NULL AND g.party != ''
		GROUP BY g.party, g.party_type
		ORDER BY n DESC, g.party ASC
		LIMIT 50
		""",
		{**a_params, **c_params, "as_of_date": target_date},
		as_dict=True,
	)
	parties = [
		{"party": r["party"], "party_type": r["party_type"]}
		for r in party_rows
	]

	return {
		"account_names": account_names,
		"voucher_types": voucher_types,
		"parties": parties,
		"scope_fanout": {
			"n_accounts": len(leaves),
			"n_companies": len(allowed),
		},
		# Companies-in-scope explicit list (commit 7 F-11 fix). Pre-fix,
		# the GL drill page derived its companies-filter universe from
		# the URL `companies=` param alone -- when absent, the dropdown
		# hid even though the resolved scope spanned multiple companies.
		# Returning the actual permission-allowed list here lets the
		# client render the filter regardless of URL shape.
		"companies_in_scope": list(allowed),
	}


def _gl_csv_filename(scope_label: str, as_of_date, fnorm: dict) -> str:
	"""HALT 2.5 filename: insert `_filtered` infix between the scope
	slug and the as_of date when any of the four HALT 2.5 filter
	slots is non-default. Companies and party are not "filters" for
	this purpose -- they're scope concerns -- per spec §8.

	Examples:
	  - no filters:   gl_entries_<slug>_<as_of>_<HHMMSS>.csv
	  - any filter:   gl_entries_<slug>_filtered_<as_of>_<HHMMSS>.csv
	"""
	slug = _slugify_for_filename(scope_label or "scope")
	if hasattr(as_of_date, "isoformat"):
		date_str = as_of_date.isoformat()
	else:
		date_str = str(as_of_date)
	ts = datetime.now().strftime("%H%M%S")
	infix = "_filtered" if _filters_active(fnorm) else ""
	return f"gl_entries_{slug}{infix}_{date_str}_{ts}.csv"


def _build_gl_csv(entries: list) -> str:
	"""Build the GL entries CSV body. UTF-8, QUOTE_MINIMAL, \\n line
	endings (csv module default).

	Numeric columns formatted as raw decimals via Python's `str()`;
	`flt()` from frappe already returns floats in the right shape.
	No locale formatting is applied -- the spec is explicit.
	"""
	buf = io.StringIO()
	w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
	w.writerow(GL_CSV_HEADERS)
	for e in entries:
		w.writerow([
			e["posting_date"],
			e["voucher_type"],
			e["voucher_no"],
			e["company"],
			e["account"],
			e["party_type"] or "",
			e["party"] or "",
			f"{flt(e['debit']):.2f}",
			f"{flt(e['credit']):.2f}",
			f"{flt(e['running_balance']):.2f}",
			e["remarks"] or "",
		])
	return buf.getvalue()
