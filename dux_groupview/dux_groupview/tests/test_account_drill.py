"""Tests for `account_drill_v1.get_account_breakdown`.

Covers:
  - both entry shapes (scope + accounts)
  - all three ScopeSpec types (account, subtree, name_pattern)
  - is_party_trackable across account_types (via fixture)
  - sparkline / trend shape and length
  - group_total == sum(by_company values) invariant
  - permission filtering (companies arg is intersected, not widened)

Account drill reads `tabDGV TB Snapshot Row` only -- no `tabGL Entry`
access. The fixture is used here only for is_party_trackable
boundary tests (account_type variety on small leaf sets).

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_account_drill
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api import account_drill_v1
from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	SPARKLINE_LENGTH,
	refresh_spotlight_cache,
)
from dux_groupview.dux_groupview.tests.fixtures.party_drill_fixture import (
	SNAPSHOT_DATE as FIXTURE_AS_OF_DATE,
	setup_fixture,
	teardown_fixture,
)


def _ensure_today_data():
	if not frappe.db.exists("DGV TB Snapshot", {"snapshot_date": getdate(today())}):
		refresh_tb_snapshot()
	refresh_spotlight_cache()


class TestAccountDrillSparklineLength(FrappeTestCase):
	"""Tests against existing dev data (no fixture). Cheap setup."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_sparkline_length_is_12(self):
		# SPARKLINE_LENGTH constant is 12 after Phase 4 commit 2 bump.
		self.assertEqual(SPARKLINE_LENGTH, 12)

	def test_trend_has_12_entries_at_default_as_of_date(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		self.assertEqual(len(out["trend_12mo"]), 12)
		# Each entry has month + value keys.
		for entry in out["trend_12mo"]:
			self.assertIn("month", entry)
			self.assertIn("value", entry)
			# month is YYYY-MM
			if entry["month"]:
				self.assertRegex(entry["month"], r"^\d{4}-\d{2}$")

	def test_by_company_sparkline_length_is_12(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		for entry in out["by_company"]:
			self.assertEqual(len(entry["sparkline"]), 12)

	def test_group_total_equals_sum_of_by_company_values(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		summed = round(sum(r["value"] for r in out["by_company"]), 2)
		self.assertEqual(out["group_total"], summed)


class TestAccountDrillEntryShapes(FrappeTestCase):
	"""Two entry shapes: scope and accounts."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_scope_account_entry_returns_payload(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		self.assertEqual(out["scope_label"], "Sundry Creditors")
		self.assertIn("group_total", out)
		self.assertIn("is_party_trackable", out)
		self.assertIn("trend_12mo", out)
		self.assertIn("by_company", out)

	def test_scope_name_pattern_entry_returns_payload(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "name_pattern", "value": "%Sundry Creditors%"},
		)
		# Label defaults to scope.value
		self.assertEqual(out["scope_label"], "%Sundry Creditors%")

	def test_subtree_scope_walks_per_company(self):
		# Pick any group account from the dev data to exercise the
		# subtree path. If no groups exist we skip.
		row = frappe.db.sql(
			"""
			SELECT account_name, company FROM `tabAccount`
			WHERE is_group = 1 AND root_type = 'Liability'
			LIMIT 1
			"""
		)
		if not row:
			self.skipTest("No Liability group account on this site.")
		account_name, _company = row[0]
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "subtree", "value": account_name},
		)
		# Just assert shape; correctness is exercised by the
		# subtree-helper test in commit 1.
		self.assertEqual(out["scope_label"], account_name)
		self.assertIsInstance(out["by_company"], list)

	def test_accounts_entry_requires_scope_label(self):
		# Pass accounts without scope_label -> error
		with self.assertRaises(frappe.ValidationError):
			account_drill_v1.get_account_breakdown(
				accounts=["Some Account - X"],
			)

	def test_accounts_entry_with_scope_label_returns_payload(self):
		# Pass a real account from existing data
		row = frappe.db.sql(
			"""
			SELECT account FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s LIMIT 1
			""",
			(getdate(today()),),
		)
		if not row:
			self.skipTest("No snapshot rows on this site.")
		account = row[0][0]
		out = account_drill_v1.get_account_breakdown(
			accounts=[account],
			scope_label="Test Custom Label",
		)
		self.assertEqual(out["scope_label"], "Test Custom Label")

	def test_no_input_raises(self):
		with self.assertRaises(frappe.ValidationError):
			account_drill_v1.get_account_breakdown()


class TestIsPartyTrackable(FrappeTestCase):
	"""Exercises the is_party_trackable flag across account_types.

	Uses the fixture so we know exactly what account_types the leaf
	set has.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.state = setup_fixture()

	@classmethod
	def tearDownClass(cls):
		teardown_fixture()
		super().tearDownClass()

	def test_receivable_leaves_set_flag_true(self):
		leaves = [
			self.state["accounts"][c]["receivable"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves, scope_label="FXT Receivable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
		)
		self.assertTrue(out["is_party_trackable"])

	def test_payable_leaves_set_flag_true(self):
		leaves = [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves, scope_label="FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
		)
		self.assertTrue(out["is_party_trackable"])

	def test_loan_account_type_sets_flag_true_when_present(self):
		"""Loan account_type isn't writable via the doctype on this
		dev site (Lending module config), so we look up an existing
		Loan-typed leaf rather than creating one in the fixture. The
		runtime path will be validated against production data during
		the Phase 4 production deploy. The literal-tuple test in
		test_party_drill pins Loan in PARTY_TRACKABLE_ACCOUNT_TYPES
		regardless of fixture coverage."""
		leaf = frappe.db.sql_list(
			"""
			SELECT name FROM `tabAccount`
			WHERE account_type = 'Loan' AND is_group = 0 LIMIT 1
			"""
		)
		if not leaf:
			self.skipTest(
				"No Loan-typed account exists on this site; "
				"production validation deferred."
			)
		out = account_drill_v1.get_account_breakdown(
			accounts=[leaf[0]], scope_label="Loan probe",
		)
		self.assertTrue(out["is_party_trackable"])

	def test_bank_leaves_set_flag_false(self):
		leaves = [
			self.state["accounts"][c]["bank"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves, scope_label="FXT Bank",
			as_of_date=str(FIXTURE_AS_OF_DATE),
		)
		self.assertFalse(out["is_party_trackable"])

	def test_equity_leaves_set_flag_false(self):
		leaves = [
			self.state["accounts"][c]["equity"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves, scope_label="FXT Equity",
			as_of_date=str(FIXTURE_AS_OF_DATE),
		)
		self.assertFalse(out["is_party_trackable"])


class TestAccountDrillCompanyScope(FrappeTestCase):
	"""Companies arg is intersected with User Permissions (security)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.state = setup_fixture()

	@classmethod
	def tearDownClass(cls):
		teardown_fixture()
		super().tearDownClass()

	def test_companies_arg_filters_by_company(self):
		A, B, _C = self.state["companies"]
		leaves = [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]
		# Restrict to companies[0] only -> by_company should not
		# include companies[1] / [2].
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves,
			scope_label="Scoped FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=[A],
		)
		returned = {r["company"] for r in out["by_company"]}
		self.assertIn(A, returned)
		self.assertNotIn(B, returned)

	def test_full_scope_matches_unscoped(self):
		leaves = [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]
		full = account_drill_v1.get_account_breakdown(
			accounts=leaves,
			scope_label="FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
		)
		default = account_drill_v1.get_account_breakdown(
			accounts=leaves,
			scope_label="FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
		)
		# default includes the user's full allowed set, which is a
		# superset of the fixture's 3 companies. The fixture leaves are
		# only present in the 3 fixture companies, so by_company values
		# should agree.
		full_by = {r["company"]: r["value"] for r in full["by_company"]}
		default_by = {r["company"]: r["value"] for r in default["by_company"]}
		for c in self.state["companies"]:
			self.assertAlmostEqual(
				full_by.get(c, 0.0), default_by.get(c, 0.0), places=2,
			)


class TestAccountDrillFixtureBalances(FrappeTestCase):
	"""Exact balance assertions against the fixture's known totals."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.state = setup_fixture()

	@classmethod
	def tearDownClass(cls):
		teardown_fixture()
		super().tearDownClass()

	def test_payable_group_total_matches_hand_calculation(self):
		leaves = [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves,
			scope_label="FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
		)
		# Hand sum from fixture plan:
		#   Asha: 500K + 700K + 300K = 1,500,000
		#   Vidarbha: 200K + 100K =      300,000
		#   Single:                       50,000
		#   Total natural-side Payable: 1,850,000
		self.assertAlmostEqual(out["group_total"], 1_850_000.0, places=2)

	def test_receivable_group_total_matches_hand_calculation(self):
		leaves = [
			self.state["accounts"][c]["receivable"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves,
			scope_label="FXT Receivable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
		)
		# Acme 100K + group_co_party 60K = 160,000
		# Net Zero Party 0K (HAVING balance != 0 in snapshot's per-leaf
		# aggregation -- but at as_of_date='2099-12-31' it nets to zero
		# per leaf so contributes 0 either way).
		# Future Party (posting_date 2150-06-15) excluded -- snapshot
		# was built with posting_date <= 2099-12-31.
		self.assertAlmostEqual(out["group_total"], 160_000.0, places=2)

	def test_by_company_sums_to_group_total_on_fixture(self):
		leaves = [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]
		out = account_drill_v1.get_account_breakdown(
			accounts=leaves,
			scope_label="FXT Payable",
			as_of_date=str(FIXTURE_AS_OF_DATE),
			companies=self.state["companies"],
		)
		summed = round(sum(r["value"] for r in out["by_company"]), 2)
		self.assertAlmostEqual(out["group_total"], summed, places=2)
