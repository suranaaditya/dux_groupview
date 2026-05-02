"""Light synthetic test data seeder for dux_groupview.

REMINDER: Take a database backup before the first run on any site that
has real data:

    bench --site <site> backup

Run with:

    bench --site <site> execute \\
        dux_groupview.dux_groupview.test_data.seed_light.seed_light_data

Creates 5 fake companies (Test Company A--E with abbrs TCA--TCE), the
standard ERPNext chart of accounts in each, plus 5 expense / 5 asset /
5 liability accounts under the standard groups. Then generates 50,000
balanced GL entries dated across the last 12 months.

The script is idempotent. On a re-run:
  * Companies that already exist are skipped (COA already in place).
  * GL entries with voucher_no LIKE 'DGV-TEST-%' are deleted and
    regenerated. A safety check aborts the purge if any matching row
    has a voucher_no that does not start with 'DGV-TEST-'.
"""

import random
import time
from datetime import date, timedelta

import frappe
from frappe.utils import flt, nowdate, getdate

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

COMPANIES = [
	{"name": "Test Company A", "abbr": "TCA"},
	{"name": "Test Company B", "abbr": "TCB"},
	{"name": "Test Company C", "abbr": "TCC"},
	{"name": "Test Company D", "abbr": "TCD"},
	{"name": "Test Company E", "abbr": "TCE"},
]

EXTRA_EXPENSE_ACCOUNTS = [
	"Travel Expenses",
	"Office Supplies",
	"Utilities",
	"Telephone Expenses",
	"Repairs and Maintenance",
]
EXTRA_ASSET_ACCOUNTS = [
	"Petty Cash",
	"Prepaid Expenses",
	"Security Deposits",
	"Accrued Income",
	"Office Equipment",
]
EXTRA_LIABILITY_ACCOUNTS = [
	"Accrued Expenses",
	"Statutory Dues",
	"Audit Fee Payable",
	"Salary Payable",
	"Director's Loan",
]

EXPENSE_PARENT_GROUPS = ["Direct Expenses", "Indirect Expenses"]
ASSET_PARENT_GROUPS = ["Current Assets", "Stock Assets"]
LIABILITY_PARENT_GROUPS = ["Current Liabilities", "Duties and Taxes"]

TARGET_GL_ENTRIES = 50_000
VOUCHER_PREFIX = "DGV-TEST-"
VOUCHER_TYPE = "DGV Test Seed"


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------

def seed_light_data():
	"""Top-level seed routine. Safe to re-run."""
	t_start = time.time()

	companies = _ensure_companies()
	_ensure_extra_accounts(companies)

	purged = _purge_test_gl_entries()
	if purged is False:
		# Safety check tripped; abort.
		return

	_generate_gl_entries(companies, purged)

	frappe.db.commit()
	print(f"\nSeed complete in {time.time() - t_start:.1f} sec.")


# ----------------------------------------------------------------------
# Companies
# ----------------------------------------------------------------------

def _ensure_companies():
	"""Create the 5 test companies if missing. Returns the company dicts."""
	created = 0
	# WORKAROUND for OPEN_QUESTIONS.md Q4 (dangling GST tax-account refs on
	# this dev site break any new Company insert). Scoped to this block only.
	with _suppress_gst_settings_revalidation():
		for c in COMPANIES:
			if frappe.db.exists("Company", c["name"]):
				continue
			doc = frappe.new_doc("Company")
			doc.company_name = c["name"]
			doc.abbr = c["abbr"]
			doc.default_currency = "INR"
			doc.country = "India"
			doc.create_chart_of_accounts_based_on = "Standard Template"
			doc.chart_of_accounts = "Standard"
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1
			print(f"  + Created company {c['name']} ({c['abbr']})")

	if created == 0:
		print(f"Companies: all {len(COMPANIES)} already exist (skipped)")
	else:
		print(f"Companies: created {created} of {len(COMPANIES)}")
	return COMPANIES


class _suppress_gst_settings_revalidation:
	"""
	WORKAROUND for a pre-existing dev-site data inconsistency
	(see OPEN_QUESTIONS.md Q4 -- GHR CACS Pune GST Settings inconsistency).

	Context manager that monkey-patches india_compliance's update_gst_settings
	to a no-op for the duration of seed company creation only. Without this,
	every new Company insert triggers a re-save of the GST Settings doctype,
	which fails on the jewonline dev site because pre-existing companies
	(GHR CACS Pune / abbr CACSPU) reference GST tax accounts that do not
	exist.

	Scope guarantees:
	  * The patch is applied only inside the `with` block in
	    _ensure_companies() and torn down on exit (or on exception, via the
	    standard context-manager protocol).
	  * The original `update_gst_settings` function reference is captured
	    before patching and restored verbatim afterward -- no global state
	    pollution beyond the `with` block.
	  * If india_compliance is not installed at all, the patch is a no-op.

	Remove this once Q4 is resolved (the dangling references are cleaned up
	on the dev site and we have verified Company creation succeeds without
	the patch).
	"""

	def __enter__(self):
		try:
			from india_compliance.gst_india.overrides import company as _ic_company

			self._mod = _ic_company
			self._orig = _ic_company.update_gst_settings
			_ic_company.update_gst_settings = lambda *a, **kw: None
		except ImportError:
			self._mod = None
			self._orig = None
		return self

	def __exit__(self, exc_type, exc, tb):
		if self._mod is not None and self._orig is not None:
			self._mod.update_gst_settings = self._orig


# ----------------------------------------------------------------------
# Extra accounts
# ----------------------------------------------------------------------

def _ensure_extra_accounts(companies):
	"""Add 5 expense / 5 asset / 5 liability accounts in each company."""
	plan = (
		(EXTRA_EXPENSE_ACCOUNTS, EXPENSE_PARENT_GROUPS, "Expense"),
		(EXTRA_ASSET_ACCOUNTS, ASSET_PARENT_GROUPS, "Asset"),
		(EXTRA_LIABILITY_ACCOUNTS, LIABILITY_PARENT_GROUPS, "Liability"),
	)
	created = 0
	for c in companies:
		abbr = c["abbr"]
		for names, parent_groups, root_type in plan:
			parent = _resolve_parent_group(parent_groups, abbr, root_type)
			if not parent:
				continue
			for name in names:
				full_name = f"{name} - {abbr}"
				if frappe.db.exists("Account", full_name):
					continue
				doc = frappe.new_doc("Account")
				doc.account_name = name
				doc.parent_account = parent
				doc.company = c["name"]
				doc.is_group = 0
				doc.root_type = root_type
				doc.account_type = ""
				doc.flags.ignore_permissions = True
				doc.insert()
				created += 1
	print(f"Extra accounts: created {created} (across {len(companies)} companies)")


def _resolve_parent_group(candidates, abbr, root_type):
	for group in candidates:
		full = f"{group} - {abbr}"
		if frappe.db.exists("Account", full):
			return full
	# Fall back to the root for the type.
	root = frappe.db.get_value(
		"Account",
		{"company": _company_by_abbr(abbr), "root_type": root_type, "is_group": 1, "parent_account": ""},
		"name",
	)
	return root


def _company_by_abbr(abbr):
	for c in COMPANIES:
		if c["abbr"] == abbr:
			return c["name"]
	return None


# ----------------------------------------------------------------------
# GL entry purge / regenerate
# ----------------------------------------------------------------------

def _purge_test_gl_entries():
	"""
	Delete prior seed GL entries. Returns the count purged on success,
	or False if the safety check tripped.
	"""
	matching = frappe.db.sql(
		"SELECT name, voucher_no FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{VOUCHER_PREFIX}%",),
		as_dict=True,
	)
	if not matching:
		print("Purge: no existing test GL entries")
		return 0

	bad = [r for r in matching if not r.voucher_no.startswith(VOUCHER_PREFIX)]
	if bad:
		print(
			f"ABORT: safety check tripped. {len(bad)} rows match "
			f"voucher_no LIKE '{VOUCHER_PREFIX}%' but do not start with "
			f"'{VOUCHER_PREFIX}'. Investigate manually before re-running."
		)
		return False

	frappe.db.sql(
		"DELETE FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{VOUCHER_PREFIX}%",),
	)
	frappe.db.commit()
	print(f"Purged {len(matching)} existing test GL entries before reseeding")
	return len(matching)


def _generate_gl_entries(companies, purged_count):
	"""Generate ~TARGET_GL_ENTRIES balanced GL rows across the last 12 months."""
	t0 = time.time()
	today_d = getdate(nowdate())
	start_d = today_d - timedelta(days=365)

	# Per-company account pools (exclude group accounts).
	pools = {}
	for c in companies:
		abbr = c["abbr"]
		accounts = frappe.db.sql(
			"""
			SELECT name, root_type, account_currency
			FROM `tabAccount`
			WHERE company = %s AND is_group = 0 AND disabled = 0
			""",
			(c["name"],),
			as_dict=True,
		)
		# Bucket: cash/bank-ish (debit-friendly) vs expense vs liability vs income.
		debit_pool = [a for a in accounts if a.root_type in ("Asset", "Expense")]
		credit_pool = [a for a in accounts if a.root_type in ("Liability", "Income", "Equity")]
		if not debit_pool or not credit_pool:
			# Fall back to all accounts if buckets are too thin.
			debit_pool = credit_pool = accounts
		pools[c["name"]] = (debit_pool, credit_pool)

	# Cost center per company (default).
	cost_centers = {}
	for c in companies:
		cc = frappe.db.get_value(
			"Cost Center",
			{"company": c["name"], "is_group": 0},
			"name",
		)
		cost_centers[c["name"]] = cc

	# Fiscal year per posting_date — cache the lookup.
	fy_cache = {}

	def fy_for(posting):
		key = posting.year
		if key in fy_cache:
			return fy_cache[key]
		fy = frappe.db.get_value(
			"Fiscal Year",
			{"year_start_date": ["<=", posting], "year_end_date": [">=", posting]},
			"name",
		)
		fy_cache[key] = fy
		return fy

	rng = random.Random(20260502)
	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"

	rows = []
	counter = 0
	target = TARGET_GL_ENTRIES

	while counter < target:
		counter += 1
		voucher_no = f"{VOUCHER_PREFIX}{counter:06d}"

		company = rng.choice(companies)["name"]
		debit_pool, credit_pool = pools[company]
		cc = cost_centers[company]

		# Random posting date in the last 365 days.
		days_back = rng.randint(0, 365)
		posting = today_d - timedelta(days=days_back)
		fy = fy_for(posting)

		# Group size: 2 to 5 entries (most are 2-3).
		# Roll: 60% -> 2, 25% -> 3, 10% -> 4, 5% -> 5
		r = rng.random()
		if r < 0.60:
			pair_count = 1  # 1 dr + 1 cr
		elif r < 0.85:
			pair_count = 1
			extra_legs = 1
		else:
			pair_count = 1
			extra_legs = 2 if r < 0.95 else 3

		extra = 0 if r < 0.60 else (extra_legs if r >= 0.60 else 0)
		legs_dr = 1 + (extra // 2)
		legs_cr = 1 + (extra - (extra // 2))
		# Random voucher amount in INR 1,000 -- 1,00,000.
		total_amount = round(rng.uniform(1000, 100000), 2)

		dr_amounts = _split_amount(total_amount, legs_dr, rng)
		cr_amounts = _split_amount(total_amount, legs_cr, rng)

		for amt in dr_amounts:
			acc = rng.choice(debit_pool)
			rows.append(_build_row(
				voucher_no, posting, company, fy, cc,
				acc, debit=amt, credit=0, now=now, user=user,
			))
		for amt in cr_amounts:
			acc = rng.choice(credit_pool)
			rows.append(_build_row(
				voucher_no, posting, company, fy, cc,
				acc, debit=0, credit=amt, now=now, user=user,
			))

		if len(rows) >= target:
			rows = rows[:target]
			break

	# Trim if we overshot (loop adds full vouchers; last one may push past target).
	rows = rows[:target]
	print(f"Built {len(rows):,} GL entry payloads in {time.time() - t0:.1f} sec")

	# Bulk insert in chunks.
	t1 = time.time()
	fields = list(rows[0].keys())
	values = [tuple(r[f] for f in fields) for r in rows]
	chunk = 5000
	inserted = 0
	for i in range(0, len(values), chunk):
		batch = values[i : i + chunk]
		frappe.db.bulk_insert("GL Entry", fields=fields, values=batch)
		inserted += len(batch)
		frappe.db.commit()
		print(f"  Inserted {inserted:,} / {len(values):,}")
	print(f"Inserted {len(values):,} GL entries in {time.time() - t1:.1f} sec")


def _split_amount(total, n, rng):
	"""Split `total` into `n` random positive parts that sum exactly to total."""
	if n == 1:
		return [round(total, 2)]
	cuts = sorted(rng.uniform(0, total) for _ in range(n - 1))
	parts = []
	prev = 0.0
	for c in cuts:
		parts.append(round(c - prev, 2))
		prev = c
	parts.append(round(total - prev, 2))
	# Adjust for rounding drift.
	drift = round(total - sum(parts), 2)
	parts[-1] = round(parts[-1] + drift, 2)
	# Eliminate any zeros from rounding.
	parts = [p if p > 0 else 0.01 for p in parts]
	return parts


def _build_row(voucher_no, posting, company, fy, cc, acc, debit, credit, now, user):
	currency = acc.account_currency or "INR"
	return {
		"name": f"{voucher_no}-{random.randint(100000, 999999)}-{int(time.time() * 1000) % 1_000_000}",
		"creation": now,
		"modified": now,
		"modified_by": user,
		"owner": user,
		"docstatus": 1,
		"idx": 0,
		"posting_date": posting,
		"transaction_date": posting,
		"account": acc.name,
		"party_type": None,
		"party": None,
		"cost_center": cc,
		"debit": debit,
		"credit": credit,
		"account_currency": currency,
		"debit_in_account_currency": debit,
		"credit_in_account_currency": credit,
		"against": "",
		"against_voucher_type": None,
		"against_voucher": None,
		"voucher_type": VOUCHER_TYPE,
		"voucher_no": voucher_no,
		"project": None,
		"remarks": "Seeded by dux_groupview seed_light",
		"is_opening": "No",
		"is_advance": "No",
		"fiscal_year": fy,
		"company": company,
		"finance_book": None,
		"due_date": None,
		"transaction_currency": currency,
		"debit_in_transaction_currency": debit,
		"credit_in_transaction_currency": credit,
		"transaction_exchange_rate": 1,
		"is_cancelled": 0,
		"voucher_subtype": None,
		"to_rename": 0,
	}
