"""Tests for the spotlight cache layer.

Runs against the seeded Phase 0 data and the Phase 1 snapshot
foundation. Writes only to DGV TB Snapshot, DGV TB Snapshot Row, and
DGV Spotlight Cache. Never touches tabGL Entry.

The gold-standard test (test_spotlight_value_matches_direct_aggregation)
is the load-bearing one for this phase. If it ever fails, the cards on
the cockpit are lying.

Run with:
    bench --site erp.jewonline.in run-tests --module \
        dux_groupview.dux_groupview.tests.test_spotlight
"""

import json
import time

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
from dux_groupview.dux_groupview.snapshots.backfill import backfill_snapshots
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	CARD_DEFINITION_HASH,
	refresh_spotlight_cache,
)
from dux_groupview.dux_groupview.spotlight.cards import CARDS, by_id


def _purge_all():
	"""Wipe all cache + snapshot tables for a clean slate."""
	frappe.db.sql("DELETE FROM `tabDGV Spotlight Cache`")
	frappe.db.sql("DELETE FROM `tabDGV TB Snapshot Row`")
	frappe.db.sql("DELETE FROM `tabDGV TB Snapshot`")
	frappe.db.commit()


class TestSpotlightCache(FrappeTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_purge_all()
		# A single TB snapshot is enough for most tests; backfill-aware
		# tests refresh inside themselves.
		refresh_tb_snapshot()

	@classmethod
	def tearDownClass(cls):
		_purge_all()
		super().tearDownClass()

	def setUp(self):
		# Each test starts with a clean spotlight cache; TB stays.
		frappe.db.sql("DELETE FROM `tabDGV Spotlight Cache`")
		frappe.db.commit()

	# ------------------------------------------------------------------
	# 1 -- six rows created
	# ------------------------------------------------------------------

	def test_refresh_spotlight_cache_creates_six_rows(self):
		refresh_spotlight_cache()
		rows = frappe.db.sql(
			"""
			SELECT card_id, card_definition_hash
			FROM `tabDGV Spotlight Cache`
			WHERE snapshot_date = %s
			""",
			(getdate(today()),),
			as_dict=True,
		)
		self.assertEqual(len(rows), 6)
		ids = sorted(r["card_id"] for r in rows)
		self.assertEqual(ids, sorted(c["id"] for c in CARDS))
		for r in rows:
			self.assertEqual(r["card_definition_hash"], CARD_DEFINITION_HASH)

	# ------------------------------------------------------------------
	# 2 -- idempotent
	# ------------------------------------------------------------------

	def test_refresh_spotlight_cache_idempotent(self):
		refresh_spotlight_cache()
		first = {
			r["name"]: r["computed_at"]
			for r in frappe.db.sql(
				"SELECT name, computed_at FROM `tabDGV Spotlight Cache`",
				as_dict=True,
			)
		}
		# Make sure clock advances at least 1 second so computed_at
		# differs measurably.
		time.sleep(1.1)
		refresh_spotlight_cache()
		second = {
			r["name"]: r["computed_at"]
			for r in frappe.db.sql(
				"SELECT name, computed_at FROM `tabDGV Spotlight Cache`",
				as_dict=True,
			)
		}
		# Same set of rows (no duplicates).
		self.assertEqual(set(first.keys()), set(second.keys()))
		self.assertEqual(len(second), 6)
		# Each row was actually re-run (computed_at advanced).
		for name in first:
			self.assertGreater(second[name], first[name])

	# ------------------------------------------------------------------
	# 3 -- gold standard correctness
	# ------------------------------------------------------------------

	def test_spotlight_value_matches_direct_aggregation(self):
		"""For each card, the cached value must equal an independent SQL
		aggregation against tabDGV TB Snapshot Row using the card's match
		rule and the same sign-flip convention.
		"""
		refresh_spotlight_cache()
		snapshot_date = getdate(today())

		for card in CARDS:
			cached = frappe.db.get_value(
				"DGV Spotlight Cache",
				{"card_id": card["id"], "snapshot_date": snapshot_date},
				"value",
			)
			expected = self._directly_aggregate(card, snapshot_date)
			self.assertAlmostEqual(
				float(cached or 0), expected, places=2,
				msg=(
					f"Card {card['id']}: cached value {cached} does not "
					f"match direct aggregation {expected}."
				),
			)

	def _directly_aggregate(self, card, snapshot_date):
		"""Independent re-aggregation, intentionally using a different
		query path than the production code (raw SQL with explicit
		WHERE/CASE rather than the strategy-dispatched code).
		"""
		match = card["match"]
		if "by_account_type" in match:
			v = match["by_account_type"]
			if isinstance(v, (list, tuple)):
				placeholders = ", ".join(["%s"] * len(v))
				where = f"account_type IN ({placeholders})"
				params = list(v) + [snapshot_date]
			else:
				where = "account_type = %s"
				params = [v, snapshot_date]
		elif "by_root_type_and_name_pattern" in match:
			conf = match["by_root_type_and_name_pattern"]
			where = "root_type = %s AND account LIKE %s"
			params = [conf["root_type"], conf["name_pattern"], snapshot_date]
		else:
			return 0.0

		rows = frappe.db.sql(
			f"""
			SELECT COALESCE(SUM(
				CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
				     THEN -balance
				     ELSE balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row`
			WHERE ({where}) AND snapshot_date = %s
			""",
			params,
		)
		return round(float(rows[0][0] or 0), 2)

	# ------------------------------------------------------------------
	# 4 -- sparkline format
	# ------------------------------------------------------------------

	def test_sparkline_data_format(self):
		# Need historical snapshots to produce a non-empty sparkline.
		backfill_snapshots(months_back=3)
		refresh_spotlight_cache()
		raw = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "sundry_creditors", "snapshot_date": getdate(today())},
			"sparkline_data",
		)
		parsed = json.loads(raw)
		self.assertIsInstance(parsed, list)
		self.assertEqual(len(parsed), 6)
		# Should have at least the 3 backfilled month-ends; older 3 may
		# be None depending on whether the dev site has older snapshots.
		non_null = [v for v in parsed if v is not None]
		self.assertGreaterEqual(len(non_null), 3)

	# ------------------------------------------------------------------
	# 5 -- delta calculation
	# ------------------------------------------------------------------

	def test_delta_calculation(self):
		"""With a backfill in place, today's delta for each card should
		equal today_value - prior_month_value.
		"""
		backfill_snapshots(months_back=2)
		# At this point the spotlight cache for each backfilled date has
		# already been written by backfill_snapshots. Today's value is a
		# fresh refresh.
		refresh_spotlight_cache()

		snapshot_date = getdate(today())
		# Find the prior-month date the way the refresh function does.
		from datetime import date as _date
		first_of_month = _date(snapshot_date.year, snapshot_date.month, 1)
		prior_month = frappe.db.sql(
			"SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot` "
			"WHERE snapshot_date < %s AND status = 'Complete'",
			(first_of_month,),
		)[0][0]
		self.assertIsNotNone(prior_month, "Backfill should have provided a prior month")

		for card in CARDS:
			today_value = float(frappe.db.get_value(
				"DGV Spotlight Cache",
				{"card_id": card["id"], "snapshot_date": snapshot_date},
				"value",
			) or 0)
			prior_value = float(frappe.db.get_value(
				"DGV Spotlight Cache",
				{"card_id": card["id"], "snapshot_date": prior_month},
				"value",
			) or 0)
			cached_delta = float(frappe.db.get_value(
				"DGV Spotlight Cache",
				{"card_id": card["id"], "snapshot_date": snapshot_date},
				"delta",
			) or 0)
			self.assertAlmostEqual(
				cached_delta, today_value - prior_value, places=2,
				msg=f"Delta mismatch for {card['id']}",
			)

	# ------------------------------------------------------------------
	# 6 -- zero match returns zero
	# ------------------------------------------------------------------

	def test_zero_match_card_returns_zero(self):
		refresh_spotlight_cache()
		# fixed_deposits has no matching accounts on the dev site --
		# verified empirically. (unsecured_loans and inter_co_receivable
		# turn out to match real accounts in the jewonline dev data.)
		row = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "fixed_deposits", "snapshot_date": getdate(today())},
			["value", "delta"],
			as_dict=True,
		)
		self.assertIsNotNone(row)
		self.assertEqual(float(row["value"]), 0.0)
		self.assertEqual(float(row["delta"]), 0.0)

	# ------------------------------------------------------------------
	# 7 -- polarity is UI-only metadata
	# ------------------------------------------------------------------

	def test_polarity_does_not_affect_value(self):
		"""Polarity is purely UI metadata. Computing the same matcher
		twice -- once via a card with polarity good_up, once via a card
		with polarity neutral -- must yield identical aggregations.
		"""
		# We can verify this purely from the matcher: refresh produces
		# values based on match rules + sign convention only. Sundry
		# debtors (bad_up) and Sundry creditors (neutral) use different
		# match rules, so we can't compare them directly. Instead we
		# synthesise: build two pretend-cards with identical match rules
		# but different polarity, and aggregate them.
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import _aggregate

		card_a = {
			"id": "synthetic_a",
			"match": {"by_account_type": "Payable"},
			"polarity": "good_up",
		}
		card_b = {
			"id": "synthetic_b",
			"match": {"by_account_type": "Payable"},
			"polarity": "neutral",
		}
		val_a = _aggregate(card_a, getdate(today()))
		val_b = _aggregate(card_b, getdate(today()))
		self.assertEqual(val_a, val_b)
