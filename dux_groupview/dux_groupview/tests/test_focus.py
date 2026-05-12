"""Tests for `focus_v1.get_focused_view`.

Focus mode reads `tabDGV TB Snapshot Row` joined to `tabAccount`
only -- no `tabGL Entry` access (per spec §4.5). Tests run against
existing dev data without a fixture; the dev seed has:

  - RGI-named companies that map cleanly to the TRUSTS definitions
    in pivot/trust_groups.py (GHREMF trust has 8 companies on dev).
  - One Complete snapshot at the most-recent business date.
  - Sufficient leaf/group account variety to exercise the full-depth
    rendering and the five summary tiles.

Tests use a known stable company ("GH Raisoni Public School Pune")
and a known stable trust ("GH Raisoni Educational And Medical
Foundation" / id 'ghremf') that have rows in the dev snapshot.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_focus
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api import focus_v1
from dux_groupview.dux_groupview.api.utils import FLIP_ROOT_TYPES
from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS


# Known-good scopes on dev. The trust "ghremf" has 8 companies on dev
# (per `bench mariadb -e 'SELECT name FROM tabCompany'`); GH Raisoni
# Public School Pune is one of them and is a small enough company to
# make per-company tests fast.
_TEST_COMPANY = "GH Raisoni Public School Pune"
_TEST_TRUST_NAME = "GH Raisoni Educational And Medical Foundation"
_TEST_TRUST_ID = "ghremf"


def _ensure_today_data():
	"""Make sure today's snapshot exists. Cheap if it already does."""
	if not frappe.db.exists("DGV TB Snapshot", {"snapshot_date": getdate(today())}):
		refresh_tb_snapshot()


class TestFocusedViewCompanyShape(FrappeTestCase):
	"""Company-focus payload shape, ordering, and depth."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()
		cls.payload = focus_v1.get_focused_view(
			scope_type="company",
			scope_value=_TEST_COMPANY,
			as_of_date=today(),
		)

	def test_get_focused_view_company_returns_5_tiles(self):
		tiles = self.payload["summary_tiles"]
		self.assertEqual(
			set(tiles.keys()),
			{"assets", "liabilities", "income", "expenses", "net_surplus"},
		)
		# net_surplus is derived from income - expenses.
		self.assertAlmostEqual(
			tiles["net_surplus"],
			round(tiles["income"] - tiles["expenses"], 2),
			places=2,
		)

	def test_get_focused_view_company_accounts_in_lft_order(self):
		# We don't have lft in the response, but the SQL ORDER BY MIN(lft)
		# means parents always precede their descendants when scope is a
		# single company (each company has a coherent lft tree).
		# Verify: every account's parent (if any in scope) appears before
		# it in the response.
		seen = set()
		for entry in self.payload["accounts"]:
			parent = entry["parent_account"]
			if parent:
				# Parent might not be in scope (root crosses out of CoA);
				# only assert ordering when it IS in scope.
				parents_in_scope = {
					e["account_name"] for e in self.payload["accounts"]
				}
				if parent in parents_in_scope:
					self.assertIn(
						parent, seen,
						f"{entry['account_name']} appears before its parent "
						f"{parent}",
					)
			seen.add(entry["account_name"])

	def test_get_focused_view_company_full_depth_includes_leaves_and_groups(self):
		accounts = self.payload["accounts"]
		self.assertGreater(len(accounts), 0, "expected at least some accounts")
		groups = [a for a in accounts if a["is_group"]]
		leaves = [a for a in accounts if not a["is_group"]]
		# Both populated; full-depth view exposes both group and leaf rows.
		self.assertGreater(len(groups), 0, "expected group accounts")
		self.assertGreater(len(leaves), 0, "expected leaf accounts")
		# Depth varies (not all rows are roots).
		depths = {a["depth"] for a in accounts}
		self.assertGreater(
			max(depths), 0,
			"expected at least one non-root account",
		)


class TestFocusedViewTrustResolution(FrappeTestCase):
	"""Trust scope resolves to the configured companies list."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_get_focused_view_trust_resolves_to_companies(self):
		# Resolve trust by full name.
		out_by_name = focus_v1.get_focused_view(
			scope_type="trust",
			scope_value=_TEST_TRUST_NAME,
			as_of_date=today(),
		)
		# Resolve trust by id.
		out_by_id = focus_v1.get_focused_view(
			scope_type="trust",
			scope_value=_TEST_TRUST_ID,
			as_of_date=today(),
		)
		# Both routes should return the same companies list.
		self.assertEqual(
			out_by_name["scope"]["companies"],
			out_by_id["scope"]["companies"],
		)
		# The companies list must be a non-empty subset of the configured
		# trust members (intersected with dev permissions, which on the
		# bench test runner is System Manager so == configured).
		ghremf = next(t for t in TRUSTS if t["id"] == _TEST_TRUST_ID)
		configured = set(ghremf["companies"])
		returned = set(out_by_name["scope"]["companies"])
		self.assertGreater(len(returned), 0)
		self.assertTrue(
			returned.issubset(configured),
			f"returned companies {returned} not subset of configured "
			f"{configured}",
		)


class TestFocusedViewTrustAggregation(FrappeTestCase):
	"""Trust focus aggregates account balances across companies."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()
		cls.trust_payload = focus_v1.get_focused_view(
			scope_type="trust",
			scope_value=_TEST_TRUST_NAME,
			as_of_date=today(),
		)

	def test_get_focused_view_trust_aggregates_across_companies(self):
		# For each company in the trust, sum a single root_type tile and
		# verify the trust tile equals the sum of company tiles.
		per_company_assets = 0.0
		for company in self.trust_payload["scope"]["companies"]:
			co_payload = focus_v1.get_focused_view(
				scope_type="company",
				scope_value=company,
				as_of_date=today(),
			)
			per_company_assets += co_payload["summary_tiles"]["assets"]
		trust_assets = self.trust_payload["summary_tiles"]["assets"]
		self.assertAlmostEqual(
			trust_assets, round(per_company_assets, 2), places=2,
			msg=(
				f"trust assets ({trust_assets}) != sum of per-company "
				f"assets ({per_company_assets})"
			),
		)

	def test_get_focused_view_trust_has_children_only_for_groups(self):
		"""Regression: catch the lft/rgt-span has_children bug.

		Before this assertion, trust focus computed `has_children` as
		`MAX(rgt) - MIN(lft) > 1`, which conflates per-company
		nested-set ranges. A leaf account ("Debtors") with
		`is_group=False` could end up with `has_children=True` because
		two companies' Debtors leaves had different (lft, rgt) pairs.
		The fix uses the response's own parent_account graph instead.
		"""
		for entry in self.trust_payload["accounts"]:
			if not entry["is_group"] and entry["has_children"]:
				# A leaf with has_children=True means a non-leaf row
				# elsewhere claims this leaf as its parent. That's
				# data-shape pathological, not a focus-mode bug -- but
				# in practice it shouldn't happen on RGI's COA. If
				# this fires, surface it; do not silently regress.
				children = [
					e["account_name"] for e in self.trust_payload["accounts"]
					if e["parent_account"] == entry["account_name"]
				]
				self.fail(
					f"leaf {entry['account_name']!r} has_children=True; "
					f"reporting parents: {children}"
				)


class TestFocusedViewValidation(FrappeTestCase):
	"""Bad input shapes raise."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_get_focused_view_invalid_scope_raises(self):
		# Bad scope_type
		with self.assertRaises(ValueError):
			focus_v1.get_focused_view(
				scope_type="trust_or_something",
				scope_value="x",
				as_of_date=today(),
			)
		# Empty scope_value
		with self.assertRaises(ValueError):
			focus_v1.get_focused_view(
				scope_type="company",
				scope_value="",
				as_of_date=today(),
			)
		# Unknown company
		with self.assertRaises(frappe.DoesNotExistError):
			focus_v1.get_focused_view(
				scope_type="company",
				scope_value="No Such Company X9Z",
				as_of_date=today(),
			)
		# Unknown trust
		with self.assertRaises(frappe.DoesNotExistError):
			focus_v1.get_focused_view(
				scope_type="trust",
				scope_value="No Such Trust X9Z",
				as_of_date=today(),
			)

	def test_get_focused_view_invalid_scope_sets_malformed_scope_flag(self):
		"""Commit 7 F-12 fix: stale focus deep-links (unknown
		company / trust name) raise `frappe.DoesNotExistError` AND
		set `frappe.local.response["malformed_scope"] = True` so the
		cockpit JS classifier routes to the "This link is no longer
		valid" tile rather than the generic "server" or "network"
		message. Matches the same pattern in
		`cards_v1.resolve_match_to_accounts`.
		"""
		# Unknown company → malformed_scope flag set.
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			focus_v1.get_focused_view(
				scope_type="company",
				scope_value="No Such Company X9Z",
				as_of_date=today(),
			)
		self.assertTrue(
			frappe.local.response.get("malformed_scope"),
			"malformed_scope flag missing on unknown-company raise",
		)
		# Unknown trust → malformed_scope flag set.
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			focus_v1.get_focused_view(
				scope_type="trust",
				scope_value="No Such Trust X9Z",
				as_of_date=today(),
			)
		self.assertTrue(
			frappe.local.response.get("malformed_scope"),
			"malformed_scope flag missing on unknown-trust raise",
		)


class TestFocusedViewSignConvention(FrappeTestCase):
	"""Sign-flip correctness against snapshot raw balances."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_get_focused_view_sign_flip_correct(self):
		"""For each root_type, the focused-view balance must equal the
		expected sign-flipped sum from the snapshot.

		Liability / Equity / Income: returned balance = -(stored Dr-Cr).
		Asset / Expense:             returned balance =  (stored Dr-Cr).
		"""
		payload = focus_v1.get_focused_view(
			scope_type="company",
			scope_value=_TEST_COMPANY,
			as_of_date=today(),
		)
		# For each root_type, compare focused-view leaf-row sums against
		# the raw stored balances on snapshot rows.
		for root_type in ("Asset", "Liability", "Income", "Expense"):
			stored = frappe.db.sql(
				"""
				SELECT COALESCE(SUM(r.balance), 0) AS total
				FROM `tabDGV TB Snapshot Row` r
				JOIN `tabAccount` a ON a.name = r.account
				WHERE r.snapshot_date = %s
				  AND r.company = %s
				  AND a.root_type = %s
				""",
				(getdate(today()), _TEST_COMPANY, root_type),
			)[0][0]
			stored = float(flt(stored))
			expected_signed = (
				-stored if root_type in FLIP_ROOT_TYPES else stored
			)
			view_total = sum(
				a["balance"] for a in payload["accounts"]
				if a["root_type"] == root_type and not a["is_group"]
			)
			self.assertAlmostEqual(
				view_total, round(expected_signed, 2), places=2,
				msg=(
					f"{root_type}: view leaves sum {view_total} != "
					f"expected sign-flipped {expected_signed}"
				),
			)


class TestFocusedViewTilesMatchAccountAggregates(FrappeTestCase):
	"""Independent invariant: tile values = SUM of leaf accounts grouped by root_type."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_get_focused_view_summary_tiles_match_account_aggregates(self):
		payload = focus_v1.get_focused_view(
			scope_type="company",
			scope_value=_TEST_COMPANY,
			as_of_date=today(),
		)
		tiles = payload["summary_tiles"]
		# Tiles aggregate leaf rows (CLAUDE.md hard rule 7); mirror that.
		by_root = {"Asset": 0.0, "Liability": 0.0, "Income": 0.0, "Expense": 0.0}
		for a in payload["accounts"]:
			if a["is_group"]:
				continue
			rt = a["root_type"]
			if rt in by_root:
				by_root[rt] += a["balance"]
		self.assertAlmostEqual(tiles["assets"], round(by_root["Asset"], 2), places=2)
		self.assertAlmostEqual(tiles["liabilities"], round(by_root["Liability"], 2), places=2)
		self.assertAlmostEqual(tiles["income"], round(by_root["Income"], 2), places=2)
		self.assertAlmostEqual(tiles["expenses"], round(by_root["Expense"], 2), places=2)
		self.assertAlmostEqual(
			tiles["net_surplus"],
			round(by_root["Income"] - by_root["Expense"], 2),
			places=2,
		)


class TestFocusedViewCsvExport(FrappeTestCase):
	"""CSV export shape, formatting, and sub-rupee filter."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def _invoke_csv(self, scope_type, scope_value):
		"""Call the endpoint and return the CSV body as a string.

		The endpoint writes to `frappe.local.response.filecontent` rather
		than returning a value, so the test pulls the bytes back from
		the response object.
		"""
		# Reset response surface so prior calls don't leak.
		frappe.local.response = frappe._dict()
		focus_v1.export_focused_view_csv(
			scope_type=scope_type,
			scope_value=scope_value,
			as_of_date=today(),
		)
		raw = frappe.local.response.get("filecontent")
		return raw.decode("utf-8") if raw else ""

	def test_export_focused_view_csv_columns(self):
		body = self._invoke_csv("company", _TEST_COMPANY)
		lines = [ln for ln in body.split("\n") if ln.strip()]
		self.assertGreater(len(lines), 1, "expected header + at least one row")
		self.assertEqual(
			lines[0].strip().rstrip("\r"),
			"Account,Root Type,Depth,Balance",
		)
		# Filename was set on the response too.
		filename = frappe.local.response.get("filename") or ""
		self.assertTrue(
			filename.startswith("focused_view_"),
			f"filename {filename!r} does not start with 'focused_view_'",
		)
		self.assertTrue(filename.endswith(".csv"))

	def test_export_focused_view_csv_raw_decimals_NOT_indian_grouped(self):
		body = self._invoke_csv("company", _TEST_COMPANY)
		# Raw decimals: each balance cell must be of the form `<digits>.<2 digits>`
		# (no commas, no Cr/L suffix, no parens). We don't assert a specific
		# number, just the shape on every non-blank balance cell.
		import csv as csv_mod
		reader = csv_mod.reader(io.StringIO(body))
		rows = list(reader)
		self.assertEqual(rows[0], ["Account", "Root Type", "Depth", "Balance"])
		import re
		balance_pat = re.compile(r"^-?\d+\.\d{2}$")
		nonblank_balance_count = 0
		for row in rows[1:]:
			balance_cell = row[3]
			if not balance_cell:
				continue
			nonblank_balance_count += 1
			self.assertRegex(
				balance_cell, balance_pat,
				f"balance cell {balance_cell!r} not raw-decimal shape",
			)
			self.assertNotIn(",", balance_cell, "Indian grouping leaked into CSV")
		self.assertGreater(
			nonblank_balance_count, 0,
			"expected at least one balance row in the CSV",
		)

	def test_export_focused_view_csv_excludes_subrupee_leaf_rows(self):
		"""Leaf rows with abs(balance) < 1 must NOT appear in the CSV.

		Group rows with no own-row balance (empty Balance cell) DO appear
		and are not subject to the filter.
		"""
		body = self._invoke_csv("company", _TEST_COMPANY)
		import csv as csv_mod
		reader = csv_mod.reader(io.StringIO(body))
		rows = list(reader)
		# No leaf row should have a present-but-tiny balance.
		# (Group rows have empty Balance, those are fine.)
		for row in rows[1:]:
			balance_cell = row[3]
			if not balance_cell:
				continue
			val = float(balance_cell)
			self.assertGreaterEqual(
				abs(val), 1.0,
				f"sub-rupee leaf row leaked into CSV: {row!r}",
			)


# Imports needed by csv-shape tests; placed at module bottom so they
# don't pollute the cleaner top-of-file imports.
import io  # noqa: E402
