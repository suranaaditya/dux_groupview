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
	SPARKLINE_LENGTH,
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
	# 1 -- one row per card
	# ------------------------------------------------------------------

	def test_refresh_spotlight_cache_creates_one_row_per_card(self):
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
		self.assertEqual(len(rows), len(CARDS))
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
		self.assertEqual(len(second), len(CARDS))
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

		Handles all three input shapes of `by_account_type`:
		  - str:  legacy shortcut, no sign filter
		  - list: legacy shortcut, no sign filter
		  - dict: canonical form with optional balance_sign (extended
		          by spec/supplier-advances-split)
		"""
		match = card["match"]
		if "by_account_type" in match:
			v = match["by_account_type"]
			balance_sign_clause = ""
			if isinstance(v, str):
				where = "account_type = %s"
				params = [v, snapshot_date]
			elif isinstance(v, (list, tuple)):
				placeholders = ", ".join(["%s"] * len(v))
				where = f"account_type IN ({placeholders})"
				params = list(v) + [snapshot_date]
			elif isinstance(v, dict):
				at = v["account_type"]
				if isinstance(at, (list, tuple)):
					placeholders = ", ".join(["%s"] * len(at))
					where = f"account_type IN ({placeholders})"
					params = list(at) + [snapshot_date]
				else:
					where = "account_type = %s"
					params = [at, snapshot_date]
				# Sign filter on RAW snapshot row balance, mirroring
				# what `_match_clause` emits.
				balance_sign = v.get("balance_sign", "any")
				if balance_sign == "positive":
					balance_sign_clause = " AND balance > 0"
				elif balance_sign == "negative":
					balance_sign_clause = " AND balance < 0"
			else:
				return 0.0
			where = where + balance_sign_clause
		elif "by_root_type_and_name_pattern" in match:
			conf = match["by_root_type_and_name_pattern"]
			where = "root_type = %s AND account LIKE %s"
			params = [conf["root_type"], conf["name_pattern"], snapshot_date]
		elif "by_parent_account_stem_in" in match:
			# Per spec `specs/cash-bank-card-split.md` §4: the new
			# predicate's direct aggregation uses an IN-subquery against
			# tabAccount. Keep the WHERE structure parallel to what
			# `_match_clause` emits in production code so this test
			# verifies the production query's shape AND its arithmetic.
			#
			# Per spec/supplier-advances-split §1: optional balance_sign
			# applies the same outer-WHERE sign filter as by_account_type.
			conf = match["by_parent_account_stem_in"]
			stems = conf["stems"]
			placeholders = ", ".join(["%s"] * len(stems))
			where = (
				"account IN ("
				"SELECT name FROM `tabAccount` "
				"WHERE is_group = 0 "
				"AND root_type = %s "
				f"AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN ({placeholders})"
				")"
			)
			params = [conf["root_type"]] + list(stems) + [snapshot_date]
			balance_sign = conf.get("balance_sign", "any")
			if balance_sign == "positive":
				where += " AND balance > 0"
			elif balance_sign == "negative":
				where += " AND balance < 0"
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
		# force=True bypasses the SAFETY_ROW_THRESHOLD check that trips
		# on the RGI-DEMO production-shaped seed (5M GL × 3 months >
		# 10M threshold). This test only verifies sparkline shape, not
		# the without-force backfill path, so force=True is the
		# minimal change.
		backfill_snapshots(months_back=3, force=True)
		refresh_spotlight_cache()
		raw = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "sundry_creditors", "snapshot_date": getdate(today())},
			"sparkline_data",
		)
		parsed = json.loads(raw)
		self.assertIsInstance(parsed, list)
		# SPARKLINE_LENGTH bumped 6 -> 12 in Phase 4 commit 2 to match
		# account drill's by-company sparkline length (spec §4.1).
		self.assertEqual(len(parsed), SPARKLINE_LENGTH)
		# Should have at least the 3 backfilled month-ends; older slots
		# may be None depending on whether the dev site has older
		# snapshots.
		non_null = [v for v in parsed if v is not None]
		self.assertGreaterEqual(len(non_null), 3)

	# ------------------------------------------------------------------
	# 5 -- delta calculation
	# ------------------------------------------------------------------

	def test_delta_calculation(self):
		"""With a backfill in place, today's delta for each card should
		equal today_value - prior_month_value.
		"""
		# force=True bypasses SAFETY_ROW_THRESHOLD on the prod-scale
		# RGI-DEMO seed. The test verifies delta math, not the
		# without-force backfill path.
		backfill_snapshots(months_back=2, force=True)
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


class TestDisabledFlagRefreshSide(FrappeTestCase):
	"""Tests for the `disabled` flag's behaviour on the refresh path.

	Per spec `specs/cash-bank-card-split.md` §5.2: refresh writes
	cache rows for ALL cards (including disabled) so a future re-
	enable preserves the historical sparkline. Only the read paths
	filter on `disabled`. This invariant is load-bearing for the
	Phase 5 cards-editor: a user disabling then re-enabling a card
	should see continuous sparkline data, not a gap from the period
	the card was hidden.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Same canonical-snapshot setUp pattern as TestSpotlightCache:
		# fresh snapshot tables, single TB snapshot for today.
		_purge_all()
		refresh_tb_snapshot()

	@classmethod
	def tearDownClass(cls):
		_purge_all()
		super().tearDownClass()

	def setUp(self):
		# Each test starts with a clean spotlight cache; TB stays.
		frappe.db.sql("DELETE FROM `tabDGV Spotlight Cache`")
		frappe.db.commit()

	def test_refresh_writes_cache_for_disabled_cards(self):
		"""Refresh writes one cache row per card in CARDS, INCLUDING
		those marked disabled. History continuity invariant: disabling
		a card must not stop its cache from updating, so re-enabling
		later shows continuous sparkline data, not a gap.
		"""
		refresh_spotlight_cache()
		snapshot_date = getdate(today())
		disabled_card_ids = [
			c["id"] for c in CARDS if c.get("disabled")
		]
		self.assertGreater(
			len(disabled_card_ids), 0,
			msg=(
				"This invariant test relies on at least one disabled "
				"card existing in CARDS. The cash & bank split PR "
				"disables 3 (sundry_debtors, cash_and_bank, "
				"inter_co_receivable); revisit if that changes."
			),
		)
		for card_id in disabled_card_ids:
			cache_row = frappe.db.exists(
				"DGV Spotlight Cache",
				{"card_id": card_id, "snapshot_date": snapshot_date},
			)
			self.assertTrue(
				cache_row,
				msg=(
					f"Disabled card '{card_id}' must continue receiving "
					f"cache rows so history is preserved across "
					f"enable/disable cycles. Found no cache row for "
					f"snapshot_date={snapshot_date}."
				),
			)

	def test_refresh_creates_one_row_per_card_including_disabled(self):
		"""Total cache row count after refresh equals len(CARDS),
		not len(visible_cards). Pin the asymmetry between refresh
		(includes disabled) and read paths (skip disabled).
		"""
		refresh_spotlight_cache()
		snapshot_date = getdate(today())
		row_count = frappe.db.count(
			"DGV Spotlight Cache",
			{"snapshot_date": snapshot_date},
		)
		self.assertEqual(
			row_count, len(CARDS),
			msg=(
				f"Refresh wrote {row_count} cache rows but CARDS has "
				f"{len(CARDS)} entries. Refresh must include disabled "
				f"cards (history continuity); only read paths skip them."
			),
		)


class TestByParentAccountStemInRefreshClause(FrappeTestCase):
	"""Tests for the new predicate's refresh-side SQL emission and
	aggregation correctness.

	Per spec `specs/cash-bank-card-split.md` §4.4: the refresh path
	uses an IN-subquery against tabAccount (since the snapshot row
	table doesn't denormalise `parent_account`). This class pins
	that the emitted clause aggregates the same set of leaves that
	`cards_v1._resolve_match` returns for the same predicate.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_purge_all()
		refresh_tb_snapshot()
		refresh_spotlight_cache()

	@classmethod
	def tearDownClass(cls):
		_purge_all()
		super().tearDownClass()

	def test_match_clause_emits_in_subquery_against_tabaccount(self):
		"""_match_clause for `by_parent_account_stem_in` emits an
		IN-subquery (not an inline column filter) and uses named
		parameters end-to-end (no string concatenation of values).
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_card",
			"match": {
				"by_parent_account_stem_in": {
					"stems": ["Bank Accounts", "Cash in Hand"],
					"root_type": "Asset",
				},
			},
		}
		clause, params = _match_clause(card)
		self.assertIsNotNone(clause)
		# Shape pins:
		self.assertIn(
			"account IN (", clause,
			msg="by_parent_account_stem_in must emit an IN-subquery",
		)
		self.assertIn(
			"SUBSTRING_INDEX(parent_account, ' - ', 1)", clause,
			msg="Predicate must extract parent stem via SUBSTRING_INDEX",
		)
		self.assertIn("is_group = 0", clause)
		# Params pins:
		self.assertEqual(params["root_type"], "Asset")
		self.assertEqual(params["st_0"], "Bank Accounts")
		self.assertEqual(params["st_1"], "Cash in Hand")
		# Defensive: no literal stem string concatenated into clause.
		self.assertNotIn(
			"Bank Accounts", clause,
			msg=(
				"Stem values must flow through named parameters, never "
				"concatenated into the SQL string"
			),
		)

	def test_match_clause_defensive_branches_return_none(self):
		"""Malformed predicate -> (None, {}) so refresh skips the card
		with a zero rather than erroring out the entire refresh batch.
		'A single broken card must not break the whole refresh' is
		load-bearing for the multi-card system.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		# Each of these should return (None, {}) -- not raise.
		cases = [
			# Empty stems list
			{"by_parent_account_stem_in": {"stems": [], "root_type": "Asset"}},
			# Missing root_type
			{"by_parent_account_stem_in": {"stems": ["Bank Accounts"]}},
			# Missing stems
			{"by_parent_account_stem_in": {"root_type": "Asset"}},
			# Non-dict conf
			{"by_parent_account_stem_in": "not-a-dict"},
			# Stems is a string, not a list
			{"by_parent_account_stem_in": {
				"stems": "Bank Accounts", "root_type": "Asset"}},
			# root_type is empty string
			{"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"], "root_type": ""}},
		]
		for match in cases:
			with self.subTest(match=match):
				clause, params = _match_clause({"match": match})
				self.assertIsNone(
					clause,
					msg=(
						f"Malformed predicate must return (None, {{}}) so "
						f"refresh treats the card as zero rather than "
						f"erroring out: {match}"
					),
				)
				self.assertEqual(params, {})

	def test_refresh_aggregation_matches_direct_sum_for_new_predicate(self):
		"""For each new-predicate card (liquid_cash + secured_loans),
		the cached value after refresh equals an independent SQL
		aggregation using the same parent-stem filter. Verifies the
		refresh path's emitted SQL is arithmetically correct, not
		just syntactically valid.
		"""
		snapshot_date = getdate(today())
		for card_id in ("liquid_cash", "secured_loans"):
			card = next((c for c in CARDS if c["id"] == card_id), None)
			if card is None:
				self.skipTest(f"Card '{card_id}' not in CARDS")
			conf = card["match"]["by_parent_account_stem_in"]
			stems = conf["stems"]
			root_type = conf["root_type"]
			ph = ", ".join(["%s"] * len(stems))
			rows = frappe.db.sql(
				f"""
				SELECT COALESCE(SUM(
					CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
					     THEN -balance
					     ELSE balance
					END
				), 0)
				FROM `tabDGV TB Snapshot Row`
				WHERE snapshot_date = %s
				  AND account IN (
				    SELECT name FROM `tabAccount`
				    WHERE is_group = 0
				      AND root_type = %s
				      AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN ({ph})
				  )
				""",
				[snapshot_date, root_type] + list(stems),
			)
			expected = round(float(rows[0][0] or 0), 2)
			cached = frappe.db.get_value(
				"DGV Spotlight Cache",
				{"card_id": card_id, "snapshot_date": snapshot_date},
				"value",
			)
			self.assertAlmostEqual(
				float(cached or 0), expected, places=2,
				msg=(
					f"Card '{card_id}': cached refresh value {cached} "
					f"does not match independent direct aggregation "
					f"{expected}. The new predicate's refresh SQL is "
					f"likely emitting a different leaf set than the "
					f"production parent-stem filter expects."
				),
			)


class TestBalanceSignRefresh(FrappeTestCase):
	"""Refresh-path tests for the `balance_sign` predicate extension.

	Per spec/supplier-advances-split §1: `_match_clause` appends
	`AND balance > 0` or `AND balance < 0` to the snapshot-row WHERE
	when `balance_sign` is set. These tests verify the EMITTED CLAUSE
	produces the right cached number, not just the right SQL shape.

	Critical invariant pinned here:
	    sundry_creditors.value + supplier_advances.value
	      == old_sundry_creditors_net_value (within rounding).

	This catches accidental double-counting or accidental exclusion if
	a future refactor breaks the sign split.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_purge_all()
		refresh_tb_snapshot()
		refresh_spotlight_cache()

	@classmethod
	def tearDownClass(cls):
		_purge_all()
		super().tearDownClass()

	def test_match_clause_appends_balance_sign_to_emitted_clause(self):
		"""`_match_clause` for `by_account_type` with `balance_sign:
		"positive"` emits `account_type IN (...) AND balance > 0`.
		Pin the SQL shape so a future refactor can't silently drop
		the sign filter.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		# Negative sign
		card_neg = {
			"id": "test_neg",
			"match": {"by_account_type": {
				"account_type": "Payable", "balance_sign": "negative"}},
		}
		clause, params = _match_clause(card_neg)
		self.assertIsNotNone(clause)
		self.assertIn("balance < 0", clause)
		self.assertIn("account_type IN", clause)
		# Positive sign
		card_pos = {
			"id": "test_pos",
			"match": {"by_account_type": {
				"account_type": "Payable", "balance_sign": "positive"}},
		}
		clause, params = _match_clause(card_pos)
		self.assertIsNotNone(clause)
		self.assertIn("balance > 0", clause)
		# `any` -> no balance clause emitted
		card_any = {
			"id": "test_any",
			"match": {"by_account_type": {"account_type": "Payable"}},
		}
		clause, params = _match_clause(card_any)
		self.assertIsNotNone(clause)
		self.assertNotIn("balance", clause)

	def test_match_clause_invalid_sign_returns_none(self):
		"""Invalid `balance_sign` value -> (None, {}). Refresh
		treats the card as zero, doesn't crash. Same defensive
		shape as other malformed-predicate cases.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_bad",
			"match": {"by_account_type": {
				"account_type": "Payable", "balance_sign": "credit"}},
		}
		clause, params = _match_clause(card)
		self.assertIsNone(
			clause,
			msg=(
				"Invalid balance_sign must yield (None, {}) so the "
				"refresh path skips with zero. Silently treating it "
				"as 'any' would mask card-definition bugs."
			),
		)

	def test_mixed_type_list_returns_none_clause(self):
		"""Mixed-type list `["Payable", {"balance_sign": "positive"}]`
		must return (None, {}). Same defensive shape as the resolver.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_mixed",
			"match": {"by_account_type": [
				"Payable", {"balance_sign": "positive"}]},
		}
		clause, params = _match_clause(card)
		self.assertIsNone(clause)

	def test_sundry_creditors_cached_matches_credit_only_aggregation(self):
		"""Cached value for `sundry_creditors` (balance_sign=negative)
		equals an independent SUM of credit-only Payable leaves on
		the snapshot. Verifies the production refresh SQL is
		arithmetically correct, not just syntactically valid.
		"""
		snapshot_date = getdate(today())
		cached = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "sundry_creditors", "snapshot_date": snapshot_date},
			"value",
		)
		# Independent: sum CASE-flipped values for Payable leaves with
		# RAW balance < 0 only.
		expected = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
				     THEN -balance
				     ELSE balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s
			  AND account_type = 'Payable'
			  AND balance < 0
			""",
			(snapshot_date,),
		)
		expected_value = round(float(expected[0][0] or 0), 2)
		self.assertAlmostEqual(
			float(cached or 0), expected_value, places=2,
			msg=(
				f"sundry_creditors cached={cached} does not match "
				f"credit-only sum={expected_value}. Predicate's "
				f"balance_sign=negative clause is wrong, OR refresh "
				f"is summing both signs (regression to pre-split "
				f"behavior)."
			),
		)

	def test_supplier_advances_cached_matches_debit_only_aggregation(self):
		"""Cached value for `supplier_advances` (balance_sign=positive)
		equals an independent SUM of debit-only Payable leaves.
		"""
		snapshot_date = getdate(today())
		cached = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "supplier_advances", "snapshot_date": snapshot_date},
			"value",
		)
		expected = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
				     THEN -balance
				     ELSE balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s
			  AND account_type = 'Payable'
			  AND balance > 0
			""",
			(snapshot_date,),
		)
		expected_value = round(float(expected[0][0] or 0), 2)
		self.assertAlmostEqual(
			float(cached or 0), expected_value, places=2,
			msg=(
				f"supplier_advances cached={cached} does not match "
				f"debit-only sum={expected_value}."
			),
		)

	def test_split_sum_invariant_matches_pre_split_net(self):
		"""CRITICAL INVARIANT: sundry_creditors + supplier_advances
		(post-split, two cards) equals the OLD sundry_creditors net
		value (pre-split, one card summing both signs) on the same
		snapshot.

		This is the load-bearing test for the split: confirms the two
		new cards together cover EXACTLY the same set of leaves the
		old netting card did -- no leaves accidentally double-counted,
		no leaves accidentally excluded.

		If this fails:
		  - sum too high  -> a leaf is counted in both cards (sign
		                     filter has overlap or a `balance == 0`
		                     bug)
		  - sum too low   -> a leaf is in neither card (e.g. one of
		                     the sign filters has a strict
		                     vs. inclusive boundary error)
		"""
		snapshot_date = getdate(today())
		sundry = float(frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "sundry_creditors", "snapshot_date": snapshot_date},
			"value",
		) or 0)
		advances = float(frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "supplier_advances", "snapshot_date": snapshot_date},
			"value",
		) or 0)
		split_total = round(sundry + advances, 2)
		# Pre-split aggregation: ALL Payable leaves, any sign.
		net_row = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
				     THEN -balance
				     ELSE balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s
			  AND account_type = 'Payable'
			""",
			(snapshot_date,),
		)
		net_value = round(float(net_row[0][0] or 0), 2)
		self.assertAlmostEqual(
			split_total, net_value, places=2,
			msg=(
				f"Split invariant violated: sundry_creditors ({sundry}) "
				f"+ supplier_advances ({advances}) = {split_total}, "
				f"but pre-split net (all-sign Payable sum) = {net_value}. "
				f"Difference of {round(split_total - net_value, 2)} "
				f"indicates either double-counting (one or more leaves "
				f"in both sign sets -- likely a boundary bug) or "
				f"missing leaves (one or more leaves in neither sign "
				f"set -- likely a balance==0 edge case mis-handled)."
			),
		)
