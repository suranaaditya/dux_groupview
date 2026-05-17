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
		          by spec/supplier-advances-split) and optional
		          exclude_parent_stems (extended by
		          spec/supplier-advances-display-and-exclude-fixes)

		Applies the same `display_sign` transform as
		`spotlight_refresh._aggregate` does, so the cached value can
		be compared apples-to-apples with this helper's return value
		on cards using `display_sign: "absolute"`.
		"""
		match = card["match"]
		exclude_parent_stems = None
		if "by_account_type" in match:
			v = match["by_account_type"]
			balance_sign_clause = ""
			if isinstance(v, str):
				where = "account_type = %s"
				params = [v]
			elif isinstance(v, (list, tuple)):
				placeholders = ", ".join(["%s"] * len(v))
				where = f"account_type IN ({placeholders})"
				params = list(v)
			elif isinstance(v, dict):
				at = v["account_type"]
				if isinstance(at, (list, tuple)):
					placeholders = ", ".join(["%s"] * len(at))
					where = f"account_type IN ({placeholders})"
					params = list(at)
				else:
					where = "account_type = %s"
					params = [at]
				# Sign filter on RAW snapshot row balance, mirroring
				# what `_match_clause` emits.
				balance_sign = v.get("balance_sign", "any")
				if balance_sign == "positive":
					balance_sign_clause = " AND balance > 0"
				elif balance_sign == "negative":
					balance_sign_clause = " AND balance < 0"
				exclude_parent_stems = v.get("exclude_parent_stems")
			else:
				return 0.0
			where = where + balance_sign_clause
			# Exclusion is an `account NOT IN (subquery)` on snapshot
			# rows, mirroring _match_clause exactly. Append AFTER any
			# balance_sign clause so the parameters bind in the order
			# the SQL is emitted.
			if (
				isinstance(exclude_parent_stems, list)
				and exclude_parent_stems
				and all(isinstance(x, str) for x in exclude_parent_stems)
			):
				ex_ph = ", ".join(["%s"] * len(exclude_parent_stems))
				where += (
					f" AND account NOT IN ("
					f"SELECT name FROM `tabAccount` "
					f"WHERE is_group = 0 "
					f"AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN ({ex_ph})"
					f")"
				)
				params += list(exclude_parent_stems)
			params += [snapshot_date]
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
			#
			# Per spec/supplier-advances-display-and-exclude-fixes:
			# optional exclude_parent_stems folds NOT IN INTO the
			# subquery's WHERE, alongside the IN.
			conf = match["by_parent_account_stem_in"]
			stems = conf["stems"]
			placeholders = ", ".join(["%s"] * len(stems))
			exclude_parent_stems = conf.get("exclude_parent_stems")
			exclusion_sql = ""
			exclusion_params = []
			if (
				isinstance(exclude_parent_stems, list)
				and exclude_parent_stems
				and all(isinstance(x, str) for x in exclude_parent_stems)
			):
				ex_ph = ", ".join(["%s"] * len(exclude_parent_stems))
				exclusion_sql = (
					f" AND SUBSTRING_INDEX(parent_account, ' - ', 1) "
					f"NOT IN ({ex_ph})"
				)
				exclusion_params = list(exclude_parent_stems)
			where = (
				"account IN ("
				"SELECT name FROM `tabAccount` "
				"WHERE is_group = 0 "
				"AND root_type = %s "
				f"AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN ({placeholders})"
				f"{exclusion_sql}"
				")"
			)
			params = (
				[conf["root_type"]]
				+ list(stems)
				+ exclusion_params
				+ [snapshot_date]
			)
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
		raw = round(float(rows[0][0] or 0), 2)
		# Mirror the production-side display_sign transform so this
		# helper's return value is directly comparable to the cached
		# value (which is post-transform).
		sign = card.get("display_sign", "natural")
		if sign == "absolute":
			return abs(raw)
		if sign == "negated":
			return -raw
		return raw

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
		"""Cached value for `sundry_creditors` (balance_sign=negative,
		exclude_parent_stems=["Unsecured Loans"]) equals an independent
		SUM of credit-only Payable leaves whose immediate parent stem
		is NOT `Unsecured Loans`. Verifies refresh emits BOTH the
		balance_sign filter AND the exclusion subquery correctly.
		"""
		snapshot_date = getdate(today())
		cached = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "sundry_creditors", "snapshot_date": snapshot_date},
			"value",
		)
		# Independent: sum CASE-flipped values for Payable leaves with
		# RAW balance < 0 AND parent stem not in the exclusion list.
		# Joins to tabAccount through `account` because snapshot rows
		# don't carry parent_account themselves -- same shape as the
		# production NOT IN subquery.
		expected = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN s.root_type IN ('Liability', 'Equity', 'Income')
				     THEN -s.balance
				     ELSE s.balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row` s
			WHERE s.snapshot_date = %s
			  AND s.account_type = 'Payable'
			  AND s.balance < 0
			  AND s.account NOT IN (
			    SELECT name FROM `tabAccount`
			    WHERE is_group = 0
			      AND SUBSTRING_INDEX(parent_account, ' - ', 1)
			          = 'Unsecured Loans'
			  )
			""",
			(snapshot_date,),
		)
		expected_value = round(float(expected[0][0] or 0), 2)
		self.assertAlmostEqual(
			float(cached or 0), expected_value, places=2,
			msg=(
				f"sundry_creditors cached={cached} does not match "
				f"credit-only-minus-UL sum={expected_value}. Predicate's "
				f"balance_sign=negative clause is wrong, the "
				f"exclude_parent_stems subquery dropped or is mis-"
				f"emitted, OR refresh is summing both signs (regression "
				f"to pre-split behaviour)."
			),
		)

	def test_supplier_advances_cached_matches_debit_only_aggregation(self):
		"""Cached value for `supplier_advances` (balance_sign=positive,
		exclude_parent_stems=["Unsecured Loans"], display_sign=absolute)
		equals `abs(...)` of the natural-side SUM of debit-only Payable
		leaves whose parent stem is NOT `Unsecured Loans`.

		Verifies the FULL transform chain: predicate (sign + exclusion)
		feeds the aggregation; display_sign applies the absolute on the
		result. Three independent moving parts; the test pins all
		three.
		"""
		snapshot_date = getdate(today())
		cached = frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "supplier_advances", "snapshot_date": snapshot_date},
			"value",
		)
		expected_raw = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN s.root_type IN ('Liability', 'Equity', 'Income')
				     THEN -s.balance
				     ELSE s.balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row` s
			WHERE s.snapshot_date = %s
			  AND s.account_type = 'Payable'
			  AND s.balance > 0
			  AND s.account NOT IN (
			    SELECT name FROM `tabAccount`
			    WHERE is_group = 0
			      AND SUBSTRING_INDEX(parent_account, ' - ', 1)
			          = 'Unsecured Loans'
			  )
			""",
			(snapshot_date,),
		)
		expected_value = abs(round(float(expected_raw[0][0] or 0), 2))
		self.assertAlmostEqual(
			float(cached or 0), expected_value, places=2,
			msg=(
				f"supplier_advances cached={cached} does not match "
				f"abs(debit-only-minus-UL natural-side sum) = "
				f"{expected_value}. If the magnitude is right but the "
				f"sign is wrong, display_sign=absolute is not being "
				f"applied. If the magnitude is wrong, either the "
				f"balance_sign clause or the exclude_parent_stems "
				f"subquery has drifted."
			),
		)
		# Cached value must be >= 0 (display_sign=absolute guarantees
		# it). Pinned defensively: a regression that re-introduces the
		# negative-stored bug would land here.
		self.assertGreaterEqual(
			float(cached or 0), 0.0,
			msg=(
				"supplier_advances must store a non-negative value "
				"(display_sign='absolute'). A negative stored value "
				"means display_sign was not applied -- likely the "
				"transform was moved out of `_aggregate` or "
				"`display_sign` was dropped from the card definition."
			),
		)

	def test_split_sum_invariant_matches_pre_PR_credit_only_aggregation(self):
		"""CRITICAL INVARIANT for the display-and-exclude PR:

		    new_sundry_creditors
		      + (credit-only Payable natural-side sum UNDER `Unsecured
		         Loans` parent)
		      == credit-only Payable natural-side sum, ANY parent
		         (== the pre-this-PR sundry_creditors stored value)

		The exclusion moves leaves OUT of sundry_creditors; this test
		pins that nothing is lost in transit (the moved leaves are
		fully accounted for by the `excluded_credit_only` term).

		On dev sites with no Unsecured Loans Payable leaves, the
		excluded-side term is 0 and the invariant degenerates to
		`new == old` -- still a useful regression check.

		Composability with `supplier_advances`: a parallel test pins
		the same shape on the debit side
		(`test_supplier_advances_invariant_with_absolute_and_exclude`).
		The two cards' exclusions are independent; pinning them
		separately gives a clearer failure message than one combined
		invariant when only one side drifts.
		"""
		snapshot_date = getdate(today())
		new_sundry = float(frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "sundry_creditors", "snapshot_date": snapshot_date},
			"value",
		) or 0)
		# Credit-only Payable, UNDER `Unsecured Loans` parent stem
		# (i.e. what was just excluded by this PR).
		excluded_row = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN s.root_type IN ('Liability', 'Equity', 'Income')
				     THEN -s.balance
				     ELSE s.balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row` s
			WHERE s.snapshot_date = %s
			  AND s.account_type = 'Payable'
			  AND s.balance < 0
			  AND s.account IN (
			    SELECT name FROM `tabAccount`
			    WHERE is_group = 0
			      AND SUBSTRING_INDEX(parent_account, ' - ', 1)
			          = 'Unsecured Loans'
			  )
			""",
			(snapshot_date,),
		)
		excluded_credit_only = round(float(excluded_row[0][0] or 0), 2)
		# Pre-this-PR sundry_creditors aggregation: credit-only
		# Payable, ANY parent.
		full_row = frappe.db.sql(
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
		pre_pr_sundry = round(float(full_row[0][0] or 0), 2)
		self.assertAlmostEqual(
			round(new_sundry + excluded_credit_only, 2),
			pre_pr_sundry, places=2,
			msg=(
				f"Sundry-credit exclusion invariant violated: "
				f"new_sundry ({new_sundry}) + excluded_credit_only "
				f"({excluded_credit_only}) = "
				f"{round(new_sundry + excluded_credit_only, 2)}, but "
				f"pre-PR sundry (all-parent credit-only) = "
				f"{pre_pr_sundry}. The exclusion is either over-"
				f"matching (excluded > what was actually under UL) "
				f"or under-matching (some UL leaves still counted "
				f"in sundry_creditors)."
			),
		)

	def test_supplier_advances_invariant_with_absolute_and_exclude(self):
		"""Parallel invariant for `supplier_advances`, accounting for
		BOTH `display_sign=absolute` AND `exclude_parent_stems`:

		    new_supplier_advances
		      == abs(debit-only Payable natural-side sum
		             EXCLUDING Unsecured Loans parent)

		    new_supplier_advances + abs(debit-only Payable natural-
		         side sum UNDER Unsecured Loans parent)
		      == abs(debit-only Payable natural-side sum,
		             ANY parent)

		Pre-this-PR `supplier_advances` stored a NEGATIVE value (the
		natural-side CASE flip turns a debit Liability balance into a
		negative number). Post-PR the absolute transform stores the
		magnitude. The right-hand side of the second invariant is the
		magnitude of the pre-PR stored value, which is what the
		regression check anchors against.
		"""
		snapshot_date = getdate(today())
		new_advances = float(frappe.db.get_value(
			"DGV Spotlight Cache",
			{"card_id": "supplier_advances", "snapshot_date": snapshot_date},
			"value",
		) or 0)
		# Natural-side debit-only sum UNDER `Unsecured Loans` (the
		# leaves moved out of supplier_advances by this PR). For
		# Payable leaves with balance > 0, the natural-side CASE
		# returns a NEGATIVE number, so the magnitude is what we add
		# back.
		excluded_natural_row = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(
				CASE WHEN s.root_type IN ('Liability', 'Equity', 'Income')
				     THEN -s.balance
				     ELSE s.balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row` s
			WHERE s.snapshot_date = %s
			  AND s.account_type = 'Payable'
			  AND s.balance > 0
			  AND s.account IN (
			    SELECT name FROM `tabAccount`
			    WHERE is_group = 0
			      AND SUBSTRING_INDEX(parent_account, ' - ', 1)
			          = 'Unsecured Loans'
			  )
			""",
			(snapshot_date,),
		)
		excluded_abs = abs(round(float(excluded_natural_row[0][0] or 0), 2))
		# Pre-PR magnitude: debit-only Payable, ANY parent.
		full_row = frappe.db.sql(
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
		pre_pr_advances_magnitude = abs(round(float(full_row[0][0] or 0), 2))
		self.assertAlmostEqual(
			round(new_advances + excluded_abs, 2),
			pre_pr_advances_magnitude, places=2,
			msg=(
				f"Supplier-advances exclusion invariant violated: "
				f"new_advances ({new_advances}) + excluded_abs "
				f"({excluded_abs}) = "
				f"{round(new_advances + excluded_abs, 2)}, but "
				f"pre-PR |supplier_advances| (all-parent debit-only "
				f"magnitude) = {pre_pr_advances_magnitude}. Either "
				f"display_sign isn't applying, the exclude_parent_stems "
				f"clause has the wrong scope, or the natural-side CASE "
				f"flip is no longer producing the magnitudes the test "
				f"expects."
			),
		)


class TestDisplaySignTransform(FrappeTestCase):
	"""Unit tests for the `display_sign` transform applied at the end
	of `_aggregate`.

	Per spec/supplier-advances-display-and-exclude-fixes Fix 1: the
	final transform routes through `_apply_display_sign` and supports
	three values (`"natural"`, `"absolute"`, `"negated"`). Invalid
	values log a warning and fall back to `"natural"` rather than
	crash. Field omission is equivalent to `"natural"`.

	These tests bypass the SQL aggregation path by exercising
	`_apply_display_sign` directly -- the SQL is already covered by
	`test_supplier_advances_cached_matches_debit_only_aggregation`
	and the gold-standard `test_spotlight_value_matches_direct_aggregation`.
	"""

	def test_natural_passthrough_positive_input(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(
			_apply_display_sign({"display_sign": "natural"}, 12.5), 12.5,
		)

	def test_natural_passthrough_negative_input(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(
			_apply_display_sign({"display_sign": "natural"}, -12.5), -12.5,
		)

	def test_absolute_positive_input_unchanged(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(
			_apply_display_sign({"display_sign": "absolute"}, 12.5), 12.5,
		)

	def test_absolute_negative_input_becomes_positive(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(
			_apply_display_sign({"display_sign": "absolute"}, -12.5), 12.5,
		)

	def test_negated_positive_input_becomes_negative(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(
			_apply_display_sign({"display_sign": "negated"}, 12.5), -12.5,
		)

	def test_negated_negative_input_becomes_positive(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(
			_apply_display_sign({"display_sign": "negated"}, -12.5), 12.5,
		)

	def test_zero_input_unchanged_under_all_modes(self):
		"""Zero is identity under all three transforms. Pinned because
		`abs(0) == 0` and `-0 == 0` are language-level promises but a
		future refactor that, say, special-cases zero could regress
		this and the test would catch it.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		for mode in ("natural", "absolute", "negated"):
			self.assertEqual(
				_apply_display_sign({"display_sign": mode}, 0.0), 0.0,
				msg=f"display_sign={mode!r} did not preserve zero",
			)

	def test_omitted_field_defaults_to_natural(self):
		"""Card definitions without a `display_sign` key -- i.e. all
		10 cards other than supplier_advances -- get the no-op
		passthrough. Regression-safe for the entire existing card set.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		self.assertEqual(_apply_display_sign({}, 7.5), 7.5)
		self.assertEqual(_apply_display_sign({}, -7.5), -7.5)

	def test_invalid_string_value_falls_back_to_natural(self):
		"""Invalid string value -> logged warning + natural passthrough.
		Captured by a log monkey-patch so the test asserts BOTH the
		fallback behaviour AND the surfaced warning (the warning is
		what makes the bug findable in production logs).
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		warnings_captured = []

		class _StubLogger:
			def warning(self, msg):
				warnings_captured.append(msg)

		original_logger = frappe.logger
		frappe.logger = lambda: _StubLogger()
		try:
			result = _apply_display_sign(
				{"id": "test_invalid", "display_sign": "flipped"}, -3.0,
			)
		finally:
			frappe.logger = original_logger

		self.assertEqual(
			result, -3.0,
			msg=(
				"Invalid display_sign must fall back to natural "
				"(input value returned unchanged). A crash here "
				"would take down spotlight refresh on a typo'd "
				"card definition -- unacceptable."
			),
		)
		self.assertEqual(
			len(warnings_captured), 1,
			msg=(
				f"Expected exactly one warning logged; got "
				f"{len(warnings_captured)}: {warnings_captured}"
			),
		)
		self.assertIn("display_sign", warnings_captured[0])
		self.assertIn("'flipped'", warnings_captured[0])

	def test_non_string_value_falls_back_to_natural(self):
		"""Non-string values (e.g. an int 42, bool True, None) hit
		the same warn + natural fallback. None is the most likely real-
		world misuse -- a card author setting `display_sign: None`
		expecting it to mean "default" -- so it's explicitly covered.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		original_logger = frappe.logger
		frappe.logger = lambda: type("S", (), {"warning": lambda self, m: None})()
		try:
			for bad in (42, True, None, ["absolute"], {"absolute": True}):
				with self.subTest(bad=bad):
					# `None` evaluates as `card.get("display_sign", "natural")`
					# returning None (key present, value is None); branch lands
					# in the warn-fallback path.
					if bad is None:
						card = {"id": "test_none", "display_sign": None}
					else:
						card = {"id": f"test_{type(bad).__name__}",
						        "display_sign": bad}
					self.assertEqual(
						_apply_display_sign(card, 5.0), 5.0,
						msg=(
							f"Non-string display_sign={bad!r} must fall "
							f"back to natural (return input unchanged), "
							f"not crash."
						),
					)
		finally:
			frappe.logger = original_logger

	def test_delta_math_under_absolute_with_monotonic_data(self):
		"""When the underlying value is monotonic and same-signed
		across the period, `display_sign=absolute` produces a delta
		on the absolute axis that matches the directional delta in
		magnitude. Pinned because this is the common case -- a card
		whose underlying value never crosses zero behaves naturally
		under `absolute`.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		card = {"display_sign": "absolute"}
		# Underlying: -2.0 last month, -3.0 this month (more negative).
		# Absolute: 2.0 last month, 3.0 this month.
		this_month = _apply_display_sign(card, -3.0)
		last_month = _apply_display_sign(card, -2.0)
		delta = round(this_month - last_month, 2)
		self.assertEqual(
			delta, 1.0,
			msg=(
				"Monotonic negative-side growth -2 -> -3 should "
				"yield delta=+1 on the absolute axis (magnitude "
				"grew by 1). Anything else means delta math broke."
			),
		)

	def test_delta_math_under_absolute_with_sign_crossing_data(self):
		"""When the underlying value crosses zero across the period,
		`display_sign=absolute` produces a delta that is NOT the same
		as the directional delta -- this is the documented caveat in
		cards.py. Test pins the documented (counter-intuitive)
		behaviour so future readers don't get a different result
		without code review.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_apply_display_sign,
		)
		card = {"display_sign": "absolute"}
		# Underlying: -2.0 last month, +3.0 this month (crossed zero).
		# Natural delta would be: 3.0 - (-2.0) = +5.0 (positive change).
		# Absolute delta: 3.0 - 2.0 = +1.0 (just the magnitude difference).
		this_month = _apply_display_sign(card, 3.0)
		last_month = _apply_display_sign(card, -2.0)
		absolute_delta = round(this_month - last_month, 2)
		natural_delta = 3.0 - (-2.0)
		self.assertEqual(
			absolute_delta, 1.0,
			msg=(
				"Sign-crossing -2 -> +3 under display_sign=absolute "
				"should yield delta=+1 (magnitude difference of "
				"2 -> 3), NOT the natural directional delta of +5. "
				"The cards.py docstring documents this non-intuitive "
				"behaviour; cards using `absolute` are expected not "
				"to cross zero in practice."
			),
		)
		# Pin the asymmetry explicitly so future readers see WHY this
		# is documented as a caveat.
		self.assertNotEqual(
			absolute_delta, natural_delta,
			msg=(
				"Absolute and natural deltas must differ when the "
				"input crosses zero. If they ever match in this case, "
				"either the test inputs changed or the transform "
				"semantics drifted -- both warrant review."
			),
		)


class TestExcludeParentStemsRefresh(FrappeTestCase):
	"""Refresh-path tests for the `exclude_parent_stems` predicate
	extension on `by_account_type` and `by_parent_account_stem_in`.

	Pinned at two levels:
	  1. Emitted-clause shape (`_match_clause` returns the right SQL
	     fragment + params for various input shapes).
	  2. Defensive behaviour for empty / missing / non-list / mixed-
	     type inputs.

	The arithmetic correctness against real production data is pinned
	in `TestBalanceSignRefresh` (specifically the credit-only, debit-
	only, and split-invariant tests, which were updated to account
	for `exclude_parent_stems`).
	"""

	def test_by_account_type_emits_not_in_subquery_when_excluding(self):
		"""`by_account_type` with `exclude_parent_stems` appends an
		`AND account NOT IN (SELECT name FROM tabAccount ...)`
		subquery. Pin the SQL shape so a future refactor can't
		silently drop or restructure the exclusion.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_at_exclude",
			"match": {"by_account_type": {
				"account_type": "Payable",
				"exclude_parent_stems": ["Unsecured Loans"],
			}},
		}
		clause, params = _match_clause(card)
		self.assertIsNotNone(clause)
		self.assertIn("account_type IN", clause)
		self.assertIn("NOT IN", clause)
		self.assertIn("`tabAccount`", clause)
		self.assertIn("SUBSTRING_INDEX(parent_account, ' - ', 1)", clause)
		# Named placeholder for the excluded stem, not a raw string.
		self.assertIn("%(ex_0)s", clause)
		self.assertEqual(params.get("ex_0"), "Unsecured Loans")

	def test_by_account_type_no_exclusion_emits_no_extra_clause(self):
		"""Without `exclude_parent_stems`, the emitted clause is the
		bare `account_type IN (...)` -- no tabAccount subquery,
		regression-safe for the 9 cards that don't use the key.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_at_no_exclude",
			"match": {"by_account_type": {"account_type": "Payable"}},
		}
		clause, params = _match_clause(card)
		self.assertIsNotNone(clause)
		self.assertNotIn("NOT IN", clause)
		self.assertNotIn("`tabAccount`", clause)

	def test_by_parent_stem_in_emits_not_in_inside_subquery(self):
		"""`by_parent_account_stem_in` with exclusion folds the
		`NOT IN` INSIDE the existing tabAccount subquery's WHERE,
		not as an outer-query addition. Pin the location since the
		SQL semantics depend on it (NOT IN outside would filter the
		snapshot row's `account` column, which can't compute
		`SUBSTRING_INDEX(parent_account, ...)`).
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_ps_exclude",
			"match": {"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"],
				"root_type": "Asset",
				"exclude_parent_stems": ["Secured Loans"],
			}},
		}
		clause, params = _match_clause(card)
		self.assertIsNotNone(clause)
		# `IN (...)` (the inclusion list) and `NOT IN (...)` (the
		# exclusion list) must both appear inside the same subquery.
		# We check by their relative order and presence.
		self.assertIn("account IN (", clause)
		self.assertIn(") IN (", clause)  # parent_stem) IN (stems)
		self.assertIn("NOT IN (", clause)
		self.assertIn("%(ex_0)s", clause)
		self.assertEqual(params.get("ex_0"), "Secured Loans")
		# Sanity: stems still bound under `st_N`.
		self.assertEqual(params.get("st_0"), "Bank Accounts")

	def test_by_parent_stem_in_no_exclusion_emits_no_not_in(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {
			"id": "test_ps_no_exclude",
			"match": {"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"],
				"root_type": "Asset",
			}},
		}
		clause, params = _match_clause(card)
		self.assertIsNotNone(clause)
		self.assertNotIn("NOT IN", clause)

	def test_empty_list_exclusion_is_noop_by_account_type(self):
		"""Empty list `exclude_parent_stems: []` -> predicate behaves
		identically to omitting the key.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		with_empty = _match_clause({"id": "test", "match": {
			"by_account_type": {
				"account_type": "Payable", "exclude_parent_stems": []}}})
		without = _match_clause({"id": "test", "match": {
			"by_account_type": {"account_type": "Payable"}}})
		self.assertEqual(with_empty, without)

	def test_empty_list_exclusion_is_noop_by_parent_stem_in(self):
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		with_empty = _match_clause({"id": "test", "match": {
			"by_parent_account_stem_in": {
				"stems": ["X"], "root_type": "Asset",
				"exclude_parent_stems": []}}})
		without = _match_clause({"id": "test", "match": {
			"by_parent_account_stem_in": {
				"stems": ["X"], "root_type": "Asset"}}})
		self.assertEqual(with_empty, without)

	def test_non_list_exclusion_is_noop(self):
		"""Non-list value (string, dict, None) -> no-op, predicate
		emits as today. Defensive shape per cards.py docstring;
		guards against a card author writing
		`exclude_parent_stems: "Unsecured Loans"` (without the list
		wrapping) and silently shipping a card that doesn't exclude
		anything.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		for bad in ("Unsecured Loans", {"x": 1}, 42, None):
			with self.subTest(bad=bad):
				result = _match_clause({"id": "test", "match": {
					"by_account_type": {
						"account_type": "Payable",
						"exclude_parent_stems": bad,
					}}})
				# Same as omitting the key.
				baseline = _match_clause({"id": "test", "match": {
					"by_account_type": {"account_type": "Payable"}}})
				self.assertEqual(
					result, baseline,
					msg=(
						f"Non-list exclude_parent_stems={bad!r} must "
						f"degrade to no-op (no NOT IN clause). "
						f"Anything else risks silently shipping a "
						f"broken exclusion -- the card author "
						f"thinks they excluded something but didn't."
					),
				)

	def test_non_string_element_in_list_is_noop(self):
		"""List containing a non-string element -> no-op. Same
		defensive shape as `stems` and `account_types` already use
		elsewhere in `_match_clause`.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		result = _match_clause({"id": "test", "match": {
			"by_account_type": {
				"account_type": "Payable",
				"exclude_parent_stems": ["Unsecured Loans", 42],
			}}})
		baseline = _match_clause({"id": "test", "match": {
			"by_account_type": {"account_type": "Payable"}}})
		self.assertEqual(result, baseline)

	def test_multiple_excluded_stems_all_appear_in_clause(self):
		"""Each excluded stem becomes a distinct named placeholder.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {"id": "test", "match": {
			"by_account_type": {
				"account_type": "Payable",
				"exclude_parent_stems": ["Unsecured Loans", "Other Stem"],
			}}}
		clause, params = _match_clause(card)
		self.assertIn("%(ex_0)s", clause)
		self.assertIn("%(ex_1)s", clause)
		self.assertEqual(params.get("ex_0"), "Unsecured Loans")
		self.assertEqual(params.get("ex_1"), "Other Stem")

	def test_balance_sign_and_exclude_compose(self):
		"""Card carrying BOTH `balance_sign` and `exclude_parent_stems`
		(the production shape of both `sundry_creditors` and
		`supplier_advances`) emits BOTH clauses. Composability
		regression test.
		"""
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_match_clause,
		)
		card = {"id": "test", "match": {
			"by_account_type": {
				"account_type": "Payable",
				"balance_sign": "negative",
				"exclude_parent_stems": ["Unsecured Loans"],
			}}}
		clause, params = _match_clause(card)
		self.assertIsNotNone(clause)
		self.assertIn("balance < 0", clause)
		self.assertIn("NOT IN", clause)
		# Both should bind without collision -- separate placeholder
		# namespaces (`at_N` for account types, `ex_N` for excluded
		# stems).
		self.assertEqual(params.get("at_0"), "Payable")
		self.assertEqual(params.get("ex_0"), "Unsecured Loans")


