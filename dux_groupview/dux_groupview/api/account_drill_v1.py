"""Account drill API.

Per Phase 4 spec §4.1. Reads `tabDGV TB Snapshot Row` only -- this is
a cockpit read in the amended CLAUDE.md rule 1, NOT a `_drill`-suffixed
GL reader. (The `_v1` suffix is a versioning convention; the `_drill`
keyword applies only to APIs that read `tabGL Entry`. See
`api/party_drill_v1.py` for the GL-reading drill.)

Two entry shapes (per spec §4.1 "Two entry shapes"):
  - `scope`:    pivot row click. ScopeSpec resolved to a leaf list
                server-side via `utils._resolve_scope_to_leaves`.
  - `accounts`: card click. Pre-resolved leaf list passed directly.
                Caller (cockpit JS, via `cards_v1.resolve_match_to_accounts`)
                has already translated the card's `match` predicate.
                When `accounts` is given, `scope` is ignored.

Sign convention: snapshot stores raw `Dr - Cr`; this reader applies
the natural-side flip via `FLIP_ROOT_TYPES` so values display
positive on their natural side. Parity with party drill SQL is
asserted by tests in `test_party_drill.py`.
"""

import csv
import io
import json
import re
from calendar import monthrange
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api.utils import (
	FLIP_ROOT_TYPES,
	PARTY_TRACKABLE_ACCOUNT_TYPES,
	_named_in,
	_resolve_scope_to_leaves,
)
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	SPARKLINE_LENGTH,
)


@frappe.whitelist()
def get_account_breakdown(scope=None, accounts=None, scope_label=None,
                          as_of_date=None, companies=None):
	"""Return the account drill panel data for one scope.

	Output shape per spec §4.1:

	    {
	      "scope_label": str,
	      "group_total": float,           # natural-side, raw rupees
	      "is_party_trackable": bool,
	      "trend_12mo": [{"month": "YYYY-MM", "value": float|None}, ...],
	      "by_company": [
	        {"company": str, "value": float, "sparkline": [12 vals]},
	        ...
	      ]
	    }
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

	# --- Resolve leaves + label ---
	if accounts is not None:
		if not scope_label:
			frappe.throw(_("scope_label is required when accounts is provided"))
		leaves = [a for a in accounts if isinstance(a, str)]
	else:
		if not isinstance(scope, dict):
			frappe.throw(_("scope or accounts is required"))
		leaves, default_label = _resolve_scope_to_leaves(scope, allowed)
		if not scope_label:
			scope_label = default_label

	if not allowed or not leaves:
		return _empty_response(scope_label, target_date)

	# --- 12-month trend dates (label, snapshot_date or None) ---
	trend = _trend_dates(target_date, SPARKLINE_LENGTH)
	known_dates = sorted({snap for _label, snap in trend if snap is not None})

	if not known_dates:
		return _empty_response(scope_label, target_date)

	# --- is_party_trackable ---
	is_party_trackable = _is_party_trackable(leaves)

	# --- Aggregate snapshot rows for all (date, company) pairs ---
	agg = _aggregate_by_date_company(leaves, allowed, known_dates)

	# --- Build response ---
	# `current_date` is the rightmost trend date (== latest snapshot
	# <= target_date). `group_total` and `by_company[].value` are taken
	# at this date.
	current_date = trend[-1][1]

	# by_company: one entry per company that has any non-zero value
	# in any of the trend dates. Prevents empty rows cluttering the
	# panel for companies whose scope has no balance.
	by_company = []
	for c in sorted(allowed):
		current_value = (
			agg.get((current_date, c), 0.0) if current_date else 0.0
		)
		sparkline = []
		any_nonzero = False
		for _label, snap in trend:
			if snap is None:
				sparkline.append(None)
			else:
				v = round(flt(agg.get((snap, c), 0.0)), 2)
				sparkline.append(v)
				if v != 0:
					any_nonzero = True
		if not any_nonzero and round(flt(current_value), 2) == 0:
			continue
		by_company.append({
			"company": c,
			"value": round(flt(current_value), 2),
			"sparkline": sparkline,
		})

	# Sort by_company by absolute value desc (largest contributors first).
	by_company.sort(key=lambda r: abs(r["value"]), reverse=True)

	# trend_12mo: sum across companies for each trend date
	trend_12mo = []
	for label, snap in trend:
		if snap is None:
			trend_12mo.append({"month": label, "value": None})
		else:
			value = sum(
				flt(agg.get((snap, c), 0.0)) for c in allowed
			)
			trend_12mo.append({
				"month": label,
				"value": round(value, 2),
			})

	group_total = sum(r["value"] for r in by_company)

	return {
		"scope_label": scope_label,
		"group_total": round(group_total, 2),
		"is_party_trackable": is_party_trackable,
		"trend_12mo": trend_12mo,
		"by_company": by_company,
	}


# ---------------------------------------------------------------------------
# Per-company per-account breakdown  (per spec/per-account-drill-expand.md)
# ---------------------------------------------------------------------------

# Cap per-company per-account rows. Mirrors the party-list cap so the
# panel's two lazy-fetch boundaries share an order-of-magnitude. When
# the query returns more than this, `truncated=True` and the response
# carries the true `total_accounts`; the CSV export is the escape
# hatch for the full list.
PER_COMPANY_ACCOUNT_CAP = 200


@frappe.whitelist()
def get_account_breakdown_for_company(scope=None, accounts=None,
                                       scope_label=None, company=None,
                                       as_of_date=None):
	"""Per-account breakdown within one company for a card / pivot scope.

	Output shape (per spec §5.2):

	    {
	      "company": str,
	      "scope_label": str,
	      "as_of_date": "YYYY-MM-DD",
	      "company_total": float,           # natural-side, sign-flipped
	      "accounts": [
	        {"account": str,                # full FK ("Sundry Cr - GHRCE")
	         "account_name": str,           # stripped ("Sundry Cr")
	         "balance": float,
	         "currency": str},
	        ...
	      ],
	      "total_accounts": int,            # count BEFORE truncation
	      "truncated": bool,
	    }

	Cap: at most `PER_COMPANY_ACCOUNT_CAP` (200) accounts in `accounts`.
	When the underlying query returns more, `truncated` is True and a
	second SQL pass populates the true `total_accounts` and
	`company_total` over the full set.

	Sort: `abs(balance) DESC, account_name ASC`.

	Two entry shapes mirror `get_account_breakdown`:
	  - `scope`:    ScopeSpec dict, resolved server-side via
	                `_resolve_scope_to_leaves(scope, [company])`.
	  - `accounts`: pre-resolved leaf list. `scope_label` required.

	Permission: `company` must be in the user's User-Permission-allowed
	companies; otherwise `PermissionError`. A missing `company` or
	missing-both-of-scope-and-accounts sets
	`frappe.local.response["malformed_scope"]` and raises
	`DoesNotExistError` -- matches the cards_v1 stale-deep-link pattern
	so the panel's invalid-scope tile renders consistently.
	"""
	from dux_groupview.dux_groupview.api.pivot import (
		_require_cockpit_role,
		_resolve_scope,
	)

	_require_cockpit_role()

	scope = _ensure_dict(scope)
	accounts = _ensure_list(accounts)

	# --- Malformed-scope checks (stale-deep-link path) ---
	if not isinstance(company, str) or not company.strip():
		frappe.local.response["malformed_scope"] = True
		frappe.throw(
			_("`company` is required."),
			frappe.DoesNotExistError,
		)
	company = company.strip()

	if scope is None and accounts is None:
		frappe.local.response["malformed_scope"] = True
		frappe.throw(
			_("Either `scope` or `accounts` must be provided."),
			frappe.DoesNotExistError,
		)

	# --- Permission check on the requested company ---
	allowed = _resolve_scope(None)
	if company not in allowed:
		frappe.throw(
			_("You do not have permission to view company '{0}'.").format(company),
			frappe.PermissionError,
		)

	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	# --- Resolve leaves + label, scoped to this one company ---
	# Passing `[company]` (rather than the full allowed list) keeps
	# subtree walks tight: only this company's tree is walked, fewer
	# leaves flow into the SQL IN-clause. For "account" / "name_pattern"
	# scopes the leaf set is unchanged; for "subtree" it is narrower.
	if accounts is not None:
		if not scope_label:
			frappe.throw(_("scope_label is required when accounts is provided"))
		leaves = [a for a in accounts if isinstance(a, str)]
	else:
		if not isinstance(scope, dict):
			frappe.throw(_("scope or accounts is required"))
		leaves, default_label = _resolve_scope_to_leaves(scope, [company])
		if not scope_label:
			scope_label = default_label

	snap_date = _latest_snapshot_le(target_date)

	# Defensive empty: no leaves resolved or no snapshot at/before
	# target. Both legitimate runtime cases (e.g. predicate matches
	# nothing in this company, or pre-Phase-1 historical date).
	if not leaves or snap_date is None:
		return {
			"company": company,
			"scope_label": scope_label or "",
			"as_of_date": str(snap_date) if snap_date else str(target_date),
			"company_total": 0.0,
			"accounts": [],
			"total_accounts": 0,
			"truncated": False,
		}

	# --- Per-account aggregation ---
	# LIMIT cap+1: fetch one extra row to detect truncation in a single
	# round-trip (cheaper than a separate COUNT(*) for the common
	# untruncated case).
	a_ph, a_params = _named_in("a", leaves)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)

	rows = frappe.db.sql(
		f"""
		SELECT
		  s.account,
		  a.account_name,
		  COALESCE(SUM(
		    CASE WHEN a.root_type IN ({f_ph})
		         THEN -s.balance
		         ELSE s.balance
		    END
		  ), 0) AS balance,
		  COALESCE(MAX(a.account_currency), '') AS currency
		FROM `tabDGV TB Snapshot Row` s
		JOIN `tabAccount` a ON a.name = s.account
		WHERE s.snapshot_date = %(snap_date)s
		  AND s.company = %(company)s
		  AND s.account IN ({a_ph})
		GROUP BY s.account, a.account_name
		HAVING ABS(balance) >= 0.01
		ORDER BY ABS(balance) DESC, a.account_name ASC
		LIMIT %(limit)s
		""",
		{
			**a_params,
			**f_params,
			"snap_date": snap_date,
			"company": company,
			"limit": PER_COMPANY_ACCOUNT_CAP + 1,
		},
		as_dict=True,
	)

	truncated = len(rows) > PER_COMPANY_ACCOUNT_CAP
	visible_rows = rows[:PER_COMPANY_ACCOUNT_CAP]

	accounts_out = [
		{
			"account": r["account"],
			"account_name": r["account_name"],
			"balance": round(flt(r["balance"]), 2),
			"currency": r.get("currency") or "",
		}
		for r in visible_rows
	]

	if truncated:
		# Second query: true total_accounts and company_total over the
		# full set (not just the visible 200). Nested subquery applies
		# the same HAVING ABS(balance) >= 0.01 filter so the count
		# matches what the user would see if cap were unbounded.
		total_row = frappe.db.sql(
			f"""
			SELECT COUNT(*) AS total_accounts,
			       COALESCE(SUM(balance), 0) AS company_total
			FROM (
			    SELECT
			      s.account,
			      SUM(
			        CASE WHEN a.root_type IN ({f_ph})
			             THEN -s.balance
			             ELSE s.balance
			        END
			      ) AS balance
			    FROM `tabDGV TB Snapshot Row` s
			    JOIN `tabAccount` a ON a.name = s.account
			    WHERE s.snapshot_date = %(snap_date)s
			      AND s.company = %(company)s
			      AND s.account IN ({a_ph})
			    GROUP BY s.account
			    HAVING ABS(balance) >= 0.01
			) t
			""",
			{
				**a_params,
				**f_params,
				"snap_date": snap_date,
				"company": company,
			},
			as_dict=True,
		)[0]
		total_accounts = int(total_row["total_accounts"])
		company_total = round(flt(total_row["company_total"]), 2)
	else:
		total_accounts = len(accounts_out)
		company_total = round(sum(a["balance"] for a in accounts_out), 2)

	return {
		"company": company,
		"scope_label": scope_label or "",
		"as_of_date": str(snap_date),
		"company_total": company_total,
		"accounts": accounts_out,
		"total_accounts": total_accounts,
		"truncated": truncated,
	}


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


def _empty_response(scope_label, target_date):
	# Pad an all-None trend so the panel still renders the 12-slot axis.
	trend = _trend_dates(target_date, SPARKLINE_LENGTH)
	return {
		"scope_label": scope_label or "",
		"group_total": 0.0,
		"is_party_trackable": False,
		"trend_12mo": [{"month": label, "value": None} for label, _ in trend],
		"by_company": [],
	}


def _is_party_trackable(leaves: list) -> bool:
	"""True if any resolved leaf has account_type in PARTY_TRACKABLE_ACCOUNT_TYPES."""
	if not leaves:
		return False
	a_ph, a_params = _named_in("a", leaves)
	t_ph, t_params = _named_in("t", PARTY_TRACKABLE_ACCOUNT_TYPES)
	row = frappe.db.sql(
		f"""
		SELECT 1 FROM `tabAccount`
		WHERE name IN ({a_ph})
		  AND account_type IN ({t_ph})
		LIMIT 1
		""",
		{**a_params, **t_params},
	)
	return bool(row)


def _aggregate_by_date_company(leaves: list, companies: list, dates: list) -> dict:
	"""Return {(snapshot_date, company): natural_side_value} for the given inputs.

	One round-trip; in-Python pivoting after.
	"""
	if not leaves or not companies or not dates:
		return {}
	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", companies)
	d_ph, d_params = _named_in("d", dates)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)
	rows = frappe.db.sql(
		f"""
		SELECT snapshot_date, company,
		       COALESCE(SUM(
		         CASE WHEN root_type IN ({f_ph})
		              THEN -balance
		              ELSE balance
		         END
		       ), 0) AS value
		FROM `tabDGV TB Snapshot Row`
		WHERE account IN ({a_ph})
		  AND company IN ({c_ph})
		  AND snapshot_date IN ({d_ph})
		GROUP BY snapshot_date, company
		""",
		{**a_params, **c_params, **d_params, **f_params},
		as_dict=True,
	)
	return {
		(getdate(r["snapshot_date"]), r["company"]): flt(r["value"])
		for r in rows
	}


def _trend_dates(target, n):
	"""Return [(month_label, snapshot_date_or_None)] for the last `n` calendar months.

	Each month's snapshot is the latest `DGV TB Snapshot` whose date
	is <= the lesser of the month-end and the caller's `target` date.
	A month with no snapshot yields a None snapshot_date but keeps its
	calendar label so the trend visualisation renders evenly-spaced
	slots.

	Order: oldest-first (left-to-right time axis).
	"""
	target = getdate(target)
	out = []
	for i in range(n - 1, -1, -1):
		mm = target.month - i
		yy = target.year
		while mm <= 0:
			mm += 12
			yy -= 1
		last_day = monthrange(yy, mm)[1]
		month_end = date(yy, mm, last_day)
		cap = min(month_end, target)
		snap = _latest_snapshot_le(cap)
		out.append((f"{yy:04d}-{mm:02d}", snap))
	return out


def _latest_snapshot_le(target):
	"""Latest `DGV TB Snapshot.snapshot_date` (status='Complete') <= target."""
	target = getdate(target)
	row = frappe.db.sql(
		"""
		SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
		WHERE snapshot_date <= %s AND status = 'Complete'
		""",
		(target,),
	)
	if not row or not row[0][0]:
		return None
	return getdate(row[0][0])


# ---------------------------------------------------------------------------
# CSV export endpoint  (HALT 2)
# ---------------------------------------------------------------------------

ACCOUNT_BREAKDOWN_CSV_HEADERS = ("Company", "Account", "Balance", "Currency")


@frappe.whitelist()
def export_account_breakdown_csv(scope=None, accounts=None, scope_label=None,
                                 as_of_date=None, companies=None):
	"""Per-(company, account) breakdown CSV for the current scope.

	Reads `tabDGV TB Snapshot Row` only -- this is a cockpit read,
	NOT a `_drill`-suffixed GL reader. (HALT 2 columns are more
	granular than spec §9's draft, which only had per-company
	roll-up + sparkline. The HALT 2 instruction is canonical for
	v1; spec §9 will be aligned in a v0.6 follow-up.)

	Output columns: Company, Account, Balance, Currency.

	One row per (company, account) leaf in scope with non-zero
	balance at the resolved snapshot date. Sign convention applies
	the FLIP_ROOT_TYPES natural-side flip (consistent with
	get_account_breakdown).

	Cell format (locked at HALT 2):
	  - Balance: raw decimal like ``"4500000.00"`` -- NO Indian
	    grouping. Same reasoning as gl_drill_v1.export_gl_entries_csv:
	    spreadsheet apps reformat per locale on import; pre-formatting
	    breaks numerical typing.
	  - Empty currency renders as empty string (some accounts may
	    have NULL `account_currency` -- rare, harmless).
	"""
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

	allowed = _resolve_scope(companies)
	target_date = getdate(as_of_date) if as_of_date else getdate(today())

	# --- Resolve leaves + label (mirrors get_account_breakdown) ---
	resolved_label = scope_label or ""
	if accounts is not None:
		leaves = [a for a in accounts if isinstance(a, str)]
	elif isinstance(scope, dict):
		leaves, default_label = _resolve_scope_to_leaves(scope, allowed)
		if not resolved_label:
			resolved_label = default_label
	else:
		leaves = []

	if not allowed or not leaves:
		csv_string = _build_account_breakdown_csv([])
		_set_csv_response(
			_csv_filename("account_breakdown", resolved_label, target_date),
			csv_string,
		)
		return

	# --- Resolve snapshot date ---
	snap_date = _latest_snapshot_le(target_date)
	if snap_date is None:
		# No snapshot exists at or before target -- empty CSV.
		csv_string = _build_account_breakdown_csv([])
		_set_csv_response(
			_csv_filename("account_breakdown", resolved_label, target_date),
			csv_string,
		)
		return

	# --- Per-(company, account) balances at the snapshot date ---
	# Snapshot table stores balance per (date, company, account); sum
	# would be a no-op (one row per tuple), but using SUM keeps the
	# CASE flip terse and tolerates duplicates if any.
	a_ph, a_params = _named_in("a", leaves)
	c_ph, c_params = _named_in("c", allowed)
	f_ph, f_params = _named_in("f", FLIP_ROOT_TYPES)

	rows = frappe.db.sql(
		f"""
		SELECT
		  s.company,
		  s.account,
		  COALESCE(SUM(
		    CASE WHEN a.root_type IN ({f_ph})
		         THEN -s.balance
		         ELSE s.balance
		    END
		  ), 0) AS balance,
		  COALESCE(MAX(a.account_currency), '') AS currency
		FROM `tabDGV TB Snapshot Row` s
		JOIN `tabAccount` a ON a.name = s.account
		WHERE s.snapshot_date = %(snap_date)s
		  AND s.account IN ({a_ph})
		  AND s.company IN ({c_ph})
		GROUP BY s.company, s.account
		HAVING ABS(balance) >= 0.01
		ORDER BY s.company ASC, s.account ASC
		""",
		{**a_params, **c_params, **f_params, "snap_date": snap_date},
		as_dict=True,
	)

	csv_string = _build_account_breakdown_csv(rows)
	_set_csv_response(
		_csv_filename("account_breakdown", resolved_label, target_date),
		csv_string,
	)


def _build_account_breakdown_csv(rows: list) -> str:
	"""Build the account-breakdown CSV body. UTF-8, QUOTE_MINIMAL,
	\\n line endings, raw decimals.
	"""
	buf = io.StringIO()
	w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
	w.writerow(ACCOUNT_BREAKDOWN_CSV_HEADERS)
	for r in rows:
		w.writerow([
			r["company"],
			r["account"],
			f"{flt(r['balance']):.2f}",
			r.get("currency") or "",
		])
	return buf.getvalue()
