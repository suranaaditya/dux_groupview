"""Augment dev seed with realistic AP/AR transaction data.

Surfaced during Phase 4 commit 3 review: the existing trust-subset
seed has only 8 unique parties total across all payable accounts, all
with near-zero balances. Insufficient for visual verification of
commits 4-7. This module creates 50 suppliers + 30 customers,
distributes them across the 13 trust-subset companies with
Pareto-shaped balance distributions, and generates ~2,000 GL entries
spread across the past 12 months.

Usage:

    bench --site <site> execute \\
      dux_groupview.dux_groupview.test_data.seed_ap_ar_generator.seed_ap_ar
    bench --site <site> execute \\
      dux_groupview.dux_groupview.test_data.seed_ap_ar_generator.seed_ap_ar \\
      --kwargs '{"dry_run": true}'

Idempotency: voucher names start with `AP-AR-SEED-`. The seed refuses
to run a second time if any rows with that prefix already exist; run
teardown_ap_ar first. Suppliers / Customers are created only if a
matching name doesn't already exist.

Architecture:
- party doc creation goes through Frappe ORM (~80 docs total, OK to
  pay validation cost; need hooks for supplier_group / customer_group)
- GL entries go through frappe.db.bulk_insert (matches the pattern in
  seed_production.py:_generate_gl_entries — 5K-row chunks)
- the GST suppression workaround (Phase 0 Q4) is reused via
  `_suppress_gst_settings_revalidation` so Supplier creation doesn't
  trip on dev's pre-existing GST Settings inconsistencies
"""

import random
import time
import uuid
from datetime import date, timedelta

import frappe
from frappe.utils import getdate, nowdate

from dux_groupview.dux_groupview.test_data.seed_ap_ar import (
	BALANCE_TIERS,
	CUSTOMER_COMPANY_COUNT_WEIGHTS,
	CUSTOMER_TEMPLATES,
	SUPPLIER_COMPANY_COUNT_WEIGHTS,
	SUPPLIER_TEMPLATES,
	TRANSACTION_VOLUMES,
)
from dux_groupview.dux_groupview.test_data.seed_light import (
	_suppress_gst_settings_revalidation,
)


VOUCHER_PREFIX = "AP-AR-SEED-"
VOUCHER_TYPE = "DGV AP-AR Seed"
SEED_RNG = 20260506   # deterministic seed; same number lands same data shape

DEFAULT_SUPPLIER_GROUP = "All Supplier Groups"
DEFAULT_CUSTOMER_GROUP = "All Customer Groups"
DEFAULT_TERRITORY = "All Territories"
DEFAULT_TRUST_SUBSET = ("ghremf", "cbs", "sgr")  # matches PHASE_LOG side PR #10


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def seed_ap_ar(companies=None, dry_run=False):
	"""Augment the existing dev seed with realistic AP/AR data.

	Args:
		companies: optional list of company names. When None, uses the
			ghremf+cbs+sgr trust subset (13 companies on dev) — same
			default as side PR #10's trust-subset seed.
		dry_run: when True, prints the affiliation plan + counts without
			writing to the DB. Use this first to sanity-check the shape
			before committing data.

	Returns:
		dict with creation/skip counts and total computed AP / AR
		balance. Useful for tests + the verification step.
	"""
	t_start = time.time()
	companies = list(companies or _default_companies())
	if not companies:
		print("ABORT: no companies resolved (trust subset not seeded?).")
		return {"status": "aborted", "reason": "no companies"}

	# Idempotency guard. seed_ap_ar refuses to run on top of an
	# existing AP-AR-SEED- GL footprint. Run teardown_ap_ar first.
	if not dry_run:
		existing = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no LIKE %s",
			(f"{VOUCHER_PREFIX}%",),
		)[0][0]
		if existing > 0:
			print(
				f"ABORT: {existing} GL entries with prefix {VOUCHER_PREFIX!r} "
				"already exist. Run teardown_ap_ar first."
			)
			return {"status": "aborted", "reason": "already seeded",
			        "existing_gl_entries": existing}

	rng = random.Random(SEED_RNG)
	plan = _build_plan(companies, rng)
	_print_plan_summary(plan, companies, dry_run=dry_run)

	if dry_run:
		# Compute totals without touching the DB.
		ap_total = sum(a["balance"] for a in plan["supplier_affiliations"])
		ar_total = sum(a["balance"] for a in plan["customer_affiliations"])
		return {
			"status": "dry_run",
			"target_companies": companies,
			"suppliers_planned": len(SUPPLIER_TEMPLATES),
			"customers_planned": len(CUSTOMER_TEMPLATES),
			"supplier_affiliations": len(plan["supplier_affiliations"]),
			"customer_affiliations": len(plan["customer_affiliations"]),
			"gl_entries_planned":
				sum(a["num_tx"] * 2 for a in plan["supplier_affiliations"])
				+ sum(a["num_tx"] * 2 for a in plan["customer_affiliations"]),
			"total_ap_balance": round(ap_total, 2),
			"total_ar_balance": round(ar_total, 2),
		}

	# --- Real run ---
	with _suppress_gst_settings_revalidation():
		sup_created, sup_skipped = _ensure_parties(
			"Supplier", [s["name"] for s in SUPPLIER_TEMPLATES],
			default_group=DEFAULT_SUPPLIER_GROUP,
		)
		cust_created, cust_skipped = _ensure_parties(
			"Customer", [c["name"] for c in CUSTOMER_TEMPLATES],
			default_group=DEFAULT_CUSTOMER_GROUP,
			default_territory=DEFAULT_TERRITORY,
		)

	gl_count = _insert_gl_entries(plan, rng)
	frappe.db.commit()

	dur = time.time() - t_start
	print(
		f"\nseed_ap_ar complete in {dur:.1f}s. "
		f"Suppliers: {sup_created} created / {sup_skipped} skipped. "
		f"Customers: {cust_created} created / {cust_skipped} skipped. "
		f"GL entries: {gl_count:,}."
	)
	return {
		"status": "complete",
		"duration_seconds": round(dur, 1),
		"suppliers_created": sup_created,
		"suppliers_skipped": sup_skipped,
		"customers_created": cust_created,
		"customers_skipped": cust_skipped,
		"gl_entries_created": gl_count,
		"total_ap_balance": round(
			sum(a["balance"] for a in plan["supplier_affiliations"]), 2),
		"total_ar_balance": round(
			sum(a["balance"] for a in plan["customer_affiliations"]), 2),
	}


# ---------------------------------------------------------------------------
# Plan building (pure logic, called from dry-run + real run)
# ---------------------------------------------------------------------------

def build_plan_for_test(companies, seed=SEED_RNG):
	"""Test entry point. Returns the same plan dict the generator uses."""
	return _build_plan(companies, random.Random(seed))


def _build_plan(companies, rng):
	"""Compute the supplier+customer-to-company affiliation list.

	Each affiliation is a dict:
	    {
	      "party_idx":  int   index into SUPPLIER_TEMPLATES / CUSTOMER_TEMPLATES
	      "party":      str   supplier/customer name
	      "tier":       str   tier id (sup_outlier/sup_top/.../cust_top/...)
	      "company":    str   tabCompany name
	      "balance":    float natural-side rupee amount (always positive)
	      "num_tx":     int   number of transactions to generate for this pair
	    }
	"""
	supplier_affs = _affiliations_for(
		SUPPLIER_TEMPLATES, companies, SUPPLIER_COMPANY_COUNT_WEIGHTS, rng,
	)
	customer_affs = _affiliations_for(
		CUSTOMER_TEMPLATES, companies, CUSTOMER_COMPANY_COUNT_WEIGHTS, rng,
	)
	# Spec calls out: every company should host >=5 suppliers and >=3
	# customers. The weighted random generally hits this on 13 companies,
	# but pad if a company came up short.
	supplier_affs = _ensure_min_per_company(
		supplier_affs, SUPPLIER_TEMPLATES, companies, min_count=5, rng=rng,
	)
	customer_affs = _ensure_min_per_company(
		customer_affs, CUSTOMER_TEMPLATES, companies, min_count=3, rng=rng,
	)
	return {
		"supplier_affiliations": supplier_affs,
		"customer_affiliations": customer_affs,
	}


def _affiliations_for(templates, companies, weights, rng):
	"""For each template, pick N companies, divide tier balance, assign tx."""
	choices, weight_values = zip(*weights)
	out = []
	for idx, party in enumerate(templates):
		n_cos = rng.choices(choices, weights=weight_values, k=1)[0]
		n_cos = min(n_cos, len(companies))
		chosen = rng.sample(companies, n_cos)
		# Total balance for this party in its tier range, then split.
		b_lo, b_hi = BALANCE_TIERS[party["tier"]]
		t_lo, t_hi = TRANSACTION_VOLUMES[party["tier"]]
		for company in chosen:
			out.append({
				"party_idx": idx,
				"party": party["name"],
				"tier": party["tier"],
				"company": company,
				# Per-company balance lives in the tier range as well so a
				# party with 3 affiliations doesn't end up with 3 tiny rows.
				# Net effect: a top-tier party serving 3 companies has a
				# total ~3x the tier midpoint, which is the "outlier"
				# ceiling. Acceptable variance for dev seed.
				"balance": round(rng.uniform(b_lo, b_hi), 2),
				"num_tx":  rng.randint(t_lo, t_hi),
			})
	return out


def _ensure_min_per_company(affs, templates, companies, min_count, rng):
	"""Pad affiliations so each company has >= min_count rows.

	Picks bottom-tier templates (least disruptive to the Pareto shape)
	and adds a per-company affiliation for them. Idempotent within the
	plan -- never adds a (party, company) pair that already exists.
	"""
	per_co = {c: 0 for c in companies}
	for a in affs:
		per_co[a["company"]] += 1
	# Sort templates by tier weight: bottom first (least balance impact).
	tier_priority = {"sup_bottom": 0, "cust_bottom": 0,
	                 "sup_mid": 1, "cust_mid": 1,
	                 "sup_top": 2, "cust_top": 2,
	                 "sup_outlier": 3}
	template_order = sorted(
		range(len(templates)),
		key=lambda i: tier_priority.get(templates[i]["tier"], 9),
	)
	existing_pairs = {(a["party_idx"], a["company"]) for a in affs}
	for company, count in per_co.items():
		while count < min_count:
			# Find a template not already affiliated with this company.
			added = False
			for idx in template_order:
				if (idx, company) not in existing_pairs:
					party = templates[idx]
					b_lo, b_hi = BALANCE_TIERS[party["tier"]]
					t_lo, t_hi = TRANSACTION_VOLUMES[party["tier"]]
					affs.append({
						"party_idx": idx,
						"party": party["name"],
						"tier": party["tier"],
						"company": company,
						"balance": round(rng.uniform(b_lo, b_hi), 2),
						"num_tx":  rng.randint(t_lo, t_hi),
					})
					existing_pairs.add((idx, company))
					count += 1
					added = True
					break
			if not added:
				break  # exhausted templates; can't fill further
	return affs


# ---------------------------------------------------------------------------
# Dry-run summary
# ---------------------------------------------------------------------------

def _print_plan_summary(plan, companies, dry_run):
	header = "DRY-RUN PLAN" if dry_run else "EXECUTION PLAN"
	print(f"\n{'=' * 72}\n{header}\n{'=' * 72}")
	print(f"Companies in scope: {len(companies)}")
	for c in companies:
		print(f"  - {c}")

	sup = plan["supplier_affiliations"]
	cust = plan["customer_affiliations"]
	print(f"\nSupplier affiliations: {len(sup)} "
	      f"(across {len({a['company'] for a in sup})} companies, "
	      f"{len({a['party'] for a in sup})} unique suppliers)")
	print(f"Customer affiliations: {len(cust)} "
	      f"(across {len({a['company'] for a in cust})} companies, "
	      f"{len({a['party'] for a in cust})} unique customers)")

	tier_breakdown_sup = {}
	for a in sup:
		tier_breakdown_sup[a["tier"]] = tier_breakdown_sup.get(a["tier"], 0) + 1
	tier_breakdown_cust = {}
	for a in cust:
		tier_breakdown_cust[a["tier"]] = tier_breakdown_cust.get(a["tier"], 0) + 1
	print(f"\nSupplier tier breakdown:")
	for t in ["sup_outlier", "sup_top", "sup_mid", "sup_bottom"]:
		print(f"  {t:13s}: {tier_breakdown_sup.get(t, 0):4d} affiliations")
	print(f"Customer tier breakdown:")
	for t in ["cust_top", "cust_mid", "cust_bottom"]:
		print(f"  {t:13s}: {tier_breakdown_cust.get(t, 0):4d} affiliations")

	# Per-company counts.
	co_sup = {c: 0 for c in companies}
	co_cust = {c: 0 for c in companies}
	for a in sup:
		co_sup[a["company"]] += 1
	for a in cust:
		co_cust[a["company"]] += 1
	print(f"\nPer-company counts (supplier / customer):")
	for c in companies:
		print(f"  {co_sup[c]:3d} / {co_cust[c]:3d}  {c}")

	ap_total = sum(a["balance"] for a in sup)
	ar_total = sum(a["balance"] for a in cust)
	gl_total = sum(a["num_tx"] * 2 for a in sup) \
	         + sum(a["num_tx"] * 2 for a in cust)
	print(f"\nTotal target AP balance:  ₹{ap_total / 10000000:7.2f} Cr")
	print(f"Total target AR balance:  ₹{ar_total / 10000000:7.2f} Cr")
	print(f"Total GL entries planned: {gl_total:,}")

	# Pareto sanity check (informational).
	sup_by_party = {}
	for a in sup:
		sup_by_party[a["party"]] = sup_by_party.get(a["party"], 0) + a["balance"]
	by_balance = sorted(sup_by_party.values(), reverse=True)
	if len(by_balance) >= 10:
		top10 = sum(by_balance[:10])
		total = sum(by_balance)
		print(f"Supplier Pareto: top 10 = ₹{top10 / 10000000:.2f} Cr "
		      f"({100 * top10 / total:.0f}% of total)")
	print()


# ---------------------------------------------------------------------------
# Party doc creation (Frappe ORM)
# ---------------------------------------------------------------------------

def _ensure_parties(doctype, names, default_group, default_territory=None):
	"""Create Supplier or Customer docs if not already present.

	Skips existing docs by name match. Returns (created_count,
	skipped_count). Wraps with the GST suppression workaround at the
	caller level — don't double-wrap here.
	"""
	created = 0
	skipped = 0
	for name in names:
		if frappe.db.exists(doctype, name):
			skipped += 1
			continue
		doc = frappe.new_doc(doctype)
		if doctype == "Supplier":
			doc.supplier_name = name
			doc.supplier_group = default_group
		else:
			doc.customer_name = name
			doc.customer_group = default_group
			if default_territory:
				doc.territory = default_territory
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		try:
			doc.insert()
			created += 1
		except Exception as e:
			print(f"WARN: could not create {doctype} {name!r}: {e}")
			skipped += 1
	return created, skipped


# ---------------------------------------------------------------------------
# GL entry generation (raw SQL bulk insert)
# ---------------------------------------------------------------------------

def _insert_gl_entries(plan, rng):
	"""Insert all GL entries for the plan in 5K-row chunks.

	Each affiliation generates `num_tx` vouchers, each with 2 legs:
	one party-stamped leg (Sundry Creditors / Debtors) and one
	counter leg (Expense / Income / Bank). Mix is 70% bills+invoices
	(grow balance), 30% payments+receipts (shrink balance), netting
	to the affiliation's target balance.
	"""
	# Per-company account caches.
	creditors_by_co  = {}
	debtors_by_co    = {}
	expense_by_co    = {}
	income_by_co     = {}
	bank_by_co       = {}
	cc_by_co         = {}
	for a in plan["supplier_affiliations"] + plan["customer_affiliations"]:
		co = a["company"]
		if co in creditors_by_co:
			continue
		creditors_by_co[co] = _resolve_account_by_type(co, "Payable")
		debtors_by_co[co]   = _resolve_account_by_type(co, "Receivable")
		expense_by_co[co]   = _resolve_account_by_root_type(co, "Expense")
		income_by_co[co]    = _resolve_account_by_root_type(co, "Income")
		bank_by_co[co]      = _resolve_bank_or_cash(co)
		cc_by_co[co]        = frappe.db.get_value(
			"Cost Center", {"company": co, "is_group": 0}, "name",
		)

	today_d = getdate(nowdate())
	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"
	fy_cache = {}

	def fy_for(d):
		key = d.year
		if key not in fy_cache:
			fy_cache[key] = frappe.db.get_value(
				"Fiscal Year",
				{"year_start_date": ["<=", d], "year_end_date": [">=", d]},
				"name",
			)
		return fy_cache[key]

	rows = []
	v_counter = 0
	skipped_pairs = 0

	def emit_party_voucher(co, party, party_type, party_account, counter_account,
	                       posting, amount, party_is_debit, fy, cc):
		"""Emit a 2-row voucher. party_is_debit decides which leg is Dr."""
		nonlocal v_counter
		v_counter += 1
		voucher_no = f"{VOUCHER_PREFIX}{v_counter:08d}"
		party_dr = amount if party_is_debit else 0
		party_cr = 0 if party_is_debit else amount
		ctr_dr = 0 if party_is_debit else amount
		ctr_cr = amount if party_is_debit else 0
		rows.append(_build_row(voucher_no, posting, co, fy, cc,
		                       party_account, party_dr, party_cr,
		                       party=party, party_type=party_type,
		                       now=now, user=user))
		rows.append(_build_row(voucher_no, posting, co, fy, cc,
		                       counter_account, ctr_dr, ctr_cr,
		                       party=None, party_type=None,
		                       now=now, user=user))

	# --- Supplier (AP) ---
	for a in plan["supplier_affiliations"]:
		co = a["company"]
		creditors = creditors_by_co.get(co)
		expense   = expense_by_co.get(co)
		bank      = bank_by_co.get(co)
		cc        = cc_by_co.get(co)
		if not (creditors and expense and bank):
			skipped_pairs += 1
			continue
		_emit_party_run(
			a, party_type="Supplier",
			party_account=creditors,
			bill_counter=expense, payment_counter=bank,
			# For AP: bills are "Cr Creditors / Dr Expense" -> party_is_debit=False
			#         payments are "Dr Creditors / Cr Bank"  -> party_is_debit=True
			bill_party_is_debit=False, pay_party_is_debit=True,
			today_d=today_d, fy_for=fy_for, cc=cc,
			rng=rng, emit=emit_party_voucher, co=co,
		)

	# --- Customer (AR) ---
	for a in plan["customer_affiliations"]:
		co = a["company"]
		debtors = debtors_by_co.get(co)
		income  = income_by_co.get(co)
		bank    = bank_by_co.get(co)
		cc      = cc_by_co.get(co)
		if not (debtors and income and bank):
			skipped_pairs += 1
			continue
		_emit_party_run(
			a, party_type="Customer",
			party_account=debtors,
			bill_counter=income, payment_counter=bank,
			# For AR: invoices are "Dr Debtors / Cr Income" -> party_is_debit=True
			#         receipts are "Cr Debtors / Dr Bank"   -> party_is_debit=False
			bill_party_is_debit=True, pay_party_is_debit=False,
			today_d=today_d, fy_for=fy_for, cc=cc,
			rng=rng, emit=emit_party_voucher, co=co,
		)

	if skipped_pairs:
		print(f"NOTE: {skipped_pairs} party-company pairs skipped "
		      "(missing Sundry Creditors/Debtors or counter accounts).")

	if not rows:
		return 0

	# Bulk insert in 5K-row chunks (matches seed_production pattern).
	fields = list(rows[0].keys())
	values = [tuple(r[f] for f in fields) for r in rows]
	chunk = 5000
	for i in range(0, len(values), chunk):
		batch = values[i : i + chunk]
		frappe.db.bulk_insert("GL Entry", fields=fields, values=batch)

	return len(rows)


def _emit_party_run(aff, party_type, party_account, bill_counter, payment_counter,
                    bill_party_is_debit, pay_party_is_debit, today_d, fy_for, cc,
                    rng, emit, co):
	"""Generate `num_tx` vouchers for one party-company pair, netting
	to the affiliation's target balance."""
	target = aff["balance"]
	n = max(aff["num_tx"], 2)  # need at least 1 bill + 1 payment
	n_pay = max(1, int(round(n * 0.30)))
	n_bill = n - n_pay

	# Choose payment total such that bill - pay = target. Bill total
	# ranges from target (no payments) to ~1.5x target (heavy payments).
	# Pick a moderate ratio so individual amounts stay realistic.
	pay_ratio = rng.uniform(0.20, 0.45)
	pay_total = round(target * pay_ratio, 2)
	bill_total = round(target + pay_total, 2)

	bill_amts = _split_amount(bill_total, n_bill, rng)
	pay_amts  = _split_amount(pay_total,  n_pay,  rng)

	# Posting dates: weighted toward last 3 months.
	def random_posting():
		bucket = rng.choices(
			["recent", "mid", "old"],
			weights=[50, 30, 20], k=1,
		)[0]
		if bucket == "recent":
			days = rng.randint(0, 90)
		elif bucket == "mid":
			days = rng.randint(91, 180)
		else:
			days = rng.randint(181, 365)
		return today_d - timedelta(days=days)

	for amt in bill_amts:
		if amt <= 0:
			continue
		posting = random_posting()
		emit(co, aff["party"], party_type, party_account,
		     bill_counter, posting, amt, bill_party_is_debit,
		     fy_for(posting), cc)
	for amt in pay_amts:
		if amt <= 0:
			continue
		posting = random_posting()
		emit(co, aff["party"], party_type, party_account,
		     payment_counter, posting, amt, pay_party_is_debit,
		     fy_for(posting), cc)


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------

def _resolve_account_by_type(company, account_type):
	"""First leaf account in this company with given account_type."""
	row = frappe.db.sql(
		"""
		SELECT name FROM `tabAccount`
		WHERE company = %s AND is_group = 0 AND disabled = 0
		  AND account_type = %s
		ORDER BY lft LIMIT 1
		""",
		(company, account_type),
	)
	return row[0][0] if row else None


def _resolve_account_by_root_type(company, root_type):
	"""First leaf account in this company with given root_type."""
	row = frappe.db.sql(
		"""
		SELECT name FROM `tabAccount`
		WHERE company = %s AND is_group = 0 AND disabled = 0
		  AND root_type = %s
		ORDER BY lft LIMIT 1
		""",
		(company, root_type),
	)
	return row[0][0] if row else None


def _resolve_bank_or_cash(company):
	"""First Bank-typed leaf, falling back to Cash, then any Asset."""
	for atype in ("Bank", "Cash"):
		acc = _resolve_account_by_type(company, atype)
		if acc:
			return acc
	return _resolve_account_by_root_type(company, "Asset")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_amount(total, n, rng):
	"""Split `total` into `n` random positive parts that sum to total."""
	if n <= 0 or total <= 0:
		return []
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


def _build_row(voucher_no, posting, company, fy, cc, account,
               debit, credit, party, party_type, now, user):
	"""Build a tabGL Entry row dict matching the schema used by
	seed_production._build_row. Uses uuid suffix so the GL Entry name
	is unique even though voucher_no is shared by both legs."""
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
		"account": account,
		"party_type": party_type,
		"party": party,
		"cost_center": cc,
		"debit": debit,
		"credit": credit,
		"account_currency": "INR",
		"debit_in_account_currency": debit,
		"credit_in_account_currency": credit,
		"against": "",
		"against_voucher_type": None,
		"against_voucher": None,
		"voucher_type": VOUCHER_TYPE,
		"voucher_subtype": "",
		"voucher_no": voucher_no,
		"voucher_detail_no": "",
		"project": None,
		"remarks": "",
		"is_opening": "No",
		"is_advance": "No",
		"fiscal_year": fy,
		"company": company,
		"finance_book": "",
		"to_rename": 0,
		"due_date": None,
		"is_cancelled": 0,
		"transaction_currency": "INR",
		"debit_in_transaction_currency": debit,
		"credit_in_transaction_currency": credit,
		"transaction_exchange_rate": 1,
	}


def _default_companies():
	"""The 13-company trust subset (ghremf+cbs+sgr) used as default seed.

	Mirrors PHASE_LOG side PR #10 ('seed-scale-for-KVM') choice. Returns
	only companies that actually exist in tabCompany so a fresh dev box
	without the trust-subset seed silently degrades to whatever IS there
	(or empty list -> aborts).
	"""
	from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS
	wanted_ids = set(DEFAULT_TRUST_SUBSET)
	declared = []
	for trust in TRUSTS:
		if trust["id"].lower() in wanted_ids:
			declared.extend(trust["companies"])
	# Filter to companies that actually exist.
	if not declared:
		return []
	placeholders = ", ".join(["%s"] * len(declared))
	rows = frappe.db.sql_list(
		f"SELECT name FROM `tabCompany` WHERE name IN ({placeholders})",
		tuple(declared),
	)
	return rows
