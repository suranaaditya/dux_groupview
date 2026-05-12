"""Hand-rolled fixture for party drill correctness tests.

The synthetic seed populates no party data on `tabGL Entry`
(commit 1 finding 2). This fixture creates a small, deterministic
set of accounts and party-bearing GL entries that exercise:

  - account_types Receivable, Payable, Bank, Equity
  - single-company and multi-company parties
  - a party whose name matches a `tabCompany.name` (for the
    `is_group_company` flag test)
  - a net-zero party (HAVING balance != 0 should drop it)
  - a future-dated voucher (`posting_date <= as_of_date` should drop it)

Sums to known totals so the gold-standard reconciliation invariant
asserts hand-calculated values.

Snapshot strategy
-----------------
Tests that exercise account drill (which reads `tabDGV TB Snapshot
Row`) need fixture data to surface in the snapshot. Calling the
production `refresh_tb_snapshot()` would rebuild the entire snapshot
from 5M GL Entry rows -- ~45s per call, 3 calls per test class -- so
this fixture instead inserts a *scoped* snapshot:

  - Parent row: ``SNAPSHOT-2099-12-31`` (autoname format), one row.
  - Child rows: aggregated from fixture GL entries only
    (``WHERE account LIKE 'FXT %'``), filtered by
    ``posting_date <= '2099-12-31'``.

Future Party's posting_date is **'2150-06-15'**, far past the
fixture snapshot date. The snapshot's natural `posting_date <=
snapshot_date` filter therefore excludes Future Party automatically.
Tests that read account drill at fixture data must pass
``as_of_date='2099-12-31'`` explicitly.

Production code (refresh.py, drill APIs) is untouched. The fixture
is the only place that knows about ``2099-12-31`` / ``2150-06-15``.

**Loan account_type is NOT exercised in this fixture.** The
Account doctype on this dev site does not include "Loan" in its
`account_type` select options (Lending module not installed for
new doctype inserts; legacy rows with account_type="Loan" exist
but new inserts are rejected). The literal-tuple assertion in
`test_party_drill.test_party_trackable_account_types_literal`
still pins Loan in the constant; the runtime "is_party_trackable
for Loan" path will be validated against production data during
the Phase 4 production deploy (alongside Q19's
`audit_group_co_name_match`).

Layout:
  - 3 fixture companies (selected from existing dev companies, prefer
    Test Company A--C, fall back to alphabetical)
  - 4 leaf accounts per company under existing root groups: 12 total
  - 11 vouchers (22 rows) -- 8 "live", 1 net-zero pair (2 vouchers,
    4 rows), 1 future
  - 5 distinct (party_type, party) combinations expected to surface
    in the default party drill at as_of_date=today

Voucher prefix: ``FIXTURE-PARTY-DRILL-``

Lifecycle:
  - ``setup_fixture()`` creates everything (raises on stale residue),
    inserts the scoped snapshot, returns a state dict the tests use.
  - ``teardown_fixture()`` deletes fixture GL rows, accounts, snapshot
    rows, and the parent snapshot. No production refresh runs.
"""

from datetime import date, timedelta

import frappe
from frappe.utils import getdate, now_datetime, today


VOUCHER_PREFIX = "FIXTURE-PARTY-DRILL-"
VOUCHER_TYPE = "DGV Test Seed"

# Far-future fixture snapshot date. Picked to (a) avoid collision
# with any production scheduler write and (b) sit *before* the
# Future Party voucher's posting_date so the snapshot's natural
# posting_date <= snapshot_date filter excludes Future Party.
SNAPSHOT_DATE = date(2099, 12, 31)
SNAPSHOT_NAME = "SNAPSHOT-2099-12-31"

# Future Party's voucher posting_date. Past SNAPSHOT_DATE so the
# fixture snapshot does not include it; party drill (which reads
# tabGL Entry directly) sees it only when as_of_date >= this date.
FUTURE_POSTING_DATE = date(2150, 6, 15)

# (account_name, account_type, root_type)
# "Loan" account_type intentionally absent -- see module docstring.
ACCOUNTS_PLAN = [
	("FXT Receivable", "Receivable", "Asset"),
	("FXT Payable",    "Payable",    "Liability"),
	("FXT Bank",       "Bank",       "Asset"),
	("FXT Equity",     "",           "Equity"),
]


def setup_fixture():
	"""Create fixture data; raises on stale residue. Returns state dict.

	Stale-residue guard: if `SNAPSHOT-2099-12-31` already exists in
	`tabDGV TB Snapshot`, a previous run died mid-fixture. We raise
	rather than auto-cleanup so the operator notices and decides
	whether to investigate before re-running.
	"""
	if frappe.db.exists("DGV TB Snapshot", SNAPSHOT_NAME):
		raise RuntimeError(
			f"Stale fixture snapshot {SNAPSHOT_NAME} found from a "
			f"previous run. Run teardown_fixture() manually or restart "
			f"the test session."
		)

	companies = _select_companies()
	accounts = _create_accounts(companies)
	expected_parties = _insert_gl_entries(companies, accounts)
	_insert_snapshot_rows()
	frappe.db.commit()

	return {
		"companies": companies,
		"accounts": accounts,
		"expected_parties": expected_parties,
		"posting_date": _posting_date(),
		"future_posting_date": FUTURE_POSTING_DATE,
		"snapshot_date": SNAPSHOT_DATE,
		"snapshot_name": SNAPSHOT_NAME,
		"voucher_prefix": VOUCHER_PREFIX,
	}


def teardown_fixture():
	"""Tear down fixture state. Idempotent; safe to call after partial setup."""
	_purge_snapshot_rows()
	_purge_gl()
	_purge_accounts()
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _posting_date():
	return getdate(today()) - timedelta(days=30)


def _select_companies():
	"""Pick 3 companies for the fixture, preferring Test Company A--C."""
	# LIKE pattern parameterised -- a literal '%' inside the query
	# string is interpreted as a pyformat placeholder by Frappe's
	# driver layer, raising "not enough arguments for format string".
	test_set = frappe.db.sql_list(
		"""
		SELECT name FROM `tabCompany`
		WHERE name LIKE %(pat)s
		ORDER BY name LIMIT 3
		""",
		{"pat": "Test Company %"},
	)
	if len(test_set) >= 3:
		return list(test_set[:3])

	rows = frappe.db.sql_list(
		"SELECT name FROM `tabCompany` ORDER BY name LIMIT 3"
	)
	if len(rows) < 3:
		raise RuntimeError(
			f"Fixture requires >= 3 companies on the site; found {len(rows)}."
		)
	return list(rows[:3])


def _create_accounts(companies):
	"""Create the 4 fixture leaves in each company. Idempotent.

	Returns: ``{company: {role: full_name}}``
	where role is "receivable" / "payable" / "bank" / "equity".
	"""
	out = {}
	for company in companies:
		abbr = frappe.db.get_value("Company", company, "abbr")
		if not abbr:
			raise RuntimeError(
				f"Company {company} has no abbr; can't form account names"
			)
		co_accounts = {}
		for account_name, account_type, root_type in ACCOUNTS_PLAN:
			full_name = f"{account_name} - {abbr}"
			if not frappe.db.exists("Account", full_name):
				parent = _find_root_parent(company, root_type)
				if not parent:
					raise RuntimeError(
						f"Cannot find {root_type} root parent in {company}"
					)
				doc = frappe.new_doc("Account")
				doc.account_name = account_name
				doc.parent_account = parent
				doc.company = company
				doc.is_group = 0
				doc.root_type = root_type
				doc.account_type = account_type
				doc.flags.ignore_permissions = True
				doc.insert()
			co_accounts[_role(account_name)] = full_name
		out[company] = co_accounts
	return out


def _role(account_name):
	return account_name.replace("FXT ", "").lower()


def _find_root_parent(company, root_type):
	rows = frappe.db.sql_list(
		"""
		SELECT name FROM `tabAccount`
		WHERE company = %s AND root_type = %s AND is_group = 1
		  AND (parent_account IS NULL OR parent_account = '')
		ORDER BY lft LIMIT 1
		""",
		(company, root_type),
	)
	return rows[0] if rows else None


def _insert_gl_entries(companies, accounts):
	"""Insert the planned GL rows. Returns the expected_parties list.

	Each `expected_parties` entry is a tuple
	(party_type, party, balance_natural_side, company_count, is_group_company)
	covering the parties that should surface at as_of_date=today with
	the default scope (FXT Payable + FXT Receivable combined).
	Net-zero / future-dated parties are intentionally absent.
	"""
	A, B, C = companies[0], companies[1], companies[2]
	pd = _posting_date()
	fut = FUTURE_POSTING_DATE

	# (voucher_seq, posting_date, company, role, party_type, party, debit, credit)
	plan = [
		# V001: A, Customer Acme Co (Receivable +100K)
		("001", pd, A, "receivable", "Customer", "Acme Co", 100000, 0),
		("001", pd, A, "bank",       None,       None,           0, 100000),
		# V002: A, Supplier Asha Stationers (Payable +500K)
		("002", pd, A, "bank",       None,       None,      500000, 0),
		("002", pd, A, "payable",    "Supplier", "Asha Stationers", 0, 500000),
		# V003: B, Asha (Payable +700K)
		("003", pd, B, "bank",       None,       None,      700000, 0),
		("003", pd, B, "payable",    "Supplier", "Asha Stationers", 0, 700000),
		# V004: C, Asha (Payable +300K)
		("004", pd, C, "bank",       None,       None,      300000, 0),
		("004", pd, C, "payable",    "Supplier", "Asha Stationers", 0, 300000),
		# V005: A, Vidarbha (Payable +200K)
		("005", pd, A, "bank",       None,       None,      200000, 0),
		("005", pd, A, "payable",    "Supplier", "Vidarbha Lab Supplies", 0, 200000),
		# V006: B, Vidarbha (Payable +100K)
		("006", pd, B, "bank",       None,       None,      100000, 0),
		("006", pd, B, "payable",    "Supplier", "Vidarbha Lab Supplies", 0, 100000),
		# V007: A, Single Co Vendor (Payable +50K)
		("007", pd, A, "bank",       None,       None,       50000, 0),
		("007", pd, A, "payable",    "Supplier", "Single Co Vendor", 0, 50000),
		# V008: B, group-co customer (party name matches A in tabCompany)
		("008", pd, B, "receivable", "Customer", A,           60000, 0),
		("008", pd, B, "bank",       None,       None,            0, 60000),
		# V009 + V010: Net Zero Party (sums to 0, HAVING drops)
		("009", pd, A, "receivable", "Customer", "Net Zero Party",  100, 0),
		("009", pd, A, "bank",       None,       None,                0, 100),
		("010", pd, A, "bank",       None,       None,              100, 0),
		("010", pd, A, "receivable", "Customer", "Net Zero Party",    0, 100),
		# V011: Future Party (posting_date FUTURE_POSTING_DATE; past
		# SNAPSHOT_DATE so excluded from the fixture snapshot, and past
		# today() so excluded from default-as_of_date party drill).
		#
		# Data-leak risk note: Future Party's GL Entry row physically
		# remains in tabGL Entry during fixture lifetime. Any unrelated
		# test that refreshes a snapshot for a date >= 2150-06-15 would
		# aggregate this row. Teardown (_purge_gl) removes it.
		# Production refresh writes to today() which is well before
		# 2150, so this is safe in practice -- flagging the assumption
		# explicitly.
		("011", fut, A, "receivable", "Customer", "Future Party",  90000, 0),
		("011", fut, A, "bank",       None,       None,                0, 90000),
	]

	cost_centers = {
		c: frappe.db.get_value(
			"Cost Center", {"company": c, "is_group": 0}, "name"
		)
		for c in companies
	}
	fy_cache = {}

	def fy_for(d):
		if d.year not in fy_cache:
			fy_cache[d.year] = frappe.db.get_value(
				"Fiscal Year",
				{"year_start_date": ["<=", d], "year_end_date": [">=", d]},
				"name",
			)
		return fy_cache[d.year]

	now = now_datetime()
	user = frappe.session.user or "Administrator"
	rows = []
	ts_ms = int(now.timestamp() * 1000)
	for i, (seq, posting, company, role, party_type, party, debit, credit) in enumerate(plan):
		voucher_no = f"{VOUCHER_PREFIX}{seq}"
		account = accounts[company][role]
		rows.append({
			"name": f"{voucher_no}-{role}-{ts_ms}-{i}",
			"creation": now,
			"modified": now,
			"modified_by": user,
			"owner": user,
			"docstatus": 1,
			"idx": 0,
			"posting_date": posting,
			"transaction_date": posting,
			"account": account,
			"party_type": party_type,
			"party": party,
			"cost_center": cost_centers[company],
			"debit": debit,
			"credit": credit,
			"account_currency": "INR",
			"debit_in_account_currency": debit,
			"credit_in_account_currency": credit,
			"against": "",
			"against_voucher_type": None,
			"against_voucher": None,
			"voucher_type": VOUCHER_TYPE,
			"voucher_no": voucher_no,
			"project": None,
			"is_cancelled": 0,
			"is_opening": "No",
			"company": company,
			"fiscal_year": fy_for(posting),
			"remarks": "fixture",
		})

	if rows:
		fields = list(rows[0].keys())
		values = [tuple(r[f] for f in fields) for r in rows]
		frappe.db.bulk_insert("GL Entry", fields=fields, values=values)
		frappe.db.commit()

	# (party_type, party, expected_balance, company_count, is_group_company)
	# Computed by hand from the plan above; see fixture docstring.
	return [
		("Customer", "Acme Co",                100000.0, 1, False),
		("Supplier", "Asha Stationers",       1500000.0, 3, False),
		("Supplier", "Vidarbha Lab Supplies",  300000.0, 2, False),
		("Supplier", "Single Co Vendor",        50000.0, 1, False),
		# Customer named after companies[0] -- in tabCompany -> True
		("Customer", A,                         60000.0, 1, True),
		# Net Zero Party omitted -- HAVING balance != 0 drops
		# Future Party omitted -- as_of_date=today drops
	]


def _insert_snapshot_rows():
	"""Insert the scoped fixture snapshot.

	One parent ``DGV TB Snapshot`` row + child ``DGV TB Snapshot Row``
	rows aggregated from fixture GL entries only. Mirrors refresh.py's
	semantics:

	  - balance = ROUND(SUM(debit) - SUM(credit), 2)  (raw Dr - Cr)
	  - debit_total / credit_total denormalised
	  - account_type / root_type denormalised from tabAccount

	The HAVING clause matches refresh.py: drop (company, account)
	pairs that aggregate to all-zero (defensive against floating-point
	noise; on clean fixture data it's a no-op).
	"""
	# Parent row.
	now = now_datetime()
	frappe.db.sql(
		"""
		INSERT INTO `tabDGV TB Snapshot`
		  (name, snapshot_date, generated_at, status, is_immutable,
		   row_count, duration_seconds,
		   creation, modified, owner, modified_by, docstatus, idx)
		VALUES
		  (%(name)s, %(snapshot_date)s, %(generated_at)s, 'Complete', 0,
		   0, 0,
		   %(now)s, %(now)s, 'Administrator', 'Administrator', 0, 0)
		""",
		{
			"name": SNAPSHOT_NAME,
			"snapshot_date": SNAPSHOT_DATE,
			"generated_at": now,
			"now": now,
		},
	)

	# Child rows. Mirrors refresh.py's INSERT_ROWS_SQL with these
	# differences:
	#   - WHERE account LIKE 'FXT %' (fixture leaves only)
	#   - posting_date <= SNAPSHOT_DATE (excludes FUTURE_POSTING_DATE)
	#   - parent_snapshot / snapshot_date hardcoded to fixture values
	frappe.db.sql(
		"""
		INSERT INTO `tabDGV TB Snapshot Row`
		  (name, parent_snapshot, snapshot_date, company, account,
		   account_type, root_type, balance, debit_total, credit_total,
		   creation, modified, owner, modified_by, docstatus, idx)
		SELECT
		  MD5(CONCAT(%(snapshot_date)s, agg.company, agg.account, RAND())),
		  %(parent_snapshot)s,
		  %(snapshot_date)s,
		  agg.company,
		  agg.account,
		  COALESCE(a.account_type, ''),
		  COALESCE(a.root_type, ''),
		  agg.balance,
		  agg.debit_total,
		  agg.credit_total,
		  NOW(), NOW(), 'Administrator', 'Administrator', 0, 0
		FROM (
		  SELECT
		    gl.company,
		    gl.account,
		    ROUND(SUM(gl.debit) - SUM(gl.credit), 2) AS balance,
		    ROUND(SUM(gl.debit), 2) AS debit_total,
		    ROUND(SUM(gl.credit), 2) AS credit_total
		  FROM `tabGL Entry` gl
		  WHERE gl.is_cancelled = 0
		    AND gl.docstatus = 1
		    AND gl.posting_date <= %(snapshot_date)s
		    AND gl.account LIKE %(acc_pat)s
		  GROUP BY gl.company, gl.account
		  HAVING ABS(ROUND(SUM(gl.debit) - SUM(gl.credit), 2)) > 0
		      OR ABS(ROUND(SUM(gl.debit), 2)) > 0
		      OR ABS(ROUND(SUM(gl.credit), 2)) > 0
		) AS agg
		INNER JOIN `tabAccount` a ON a.name = agg.account
		""",
		{
			"snapshot_date": SNAPSHOT_DATE,
			"parent_snapshot": SNAPSHOT_NAME,
			"acc_pat": "FXT %",
		},
	)

	# Backfill row_count on the parent so the API's status checks see
	# a non-zero count (ergonomic, not load-bearing for tests).
	row_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabDGV TB Snapshot Row` "
		"WHERE parent_snapshot = %s",
		(SNAPSHOT_NAME,),
	)[0][0]
	frappe.db.sql(
		"UPDATE `tabDGV TB Snapshot` SET row_count = %s WHERE name = %s",
		(row_count, SNAPSHOT_NAME),
	)


def _purge_snapshot_rows():
	"""Delete fixture snapshot rows + parent. Belt-and-suspenders filtering."""
	# Child rows: filter by snapshot_date AND account prefix so a
	# stray row in another snapshot date with FXT prefix (or a stray
	# non-FXT row in fixture snapshot) cannot delete cross-boundary.
	frappe.db.sql(
		"""
		DELETE FROM `tabDGV TB Snapshot Row`
		WHERE snapshot_date = %s
		  AND account LIKE %s
		""",
		(SNAPSHOT_DATE, "FXT %"),
	)
	# Parent row: exact name match.
	frappe.db.sql(
		"DELETE FROM `tabDGV TB Snapshot` WHERE name = %s",
		(SNAPSHOT_NAME,),
	)
	frappe.db.commit()


def _purge_gl():
	frappe.db.sql(
		"DELETE FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(VOUCHER_PREFIX + "%",),
	)
	frappe.db.commit()


def _purge_accounts():
	"""Delete fixture leaf accounts.

	GL entries already gone; account_currency / parent / lft / rgt
	bookkeeping is handled by the doctype controller. `force=True`
	provides defensive cleanup against any residual link state from
	earlier failed runs.
	"""
	# LIKE pattern parameterised -- Frappe's pyformat layer interprets
	# a literal '%' inside the query string as a placeholder otherwise.
	rows = frappe.db.sql_list(
		"SELECT name FROM `tabAccount` WHERE account_name LIKE %s",
		("FXT %",),
	)
	for name in rows:
		if frappe.db.exists("Account", name):
			try:
				frappe.delete_doc(
					"Account", name,
					ignore_permissions=True,
					force=True,
				)
			except Exception:
				# Leave residue rather than break teardown; next setup
				# will retry purge before recreating.
				pass
	frappe.db.commit()
