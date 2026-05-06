"""Tests for the snapshot refresh layer.

These tests run against the seeded synthetic data already on the dev
site (5 companies, 50K GL entries). They write to DGV TB Snapshot /
DGV TB Snapshot Row but never to tabGL Entry, in keeping with the
CLAUDE.md hard rule.

The gold-standard test (test_refresh_aggregations_match_gl_entry) is
the one that proves the cache is honest. If it ever fails, the entire
premise of the cockpit is broken.

Run with:
    bench --site erp.jewonline.in run-tests --module \
        dux_groupview.dux_groupview.tests.test_refresh
"""

from datetime import date, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from dux_groupview.dux_groupview.snapshots import backfill as backfill_module
from dux_groupview.dux_groupview.snapshots.refresh import (
	finalize_past_snapshots,
	refresh_tb_snapshot,
)
from dux_groupview.dux_groupview.snapshots.backfill import backfill_snapshots


def _purge_snapshots():
	"""Clean slate -- remove all existing snapshots and rows."""
	frappe.db.sql("DELETE FROM `tabDGV TB Snapshot Row`")
	frappe.db.sql("DELETE FROM `tabDGV TB Snapshot`")
	frappe.db.commit()


class TestRefresh(FrappeTestCase):

	# ------------------------------------------------------------------
	# Setup / teardown
	# ------------------------------------------------------------------

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_purge_snapshots()

	@classmethod
	def tearDownClass(cls):
		_purge_snapshots()
		super().tearDownClass()

	def setUp(self):
		# Ensure each test starts with a clean snapshot table.
		_purge_snapshots()

	# ------------------------------------------------------------------
	# 1 -- creates a snapshot
	# ------------------------------------------------------------------

	def test_refresh_creates_snapshot(self):
		result = refresh_tb_snapshot()

		today_d = getdate(today())
		self.assertEqual(result["snapshot_date"], str(today_d))
		self.assertEqual(result["status"], "Complete")
		self.assertGreater(result["duration_seconds"], 0)

		# Duration target branches by seed size. Three tiers since the
		# side PR ("seed scale for KVM") that introduced the
		# trust-subset RGI seed -- a ~1.1M-row dev/staging seed sits
		# between the synthetic-tiny (50K) and full-RGI (5M) shapes.
		# Per PHASE_LOG.md:
		#   - production-scale (>2M rows): 60s, post Phase 3
		#     covering-index + subquery-restructure optimisation
		#   - dev/staging-scale (>100K rows): 30s, the trust-subset
		#     RGI seed produced by `seed_rgi_named_data(trusts=[...])`
		#   - synthetic/CI-tiny (<=100K rows): 15s, Phase 1 target
		#     (seed_light or unseeded sites)
		# All tiers stay enforced; no environment is silently exempted.
		gl_count = frappe.db.count("GL Entry")
		if gl_count > 2_000_000:
			threshold = 60.0
		elif gl_count > 100_000:
			threshold = 30.0
		else:
			threshold = 15.0
		self.assertLess(
			result["duration_seconds"], threshold,
			f"refresh took {result['duration_seconds']:.1f}s with "
			f"{gl_count:,} GL entries; target was < {threshold:.0f}s "
			f"(branched on row-count thresholds 2,000,000 / 100,000)",
		)

		# Parent record exists with expected status.
		parent = frappe.get_doc("DGV TB Snapshot", result["snapshot_name"])
		self.assertEqual(parent.status, "Complete")
		self.assertEqual(parent.snapshot_date, today_d)

		# row_count on the parent matches actual count of child rows.
		actual = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabDGV TB Snapshot Row` "
			"WHERE parent_snapshot = %s",
			(parent.name,),
		)[0][0]
		self.assertEqual(parent.row_count, actual)
		self.assertEqual(result["row_count"], actual)
		self.assertGreater(actual, 0)

	# ------------------------------------------------------------------
	# 2 -- gold standard: snapshot matches tabGL Entry exactly
	# ------------------------------------------------------------------

	def test_refresh_aggregations_match_gl_entry(self):
		"""For every snapshot row, balance must equal SUM(debit-credit)
		from tabGL Entry for the same (company, account, posting_date).

		If this test ever fails, the cache layer is dishonest and the
		entire cockpit's read path is unreliable.
		"""
		refresh_tb_snapshot()

		mismatches = frappe.db.sql(
			"""
			SELECT r.company, r.account, r.balance, r.debit_total, r.credit_total,
			       (
			         SELECT COALESCE(ROUND(SUM(gl.debit) - SUM(gl.credit), 2), 0)
			         FROM `tabGL Entry` gl
			         WHERE gl.company = r.company
			           AND gl.account = r.account
			           AND gl.posting_date <= r.snapshot_date
			           AND gl.is_cancelled = 0
			           AND gl.docstatus = 1
			       ) AS expected_balance
			FROM `tabDGV TB Snapshot Row` r
			WHERE r.balance != (
			         SELECT COALESCE(ROUND(SUM(gl.debit) - SUM(gl.credit), 2), 0)
			         FROM `tabGL Entry` gl
			         WHERE gl.company = r.company
			           AND gl.account = r.account
			           AND gl.posting_date <= r.snapshot_date
			           AND gl.is_cancelled = 0
			           AND gl.docstatus = 1
			       )
			""",
			as_dict=True,
		)
		self.assertEqual(
			mismatches, [],
			f"Gold-standard correctness failed -- {len(mismatches)} rows do "
			f"not match tabGL Entry. Sample: {mismatches[:3]}"
		)

		# Also verify the invariant: balance = debit_total - credit_total.
		invariant_violations = frappe.db.sql(
			"""
			SELECT name, balance, debit_total, credit_total
			FROM `tabDGV TB Snapshot Row`
			WHERE ABS(balance - (debit_total - credit_total)) > 0.01
			""",
			as_dict=True,
		)
		self.assertEqual(
			invariant_violations, [],
			f"Invariant balance = debit_total - credit_total violated in "
			f"{len(invariant_violations)} rows."
		)

	# ------------------------------------------------------------------
	# 3 -- idempotent re-run
	# ------------------------------------------------------------------

	def test_refresh_idempotent(self):
		first = refresh_tb_snapshot()
		first_balances = self._read_balances(first["snapshot_name"])

		second = refresh_tb_snapshot()
		second_balances = self._read_balances(second["snapshot_name"])

		self.assertEqual(first["snapshot_name"], second["snapshot_name"])
		self.assertEqual(first["row_count"], second["row_count"])
		self.assertEqual(first_balances, second_balances)

	def _read_balances(self, snapshot_name):
		"""Return a sorted (company, account, balance) tuple list."""
		rows = frappe.db.sql(
			"""
			SELECT company, account, balance
			FROM `tabDGV TB Snapshot Row`
			WHERE parent_snapshot = %s
			ORDER BY company, account
			""",
			(snapshot_name,),
			as_dict=False,
		)
		return rows

	# ------------------------------------------------------------------
	# 4 -- immutable protection
	# ------------------------------------------------------------------

	def test_refresh_immutable_protection(self):
		first = refresh_tb_snapshot()
		frappe.db.set_value(
			"DGV TB Snapshot", first["snapshot_name"], "is_immutable", 1
		)
		frappe.db.commit()

		with self.assertRaises(frappe.ValidationError):
			refresh_tb_snapshot()

	# ------------------------------------------------------------------
	# 5 -- backfill creates N snapshots
	# ------------------------------------------------------------------

	def test_backfill_creates_n_snapshots(self):
		# force=True bypasses the SAFETY_ROW_THRESHOLD check, which trips
		# at 12 × COUNT(tabGL Entry) > 10M on the RGI-DEMO production-
		# shaped seed (5M rows × 3 months = 15.2M estimated). This test
		# only verifies snapshot count + status, so force=True is the
		# minimal change to make the test pass on prod-scale data
		# without altering its intent. (For tests that actually
		# exercise the without-force code paths, see
		# test_backfill_skips_immutable / test_backfill_force_override
		# which monkey-patch SAFETY_ROW_THRESHOLD instead.)
		result = backfill_snapshots(months_back=3, force=True)

		self.assertEqual(len(result["dates_processed"]), 3)
		for entry in result["dates_processed"]:
			self.assertEqual(entry["status"], "Complete")

		# All three snapshots should exist with status Complete.
		dates_in_db = frappe.db.sql(
			"""
			SELECT snapshot_date FROM `tabDGV TB Snapshot`
			WHERE status = 'Complete'
			ORDER BY snapshot_date
			""",
		)
		self.assertEqual(len(dates_in_db), 3)

	# ------------------------------------------------------------------
	# 6 -- backfill skips immutable
	# ------------------------------------------------------------------

	def test_backfill_skips_immutable(self):
		# This test exercises the immutable-skip path in
		# backfill_snapshots, which only fires when force is False.
		# We can't simply pass force=True to bypass the prod-scale
		# safety check because that would also disable the very
		# behaviour under test (force=True regenerates immutable
		# snapshots; force=False skips them). Monkey-patch the
		# SAFETY_ROW_THRESHOLD constant for this test only so both
		# calls run with force=False, preserving test intent.
		with patch.object(
			backfill_module, "SAFETY_ROW_THRESHOLD", 10**12
		):
			# First pass: backfill 3 months.
			first_result = backfill_snapshots(months_back=3)
			first_dates = sorted(e["date"] for e in first_result["dates_processed"])
			self.assertEqual(len(first_dates), 3)

			# All three should now be immutable (because date < today).
			immutable_count = frappe.db.count(
				"DGV TB Snapshot", {"is_immutable": 1}
			)
			self.assertEqual(immutable_count, 3)

			# Capture the original generated_at timestamps.
			original = {
				row["snapshot_date"]: row["generated_at"]
				for row in frappe.db.sql(
					"SELECT snapshot_date, generated_at "
					"FROM `tabDGV TB Snapshot`",
					as_dict=True,
				)
			}

			# Second pass without force -- should skip all 3.
			result = backfill_snapshots(months_back=3)
			self.assertEqual(len(result["skipped"]), 3)
			self.assertEqual(len(result["dates_processed"]), 0)

			# generated_at unchanged for all 3.
			for row in frappe.db.sql(
				"SELECT snapshot_date, generated_at "
				"FROM `tabDGV TB Snapshot`",
				as_dict=True,
			):
				self.assertEqual(
					row["generated_at"], original[row["snapshot_date"]],
					"Immutable snapshot should not be regenerated."
				)

	# ------------------------------------------------------------------
	# 7 -- backfill force override
	# ------------------------------------------------------------------

	def test_backfill_force_override(self):
		# The seeding call (first backfill_snapshots below) runs without
		# force, so it would trip SAFETY_ROW_THRESHOLD on the prod-scale
		# RGI-DEMO seed before any of the test logic runs. Monkey-patch
		# the threshold so only the seeding call bypasses safety; the
		# subsequent force=True call (already explicitly tested) is
		# what actually exercises the override path. The patch covers
		# the whole test for simplicity, but the semantics under test
		# are unchanged: force=True must regenerate immutable snapshots.
		with patch.object(
			backfill_module, "SAFETY_ROW_THRESHOLD", 10**12
		):
			# Initial backfill creates 3 snapshots, all marked immutable.
			first_result = backfill_snapshots(months_back=3)
			self.assertEqual(len(first_result["dates_processed"]), 3)

			# Capture original generated_at.
			original = {
				row["snapshot_date"]: row["generated_at"]
				for row in frappe.db.sql(
					"SELECT snapshot_date, generated_at "
					"FROM `tabDGV TB Snapshot`",
					as_dict=True,
				)
			}

			# Force re-backfill -- generated_at should change for all.
			# Sleep a sub-second to ensure NOW() changes.
			import time
			time.sleep(1.1)

			forced = backfill_snapshots(months_back=3, force=True)
			self.assertEqual(len(forced["dates_processed"]), 3)
			self.assertEqual(len(forced["skipped"]), 0)

			for row in frappe.db.sql(
				"SELECT snapshot_date, generated_at "
				"FROM `tabDGV TB Snapshot`",
				as_dict=True,
			):
				self.assertNotEqual(
					row["generated_at"], original[row["snapshot_date"]],
					"force=True should regenerate immutable snapshots "
					"(generated_at expected to advance)."
				)
