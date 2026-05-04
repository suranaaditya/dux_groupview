"""Tests for the pivot data layer.

Reads only DGV TB Snapshot Row, tabAccount, tabCompany. Never tabGL Entry.
Never tabDGV Spotlight Cache.

The gold-standard test (test_pivot_data_matches_snapshot) is the load-
bearing one for this phase. If a cell value in the API response
doesn't match the underlying snapshot row, the cockpit pivot is lying.

Run with:
    bench --site erp.jewonline.in run-tests --module \
        dux_groupview.dux_groupview.tests.test_pivot
"""

import time
from collections import defaultdict

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api import pivot as pivot_api
from dux_groupview.dux_groupview.pivot.trust_groups import (
	TRUSTS,
	get_trust_for_company,
)
from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot


def _ensure_today_snapshot():
	if not frappe.db.exists("DGV TB Snapshot", {"snapshot_date": getdate(today())}):
		refresh_tb_snapshot()


class TestPivot(FrappeTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_snapshot()

	# ------------------------------------------------------------------
	# 1 -- response shape
	# ------------------------------------------------------------------

	def test_get_pivot_data_structure(self):
		data = pivot_api.get_pivot_data(today())
		for key in ("snapshot_date", "snapshot_age_seconds", "format",
		             "trusts", "accounts", "balances"):
			self.assertIn(key, data)
		self.assertIsInstance(data["trusts"], list)
		self.assertGreaterEqual(len(data["trusts"]), 1)
		self.assertIsInstance(data["accounts"], list)
		self.assertIsInstance(data["balances"], dict)
		# accounts ordered by hierarchy: depth-0 entries appear before
		# their children.
		seen = set()
		for a in data["accounts"]:
			if a["parent"]:
				self.assertIn(
					a["parent"], seen,
					f"Account {a['name']} appears before its parent {a['parent']}",
				)
			seen.add(a["name"])

	# ------------------------------------------------------------------
	# 2 -- leaf invariant: for every account that has no children in the
	# response tree, its balance must equal the direct sum of snapshot
	# rows for that account_name. Group / non-leaf accounts are
	# covered by the recursive invariant in test_pivot_filter
	# (test_get_pivot_data_group_totals_match_descendants_recursively),
	# which asserts balance[node] = own_row + sum(balance[child]).
	# ------------------------------------------------------------------

	def test_pivot_leaf_balances_match_snapshot(self):
		"""Leaf-only invariant.

		Post-Phase-3.5 the API bubbles every direct snapshot row up
		through its ancestor chain so non-leaf accounts carry rolled-up
		totals. The original Phase 3 assertion (every cell == direct
		snapshot sum by account_name) no longer holds for any account
		that has descendants in the response.

		This rewritten test guards the OTHER half of the invariant:
		accounts that have NO children in the response tree must still
		equal their direct snapshot sum. A regression that accidentally
		bubbles values into a true leaf would fail this assertion;
		Phase 3.5's recursive test would not catch that case (leaves
		trivially satisfy `own + sum([]) == own`).

		Together the two tests are complementary:
		- Phase 3.5 recursive test: balance[node] == own + sum(children),
		  asserted at every level.
		- This test: leaves have no descendants, so balance[leaf] ==
		  direct snapshot sum, asserted only on accounts the response
		  tree treats as leaves.

		Note: "leaf" here is determined by the response tree shape, NOT
		by the per-account_name `is_group` field returned in the
		response. `is_group` reflects a majority vote across companies
		and can be False on accounts that have children in the response
		(e.g. "Unsecured Loans" is is_group=False in most companies but
		parents many child accounts in the unified hierarchy). The
		reliable leaf signal is "no other account references this one
		as `parent`".
		"""
		snapshot_date = today()
		data = pivot_api.get_pivot_data(snapshot_date)

		# Direct sum of snapshot rows by (account_name, company) —
		# the expected value for any true leaf.
		expected = defaultdict(lambda: defaultdict(float))
		rows = frappe.db.sql(
			"""
			SELECT r.company, r.balance, COALESCE(a.account_name, r.account) AS account_name
			FROM `tabDGV TB Snapshot Row` r
			LEFT JOIN `tabAccount` a ON a.name = r.account
			WHERE r.snapshot_date = %s
			""",
			(snapshot_date,),
			as_dict=True,
		)
		for r in rows:
			expected[r["account_name"]][r["company"]] += float(flt(r["balance"]))

		# Identify accounts referenced as a parent by anyone -- these
		# are non-leaves regardless of their is_group flag.
		has_children = set()
		for a in data["accounts"]:
			parent = a.get("parent") or ""
			if parent:
				has_children.add(parent)

		leaves_checked = 0
		for account_name, company_map in data["balances"].items():
			if account_name in has_children:
				# Non-leaf in the response tree; covered by the Phase
				# 3.5 recursive invariant test.
				continue
			for company, value in company_map.items():
				exp = expected[account_name][company]
				self.assertAlmostEqual(
					float(value), exp, places=2,
					msg=(
						f"Leaf {account_name!r} × {company!r}: "
						f"got {value}, expected direct snapshot sum {exp}. "
						f"(A non-zero diff here means a leaf account is "
						f"receiving bubbled-up descendant values, which "
						f"violates the post-Phase-3.5 leaf invariant.)"
					),
				)
			leaves_checked += 1

		self.assertGreater(
			leaves_checked, 0,
			"Expected at least one true leaf in the response to verify",
		)

	# ------------------------------------------------------------------
	# 3 -- user permissions
	# ------------------------------------------------------------------

	def test_get_pivot_data_respects_user_permissions(self):
		"""A user with User Permissions on only 2 of 5 dev companies
		should see exactly those 2 in the response.
		"""
		# Pick two existing test companies.
		test_companies = frappe.db.sql_list(
			"SELECT name FROM tabCompany WHERE name LIKE %s ORDER BY name LIMIT 2",
			("Test Company %",),
		)
		if len(test_companies) < 2:
			self.skipTest("Need at least 2 test companies on dev to run perm test.")

		# Create a temp user with GroupView Viewer + restricted to 2 companies.
		user_email = "dgv_perm_test@example.invalid"
		try:
			frappe.delete_doc("User", user_email, force=True, ignore_missing=True)
		except Exception:
			pass

		try:
			user = frappe.get_doc({
				"doctype": "User",
				"email": user_email,
				"first_name": "DGV Perm Test",
				"send_welcome_email": 0,
				"new_password": "dgv-test-pw-9j2k",
				"roles": [
					{"role": "GroupView Viewer"},
				],
			})
			user.flags.ignore_permissions = True
			user.insert()

			for c in test_companies:
				perm = frappe.get_doc({
					"doctype": "User Permission",
					"user": user_email,
					"allow": "Company",
					"for_value": c,
					"apply_to_all_doctypes": 1,
				})
				perm.flags.ignore_permissions = True
				perm.insert()

			frappe.db.commit()

			# Switch session to the test user and call the API.
			original_user = frappe.session.user
			try:
				frappe.set_user(user_email)
				data = pivot_api.get_pivot_data(today())
			finally:
				frappe.set_user(original_user)

			# Compute the visible company set from the response.
			visible_companies = set()
			for trust in data["trusts"]:
				visible_companies.update(trust["companies"])

			self.assertEqual(visible_companies, set(test_companies))
			# Balances dict should only contain entries for those 2 companies.
			for account_name, company_map in data["balances"].items():
				for c in company_map:
					self.assertIn(c, set(test_companies))
		finally:
			# Clean up. Permissions cascade-delete with the user.
			frappe.db.sql(
				"DELETE FROM `tabUser Permission` WHERE user = %s",
				(user_email,),
			)
			frappe.delete_doc("User", user_email, force=True, ignore_missing=True)
			frappe.db.commit()

	# ------------------------------------------------------------------
	# 4 -- trust assignment
	# ------------------------------------------------------------------

	def test_trust_assignment_function(self):
		# Spot check known names against expected trust ids.
		spot_checks = {
			"GH Raisoni College Of Engineering": "ass",
			"Ankush Shikshan Sanstha Society": "ass",
			"GHR CACS Pune": "ghremf",
			"Sadabai Raisoni Womens College": "ghref",
			"GH Raisoni University Saikheda": "ghrus",
			"GH Raisoni University Amravati": "ghrua",
			"GH Raisoni Skill Tech University Nagpur": "ghrstu",
			"SGR Foundation": "sgr",
			"Test Company A": "default",  # not in any trust
			"Made Up Company": "default",
		}
		for company_name, expected_trust in spot_checks.items():
			self.assertEqual(
				get_trust_for_company(company_name), expected_trust,
				f"{company_name} -> expected {expected_trust}",
			)

		# Every company in every trust should reverse-map to that trust id.
		for trust in TRUSTS:
			for c in trust["companies"]:
				self.assertEqual(
					get_trust_for_company(c), trust["id"],
					f"Company {c!r} should map to trust {trust['id']!r}",
				)

	# ------------------------------------------------------------------
	# 5 -- performance smoke test
	# ------------------------------------------------------------------

	def test_get_pivot_data_performance(self):
		# Warm cache + index.
		pivot_api.get_pivot_data(today())

		t0 = time.time()
		pivot_api.get_pivot_data(today())
		duration = time.time() - t0
		self.assertLess(
			duration, 0.5,
			f"get_pivot_data took {duration*1000:.0f} ms on dev (target < 500 ms)",
		)
