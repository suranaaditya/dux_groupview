"""Tests for `gl_drill_v1.get_gl_entries`.

Reuses the party drill fixture (3 companies × 4 accounts × 22 GL
rows) -- the fixture's hand-rolled vouchers map cleanly to GL drill
verification:

  - 3 vouchers on company A's Payable: Asha 500K, Vidarbha 200K,
    Single Co 50K  -> running balance 500K -> 700K -> 750K
  - V003 on B's Payable: 700K standalone
  - V004 on C's Payable: 300K standalone

The fixture's `Net Zero Party` (V009 + V010) and `Future Party`
(V011, posting_date 2150) are NOT exercised here -- GL drill returns
ALL rows including net-zero pairs and only filters by
`posting_date <= as_of_date`. With as_of_date=today(), Future Party
is excluded and Net Zero Party rows surface (the HAVING filter from
party drill is party-aggregate-level, not GL-row-level).

Tests:
  1. pagination_offset            -- offset paginates correctly
  2. pagination_total_count       -- total_entries unaffected by page size
  3. running_balance_correctness  -- exact values vs fixture plan
  4. running_balance_continuous_across_partitions  -- v0.5 dropped the
     PARTITION BY clause; running balance is now scope-wide
  5. truncation_at_50k            -- is_truncated flag (HARD_TRUNCATE_AT
                                     monkeypatched down to 5 to avoid
                                     having to seed 50K rows)
  6. party_filter                 -- party=... narrows to one party
  7. sort_options                 -- amount_* vs posting_date_* reorders

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_gl_drill
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from dux_groupview.dux_groupview.api import gl_drill_v1
from dux_groupview.dux_groupview.tests.fixtures.party_drill_fixture import (
	setup_fixture,
	teardown_fixture,
)


# ---------------------------------------------------------------------------
# Shared fixture lifecycle
# ---------------------------------------------------------------------------

class _GlDrillBase(FrappeTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.state = setup_fixture()

	@classmethod
	def tearDownClass(cls):
		teardown_fixture()
		super().tearDownClass()

	def _payable_leaves(self, *companies):
		"""Payable account full-names for the named companies (default all)."""
		cos = companies or tuple(self.state["companies"])
		return [self.state["accounts"][c]["payable"] for c in cos]

	def _all_fixture_leaves(self):
		"""Every fixture leaf across every fixture company (12 leaves)."""
		out = []
		for c in self.state["companies"]:
			for role in ("receivable", "payable", "bank", "equity"):
				out.append(self.state["accounts"][c][role])
		return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGlDrillPagination(_GlDrillBase):
	"""Tests 1 + 2 -- pagination plumbing."""

	def test_get_gl_entries_pagination_offset(self):
		"""Pages 1 and 2 of size 2 over 3 rows return distinct rows."""
		A = self.state["companies"][0]
		page1 = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(A),
			as_of_date=today(),
			companies=[A],
			page=1, page_size=2,
			sort="posting_date_asc",
		)
		page2 = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(A),
			as_of_date=today(),
			companies=[A],
			page=2, page_size=2,
			sort="posting_date_asc",
		)
		# Total is 3 (Asha + Vidarbha + Single Co Vendor)
		self.assertEqual(page1["total_entries"], 3)
		self.assertEqual(page2["total_entries"], 3)
		# Page sizes
		self.assertEqual(len(page1["entries"]), 2)
		self.assertEqual(len(page2["entries"]), 1)
		# No row appears on both pages
		page1_names = {e["name"] for e in page1["entries"]}
		page2_names = {e["name"] for e in page2["entries"]}
		self.assertEqual(page1_names & page2_names, set())

	def test_get_gl_entries_pagination_total_count(self):
		"""total_entries equals the unpaginated row count regardless of
		page_size/page args."""
		A = self.state["companies"][0]
		full = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(A),
			as_of_date=today(),
			companies=[A],
			page=1, page_size=1000,
		)
		small = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(A),
			as_of_date=today(),
			companies=[A],
			page=1, page_size=1,
		)
		self.assertEqual(full["total_entries"], small["total_entries"])
		self.assertEqual(full["total_entries"], 3)
		# is_truncated is False for both -- well under 50K cap
		self.assertFalse(full["is_truncated"])
		self.assertFalse(small["is_truncated"])
		# scope_fanout exposed in response
		self.assertEqual(full["scope_fanout"]["n_accounts"], 1)
		self.assertEqual(full["scope_fanout"]["n_companies"], 1)


class TestGlDrillRunningBalance(_GlDrillBase):
	"""Tests 3 + 4 -- the load-bearing window-function correctness."""

	def test_get_gl_entries_running_balance_correctness(self):
		"""Hand-verified accumulator vs known fixture totals.

		Company A's Payable account has three vouchers:
		  V002 Asha     500_000
		  V005 Vidarbha 200_000
		  V007 Single   50_000

		Liability sign-flip means signed_amount = credit - debit, so
		each row contributes its credit (positive). Running balance
		partitions by (company, account), orders by (posting_date ASC,
		name ASC). All three rows share posting_date; tie-break is
		voucher_no -> V002 < V005 < V007, so accumulator goes
		500K -> 700K -> 750K.
		"""
		A = self.state["companies"][0]
		out = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(A),
			as_of_date=today(),
			companies=[A],
			page=1, page_size=100,
			sort="posting_date_asc",
		)
		# All three rows on a single page
		self.assertEqual(out["total_entries"], 3)
		entries = out["entries"]
		self.assertEqual(len(entries), 3)

		# Verify by voucher_no -> running_balance map (display sort
		# matches window order in this case so display rows are in
		# accumulator order).
		bal_by_voucher = {e["voucher_no"]: e["running_balance"]
		                  for e in entries}
		self.assertAlmostEqual(
			bal_by_voucher["FIXTURE-PARTY-DRILL-002"], 500_000.0, places=2)
		self.assertAlmostEqual(
			bal_by_voucher["FIXTURE-PARTY-DRILL-005"], 700_000.0, places=2)
		self.assertAlmostEqual(
			bal_by_voucher["FIXTURE-PARTY-DRILL-007"], 750_000.0, places=2)

	def test_get_gl_entries_running_balance_continuous_across_partitions(self):
		"""Spec v0.5: dropped the PARTITION BY clause; running balance
		now accumulates scope-wide in (posting_date, name) ASC order
		regardless of (company, account).

		With (A + B + C) Payable scope, all 6 vouchers share the same
		posting_date so the order is by `g.name` ASC. Voucher-name
		order: V002 < V003 < V004 < V005 < V006 < V007. (Each row's
		full GL Entry name is `<voucher_no>-payable-<ts>-<i>` so the
		voucher_no prefix dominates the ASCII sort.)

		Expected scope-wide cumulative:
		  V002 (A, +500K) -> 500K
		  V003 (B, +700K) -> 1,200K   <-- if partition were still on, this would reset to 700K
		  V004 (C, +300K) -> 1,500K   <-- if partition were still on, this would reset to 300K
		  V005 (A, +200K) -> 1,700K
		  V006 (B, +100K) -> 1,800K
		  V007 (A,  +50K) -> 1,850K

		Final row's running_balance == sum of all signed_amounts.
		"""
		out = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),  # all three companies
			as_of_date=today(),
			companies=self.state["companies"],
			page=1, page_size=100,
			sort="posting_date_asc",
		)

		# Map by voucher_no (unique within payable scope -- one row per voucher)
		bal = {e["voucher_no"]: e["running_balance"] for e in out["entries"]}

		# Scope-wide cumulative -- each row carries the running total
		# of all rows up to and including itself in (date, name) ASC order.
		self.assertAlmostEqual(bal["FIXTURE-PARTY-DRILL-002"],   500_000.0, places=2)
		self.assertAlmostEqual(bal["FIXTURE-PARTY-DRILL-003"], 1_200_000.0, places=2,
			msg="V003 (B) should be A's 500K + B's 700K = 1,200K -- continuous, no partition reset")
		self.assertAlmostEqual(bal["FIXTURE-PARTY-DRILL-004"], 1_500_000.0, places=2,
			msg="V004 (C) should be 500+700+300 = 1,500K -- continuous, no partition reset")
		self.assertAlmostEqual(bal["FIXTURE-PARTY-DRILL-005"], 1_700_000.0, places=2)
		self.assertAlmostEqual(bal["FIXTURE-PARTY-DRILL-006"], 1_800_000.0, places=2)
		self.assertAlmostEqual(bal["FIXTURE-PARTY-DRILL-007"], 1_850_000.0, places=2)

		# Independent invariant: last visible row's running_balance equals
		# the sum of all visible rows' signed_amounts. Holds regardless
		# of order or which test rows changed; catches drift in either
		# the window function or signed_amount sign convention.
		entries_by_name = sorted(out["entries"], key=lambda e: (e["posting_date"], e["name"]))
		expected_total = sum(e["signed_amount"] for e in entries_by_name)
		self.assertAlmostEqual(
			entries_by_name[-1]["running_balance"], expected_total, places=2,
			msg="Last row's running balance should equal sum of all signed_amounts (scope-wide cumulative)",
		)


class TestGlDrillTruncation(_GlDrillBase):
	"""Test 5 -- 50K cap. Monkeypatched to 5 so the fixture's ~22 rows
	are enough to exercise the cap; avoids needing to seed 50K rows
	in CI."""

	def test_get_gl_entries_truncation_at_50k(self):
		# All-fixture-leaves scope across all companies = ~22 rows.
		# With HARD_TRUNCATE_AT=5, we expect is_truncated=True and
		# total_entries to still report the actual count.
		with mock.patch.object(gl_drill_v1, "HARD_TRUNCATE_AT", 5):
			out = gl_drill_v1.get_gl_entries(
				accounts=self._all_fixture_leaves(),
				as_of_date=today(),
				companies=self.state["companies"],
				page=1, page_size=100,
				sort="posting_date_desc",
			)
		# Actual count > 5 -> truncated
		self.assertGreater(out["total_entries"], 5)
		self.assertTrue(out["is_truncated"])
		# Returned entries are <= cap (5). With page_size=100, page 1
		# returns the full capped set.
		self.assertEqual(len(out["entries"]), 5)
		# scope_fanout should reflect 4 leaves * 3 companies = 12.
		self.assertEqual(out["scope_fanout"]["n_accounts"], 12)
		self.assertEqual(out["scope_fanout"]["n_companies"], 3)


class TestGlDrillPartyFilter(_GlDrillBase):
	"""Test 6 -- party arg narrows results."""

	def test_get_gl_entries_party_filter(self):
		"""Filter Payable scope by party=Asha; should return only the
		3 Asha rows (one per company A/B/C, V002/V003/V004)."""
		out = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			party="Asha Stationers",
			party_type="Supplier",
			page=1, page_size=100,
		)
		self.assertEqual(out["total_entries"], 3)
		parties = {e["party"] for e in out["entries"]}
		self.assertEqual(parties, {"Asha Stationers"})
		voucher_nos = {e["voucher_no"] for e in out["entries"]}
		self.assertEqual(voucher_nos, {
			"FIXTURE-PARTY-DRILL-002",
			"FIXTURE-PARTY-DRILL-003",
			"FIXTURE-PARTY-DRILL-004",
		})

		# Without party filter, the same scope returns more rows
		# (Asha + Vidarbha + Single Co = 6 rows total across 3 cos).
		out_unfiltered = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			page=1, page_size=100,
		)
		self.assertGreater(out_unfiltered["total_entries"],
		                   out["total_entries"])


class TestGlDrillSortOptions(_GlDrillBase):
	"""Test 7 -- the four sort keys reorder as advertised."""

	def test_get_gl_entries_sort_options(self):
		A = self.state["companies"][0]
		# A's Payable: V002 (500K), V005 (200K), V007 (50K) -- same
		# posting_date so date sort is by name; amount sort is by ABS.
		def vouchers(sort):
			out = gl_drill_v1.get_gl_entries(
				accounts=self._payable_leaves(A),
				as_of_date=today(),
				companies=[A],
				page=1, page_size=100,
				sort=sort,
			)
			return [e["voucher_no"] for e in out["entries"]]

		# posting_date_asc: V002, V005, V007 (name asc tiebreaker)
		self.assertEqual(vouchers("posting_date_asc"), [
			"FIXTURE-PARTY-DRILL-002",
			"FIXTURE-PARTY-DRILL-005",
			"FIXTURE-PARTY-DRILL-007",
		])
		# posting_date_desc: reversed (name desc tiebreaker)
		self.assertEqual(vouchers("posting_date_desc"), [
			"FIXTURE-PARTY-DRILL-007",
			"FIXTURE-PARTY-DRILL-005",
			"FIXTURE-PARTY-DRILL-002",
		])
		# amount_desc: 500K (V002), 200K (V005), 50K (V007)
		self.assertEqual(vouchers("amount_desc"), [
			"FIXTURE-PARTY-DRILL-002",
			"FIXTURE-PARTY-DRILL-005",
			"FIXTURE-PARTY-DRILL-007",
		])
		# amount_asc: 50K, 200K, 500K
		self.assertEqual(vouchers("amount_asc"), [
			"FIXTURE-PARTY-DRILL-007",
			"FIXTURE-PARTY-DRILL-005",
			"FIXTURE-PARTY-DRILL-002",
		])

		# Unknown sort silently falls back to posting_date_asc (spec v0.4
		# default flip; previously posting_date_desc).
		self.assertEqual(vouchers("nonexistent_sort"),
		                 vouchers("posting_date_asc"))


# ---------------------------------------------------------------------------
# CSV export -- export_gl_entries_csv (HALT 2)
# ---------------------------------------------------------------------------

class TestExportGlEntriesCsv(_GlDrillBase):
	"""HALT 2: full-result CSV export with 50K hard cap.

	Endpoint mutates `frappe.local.response` and returns None; tests
	read filename + filecontent back and parse the CSV body. The
	frappe.local.response is snapshot-and-restored around each call
	so other tests aren't polluted.
	"""

	def _invoke(self, **kwargs):
		"""Default kwargs target all-payable-leaves across all
		fixture cos -- a 6-row dataset that exercises the windowed
		read end-to-end.
		"""
		import frappe as _f
		from frappe import _dict
		default = dict(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
		)
		default.update(kwargs)
		original = dict(getattr(_f.local, "response", {}))
		try:
			_f.local.response = _dict()
			gl_drill_v1.export_gl_entries_csv(**default)
			body = _f.local.response.get("filecontent") or b""
			filename = _f.local.response.get("filename") or ""
			rtype = _f.local.response.get("type") or ""
			body_str = body.decode("utf-8") if isinstance(body, bytes) else str(body)
			return body_str, filename, rtype
		finally:
			_f.local.response = _dict(original)

	def test_export_gl_entries_csv_columns(self):
		"""Header row matches the HALT 2 spec, in order."""
		body, filename, rtype = self._invoke()
		self.assertEqual(rtype, "binary")
		self.assertTrue(filename.startswith("gl_entries_"),
		                msg=f"unexpected filename: {filename}")
		self.assertTrue(filename.endswith(".csv"))
		lines = [ln for ln in body.splitlines() if ln]
		self.assertTrue(lines)
		expected_header = (
			"Posting Date,Voucher Type,Voucher No,Company,Account,"
			"Party Type,Party,Debit,Credit,Running Balance,Remarks"
		)
		self.assertEqual(lines[0], expected_header)
		# All 6 payable rows present (header + 6 data rows)
		self.assertEqual(len(lines), 7)

	def test_export_gl_entries_csv_caps_at_50k(self):
		"""When total_count > 50K, raise via frappe.throw with the
		export-too-large message. Validated by monkeypatching the cap
		down to a small number so the fixture's ~22 rows trip it.
		"""
		from unittest import mock
		with self.assertRaises(frappe.exceptions.ValidationError) as cm:
			with mock.patch.object(gl_drill_v1, "HARD_TRUNCATE_AT", 5):
				self._invoke(accounts=self._all_fixture_leaves())
		# The frappe.throw renders the formatted message into the
		# exception. Check key phrasing.
		msg = str(cm.exception)
		self.assertIn("Scope too large for CSV export", msg)
		self.assertIn("Narrow the scope", msg)

	def test_export_gl_entries_csv_party_filter(self):
		"""Party filter narrows the CSV. Asha Stationers has 3
		(payable, A/B/C) rows in the fixture; without filter the
		payable scope yields 6 rows.
		"""
		body_filtered, _, _ = self._invoke(
			party="Asha Stationers", party_type="Supplier",
		)
		body_unfiltered, _, _ = self._invoke()
		# Count data rows (skip header).
		filtered_rows = len(body_filtered.splitlines()) - 1
		unfiltered_rows = len(body_unfiltered.splitlines()) - 1
		self.assertEqual(filtered_rows, 3,
		                 msg="expected 3 Asha rows in filtered CSV")
		self.assertEqual(unfiltered_rows, 6)
		# Every party cell in filtered CSV is "Asha Stationers".
		import csv as _csv
		import io as _io
		data = list(_csv.reader(_io.StringIO(body_filtered)))[1:]
		party_col = 6  # Posting,Type,No,Co,Acct,PType,Party
		parties = {r[party_col] for r in data}
		self.assertEqual(parties, {"Asha Stationers"})

	def test_export_gl_entries_csv_raw_decimals_NOT_indian_grouped(self):
		"""Numeric cells (debit, credit, running_balance) contain
		raw decimals like '500000.00', NOT Indian-grouped strings
		like '5,00,000.00'. CSV is data-interchange; spreadsheet
		apps reformat per locale on import.
		"""
		import csv as _csv
		import io as _io
		body, _, _ = self._invoke()
		rows = list(_csv.reader(_io.StringIO(body)))
		header = rows[0]
		data = rows[1:]
		debit_col = header.index("Debit")
		credit_col = header.index("Credit")
		running_col = header.index("Running Balance")
		self.assertTrue(data, msg="expected at least one data row")
		for r in data:
			for col_name, idx in (("Debit", debit_col),
			                       ("Credit", credit_col),
			                       ("Running Balance", running_col)):
				cell = r[idx]
				self.assertNotIn(",", cell,
					msg=f"{col_name} cell {cell!r} contains a comma -- "
					    f"looks Indian-grouped")
				try:
					float(cell)
				except ValueError:
					self.fail(
						f"{col_name} cell {cell!r} does not parse as a float"
					)

	def test_export_gl_entries_csv_iso_dates(self):
		"""Posting-date cells match YYYY-MM-DD."""
		import csv as _csv
		import io as _io
		import re as _re
		body, _, _ = self._invoke()
		rows = list(_csv.reader(_io.StringIO(body)))
		header = rows[0]
		data = rows[1:]
		date_col = header.index("Posting Date")
		iso_pattern = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
		for r in data:
			cell = r[date_col]
			self.assertRegex(cell, iso_pattern,
				msg=f"Posting date {cell!r} not in YYYY-MM-DD form")


# ---------------------------------------------------------------------------
# HALT 2.5 -- New filters (account_names, from_date, to_date, voucher_types)
# ---------------------------------------------------------------------------

class TestGlDrillFilters(_GlDrillBase):
	"""HALT 2.5: per-spec §3 filter set + §6 endpoint extensions.

	Fixture refresher:
	  - 3 fixture companies each have 4 leaves (receivable, payable,
	    bank, equity).
	  - 6 payable rows (one per voucher V002-V007 across A/B/C);
	    voucher_type="DGV Test Seed" for all fixture vouchers.
	  - All vouchers share posting_date = today() - 30 days, except
	    V011 Future Party at FUTURE_POSTING_DATE (2150-06-15).
	"""

	def _fixture_pd(self):
		"""Return the fixture's primary posting_date (today - 30d)."""
		from datetime import timedelta
		from frappe.utils import getdate
		return getdate(today()) - timedelta(days=30)

	def test_get_gl_entries_account_names_filter(self):
		"""account_names=['FXT Payable'] returns only payable rows
		from the all-leaves scope (drops Receivable/Bank/Equity)."""
		out_unfiltered = gl_drill_v1.get_gl_entries(
			accounts=self._all_fixture_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			page=1, page_size=500,
		)
		out_filtered = gl_drill_v1.get_gl_entries(
			accounts=self._all_fixture_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			account_names=["FXT Payable"],
			page=1, page_size=500,
		)
		self.assertGreater(out_unfiltered["total_entries"],
		                   out_filtered["total_entries"])
		# All filtered rows have account_name = "FXT Payable"
		# (account column is full-suffixed; each fixture company's
		# payable leaf is "FXT Payable - <abbr>").
		for e in out_filtered["entries"]:
			self.assertTrue(
				e["account"].startswith("FXT Payable"),
				msg=f"non-payable row leaked through filter: {e['account']}",
			)
		# filter_state echo confirms the server normalised the input.
		self.assertEqual(out_filtered["filter_state"]["account_names"],
		                 ["FXT Payable"])

	def test_get_gl_entries_from_date_filter(self):
		"""from_date excludes rows older than the cutoff. Fixture has
		all rows on a single date, so from_date > that date returns 0
		while from_date <= that date returns the full set."""
		from datetime import timedelta
		fixture_pd = self._fixture_pd()
		# from_date = 1 day after fixture posting_date -> excludes all
		# fixture rows that are not Future Party (Future Party is
		# also excluded because today() upper bound is well before
		# 2150).
		out_excluded = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			from_date=str(fixture_pd + timedelta(days=1)),
			page=1, page_size=500,
		)
		self.assertEqual(out_excluded["total_entries"], 0)
		# from_date = fixture posting_date -> all rows still in
		out_included = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			from_date=str(fixture_pd),
			page=1, page_size=500,
		)
		self.assertEqual(out_included["total_entries"], 6)
		self.assertEqual(out_included["filter_state"]["from_date"],
		                 fixture_pd.isoformat())

	def test_get_gl_entries_to_date_filter(self):
		"""to_date excludes rows newer than the cutoff."""
		from datetime import timedelta
		fixture_pd = self._fixture_pd()
		# to_date = 1 day before fixture posting_date -> excludes all
		out_excluded = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			to_date=str(fixture_pd - timedelta(days=1)),
			page=1, page_size=500,
		)
		self.assertEqual(out_excluded["total_entries"], 0)
		# to_date = fixture posting_date -> all 6 rows in
		out_included = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			to_date=str(fixture_pd),
			page=1, page_size=500,
		)
		self.assertEqual(out_included["total_entries"], 6)
		self.assertFalse(out_included["clamped_to_date"])

	def test_get_gl_entries_to_date_clamps_to_as_of_date(self):
		"""to_date > as_of_date silently clamps; clamped_to_date=True
		flagged in the response."""
		from datetime import timedelta
		far_future = str(getdate(today()) + timedelta(days=365))
		out = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			to_date=far_future,
			page=1, page_size=500,
		)
		# Same row count as no to_date -- the clamp made the filter
		# a no-op (effective_to_date == as_of_date).
		out_no_to = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			page=1, page_size=500,
		)
		self.assertEqual(out["total_entries"], out_no_to["total_entries"])
		self.assertTrue(out["clamped_to_date"],
			msg="to_date > as_of_date should set clamped_to_date=True")

	def test_get_gl_entries_voucher_types_filter(self):
		"""voucher_types narrows the result. Fixture all have
		voucher_type='DGV Test Seed'; filtering to a non-existent
		type returns 0; filtering to the actual type returns all
		fixture rows."""
		# Non-existent voucher type
		out_zero = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			voucher_types=["NonExistentVoucherType"],
			page=1, page_size=500,
		)
		self.assertEqual(out_zero["total_entries"], 0)
		# Actual voucher type
		out_all = gl_drill_v1.get_gl_entries(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			voucher_types=["DGV Test Seed"],
			page=1, page_size=500,
		)
		self.assertEqual(out_all["total_entries"], 6)
		# voucher_types_in_scope echoes the unfiltered universe so the
		# UI dropdown shows what's available even when narrowed.
		self.assertIn("DGV Test Seed", out_all["voucher_types_in_scope"])

	def test_get_gl_entries_filters_combined(self):
		"""Apply account_names + voucher_types together: result is
		the intersection."""
		out = gl_drill_v1.get_gl_entries(
			accounts=self._all_fixture_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
			account_names=["FXT Payable"],
			voucher_types=["DGV Test Seed"],
			page=1, page_size=500,
		)
		# Same 6 payable rows from the account_names test -- both
		# filters happen to align on the fixture data, but the test
		# verifies the SQL combines AND-style.
		self.assertEqual(out["total_entries"], 6)
		for e in out["entries"]:
			self.assertTrue(e["account"].startswith("FXT Payable"))
			self.assertEqual(e["voucher_type"], "DGV Test Seed")
		# All four filter slots echoed in filter_state
		self.assertEqual(out["filter_state"]["account_names"], ["FXT Payable"])
		self.assertEqual(out["filter_state"]["voucher_types"], ["DGV Test Seed"])
		self.assertIsNone(out["filter_state"]["from_date"])
		self.assertIsNone(out["filter_state"]["to_date"])


# ---------------------------------------------------------------------------
# HALT 2.5 -- Export CSV honors new filters + filename _filtered segment
# ---------------------------------------------------------------------------

class TestExportGlEntriesCsvFilters(_GlDrillBase):
	"""HALT 2.5: export_gl_entries_csv carries the same filter
	semantics as get_gl_entries; filename gets `_filtered` infix."""

	def _invoke(self, **kwargs):
		from frappe import _dict
		default = dict(
			accounts=self._payable_leaves(),
			as_of_date=today(),
			companies=self.state["companies"],
		)
		default.update(kwargs)
		original = dict(getattr(frappe.local, "response", {}))
		try:
			frappe.local.response = _dict()
			gl_drill_v1.export_gl_entries_csv(**default)
			body = frappe.local.response.get("filecontent") or b""
			filename = frappe.local.response.get("filename") or ""
			body_str = body.decode("utf-8") if isinstance(body, bytes) else str(body)
			return body_str, filename
		finally:
			frappe.local.response = _dict(original)

	def test_export_gl_entries_csv_honors_filters(self):
		"""account_names filter applied to CSV: row count matches
		the filtered get_gl_entries."""
		body_unfiltered, _ = self._invoke()
		body_filtered, _ = self._invoke(
			accounts=self._all_fixture_leaves(),
			account_names=["FXT Payable"],
		)
		# Header + N data rows; subtract header
		unfiltered_rows = len(body_unfiltered.splitlines()) - 1
		filtered_rows = len(body_filtered.splitlines()) - 1
		# Unfiltered payable scope = 6 rows; account_names-filtered
		# all-leaves scope also = 6 rows (only payable leaves match).
		self.assertEqual(unfiltered_rows, 6)
		self.assertEqual(filtered_rows, 6)
		# Same row count, but the filtered filename should differ
		# (presence of the `_filtered_` segment).

	def test_export_gl_entries_csv_filename_includes_filtered_segment(self):
		"""Filename has `_filtered_` infix when ANY of the four HALT
		2.5 filter slots is non-default. Companies + party are scope
		concerns and don't trip the marker per spec §8."""
		# No filters -> no marker
		_, filename_plain = self._invoke()
		self.assertIn("gl_entries_", filename_plain)
		self.assertNotIn("_filtered_", filename_plain,
			msg=f"filename should not have _filtered_ when no filters: {filename_plain}")

		# account_names active -> marker present
		_, filename_an = self._invoke(account_names=["FXT Payable"])
		self.assertIn("_filtered_", filename_an,
			msg=f"filename should include _filtered_ with account_names filter: {filename_an}")

		# voucher_types active -> marker present
		_, filename_vt = self._invoke(voucher_types=["DGV Test Seed"])
		self.assertIn("_filtered_", filename_vt,
			msg=f"filename should include _filtered_ with voucher_types filter: {filename_vt}")

		# from_date active -> marker present
		from datetime import timedelta
		_, filename_fd = self._invoke(
			from_date=str(self._fixture_pd() - timedelta(days=10)),
		)
		self.assertIn("_filtered_", filename_fd,
			msg=f"filename should include _filtered_ with from_date filter: {filename_fd}")

		# party active alone -> marker should NOT appear (party is a
		# scope concern per spec §8, not a HALT 2.5 filter).
		_, filename_party = self._invoke(
			party="Asha Stationers", party_type="Supplier",
		)
		self.assertNotIn("_filtered_", filename_party,
			msg=f"filename should NOT include _filtered_ for party-only narrowing: {filename_party}")

	def _fixture_pd(self):
		from datetime import timedelta
		return getdate(today()) - timedelta(days=30)


class TestGlDrillFilterMetadataCompaniesInScope(_GlDrillBase):
	"""Commit 7 F-11: get_filter_metadata now returns the explicit
	companies-in-scope list so the GL drill page can render the
	COMPANIES filter dropdown even when the URL has no `companies=`
	param. Pre-fix the dropdown hid entirely under that condition,
	violating commit-4 spec §3.1 visibility rule.
	"""

	def test_filter_metadata_returns_companies_in_scope(self):
		# Use the fixture's 3 companies as the scope (no companies=
		# arg → server uses permission-allowed set, which on the
		# test runner is "all"; we narrow via the accounts list).
		out = gl_drill_v1.get_filter_metadata(
			accounts=self._payable_leaves(),
			as_of_date=today(),
		)
		self.assertIn("companies_in_scope", out)
		self.assertIsInstance(out["companies_in_scope"], list)
		# Server returns ALL permission-allowed companies regardless
		# of which the accounts list spans -- that's the "scope
		# universe" the filter dropdown needs.
		self.assertGreaterEqual(len(out["companies_in_scope"]), 1)
		for c in out["companies_in_scope"]:
			self.assertIsInstance(c, str)
			self.assertTrue(c, "company name should be non-empty")

	def test_filter_metadata_companies_in_scope_matches_n_companies(self):
		out = gl_drill_v1.get_filter_metadata(
			accounts=self._payable_leaves(),
			as_of_date=today(),
		)
		# The list length must match the scope_fanout count -- pins
		# the invariant that the visibility check on the client
		# (n_companies > 1) and the universe-populate logic
		# (len(companies_in_scope) > 1) agree.
		self.assertEqual(
			len(out["companies_in_scope"]),
			out["scope_fanout"]["n_companies"],
		)
