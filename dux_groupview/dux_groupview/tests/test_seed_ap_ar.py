"""Pure-logic tests for the AP/AR seed augmentation.

No DB. These check the data-shape properties (Pareto distribution,
per-company minimums, idempotent plan building, tier counts) using
just the templates + the plan builder. The only DB-touching path is
the actual seed run on dev, verified manually in the cockpit.
"""

import unittest

from dux_groupview.dux_groupview.test_data.seed_ap_ar import (
	BALANCE_TIERS,
	CUSTOMER_TEMPLATES,
	SUPPLIER_TEMPLATES,
	TRANSACTION_VOLUMES,
)
from dux_groupview.dux_groupview.test_data.seed_ap_ar_generator import (
	build_plan_for_test,
	SEED_RNG,
)


# Same 13-company list the trust-subset side PR uses, hardcoded here
# so the tests don't depend on the live tabCompany state.
DEV_COMPANIES_FIXTURE = [
	# ghremf — 8
	"GH Raisoni College Of Engineering And Management Pune",
	"GHRCEM Pune — MBA",
	"GHRCEM Pune — MCA",
	"GHR CACS Pune",
	"GH Raisoni Public School Pune",
	"GH Raisoni Junior College Pune",
	"GHREMF Society Pune",
	"GHREMF Society Nagpur",
	# cbs — 3 (placeholder names; real ones come from trust_groups.py)
	"CBS Society",
	"CBS Junior College",
	"CBS Public School",
	# sgr — 2
	"SGR Foundation",
	"SGR Education Foundation",
]


class TestTemplateCounts(unittest.TestCase):
	"""Sanity checks on the template lists themselves."""

	def test_supplier_count(self):
		self.assertEqual(len(SUPPLIER_TEMPLATES), 50,
		                 "Spec calls for 50 suppliers; "
		                 f"got {len(SUPPLIER_TEMPLATES)}")

	def test_customer_count(self):
		self.assertEqual(len(CUSTOMER_TEMPLATES), 30,
		                 "Spec calls for 30 customers; "
		                 f"got {len(CUSTOMER_TEMPLATES)}")

	def test_supplier_tier_distribution(self):
		"""4 outliers + 6 top + 20 mid + 20 bottom = 50."""
		counts = {}
		for s in SUPPLIER_TEMPLATES:
			counts[s["tier"]] = counts.get(s["tier"], 0) + 1
		self.assertEqual(counts.get("sup_outlier"),  4)
		self.assertEqual(counts.get("sup_top"),      6)
		self.assertEqual(counts.get("sup_mid"),     20)
		self.assertEqual(counts.get("sup_bottom"),  20)

	def test_customer_tier_distribution(self):
		"""5 top + 15 mid + 10 bottom = 30."""
		counts = {}
		for c in CUSTOMER_TEMPLATES:
			counts[c["tier"]] = counts.get(c["tier"], 0) + 1
		self.assertEqual(counts.get("cust_top"),     5)
		self.assertEqual(counts.get("cust_mid"),    15)
		self.assertEqual(counts.get("cust_bottom"), 10)

	def test_unique_supplier_names(self):
		names = [s["name"] for s in SUPPLIER_TEMPLATES]
		self.assertEqual(len(names), len(set(names)),
		                 "Duplicate supplier names")

	def test_unique_customer_names(self):
		names = [c["name"] for c in CUSTOMER_TEMPLATES]
		self.assertEqual(len(names), len(set(names)),
		                 "Duplicate customer names")

	def test_tiers_have_balance_ranges(self):
		"""Every tier referenced in templates has a BALANCE_TIERS entry."""
		ref = set(s["tier"] for s in SUPPLIER_TEMPLATES) \
		    | set(c["tier"] for c in CUSTOMER_TEMPLATES)
		for tier in ref:
			self.assertIn(tier, BALANCE_TIERS,
			              f"Tier {tier!r} missing from BALANCE_TIERS")
			self.assertIn(tier, TRANSACTION_VOLUMES,
			              f"Tier {tier!r} missing from TRANSACTION_VOLUMES")
			lo, hi = BALANCE_TIERS[tier]
			self.assertLess(lo, hi, f"Tier {tier!r} has lo >= hi")


class TestPlanShape(unittest.TestCase):
	"""Distribution-shape assertions on the built plan."""

	def setUp(self):
		self.plan = build_plan_for_test(DEV_COMPANIES_FIXTURE)

	def test_supplier_pareto_top_10(self):
		"""Top 10 suppliers (by per-party total balance) account for
		>=50% of total AP value (Pareto check, ±10% margin)."""
		by_party = {}
		for a in self.plan["supplier_affiliations"]:
			by_party[a["party"]] = by_party.get(a["party"], 0) + a["balance"]
		ranked = sorted(by_party.values(), reverse=True)
		top10 = sum(ranked[:10])
		total = sum(ranked)
		ratio = top10 / total
		self.assertGreater(ratio, 0.50,
		                   f"Top-10 share {ratio:.2%} < 50% (Pareto floor)")
		# Upper sanity bound: shouldn't be >85% — the mid+bottom tiers
		# should still contribute meaningfully.
		self.assertLess(ratio, 0.85,
		                f"Top-10 share {ratio:.2%} > 85% (Pareto ceiling)")

	def test_customer_pareto_top_5(self):
		"""Top 5 customers account for >=40% of total AR value."""
		by_party = {}
		for a in self.plan["customer_affiliations"]:
			by_party[a["party"]] = by_party.get(a["party"], 0) + a["balance"]
		ranked = sorted(by_party.values(), reverse=True)
		top5 = sum(ranked[:5])
		total = sum(ranked)
		ratio = top5 / total
		self.assertGreater(ratio, 0.40,
		                   f"Top-5 customer share {ratio:.2%} < 40%")

	def test_per_company_supplier_minimum(self):
		"""Every company has >=5 suppliers."""
		per_co = {c: 0 for c in DEV_COMPANIES_FIXTURE}
		for a in self.plan["supplier_affiliations"]:
			per_co[a["company"]] += 1
		for c, n in per_co.items():
			self.assertGreaterEqual(n, 5, f"{c} has only {n} suppliers")

	def test_per_company_customer_minimum(self):
		"""Every company has >=3 customers."""
		per_co = {c: 0 for c in DEV_COMPANIES_FIXTURE}
		for a in self.plan["customer_affiliations"]:
			per_co[a["company"]] += 1
		for c, n in per_co.items():
			self.assertGreaterEqual(n, 3, f"{c} has only {n} customers")

	def test_no_duplicate_party_company_pairs(self):
		"""(party, company) tuples are unique across the affiliation list."""
		sup_pairs = [(a["party"], a["company"])
		             for a in self.plan["supplier_affiliations"]]
		self.assertEqual(len(sup_pairs), len(set(sup_pairs)),
		                 "Duplicate (supplier, company) affiliations")
		cust_pairs = [(a["party"], a["company"])
		              for a in self.plan["customer_affiliations"]]
		self.assertEqual(len(cust_pairs), len(set(cust_pairs)),
		                 "Duplicate (customer, company) affiliations")

	def test_tx_count_in_tier_range(self):
		"""Every affiliation's num_tx falls within its tier's
		TRANSACTION_VOLUMES range."""
		for a in (self.plan["supplier_affiliations"]
		          + self.plan["customer_affiliations"]):
			lo, hi = TRANSACTION_VOLUMES[a["tier"]]
			self.assertGreaterEqual(a["num_tx"], lo,
			                        f"{a['party']} num_tx {a['num_tx']} < {lo}")
			self.assertLessEqual(a["num_tx"], hi,
			                     f"{a['party']} num_tx {a['num_tx']} > {hi}")

	def test_balance_in_tier_range(self):
		"""Every affiliation's balance falls within its tier range."""
		for a in (self.plan["supplier_affiliations"]
		          + self.plan["customer_affiliations"]):
			lo, hi = BALANCE_TIERS[a["tier"]]
			self.assertGreaterEqual(a["balance"], lo,
			                        f"{a['party']} balance {a['balance']} < {lo}")
			self.assertLessEqual(a["balance"], hi,
			                     f"{a['party']} balance {a['balance']} > {hi}")


class TestPlanDeterminism(unittest.TestCase):
	"""Same RNG seed produces the same plan across runs."""

	def test_same_seed_same_plan(self):
		p1 = build_plan_for_test(DEV_COMPANIES_FIXTURE, seed=SEED_RNG)
		p2 = build_plan_for_test(DEV_COMPANIES_FIXTURE, seed=SEED_RNG)
		# Sort by (party_idx, company) for stable comparison.
		def key(a): return (a["party_idx"], a["company"])
		sup1 = sorted(p1["supplier_affiliations"], key=key)
		sup2 = sorted(p2["supplier_affiliations"], key=key)
		self.assertEqual(len(sup1), len(sup2))
		for a, b in zip(sup1, sup2):
			self.assertEqual(a, b)


class TestPartyAffiliationCounts(unittest.TestCase):
	"""Each template gets affiliated with at least one company."""

	def test_every_supplier_has_affiliation(self):
		plan = build_plan_for_test(DEV_COMPANIES_FIXTURE)
		affiliated = set(a["party_idx"] for a in plan["supplier_affiliations"])
		self.assertEqual(affiliated, set(range(len(SUPPLIER_TEMPLATES))),
		                 "Some suppliers have no affiliations")

	def test_every_customer_has_affiliation(self):
		plan = build_plan_for_test(DEV_COMPANIES_FIXTURE)
		affiliated = set(a["party_idx"] for a in plan["customer_affiliations"])
		self.assertEqual(affiliated, set(range(len(CUSTOMER_TEMPLATES))),
		                 "Some customers have no affiliations")
