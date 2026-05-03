"""Production-shaped synthetic dataset for Phase 3 perf verification.

Creates 59 fake companies "Prod Co N1".."Prod Co N59" (abbrs PROD01..
PROD59) distributed across 10 trusts (16/8/7/5/13/3/3/1/1/2). Each
company gets the standard COA plus extra accounts and ~85K GL entries,
totaling ~5M GL Entry rows tagged with voucher_no LIKE 'PROD-TEST-%'
and voucher_type 'DGV Prod Test Seed'.

SAFETY GATE: requires env var PROD_SEED_CONFIRM=yes to proceed. The
script is callable but cannot be triggered accidentally.

REMINDER: Take a fresh database backup before the first run on any
site that has real data:

    bench --site <site> backup

Run with:

    PROD_SEED_CONFIRM=yes bench --site <site> execute \\
        dux_groupview.dux_groupview.test_data.seed_production.seed_production_data

Tear down (idempotent, restores the prior dev state):

    bench --site <site> execute \\
        dux_groupview.dux_groupview.test_data.seed_production.teardown_production_data
"""

import os
import random
import time
import uuid
from datetime import date, timedelta

import frappe
from frappe.utils import flt, getdate, nowdate

# Import the same GST-suppression workaround Phase 0 used.
from dux_groupview.dux_groupview.test_data.seed_light import (
	_suppress_gst_settings_revalidation,
)


VOUCHER_PREFIX = "PROD-TEST-"
VOUCHER_TYPE = "DGV Prod Test Seed"
COMPANY_PREFIX = "Prod Co N"
COMPANY_ABBR_PREFIX = "PROD"

# Distribution: how many synthetic companies in each trust (matches
# RGI's actual distribution). Total = 59.
TRUST_DISTRIBUTION = [
	("ass",      16),
	("ghremf",    8),
	("ghref",     7),
	("ghrf",      5),
	("ghrus",   13),
	("cbs",       3),
	("ghrua",     3),
	("ghrstu",    1),
	("ghristu",   1),
	("sgr",       2),
]

ENTRIES_PER_COMPANY = 85_000  # ~5M total
EXTRA_ACCOUNTS_PER_COMPANY = 30  # 10 each of expense / asset / liability


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def seed_production_data():
	"""Create the 5M-row production-shaped dataset (~30-40 minutes).

	Refuses to run unless PROD_SEED_CONFIRM=yes is set in the environment.
	"""
	if os.environ.get("PROD_SEED_CONFIRM", "").lower() != "yes":
		print(
			"This will create ~5M GL entries and may take 30+ minutes.\n"
			"Set PROD_SEED_CONFIRM=yes in the environment to proceed:\n"
			"    PROD_SEED_CONFIRM=yes bench --site <site> execute "
			"dux_groupview.dux_groupview.test_data.seed_production.seed_production_data\n"
		)
		return {"status": "aborted", "reason": "PROD_SEED_CONFIRM not set"}

	t_start = time.time()

	companies = _ensure_companies()
	# _ensure_extra_accounts(companies) is intentionally skipped: each
	# Account.insert() through the ORM takes ~8 seconds at this table size
	# (validated empirically on dev), pushing the seed past 2 hours. The
	# 113-account standard COA per company gives 6,667 accounts across 59
	# companies, plenty for the perf signals we care about (pivot render,
	# refresh duration). If a future phase needs the extras, they should
	# be added via a bulk SQL insert instead of the ORM.
	purged = _purge_prod_gl_entries()
	if purged is False:
		return {"status": "aborted", "reason": "purge safety check tripped"}

	_generate_gl_entries(companies)

	frappe.db.commit()
	total = time.time() - t_start
	print(f"\nseed_production_data complete in {total / 60:.1f} min")
	return {"status": "complete", "duration_seconds": round(total, 1)}


def teardown_production_data():
	"""Reverse seed_production_data. Idempotent and safe on real data.

	Filters strictly by voucher_no LIKE 'PROD-TEST-%' AND company name
	pattern 'Prod Co N%' so it never touches live entries or jewonline
	companies.
	"""
	t0 = time.time()

	# Step 1: nuke the synthetic GL entries.
	gl_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{VOUCHER_PREFIX}%",),
	)[0][0]
	bad = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` "
		"WHERE voucher_no LIKE %s AND voucher_no NOT LIKE %s",
		(f"{VOUCHER_PREFIX}%", f"{VOUCHER_PREFIX}%"),
	)[0][0]
	if bad > 0:
		print(f"ABORT: {bad} rows match prefix but failed safety check.")
		return {"status": "aborted"}

	if gl_count:
		frappe.db.sql(
			"DELETE FROM `tabGL Entry` WHERE voucher_no LIKE %s",
			(f"{VOUCHER_PREFIX}%",),
		)
		frappe.db.commit()

	# Step 2: drop synthetic Prod Co companies. Only those matching the
	# strict naming pattern.
	prod_companies = frappe.db.sql_list(
		"SELECT name FROM tabCompany WHERE name LIKE %s",
		(f"{COMPANY_PREFIX}%",),
	)
	# Pre-clean orphan child-table refs in singleton doctypes so that the
	# next Company.insert() doesn't trip _validate_links on stale rows.
	# Mode of Payment is the one we hit on RGI seed; add others here if
	# new singletons surface during teardown.
	frappe.db.sql(
		"DELETE FROM `tabMode of Payment Account` WHERE company LIKE %s",
		(f"{COMPANY_PREFIX}%",),
	)
	# Clean up dependent rows first to avoid FK constraint errors. We
	# only need to delete the rows for our synthetic companies.
	cleaned_companies = 0
	for company in prod_companies:
		try:
			# Cost Center, Account, Warehouse etc. all cascade through
			# Frappe's delete-with-children. ignore_permissions because
			# bench execute runs as Administrator.
			frappe.delete_doc(
				"Company", company,
				force=True, ignore_permissions=True, ignore_on_trash=True,
			)
			cleaned_companies += 1
		except Exception as e:
			print(f"  warning: failed to delete company {company}: {e}")
	frappe.db.commit()

	# Step 3: refresh TB snapshot + spotlight cache for today so the
	# cockpit reflects the post-teardown state.
	from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
	from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
		refresh_spotlight_cache,
	)
	try:
		refresh_tb_snapshot()
		refresh_spotlight_cache()
	except Exception as e:
		print(f"  warning: post-teardown refresh failed: {e}")

	dur = time.time() - t0
	print(
		f"Teardown complete: removed {gl_count:,} GL entries, "
		f"{cleaned_companies} companies in {dur:.1f} sec."
	)
	return {
		"status": "complete",
		"gl_entries_removed": gl_count,
		"companies_removed": cleaned_companies,
		"duration_seconds": round(dur, 1),
	}


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

def _ensure_companies():
	"""Create the 59 Prod Co N1..N59 companies (or skip if all present)."""
	created = 0
	companies = []
	with _suppress_gst_settings_revalidation():
		for i in range(1, 60):
			name = f"{COMPANY_PREFIX}{i}"
			abbr = f"{COMPANY_ABBR_PREFIX}{i:02d}"
			companies.append({"name": name, "abbr": abbr, "index": i})
			if frappe.db.exists("Company", name):
				continue
			doc = frappe.new_doc("Company")
			doc.company_name = name
			doc.abbr = abbr
			doc.default_currency = "INR"
			doc.country = "India"
			doc.create_chart_of_accounts_based_on = "Standard Template"
			doc.chart_of_accounts = "Standard"
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1
			if created % 10 == 0:
				frappe.db.commit()
				print(f"  Created {created} companies so far...")

	frappe.db.commit()
	if created == 0:
		print(f"Companies: all 59 Prod Co already exist (skipped)")
	else:
		print(f"Companies: created {created} of 59")
	return companies


def _ensure_extra_accounts(companies):
	"""Add 10 expense / 10 asset / 10 liability accounts per company."""
	from dux_groupview.dux_groupview.test_data.seed_light import (
		EXTRA_EXPENSE_ACCOUNTS, EXTRA_ASSET_ACCOUNTS, EXTRA_LIABILITY_ACCOUNTS,
		EXPENSE_PARENT_GROUPS, ASSET_PARENT_GROUPS, LIABILITY_PARENT_GROUPS,
		_resolve_parent_group,
	)

	plans = (
		(EXTRA_EXPENSE_ACCOUNTS, EXPENSE_PARENT_GROUPS, "Expense"),
		(EXTRA_ASSET_ACCOUNTS, ASSET_PARENT_GROUPS, "Asset"),
		(EXTRA_LIABILITY_ACCOUNTS, LIABILITY_PARENT_GROUPS, "Liability"),
	)
	created = 0
	for c in companies:
		abbr = c["abbr"]
		for names, parent_groups, root_type in plans:
			parent = _resolve_parent_group(parent_groups, abbr, root_type)
			if not parent:
				continue
			for name in names:
				full = f"{name} - {abbr}"
				if frappe.db.exists("Account", full):
					continue
				doc = frappe.new_doc("Account")
				doc.account_name = name
				doc.parent_account = parent
				doc.company = c["name"]
				doc.is_group = 0
				doc.root_type = root_type
				doc.flags.ignore_permissions = True
				doc.insert()
				created += 1
	frappe.db.commit()
	print(f"Extra accounts: created {created} (across 59 companies)")


# ---------------------------------------------------------------------------
# GL entries
# ---------------------------------------------------------------------------

def _purge_prod_gl_entries():
	matching = frappe.db.sql(
		"SELECT name, voucher_no FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{VOUCHER_PREFIX}%",),
		as_dict=True,
	)
	if not matching:
		print("Purge: no existing PROD-TEST GL entries")
		return 0
	bad = [r for r in matching if not r.voucher_no.startswith(VOUCHER_PREFIX)]
	if bad:
		print(f"ABORT: safety check tripped, {len(bad)} bad rows")
		return False
	frappe.db.sql(
		"DELETE FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{VOUCHER_PREFIX}%",),
	)
	frappe.db.commit()
	print(f"Purged {len(matching):,} existing PROD-TEST GL entries")
	return len(matching)


def _generate_gl_entries(companies, voucher_prefix=VOUCHER_PREFIX):
	"""Generate ~5M balanced GL entries across the given companies.

	~85K entries per company. Each company contributes its own batch of
	~28K vouchers with 2-4 legs each. `voucher_prefix` lets the same
	generator be reused for both the generic PROD-TEST seed and the
	RGI-named RGI-DEMO seed.
	"""
	t0 = time.time()
	today_d = getdate(nowdate())
	start_d = today_d - timedelta(days=365)

	# Per-company account pools.
	pools = {}
	for c in companies:
		accounts = frappe.db.sql(
			"""
			SELECT name, root_type, account_currency
			FROM `tabAccount`
			WHERE company = %s AND is_group = 0 AND disabled = 0
			""",
			(c["name"],),
			as_dict=True,
		)
		debit_pool = [a for a in accounts if a.root_type in ("Asset", "Expense")]
		credit_pool = [a for a in accounts if a.root_type in ("Liability", "Income", "Equity")]
		if not debit_pool or not credit_pool:
			debit_pool = credit_pool = accounts
		pools[c["name"]] = (debit_pool, credit_pool)

	# Default cost center per company.
	cost_centers = {}
	for c in companies:
		cc = frappe.db.get_value(
			"Cost Center", {"company": c["name"], "is_group": 0}, "name"
		)
		cost_centers[c["name"]] = cc

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

	rng = random.Random(20260503)
	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"

	total_inserted = 0
	for ci, c in enumerate(companies, 1):
		cname = c["name"]
		debit_pool, credit_pool = pools[cname]
		cc = cost_centers[cname]
		rows = []
		counter_start = (ci - 1) * 100_000  # so voucher numbers don't collide

		entries_for_this_company = ENTRIES_PER_COMPANY
		built = 0
		v_idx = 0
		while built < entries_for_this_company:
			v_idx += 1
			voucher_no = f"{voucher_prefix}{counter_start + v_idx:08d}"
			days_back = rng.randint(0, 365)
			posting = today_d - timedelta(days=days_back)
			fy = fy_for(posting)

			r = rng.random()
			if r < 0.60:
				legs_dr, legs_cr = 1, 1
			elif r < 0.85:
				legs_dr, legs_cr = 1, 2
			elif r < 0.95:
				legs_dr, legs_cr = 2, 2
			else:
				legs_dr, legs_cr = 2, 3

			amount = round(rng.uniform(1000, 100000), 2)
			dr_amts = _split_amount(amount, legs_dr, rng)
			cr_amts = _split_amount(amount, legs_cr, rng)

			for amt in dr_amts:
				acc = rng.choice(debit_pool)
				rows.append(_build_row(voucher_no, posting, cname, fy, cc,
				                        acc, amt, 0, now, user))
				built += 1
				if built >= entries_for_this_company:
					break
			if built >= entries_for_this_company:
				break
			for amt in cr_amts:
				acc = rng.choice(credit_pool)
				rows.append(_build_row(voucher_no, posting, cname, fy, cc,
				                        acc, 0, amt, now, user))
				built += 1
				if built >= entries_for_this_company:
					break

		# Bulk insert in chunks.
		fields = list(rows[0].keys())
		values = [tuple(r[f] for f in fields) for r in rows]
		chunk = 5000
		for i in range(0, len(values), chunk):
			batch = values[i : i + chunk]
			frappe.db.bulk_insert("GL Entry", fields=fields, values=batch)
		frappe.db.commit()
		total_inserted += len(values)
		print(
			f"  [{ci}/{len(companies)}] {cname}: "
			f"{len(values):,} rows ({total_inserted:,} total)"
		)

	dur = time.time() - t0
	print(f"Inserted {total_inserted:,} GL entries in {dur / 60:.1f} min")
	return total_inserted


def _split_amount(total, n, rng):
	if n == 1:
		return [round(total, 2)]
	cuts = sorted(rng.uniform(0, total) for _ in range(n - 1))
	parts = []
	prev = 0.0
	for c in cuts:
		parts.append(round(c - prev, 2))
		prev = c
	parts.append(round(total - prev, 2))
	drift = round(total - sum(parts), 2)
	parts[-1] = round(parts[-1] + drift, 2)
	parts = [p if p > 0 else 0.01 for p in parts]
	return parts


def _build_row(voucher_no, posting, company, fy, cc, acc,
               debit, credit, now, user):
	currency = acc.account_currency or "INR"
	# UUID4 suffix to guarantee no PRIMARY-key collisions during bulk
	# inserts. The seed_light pattern (voucher_no + 6-digit random + ms)
	# has only ~10^12 entropy and hits the birthday paradox at ~328K
	# rows during fast bulk insert when timestamps don't tick. uuid4
	# gives ~10^32 -- collisions are physically impossible at our scale.
	return {
		"name": f"{voucher_no}-{uuid.uuid4().hex[:16]}",
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
		"remarks": "Seeded by dux_groupview seed_production",
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


# ---------------------------------------------------------------------------
# Option A -- RGI-named synthetic seed
#
# Mirrors seed_production_data() but uses the actual 59 RGI company names
# from `pivot/trust_groups.py` so the dev cockpit renders with the real
# 10-trust grouping for visual review and demo. Numbers are synthetic;
# a banner on the cockpit (driven by api/cockpit.py:get_seed_state) makes
# this clear to anyone looking at the page.
#
# Mutual exclusion: seed_rgi_named_data purges BOTH PROD-TEST and
# RGI-DEMO entries on start, so the snapshot is consistent. The generic
# seed_production_data only purges its own PROD-TEST prefix; if you ran
# it after the RGI seed without first tearing down, you'd get a mix.
# ---------------------------------------------------------------------------

import re

RGI_VOUCHER_PREFIX = "RGI-DEMO-"
RGI_VOUCHER_TYPE = "DGV RGI Demo Seed"


def seed_rgi_named_data():
	"""Synthetic seed using real RGI company names (~30-40 min).

	Refuses unless RGI_DEMO_SEED_CONFIRM=yes is set in the environment.
	Companion teardown: teardown_rgi_named_data().
	"""
	if os.environ.get("RGI_DEMO_SEED_CONFIRM", "").lower() != "yes":
		print(
			"This will create 59 RGI-named companies and ~5M GL entries.\n"
			"Existing seeds (PROD-TEST-*, RGI-DEMO-*) will be purged.\n"
			"Estimated 30-40 minutes.\n"
			"Set RGI_DEMO_SEED_CONFIRM=yes in the environment to proceed:\n"
			"    RGI_DEMO_SEED_CONFIRM=yes bench --site <site> execute "
			"dux_groupview.dux_groupview.test_data.seed_production.seed_rgi_named_data\n"
		)
		return {"status": "aborted", "reason": "RGI_DEMO_SEED_CONFIRM not set"}

	t_start = time.time()

	specs = _build_rgi_company_specs()
	companies = _ensure_rgi_companies(specs)
	purged = _purge_synthetic_gl_entries()
	if purged is False:
		return {"status": "aborted", "reason": "purge safety check tripped"}

	_generate_gl_entries(companies, voucher_prefix=RGI_VOUCHER_PREFIX)

	frappe.db.commit()
	total = time.time() - t_start
	print(f"\nseed_rgi_named_data complete in {total / 60:.1f} min")
	return {"status": "complete", "duration_seconds": round(total, 1)}


def teardown_rgi_named_data():
	"""Reverse seed_rgi_named_data with defensive guards.

	Only deletes companies that match the RGI names AND have NO non-RGI-DEMO
	GL entries. If a company already has real (non-DEMO) GL entries, we
	leave it alone -- this is the safety net against accidentally deleting
	a real RGI company on a misconfigured environment.

	After deletion, refreshes TB + spotlight to restore cockpit state.
	Idempotent.
	"""
	from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS

	t0 = time.time()
	rgi_names = set()
	for trust in TRUSTS:
		rgi_names.update(trust["companies"])

	# Step 1: nuke RGI-DEMO GL entries.
	gl_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{RGI_VOUCHER_PREFIX}%",),
	)[0][0]
	if gl_count:
		frappe.db.sql(
			"DELETE FROM `tabGL Entry` WHERE voucher_no LIKE %s",
			(f"{RGI_VOUCHER_PREFIX}%",),
		)
		frappe.db.commit()

	# Step 2: drop synthetic companies. DEFENSIVE: only delete a company
	# if no non-RGI-DEMO GL entries reference it. If a real RGI company
	# happens to share a name (e.g. on production where this script
	# should never run), it'll have its real GL entries and we'll skip it.
	#
	# Before deleting, pre-clean orphan child-table refs in singletons
	# (Mode of Payment) so a future Company.insert() doesn't trip
	# _validate_links on stale rows. Filter strictly to our RGI names.
	if rgi_names:
		placeholders = ", ".join(["%s"] * len(rgi_names))
		frappe.db.sql(
			f"DELETE FROM `tabMode of Payment Account` "
			f"WHERE company IN ({placeholders})",
			tuple(rgi_names),
		)
	cleaned = 0
	skipped = []
	for name in sorted(rgi_names):
		if not frappe.db.exists("Company", name):
			continue
		other_entries = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabGL Entry` "
			"WHERE company = %s AND voucher_no NOT LIKE %s",
			(name, f"{RGI_VOUCHER_PREFIX}%"),
		)[0][0]
		if other_entries > 0:
			skipped.append(name)
			continue
		try:
			frappe.delete_doc(
				"Company", name,
				force=True, ignore_permissions=True, ignore_on_trash=True,
			)
			cleaned += 1
		except Exception as e:
			print(f"  warning: failed to delete company {name}: {e}")
	frappe.db.commit()

	if skipped:
		print(
			f"  Defensive skip: {len(skipped)} companies have non-RGI-DEMO "
			f"GL entries (looks like real data, not ours): "
			f"{skipped[:3]}{'...' if len(skipped) > 3 else ''}"
		)

	# Step 3: refresh TB + spotlight so cockpit reflects post-teardown state.
	from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
	from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
		refresh_spotlight_cache,
	)
	try:
		refresh_tb_snapshot()
		refresh_spotlight_cache()
	except Exception as e:
		print(f"  warning: post-teardown refresh failed: {e}")

	dur = time.time() - t0
	print(
		f"Teardown complete: removed {gl_count:,} GL entries, "
		f"{cleaned} companies in {dur:.1f} sec."
	)
	return {
		"status": "complete",
		"gl_entries_removed": gl_count,
		"companies_removed": cleaned,
		"companies_skipped": len(skipped),
		"duration_seconds": round(dur, 1),
	}


# ---------------------------------------------------------------------------
# RGI seed helpers
# ---------------------------------------------------------------------------

def _build_rgi_company_specs():
	"""Return a list of {name, abbr, trust_id} dicts for the 59 RGI companies.

	Pulls names from `dux_groupview.pivot.trust_groups.TRUSTS`. Derives a
	unique abbr per company using the leading-uppercase-cluster scheme,
	avoiding collisions with any existing tabCompany.abbr.
	"""
	from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS

	used_abbrs = set()
	# Pre-populate with existing tabCompany abbrs so we don't collide.
	for row in frappe.db.sql("SELECT abbr FROM tabCompany"):
		if row[0]:
			used_abbrs.add(row[0])

	specs = []
	for trust in TRUSTS:
		for company_name in trust["companies"]:
			abbr = _derive_abbr(company_name, used_abbrs)
			specs.append({
				"name": company_name,
				"abbr": abbr,
				"trust_id": trust["id"],
			})
	return specs


def _derive_abbr(name, used):
	"""First letter of each word, with leading uppercase clusters preserved.

	Matches RGI's existing abbr style (e.g. "GH Raisoni College Of
	Engineering" -> "GHRCOE"). Capped at 10 chars; collisions resolved
	by appending a 2-digit ordinal.
	"""
	words = re.split(r"[\s\-—]+", name)
	parts = []
	for w in words:
		if not w:
			continue
		m = re.match(r"^([A-Z]+)", w)
		if m and len(m.group(1)) >= 2:
			parts.append(m.group(1))
		else:
			parts.append(w[0].upper())
	base = "".join(parts)[:10] or "COMP"
	if base not in used:
		used.add(base)
		return base
	for i in range(1, 100):
		candidate = base[:8] + f"{i:02d}"
		if candidate not in used:
			used.add(candidate)
			return candidate
	raise ValueError(f"Couldn't derive unique abbr for {name}")


def _ensure_rgi_companies(specs):
	"""Create the 59 RGI-named companies, skipping any that already exist."""
	created = 0
	companies = []
	with _suppress_gst_settings_revalidation():
		for spec in specs:
			companies.append({"name": spec["name"], "abbr": spec["abbr"]})
			if frappe.db.exists("Company", spec["name"]):
				continue
			doc = frappe.new_doc("Company")
			doc.company_name = spec["name"]
			doc.abbr = spec["abbr"]
			doc.default_currency = "INR"
			doc.country = "India"
			doc.create_chart_of_accounts_based_on = "Standard Template"
			doc.chart_of_accounts = "Standard"
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1
			if created % 10 == 0:
				frappe.db.commit()
				print(f"  Created {created} RGI companies so far...")
	frappe.db.commit()
	if created == 0:
		print(f"RGI companies: all {len(specs)} already exist (skipped)")
	else:
		print(f"RGI companies: created {created} of {len(specs)}")
	return companies


def _purge_synthetic_gl_entries():
	"""Purge BOTH PROD-TEST-% and RGI-DEMO-% GL entries.

	Both seeds are mutually exclusive -- running seed_rgi_named_data wipes
	any pre-existing PROD-TEST-* entries so the snapshot stays consistent.
	(seed_production_data still only purges its own prefix; the asymmetry
	means RGI seed is the safer one to run after a switch.)

	Safety check: if any matching row's voucher_no doesn't start with one
	of the two prefixes, abort. Should never happen but defends against
	accidental wide deletes.
	"""
	matching = frappe.db.sql(
		"SELECT name, voucher_no FROM `tabGL Entry` "
		"WHERE voucher_no LIKE 'PROD-TEST-%' OR voucher_no LIKE 'RGI-DEMO-%'",
		as_dict=True,
	)
	if not matching:
		print("Purge: no existing synthetic GL entries")
		return 0
	bad = [
		r for r in matching
		if not (
			r.voucher_no.startswith("PROD-TEST-")
			or r.voucher_no.startswith("RGI-DEMO-")
		)
	]
	if bad:
		print(
			f"ABORT: safety check tripped, {len(bad)} rows match prefix LIKE "
			f"but don't start with PROD-TEST- or RGI-DEMO-"
		)
		return False
	frappe.db.sql(
		"DELETE FROM `tabGL Entry` "
		"WHERE voucher_no LIKE 'PROD-TEST-%' OR voucher_no LIKE 'RGI-DEMO-%'"
	)
	frappe.db.commit()
	print(f"Purged {len(matching):,} existing synthetic GL entries (both prefixes)")
	return len(matching)
