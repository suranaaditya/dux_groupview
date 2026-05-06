"""Tests for `party_drill_v1` (both endpoints + audit fn).

This file owns the sign-convention parity tests. Per spec §4.2 and
the v3.3 commit-2 brief, sign convention is the most fragile place
in the drill SQL. Two layers of defence:

  - test_flip_root_types_literal -- catches accidental edits to the
    constant tuple itself (e.g. someone reorders or drops "Equity").
  - test_sign_convention_parity_account_drill_vs_party_drill --
    catches semantic divergence even if the literal test was "fixed."
    Exercises both readers (snapshot / GL) against the deterministic
    fixture and asserts they produce the same number for the same
    (account, company) pair.

Reads `tabGL Entry` (party drill is `_drill`-suffixed per Phase 4 §3
amendment), `tabAccount`, `tabCompany`, and the snapshot tables.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_party_drill
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, today
from datetime import timedelta

from dux_groupview.dux_groupview.api import account_drill_v1, party_drill_v1
from dux_groupview.dux_groupview.api.utils import (
	FLIP_ROOT_TYPES,
	PARTY_TRACKABLE_ACCOUNT_TYPES,
)
from dux_groupview.dux_groupview.tests.fixtures.party_drill_fixture import (
	SNAPSHOT_DATE as FIXTURE_AS_OF_DATE,
	setup_fixture,
	teardown_fixture,
)


# ---------------------------------------------------------------------------
# Sign convention -- the load-bearing tests
# ---------------------------------------------------------------------------

class TestSignConventionLiterals(FrappeTestCase):
	"""Catches accidental edits to the FLIP_ROOT_TYPES tuple."""

	def test_flip_root_types_literal(self):
		# This tuple is consumed by spotlight aggregation, account
		# drill, party drill, and (eventually) focus mode tiles.
		# Drift here = silent disagreement between cockpit panels.
		self.assertEqual(
			FLIP_ROOT_TYPES,
			("Liability", "Equity", "Income"),
		)

	def test_party_trackable_account_types_literal(self):
		# Q17 default, locked by convention per commit 1 finding 2.
		# Empirical validation deferred to production rollout (Q19).
		self.assertEqual(
			PARTY_TRACKABLE_ACCOUNT_TYPES,
			("Receivable", "Payable", "Loan"),
		)

	def test_spotlight_imports_flip_from_utils(self):
		"""Spotlight refresh must import the same FLIP_ROOT_TYPES that
		the drill APIs use. Prevents a future commit from re-defining
		a local FLIP_ROOT_TYPES in spotlight_refresh.py."""
		from dux_groupview.dux_groupview.snapshots import (
			spotlight_refresh as sr,
		)
		self.assertIs(sr.FLIP_ROOT_TYPES, FLIP_ROOT_TYPES)


# ---------------------------------------------------------------------------
# Fixture-backed party drill tests
# ---------------------------------------------------------------------------

class _PartyDrillFixtureBase(FrappeTestCase):
	"""Shared fixture lifecycle for the rest of the file."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.state = setup_fixture()

	@classmethod
	def tearDownClass(cls):
		teardown_fixture()
		super().tearDownClass()

	def _all_fixture_party_leaves(self):
		"""All fixture leaves with party-trackable account types.

		Loan is intentionally absent -- see fixture docstring. The
		fixture exercises Receivable + Payable; the literal-tuple
		test below pins Loan in PARTY_TRACKABLE_ACCOUNT_TYPES for the
		production deploy to validate.
		"""
		out = []
		for company in self.state["companies"]:
			for role in ("receivable", "payable"):
				out.append(self.state["accounts"][company][role])
		return out

	def _payable_leaves(self):
		return [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]

	def _receivable_leaves(self):
		return [
			self.state["accounts"][c]["receivable"]
			for c in self.state["companies"]
		]


class TestGetPartyBreakdownBalances(_PartyDrillFixtureBase):

	def test_payable_party_breakdown_balances_match_fixture(self):
		out = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		got = {(p["party_type"], p["party"]): p["balance"] for p in out["parties"]}
		# Hand-rolled expectations from the fixture plan
		self.assertAlmostEqual(got[("Supplier", "Asha Stationers")],       1_500_000.0, places=2)
		self.assertAlmostEqual(got[("Supplier", "Vidarbha Lab Supplies")],   300_000.0, places=2)
		self.assertAlmostEqual(got[("Supplier", "Single Co Vendor")],         50_000.0, places=2)
		self.assertEqual(out["total_parties"], 3)

	def test_receivable_party_breakdown_balances_match_fixture(self):
		A = self.state["companies"][0]
		out = party_drill_v1.get_party_breakdown(
			accounts=self._receivable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		got = {(p["party_type"], p["party"]): p["balance"] for p in out["parties"]}
		# Acme: 100K
		self.assertAlmostEqual(got[("Customer", "Acme Co")], 100_000.0, places=2)
		# group-co customer (named after companies[0]): 60K, is_group_company True
		self.assertAlmostEqual(got[("Customer", A)], 60_000.0, places=2)

	def test_company_count_is_correct(self):
		out = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		got = {p["party"]: p["company_count"] for p in out["parties"]}
		self.assertEqual(got["Asha Stationers"], 3)
		self.assertEqual(got["Vidarbha Lab Supplies"], 2)
		self.assertEqual(got["Single Co Vendor"], 1)

	def test_zero_balance_party_excluded(self):
		"""Net Zero Party has balanced rows (Receivable +100/-100); the
		HAVING balance != 0 clause should drop it from results."""
		out = party_drill_v1.get_party_breakdown(
			accounts=self._receivable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		party_names = {p["party"] for p in out["parties"]}
		self.assertNotIn("Net Zero Party", party_names)

	def test_future_dated_voucher_excluded_at_today(self):
		"""Future Party has a voucher dated 2150-06-15; an as_of_date
		of today should exclude it (today < 2150-06-15)."""
		out = party_drill_v1.get_party_breakdown(
			accounts=self._receivable_leaves(),
			as_of_date=str(today()),
			companies=self.state["companies"],
			page_size=200,
		)
		party_names = {p["party"] for p in out["parties"]}
		self.assertNotIn("Future Party", party_names)

	def test_future_dated_voucher_visible_at_future_as_of_date(self):
		"""Inverse: passing as_of_date >= future_posting_date should
		surface Future Party."""
		out = party_drill_v1.get_party_breakdown(
			accounts=self._receivable_leaves(),
			companies=self.state["companies"],
			as_of_date=str(self.state["future_posting_date"]),
			page_size=200,
		)
		party_names = {p["party"] for p in out["parties"]}
		self.assertIn("Future Party", party_names)


class TestGetPartyBreakdownPagination(_PartyDrillFixtureBase):

	def test_pagination_total_unchanged_across_pages(self):
		page1 = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page=1, page_size=2,
		)
		page2 = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page=2, page_size=2,
		)
		self.assertEqual(page1["total_parties"], page2["total_parties"])
		self.assertEqual(page1["total_parties"], 3)

	def test_pagination_no_overlap_between_pages(self):
		page1 = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page=1, page_size=2,
		)
		page2 = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page=2, page_size=2,
		)
		ids1 = {(p["party_type"], p["party"]) for p in page1["parties"]}
		ids2 = {(p["party_type"], p["party"]) for p in page2["parties"]}
		self.assertEqual(len(ids1 & ids2), 0)
		# Pages 1+2 union should equal the full unpaginated set.
		full = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		full_ids = {(p["party_type"], p["party"]) for p in full["parties"]}
		self.assertEqual(ids1 | ids2, full_ids)

	def test_page_size_max_clamp(self):
		# page_size=999 should be clamped to MAX_PAGE_SIZE (200)
		out = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=999,
		)
		self.assertEqual(out["page_size"], 200)


class TestGetPartyBreakdownSort(_PartyDrillFixtureBase):

	def test_sort_balance_desc(self):
		out = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			sort="balance_desc",
		)
		balances = [p["balance"] for p in out["parties"]]
		# Asha 1.5M > Vidarbha 300K > Single 50K
		self.assertEqual(balances, [1_500_000.0, 300_000.0, 50_000.0])

	def test_sort_balance_asc(self):
		out = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			sort="balance_asc",
		)
		balances = [p["balance"] for p in out["parties"]]
		self.assertEqual(balances, [50_000.0, 300_000.0, 1_500_000.0])

	def test_sort_name_asc(self):
		out = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			sort="name_asc",
		)
		names = [p["party"] for p in out["parties"]]
		self.assertEqual(names, sorted(names))


class TestIsGroupCompanyFlag(_PartyDrillFixtureBase):

	def test_is_group_company_true_when_party_in_tabcompany(self):
		A = self.state["companies"][0]
		out = party_drill_v1.get_party_breakdown(
			accounts=self._receivable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		party = next(p for p in out["parties"] if p["party"] == A)
		self.assertTrue(party["is_group_company"])

	def test_is_group_company_false_for_external_party(self):
		out = party_drill_v1.get_party_breakdown(
			accounts=self._receivable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		party = next(p for p in out["parties"] if p["party"] == "Acme Co")
		self.assertFalse(party["is_group_company"])


class TestGetPartyCompanyBreakdown(_PartyDrillFixtureBase):

	def test_company_breakdown_for_multi_company_party(self):
		out = party_drill_v1.get_party_company_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			party="Asha Stationers",
			party_type="Supplier",
		)
		self.assertEqual(out["party"], "Asha Stationers")
		self.assertEqual(out["total_companies"], 3)
		got = {r["company"]: r["balance"] for r in out["by_company"]}
		A, B, C = self.state["companies"]
		self.assertAlmostEqual(got[A], 500_000.0, places=2)
		self.assertAlmostEqual(got[B], 700_000.0, places=2)
		self.assertAlmostEqual(got[C], 300_000.0, places=2)

	def test_company_breakdown_invariant_sum_equals_party_total(self):
		"""sum(by_company) for one party == that party's balance in
		get_party_breakdown -- gold-standard invariant per spec §8."""
		full = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		asha_total = next(
			p["balance"] for p in full["parties"]
			if p["party"] == "Asha Stationers"
		)
		breakdown = party_drill_v1.get_party_company_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			party="Asha Stationers",
			party_type="Supplier",
		)
		summed = round(sum(r["balance"] for r in breakdown["by_company"]), 2)
		self.assertAlmostEqual(asha_total, summed, places=2)

	def test_company_breakdown_requires_party_and_party_type(self):
		with self.assertRaises(frappe.ValidationError):
			party_drill_v1.get_party_company_breakdown(
				accounts=self._payable_leaves(),
				as_of_date=str(FIXTURE_AS_OF_DATE),
				companies=self.state["companies"],
			)


# ---------------------------------------------------------------------------
# Sign convention parity -- THE gold-standard reconciliation test
# ---------------------------------------------------------------------------

class TestSignConventionParity(_PartyDrillFixtureBase):
	"""Both readers (snapshot / GL) against the same fixture leaves and
	companies must produce the same natural-side balance for every
	(account, company) pair. This is the load-bearing parity assertion
	demanded by the user in commit 2's brief."""

	def test_account_drill_group_total_equals_party_breakdown_sum(self):
		"""Spec §8 gold-standard: account_drill.group_total ==
		sum(party_drill.parties[].balance) for party-trackable scope."""
		ad = account_drill_v1.get_account_breakdown(
			accounts=self._payable_leaves(),
			scope_label="FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
		)
		pd = party_drill_v1.get_party_breakdown(
			accounts=self._payable_leaves(),
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
			page_size=200,
		)
		summed = round(sum(p["balance"] for p in pd["parties"]), 2)
		self.assertAlmostEqual(
			ad["group_total"], summed, places=2,
			msg=(
				f"Sign convention disagreement: account_drill.group_total="
				f"{ad['group_total']} != sum(party_drill.parties[].balance)="
				f"{summed}. Investigate FLIP_ROOT_TYPES drift between "
				f"snapshot and GL aggregation."
			),
		)

	def test_per_company_per_account_parity(self):
		"""For every (leaf_account, company) pair in the fixture, the
		natural-side aggregation done via the snapshot path matches
		the natural-side aggregation done via the GL path.

		Uses FIXTURE_AS_OF_DATE for both readers so the snapshot query
		hits the fixture's scoped snapshot and the GL query applies
		the same posting_date cutoff."""
		as_of_date = getdate(FIXTURE_AS_OF_DATE)
		all_leaves = self._all_fixture_party_leaves()

		# Add bank/equity leaves too -- exercise the non-flip branch.
		for company in self.state["companies"]:
			all_leaves.append(self.state["accounts"][company]["bank"])
			all_leaves.append(self.state["accounts"][company]["equity"])

		flip_in = ", ".join(f"'{r}'" for r in FLIP_ROOT_TYPES)

		for leaf in all_leaves:
			company = frappe.db.get_value("Account", leaf, "company")
			# Snapshot reader: natural-side via -balance / balance.
			snap_row = frappe.db.sql(
				f"""
				SELECT COALESCE(SUM(
				  CASE WHEN root_type IN ({flip_in})
				       THEN -balance
				       ELSE balance END
				), 0)
				FROM `tabDGV TB Snapshot Row`
				WHERE snapshot_date = %s
				  AND account = %s AND company = %s
				""",
				(as_of_date, leaf, company),
			)
			snap_value = float(snap_row[0][0]) if snap_row else 0.0

			# GL reader: natural-side via credit-debit / debit-credit.
			gl_row = frappe.db.sql(
				f"""
				SELECT COALESCE(SUM(
				  CASE WHEN a.root_type IN ({flip_in})
				       THEN g.credit - g.debit
				       ELSE g.debit - g.credit END
				), 0)
				FROM `tabGL Entry` g
				JOIN `tabAccount` a ON a.name = g.account
				WHERE g.account = %s AND g.company = %s
				  AND g.posting_date <= %s
				  AND g.is_cancelled = 0 AND g.docstatus = 1
				""",
				(leaf, company, as_of_date),
			)
			gl_value = float(gl_row[0][0]) if gl_row else 0.0

			self.assertAlmostEqual(
				snap_value, gl_value, places=2,
				msg=(
					f"Sign convention parity broke for "
					f"({leaf}, {company}): snapshot path = {snap_value}, "
					f"GL path = {gl_value}. Investigate FLIP_ROOT_TYPES."
				),
			)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class TestAuditGroupCoNameMatch(FrappeTestCase):

	def test_audit_runs_without_errors(self):
		"""Smoke test: the audit returns a list-of-dicts. On dev seed
		every row will have matched=False (synthetic seed has no
		Customer/Supplier records mirroring group cos -- this is the
		Q19 "validation deferred to production" scenario)."""
		out = party_drill_v1.audit_group_co_name_match()
		self.assertIsInstance(out, list)
		for row in out:
			self.assertIn("company", row)
			self.assertIn("has_customer", row)
			self.assertIn("has_supplier", row)
			self.assertIn("matched", row)
			self.assertEqual(
				row["matched"],
				row["has_customer"] or row["has_supplier"],
			)

	def test_audit_reports_all_companies(self):
		out = party_drill_v1.audit_group_co_name_match()
		audit_companies = {r["company"] for r in out}
		all_companies = set(
			frappe.db.sql_list("SELECT name FROM `tabCompany`")
		)
		self.assertEqual(audit_companies, all_companies)
