"""Tests for `cockpit.get_cockpit_headline` (Phase 4 commit 2.5).

Most of the headline logic is pure-Python composition over a list of
delta dicts -- no DB needed. Those tests target `_compose_headline`
directly. One smoke test exercises the whitelisted entry point against
the dev seed to confirm wiring.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_cockpit
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from dux_groupview.dux_groupview.api import cockpit
from dux_groupview.dux_groupview.api.cockpit import (
	_compose_headline,
	_cr,
	get_cockpit_headline,
)


# ---------------------------------------------------------------------------
# Pure-function tests for _compose_headline (no DB)
# ---------------------------------------------------------------------------

PREV = "April"


def _d(name, delta):
	"""Shorthand for a delta dict in the shape _compose_headline expects."""
	return {"card_id": name.lower().replace(" ", "_"), "name": name, "delta": delta}


class TestComposeHeadline(FrappeTestCase):

	def test_no_significant_deltas(self):
		out = _compose_headline([], PREV)
		self.assertEqual(out, "All key metrics held steady from April.")

	def test_one_significant_delta_up(self):
		out = _compose_headline([_d("Sundry creditors", 41_000_000)], PREV)
		self.assertEqual(
			out,
			"Sundry creditors up by ₹4.1 Cr since April.",
		)

	def test_one_significant_delta_down(self):
		out = _compose_headline([_d("Cash position", -8_000_000)], PREV)
		self.assertEqual(
			out,
			"Cash position down by ₹0.8 Cr since April.",
		)

	def test_two_deltas_same_sign_positive(self):
		out = _compose_headline(
			[_d("Sundry creditors", 41_000_000),
			 _d("Sundry debtors", 27_000_000)],
			PREV,
		)
		self.assertEqual(
			out,
			"Sundry creditors and Sundry debtors both rose over the month, "
			"by ₹4.1 Cr and ₹2.7 Cr respectively.",
		)

	def test_two_deltas_same_sign_negative(self):
		out = _compose_headline(
			[_d("Cash position", -28_000_000),
			 _d("Fixed deposits", -15_000_000)],
			PREV,
		)
		self.assertEqual(
			out,
			"Cash position and Fixed deposits both fell over the month, "
			"by ₹2.8 Cr and ₹1.5 Cr respectively.",
		)

	def test_two_deltas_opposite_signs_up_first(self):
		out = _compose_headline(
			[_d("Sundry creditors", 41_000_000),
			 _d("Cash position", -8_000_000)],
			PREV,
		)
		self.assertEqual(
			out,
			"Sundry creditors strengthened by ₹4.1 Cr; "
			"Cash position declined by ₹0.8 Cr over the month.",
		)

	def test_two_deltas_opposite_signs_down_first(self):
		"""Top abs(delta) is the down card -- template still puts the up
		card first ("strengthened by") and down card second
		("declined by"). Order independent of input ordering."""
		out = _compose_headline(
			[_d("Cash position", -82_000_000),
			 _d("Sundry creditors", 8_000_000)],
			PREV,
		)
		self.assertEqual(
			out,
			"Sundry creditors strengthened by ₹0.8 Cr; "
			"Cash position declined by ₹8.2 Cr over the month.",
		)

	def test_three_plus_deltas_picks_top_two(self):
		"""When 3+ are above threshold, only the top two by abs(delta)
		drive the sentence template."""
		out = _compose_headline(
			[_d("Sundry creditors", 41_000_000),    # 4.1 Cr
			 _d("Sundry debtors",   27_000_000),    # 2.7 Cr
			 _d("Cash position",   -15_000_000)],   # 1.5 Cr
			PREV,
		)
		# Top two are same sign (positive), so "both rose" template.
		self.assertEqual(
			out,
			"Sundry creditors and Sundry debtors both rose over the month, "
			"by ₹4.1 Cr and ₹2.7 Cr respectively.",
		)

	def test_one_delta_unambiguous_rounding(self):
		"""₹1.46 Cr rounds to ₹1.5 Cr regardless of half-rounding rule.
		(Python's :.1f does round-half-to-even for ties; values not
		on a tie are unambiguous.)"""
		out = _compose_headline([_d("Cash position", 14_600_000)], PREV)
		self.assertEqual(
			out,
			"Cash position up by ₹1.5 Cr since April.",
		)


# ---------------------------------------------------------------------------
# Pure-function tests for _cr formatting
# ---------------------------------------------------------------------------

class TestCrFormatter(FrappeTestCase):

	def test_one_crore(self):
		self.assertEqual(_cr(10_000_000), "₹1.0 Cr")

	def test_one_decimal_rounding(self):
		self.assertEqual(_cr(12_300_000), "₹1.2 Cr")

	def test_negative_returns_absolute(self):
		"""_cr is for display; sign is conveyed by the surrounding template."""
		self.assertEqual(_cr(-8_000_000), "₹0.8 Cr")

	def test_below_one_crore(self):
		self.assertEqual(_cr(1_000_000), "₹0.1 Cr")

	def test_zero(self):
		self.assertEqual(_cr(0), "₹0.0 Cr")


# ---------------------------------------------------------------------------
# Constants pinning
# ---------------------------------------------------------------------------

class TestConstants(FrappeTestCase):

	def test_friendly_names_cover_all_cards(self):
		"""Every card id in cards.py must have a friendly headline name.
		If a new card is added, this test forces the headline copy to be
		updated explicitly rather than falling back to the raw label."""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		card_ids = {c["id"] for c in CARDS}
		mapped = set(cockpit.HEADLINE_CARD_NAMES.keys())
		missing = card_ids - mapped
		self.assertEqual(
			missing, set(),
			f"Cards without a friendly headline name: {sorted(missing)}",
		)

	def test_significance_threshold_is_ten_lakh(self):
		"""Pinning the threshold prevents accidental edits. ₹0.10 Cr =
		1,000,000 rupees. If a future commit raises this, it should be
		a deliberate change visible in the test."""
		self.assertEqual(cockpit.HEADLINE_DELTA_THRESHOLD_RUPEES, 1_000_000.0)


# ---------------------------------------------------------------------------
# Whitelist smoke test
# ---------------------------------------------------------------------------

class TestGetCockpitHeadlineSmoke(FrappeTestCase):

	def test_whitelist_returns_dict_with_headline_key(self):
		"""Smoke: endpoint runs without error and returns the contract
		shape. Content varies with seed state -- not asserted."""
		result = get_cockpit_headline(today())
		self.assertIsInstance(result, dict)
		self.assertIn("headline", result)
		self.assertIsInstance(result["headline"], str)


# ---------------------------------------------------------------------------
# Phase 4 commit 2.5 fix 3 — missing-prior-baseline branches
# ---------------------------------------------------------------------------

class TestMissingBaselineBranches(FrappeTestCase):
	"""Cards with no prior-month baseline are filtered before
	significance evaluation. If ALL cards lack baseline, the headline
	emits the seventh template branch ("First snapshot for this scope").
	The filtering happens inside `get_cockpit_headline` (DB calls), so
	we monkey-patch the helpers to simulate baseline presence/absence
	without touching the live cache.
	"""

	def setUp(self):
		from dux_groupview.dux_groupview.api import cockpit as _ck
		# Snapshot original references so tearDown restores cleanly.
		self._orig_resolve_scope = _ck._resolve_scope
		self._orig_prior_date = _ck.prior_month_snapshot_date
		self._orig_has_baseline = _ck._card_has_prior_baseline
		self._orig_aggregate = _ck.aggregate_card_value
		self._mod = _ck

		# Always pretend the user has companies and a prior month exists.
		_ck._resolve_scope = lambda c: ["Test Company A"]
		_ck.prior_month_snapshot_date = lambda d: getdate("2026-04-30")

	def tearDown(self):
		self._mod._resolve_scope = self._orig_resolve_scope
		self._mod.prior_month_snapshot_date = self._orig_prior_date
		self._mod._card_has_prior_baseline = self._orig_has_baseline
		self._mod.aggregate_card_value = self._orig_aggregate

	def test_all_cards_missing_prior_emits_first_snapshot_template(self):
		"""When no card has a baseline, return the dedicated
		'first snapshot for this scope' headline."""
		self._mod._card_has_prior_baseline = lambda card, prior, allowed: False
		out = get_cockpit_headline("2026-05-06")
		self.assertEqual(
			out["headline"],
			"First snapshot for this scope — no prior month to compare.",
		)

	def test_one_card_with_baseline_one_without_uses_only_real_one(self):
		"""When a subset of cards have baselines, only those drive the
		headline. The card without baseline is filtered before
		significance evaluation."""
		# sundry_creditors has baseline; everyone else doesn't.
		self._mod._card_has_prior_baseline = (
			lambda card, prior, allowed: card["id"] == "sundry_creditors"
		)
		# Mock current/prior values so sundry_creditors shows a +4.1 Cr delta.
		def fake_aggregate(card, snap, companies=None):
			if card["id"] != "sundry_creditors":
				return 0.0
			# 81M current vs 40M prior on the prior date -> delta 41M
			snap = getdate(snap) if snap else None
			if snap == getdate("2026-05-06"):
				return 81_000_000.0
			if snap == getdate("2026-04-30"):
				return 40_000_000.0
			return 0.0
		self._mod.aggregate_card_value = fake_aggregate

		out = get_cockpit_headline("2026-05-06")
		# Only the one real card surfaces; template = single delta.
		self.assertEqual(
			out["headline"],
			"Sundry creditors up by ₹4.1 Cr since April.",
		)


class TestSpotlightCacheEmptyFallback(FrappeTestCase):
	"""Commit 7 F-3: `get_spotlight_cards` falls back to live
	recompute when the cache table has zero rows for the requested
	snapshot date. Pre-fix the endpoint silently returned all-zero
	cards (indistinguishable from a "no activity" cockpit). Now the
	fallback path runs and returns the live aggregate, with a
	frappe.log_error entry for observability.
	"""

	def setUp(self):
		super().setUp()
		# Pick a date that has snapshot rows but no spotlight cache
		# rows. We pick a date 90 days in the past + nuke any cache
		# rows for that date as our test setup.
		from datetime import timedelta
		self.test_date = (getdate(today()) - timedelta(days=90)).isoformat()

	def test_get_spotlight_cards_falls_back_when_cache_empty(self):
		# Make sure there's no cache row for self.test_date (DELETE
		# only fires if some test seeded one previously; harmless
		# otherwise).
		frappe.db.sql(
			"DELETE FROM `tabDGV Spotlight Cache` WHERE snapshot_date = %s",
			(self.test_date,),
		)
		frappe.db.commit()
		# Endpoint should not raise; should return the 6-card list
		# in the same shape as the cached path. Values may be zero
		# (no snapshot rows for that date on dev) -- what we're
		# testing is the SHAPE, not the magnitude.
		out = cockpit.get_spotlight_cards(self.test_date)
		self.assertEqual(len(out), 6)
		for card in out:
			self.assertIn("card_id", card)
			self.assertIn("value", card)
			self.assertIn("formatted_value", card)
			# Live-recompute path returns numeric (not None) values.
			self.assertIsNotNone(card["value"])
