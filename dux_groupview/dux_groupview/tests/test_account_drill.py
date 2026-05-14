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

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api import account_drill_v1, party_drill_v1
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


class TestGetAccountBreakdownShapeStability(FrappeTestCase):
	"""Regression pin for `get_account_breakdown` response shape.

	Per spec `specs/per-account-drill-expand.md` §5.1: the new
	`get_account_breakdown_for_company` endpoint deliberately does NOT
	extend `get_account_breakdown`. Lazy-loaded per-account data
	deserves its own fetch boundary, and response stability for the
	existing endpoint matters more than DRY. This test pins the shape
	so a future refactor can't silently fold per-account fields into
	the by-company response.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_top_level_response_keys_unchanged(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		self.assertEqual(
			set(out.keys()),
			{"scope_label", "group_total", "is_party_trackable",
			 "trend_12mo", "by_company"},
		)

	def test_by_company_entry_keys_unchanged(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		if not out["by_company"]:
			self.skipTest("No by_company rows on this dev seed.")
		# Pin the per-company-row shape: company + value + sparkline.
		# Explicitly NOT carrying `accounts`, `total_accounts`, or
		# `truncated` fields -- those live on the new endpoint.
		self.assertEqual(
			set(out["by_company"][0].keys()),
			{"company", "value", "sparkline"},
		)

	def test_trend_12mo_entry_keys_unchanged(self):
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		for entry in out["trend_12mo"]:
			self.assertEqual(set(entry.keys()), {"month", "value"})

	def test_by_company_sort_order_abs_value_desc(self):
		# `get_account_breakdown` sort: abs(value) descending. Pin this
		# so the panel's "largest contributors first" reading stays
		# accurate when the new per-account endpoint lands.
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		if len(out["by_company"]) < 2:
			self.skipTest("Need >= 2 by_company rows to verify sort.")
		values = [abs(r["value"]) for r in out["by_company"]]
		for i in range(len(values) - 1):
			self.assertGreaterEqual(values[i], values[i + 1])

	def test_group_total_equals_sum_of_by_company_values(self):
		# Same invariant as test_group_total_equals_sum_of_by_company_values
		# in TestAccountDrillSparklineLength, repeated here so this
		# regression class is self-contained.
		out = account_drill_v1.get_account_breakdown(
			scope={"type": "account", "value": "Sundry Creditors"},
		)
		summed = round(sum(r["value"] for r in out["by_company"]), 2)
		self.assertEqual(out["group_total"], summed)


class TestGetAccountBreakdownForCompany(FrappeTestCase):
	"""Tests for the new per-company per-account endpoint.

	Per spec `specs/per-account-drill-expand.md` §10.1. Mirrors the
	`TestAccountDrillEntryShapes` structure but exercises the lazy-
	loaded per-account drill. Truncation tests patch
	`PER_COMPANY_ACCOUNT_CAP` rather than seeding 200+ accounts of
	synthetic data -- the truncation LOGIC is what we're pinning, not
	the specific cap value.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()
		# Pick the company with the most snapshot rows so name_pattern
		# scopes have something to match.
		row = frappe.db.sql(
			"""
			SELECT company, COUNT(*) AS c
			FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s
			GROUP BY company
			ORDER BY c DESC LIMIT 1
			""",
			(getdate(today()),),
		)
		cls.dev_company = row[0][0] if row else None

	def setUp(self):
		if not self.dev_company:
			self.skipTest("No snapshot rows on this site.")

	# --- Happy-path shape ---

	def test_response_keys(self):
		out = account_drill_v1.get_account_breakdown_for_company(
			scope={"type": "account", "value": "Sundry Creditors"},
			company=self.dev_company,
		)
		self.assertEqual(
			set(out.keys()),
			{"company", "scope_label", "as_of_date", "company_total",
			 "accounts", "total_accounts", "truncated"},
		)
		self.assertEqual(out["company"], self.dev_company)
		self.assertIsInstance(out["accounts"], list)
		self.assertIsInstance(out["total_accounts"], int)
		self.assertIsInstance(out["truncated"], bool)

	def test_account_row_shape(self):
		# "%" wildcard matches all leaves; we just need a non-empty
		# result set to assert per-row shape. Truncation is tested
		# separately via patched cap, not via large fixture set.
		out = account_drill_v1.get_account_breakdown_for_company(
			scope={"type": "name_pattern", "value": "%"},
			company=self.dev_company,
		)
		if not out["accounts"]:
			self.skipTest("No matching accounts in this company.")
		for row in out["accounts"]:
			self.assertEqual(
				set(row.keys()),
				{"account", "account_name", "balance", "currency"},
			)
			self.assertIsInstance(row["account"], str)
			self.assertIsInstance(row["account_name"], str)
			self.assertIsInstance(row["balance"], (int, float))

	# --- Sort + sum invariants ---

	def test_sort_order_abs_balance_desc(self):
		out = account_drill_v1.get_account_breakdown_for_company(
			scope={"type": "name_pattern", "value": "%"},
			company=self.dev_company,
		)
		if len(out["accounts"]) < 2:
			self.skipTest("Need >= 2 accounts to verify sort.")
		balances = [abs(a["balance"]) for a in out["accounts"]]
		for i in range(len(balances) - 1):
			self.assertGreaterEqual(balances[i], balances[i + 1])

	def test_company_total_matches_account_sum_when_not_truncated(self):
		# Pin: sum(accounts.balance) == company_total when truncated=False.
		# (When truncated, company_total reflects the full set, NOT the
		# visible 200 -- separately tested in
		# test_truncation_company_total_reflects_full_set.)
		out = account_drill_v1.get_account_breakdown_for_company(
			scope={"type": "name_pattern", "value": "%"},
			company=self.dev_company,
		)
		if out["truncated"]:
			self.skipTest("Need non-truncated result for this invariant.")
		summed = round(sum(a["balance"] for a in out["accounts"]), 2)
		self.assertEqual(out["company_total"], summed)

	# --- Entry-shape requirements (mirrors get_account_breakdown) ---

	def test_accounts_entry_requires_scope_label(self):
		with self.assertRaises(frappe.ValidationError):
			account_drill_v1.get_account_breakdown_for_company(
				accounts=["Some Account - X"],
				company=self.dev_company,
			)

	def test_accounts_entry_with_scope_label_returns_payload(self):
		# Pick a real account in this company so the scope is non-empty.
		row = frappe.db.sql(
			"""
			SELECT account FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s AND company = %s LIMIT 1
			""",
			(getdate(today()), self.dev_company),
		)
		if not row:
			self.skipTest("No snapshot rows for this company.")
		account = row[0][0]
		out = account_drill_v1.get_account_breakdown_for_company(
			accounts=[account],
			scope_label="Test Custom Label",
			company=self.dev_company,
		)
		self.assertEqual(out["scope_label"], "Test Custom Label")

	# --- Empty-result behaviour ---

	def test_zero_match_scope_returns_empty_accounts(self):
		# Predicate matches nothing -> empty array, NOT an error.
		out = account_drill_v1.get_account_breakdown_for_company(
			scope={"type": "name_pattern",
			       "value": "%ZZZZ_definitely_no_match_ZZZZ%"},
			company=self.dev_company,
		)
		self.assertEqual(out["accounts"], [])
		self.assertEqual(out["total_accounts"], 0)
		self.assertFalse(out["truncated"])
		self.assertEqual(out["company_total"], 0.0)

	# --- Malformed scope (stale-deep-link, sets the flag) ---

	def test_missing_company_sets_malformed_scope(self):
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "account", "value": "Sundry Creditors"},
				company=None,
			)
		self.assertTrue(frappe.local.response.get("malformed_scope"))

	def test_empty_company_string_sets_malformed_scope(self):
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "account", "value": "Sundry Creditors"},
				company="   ",
			)
		self.assertTrue(frappe.local.response.get("malformed_scope"))

	def test_missing_scope_and_accounts_sets_malformed_scope(self):
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			account_drill_v1.get_account_breakdown_for_company(
				company=self.dev_company,
			)
		self.assertTrue(frappe.local.response.get("malformed_scope"))

	# --- Permission denial (NOT malformed -- a different signal) ---

	def test_disallowed_company_raises_permission_error(self):
		# A company that doesn't exist (and so isn't in any user's
		# allowed set) exercises the `company not in allowed` branch.
		with self.assertRaises(frappe.PermissionError):
			account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "account", "value": "Sundry Creditors"},
				company="Nonexistent Company XYZ 12345",
			)

	# --- Truncation logic ---

	def test_truncation_caps_accounts_and_reports_total(self):
		from unittest.mock import patch
		# First, find a baseline at a high cap so we know the true count.
		# `%` matches every account name, so any company with rows has
		# data here.
		with patch.object(account_drill_v1, "PER_COMPANY_ACCOUNT_CAP", 10000):
			baseline = account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "name_pattern", "value": "%"},
				company=self.dev_company,
			)
		if baseline["total_accounts"] < 2:
			self.skipTest("Need >= 2 accounts in scope to test truncation.")

		# Now lower cap to 1 and verify the truncation contract.
		with patch.object(account_drill_v1, "PER_COMPANY_ACCOUNT_CAP", 1):
			out = account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "name_pattern", "value": "%"},
				company=self.dev_company,
			)
		self.assertTrue(out["truncated"])
		self.assertEqual(len(out["accounts"]), 1)
		# total_accounts reports the TRUE count (full set), not the
		# visible count.
		self.assertEqual(out["total_accounts"], baseline["total_accounts"])
		# Sort order preserved: the visible row is the top-of-baseline.
		self.assertEqual(
			out["accounts"][0]["account"],
			baseline["accounts"][0]["account"],
		)

	def test_truncation_company_total_reflects_full_set(self):
		# When truncated, company_total must equal the sum over ALL
		# matching accounts, NOT just the visible 200. Pin this
		# explicitly because the implementation has two branches
		# (sum-of-rows vs separate-SUM-query).
		from unittest.mock import patch
		with patch.object(account_drill_v1, "PER_COMPANY_ACCOUNT_CAP", 10000):
			baseline = account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "name_pattern", "value": "%"},
				company=self.dev_company,
			)
		if baseline["total_accounts"] < 2:
			self.skipTest("Need >= 2 accounts to compare full-set total.")

		with patch.object(account_drill_v1, "PER_COMPANY_ACCOUNT_CAP", 1):
			truncated_out = account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "name_pattern", "value": "%"},
				company=self.dev_company,
			)
		self.assertTrue(truncated_out["truncated"])
		# Truncated company_total equals baseline company_total exactly
		# (both are the full-set natural-side sum).
		self.assertEqual(
			truncated_out["company_total"],
			baseline["company_total"],
		)

	def test_truncation_not_set_when_cap_exceeds_data(self):
		# Defensive: when cap exceeds the result set size, truncated
		# must be False and total_accounts == len(accounts).
		from unittest.mock import patch
		with patch.object(account_drill_v1, "PER_COMPANY_ACCOUNT_CAP", 100000):
			out = account_drill_v1.get_account_breakdown_for_company(
				scope={"type": "name_pattern", "value": "%"},
				company=self.dev_company,
			)
		self.assertFalse(out["truncated"])
		self.assertEqual(out["total_accounts"], len(out["accounts"]))


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


# ---------------------------------------------------------------------------
# CSV export -- export_account_breakdown_csv (HALT 2)
# ---------------------------------------------------------------------------

class TestExportAccountBreakdownCsv(FrappeTestCase):
	"""HALT 2: per-(company, account) breakdown CSV export.

	The endpoint sets `frappe.local.response` and returns None. Tests
	read the response back and parse the CSV body.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.state = setup_fixture()

	@classmethod
	def tearDownClass(cls):
		teardown_fixture()
		super().tearDownClass()

	def _payable_leaves(self):
		return [
			self.state["accounts"][c]["payable"]
			for c in self.state["companies"]
		]

	def _invoke_export(self):
		"""Call the endpoint and return the captured CSV body string.

		Resets `frappe.local.response` before / after so other tests
		aren't polluted.
		"""
		# Snapshot prior response state (the fixture-base class doesn't
		# touch it but other tests in the suite may).
		original = dict(getattr(frappe.local, "response", {}))
		try:
			frappe.local.response = frappe._dict()
			account_drill_v1.export_account_breakdown_csv(
				accounts=self._payable_leaves(),
				scope_label="FXT Payable",
				as_of_date=str(FIXTURE_AS_OF_DATE),
				companies=self.state["companies"],
			)
			content = frappe.local.response.get("filecontent") or b""
			filename = frappe.local.response.get("filename") or ""
			rtype = frappe.local.response.get("type") or ""
			body = content.decode("utf-8") if isinstance(content, bytes) else str(content)
			return body, filename, rtype
		finally:
			frappe.local.response = frappe._dict(original)

	def test_export_account_breakdown_csv_columns(self):
		"""Header row matches the HALT 2 spec: Company, Account, Balance, Currency."""
		body, filename, rtype = self._invoke_export()
		self.assertEqual(rtype, "binary")
		# Filename pattern: account_breakdown_<slug>_<as_of>_<HHMMSS>.csv
		self.assertTrue(filename.startswith("account_breakdown_"),
		                msg=f"unexpected filename: {filename}")
		self.assertTrue(filename.endswith(".csv"),
		                msg=f"unexpected filename: {filename}")
		# First non-empty line is the header.
		lines = [ln for ln in body.splitlines() if ln]
		self.assertTrue(lines, msg="CSV body unexpectedly empty")
		self.assertEqual(lines[0], "Company,Account,Balance,Currency")
		# Body has at least one data row -- fixture has 3 payable
		# accounts with non-zero balance.
		self.assertGreaterEqual(len(lines), 2, msg="expected >= 1 data row")

	def test_export_account_breakdown_csv_raw_decimals_NOT_indian_grouped(self):
		"""Balance cells contain raw decimals like '500000.00', NOT
		Indian-grouped strings like '5,00,000.00' or '5,00,000'.

		The locked spec position: CSV is data-interchange; presentation
		formatting (Indian grouping, currency symbols) belongs in the
		rendered UI, not in cells. Spreadsheet apps numerically-type
		raw decimal cells; pre-formatted strings would import as text
		and break sum/filter/pivot.
		"""
		import csv as _csv
		import io as _io
		body, _, _ = self._invoke_export()
		reader = _csv.reader(_io.StringIO(body))
		rows = list(reader)
		# Skip header
		data_rows = rows[1:]
		self.assertTrue(data_rows, msg="expected at least one data row")
		for row in data_rows:
			balance_cell = row[2]
			# Raw decimal: parses as float without commas
			self.assertNotIn(",", balance_cell,
				msg=f"Balance cell {balance_cell!r} contains a comma -- looks Indian-grouped")
			# Must parse as float
			try:
				float(balance_cell)
			except ValueError:
				self.fail(f"Balance cell {balance_cell!r} does not parse as a float")


class TestZeroPartiesNotFour04(FrappeTestCase):
	"""Counterpart to the malformed_scope test in test_cards_v1: a
	trackable scope with no party data must return 200 + empty list,
	NOT a 404. Distinguishes "valid request, empty result" (this
	test) from "predicate is malformed" (the cards_v1 test).

	(Commit-6 HALT 6.4 — pins the empty-vs-missing semantics on the
	party-list endpoint so a future refactor doesn't accidentally
	collapse them.)
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_get_party_breakdown_zero_parties_returns_empty_array_not_404(self):
		# A trackable scope (Sundry Debtors / Receivable) at a date
		# in the distant past, before any GL entry exists. Snapshot
		# rows + GL rows are empty under that filter; party breakdown
		# should report 0 parties cleanly.
		out = party_drill_v1.get_party_breakdown(
			scope=json.dumps({"type": "account", "value": "Sundry Debtors"}),
			as_of_date="1900-01-01",
		)
		self.assertEqual(out["parties"], [])
		self.assertEqual(out["total_parties"], 0)
