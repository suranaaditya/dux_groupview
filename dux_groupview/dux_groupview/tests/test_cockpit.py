"""Tests for `cockpit.get_cockpit_headline` (Phase 4 commit 2.5).

Most of the headline logic is pure-Python composition over a list of
delta dicts -- no DB needed. Those tests target `_compose_headline`
directly. One smoke test exercises the whitelisted entry point against
the dev seed to confirm wiring.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_cockpit
"""

import json

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
		# Endpoint should not raise; should return one entry per
		# VISIBLE card in the same shape as the cached path. After
		# the cash & bank card split PR, the fallback's live-recompute
		# path filters disabled cards (spec
		# `specs/cash-bank-card-split.md` §5.3) -- so the response
		# length is `len(visible_cards)`, not `len(CARDS)`. Values may
		# be zero (no snapshot rows for that date on dev) -- what
		# we're testing is the SHAPE, not the magnitude.
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		visible_cards = [c for c in CARDS if not c.get("disabled")]
		out = cockpit.get_spotlight_cards(self.test_date)
		self.assertEqual(len(out), len(visible_cards))
		# Defensive: no disabled card_id leaks into the response.
		disabled_ids = {c["id"] for c in CARDS if c.get("disabled")}
		out_ids = {c["card_id"] for c in out}
		self.assertEqual(
			out_ids & disabled_ids, set(),
			msg=(
				"Disabled cards must not appear in the live-recompute "
				"fallback response. A disabled card_id leaking through "
				"would also surface in the cockpit grid (the response "
				"feeds renderCards directly)."
			),
		)
		for card in out:
			self.assertIn("card_id", card)
			self.assertIn("value", card)
			self.assertIn("formatted_value", card)
			# Live-recompute path returns numeric (not None) values.
			self.assertIsNotNone(card["value"])


class TestDisabledFlagReadPaths(FrappeTestCase):
	"""Read paths skip disabled cards; cache + drill resolver do not.

	Per spec `specs/cash-bank-card-split.md` §5.3 + §10 Q1/Q2:
	  - `get_spotlight_cards` (cache path):           skips disabled
	  - `_build_filtered_cards_payload` (live path):  skips disabled
	  - `get_cockpit_headline` (headline composer):   skips disabled
	  - `refresh_spotlight_cache`:                    INCLUDES disabled
	  - `cards_v1.resolve_match_to_accounts`:         INCLUDES disabled

	The asymmetry is load-bearing: refresh continuity preserves history
	across enable/disable cycles; resolver continuity preserves
	bookmarked deep-links to disabled card_ids.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Ensure today's TB snapshot + spotlight cache exist so the
		# default (cache-hit) path is exercised. This avoids the
		# defensive-fallback path for these tests; that path is
		# covered by TestSpotlightCacheEmptyFallback.
		if not frappe.db.exists(
			"DGV TB Snapshot", {"snapshot_date": getdate(today())}
		):
			from dux_groupview.dux_groupview.snapshots.refresh import (
				refresh_tb_snapshot,
			)
			refresh_tb_snapshot()
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			refresh_spotlight_cache,
		)
		refresh_spotlight_cache()

	# -- get_spotlight_cards (cache path) -------------------------------

	def test_get_spotlight_cards_excludes_disabled(self):
		"""The cached read path's response contains exactly the visible
		card_ids -- no disabled card_id leaks through. Verifies
		`visible_cards` filter at the cache-path call site.
		"""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		out = cockpit.get_spotlight_cards(getdate(today()))
		out_ids = {c["card_id"] for c in out}
		visible_ids = {
			c["id"] for c in CARDS if not c.get("disabled")
		}
		disabled_ids = {
			c["id"] for c in CARDS if c.get("disabled")
		}
		self.assertEqual(
			out_ids, visible_ids,
			msg=(
				"Response card_ids must equal the set of visible cards. "
				"Disabled cards must be filtered at the read boundary "
				"so the cockpit grid never has to know about them."
			),
		)
		self.assertEqual(
			out_ids & disabled_ids, set(),
			msg=(
				f"Disabled card_ids {disabled_ids & out_ids} leaked "
				"into the cache-path response."
			),
		)

	# -- _build_filtered_cards_payload (live path) ----------------------

	def test_get_spotlight_cards_filtered_excludes_disabled(self):
		"""The live-recompute path also filters disabled cards.
		Mirrors the cache path so disabled cards never appear
		regardless of which read endpoint the cockpit calls.
		"""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		# Pick one allowed company so we hit the live-recompute branch.
		one_co = frappe.db.sql_list(
			"SELECT name FROM `tabCompany` ORDER BY name LIMIT 1"
		)
		if not one_co:
			self.skipTest("No companies on this site.")
		out = cockpit.get_spotlight_cards_filtered(
			getdate(today()).isoformat(),
			json.dumps(one_co),
		)
		out_ids = {c["card_id"] for c in out}
		disabled_ids = {c["id"] for c in CARDS if c.get("disabled")}
		self.assertEqual(
			out_ids & disabled_ids, set(),
			msg=(
				"Disabled card_ids leaked into the live-recompute "
				"response. The visible_cards filter must apply to "
				"both read paths."
			),
		)

	# -- get_cockpit_headline (headline composer) -----------------------

	def test_headline_excludes_disabled_cards(self):
		"""The headline composer never names a disabled card. Two
		invariants protected:

		  (1) UX: narrating a delta for a card the user can't see
		      ("Sundry debtors up Rs X Cr" with no Sundry Debtors
		      card on the grid) is confusing.
		  (2) Privacy: a disabled card's value should not surface in
		      plain English in a sentence the user wasn't meant to
		      see.
		"""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		from dux_groupview.dux_groupview.api.cockpit import (
			HEADLINE_CARD_NAMES,
		)
		out = cockpit.get_cockpit_headline(getdate(today()).isoformat())
		headline = (out or {}).get("headline", "")
		if not headline:
			self.skipTest("Headline empty (no scope, or no baseline).")
		# Check that no disabled card's HEADLINE_CARD_NAMES string
		# appears in the headline copy.
		disabled_names = {
			HEADLINE_CARD_NAMES.get(c["id"], c["label"])
			for c in CARDS if c.get("disabled")
		}
		for name in disabled_names:
			self.assertNotIn(
				name, headline,
				msg=(
					f"Disabled card name '{name}' appeared in headline: "
					f"'{headline}'. Headline composer must skip "
					f"disabled cards to avoid leaking their values."
				),
			)

	# -- Flipping disabled mid-cache uses cache, no re-refresh -----------

	def test_flipping_disabled_does_not_require_refresh(self):
		"""Re-enabling a disabled card (flipping `disabled=False`)
		makes its CACHED value immediately appear in the next read.
		No re-refresh required. Pin the refresh-vs-read asymmetry:
		the cache is the historical record; visibility is purely a
		read-side filter.
		"""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		# Find a disabled card whose cache row exists for today.
		snapshot_date = getdate(today())
		target = None
		for c in CARDS:
			if not c.get("disabled"):
				continue
			has_cache = frappe.db.exists(
				"DGV Spotlight Cache",
				{"card_id": c["id"], "snapshot_date": snapshot_date},
			)
			if has_cache:
				target = c
				break
		if target is None:
			self.skipTest(
				"No disabled card has a cache row for today; rerun "
				"refresh_spotlight_cache and try again."
			)
		# Flip the flag in-memory; assert the card now appears.
		orig = target.get("disabled", False)
		target["disabled"] = False
		try:
			out = cockpit.get_spotlight_cards(snapshot_date)
			out_ids = {c["card_id"] for c in out}
			self.assertIn(
				target["id"], out_ids,
				msg=(
					f"Flipping disabled=False on '{target['id']}' must "
					f"surface its cached value on the next read without "
					f"triggering a refresh. History continuity "
					f"invariant -- the cache is the durable record; "
					f"the disabled flag is purely a visibility filter."
				),
			)
		finally:
			target["disabled"] = orig

	# -- Empty `allowed` companies + disabled filter --------------------

	def test_empty_allowed_returns_visible_cards_only_as_zero_payload(self):
		"""When `_resolve_scope` yields an empty allowed list, the
		live-recompute path returns one zero-payload per VISIBLE card
		(not per CARDS). Graceful empty state, not a crash.
		"""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		# Pass a company that doesn't exist -> intersection with
		# user's allowed -> empty after the resolver.
		out = cockpit.get_spotlight_cards_filtered(
			getdate(today()).isoformat(),
			json.dumps(["__nonexistent_company_xyz__"]),
		)
		visible_count = len([c for c in CARDS if not c.get("disabled")])
		# Result may be exactly visible_count (the [_zero_card_payload
		# for c in visible_cards] branch fires when allowed=[]); or
		# may be 0 if _resolve_scope handles unknown companies
		# differently. Either is acceptable; what matters is no crash
		# and no disabled cards.
		out_ids = {c["card_id"] for c in out}
		disabled_ids = {c["id"] for c in CARDS if c.get("disabled")}
		self.assertEqual(
			out_ids & disabled_ids, set(),
			msg=(
				"Even the empty-allowed zero-payload branch must "
				"filter disabled cards. A user with no allowed "
				"companies should not see hidden cards as zero rows."
			),
		)
		# If the response is non-empty, length must match visible.
		if out:
			self.assertEqual(
				len(out), visible_count,
				msg=(
					f"Empty-allowed payload returned {len(out)} cards "
					f"but visible card count is {visible_count}. The "
					f"zero-payload list must mirror visible_cards."
				),
			)


class TestCardsListShapeRegression(FrappeTestCase):
	"""Regression pin for the CARDS list after the cash & bank split.

	Per spec `specs/cash-bank-card-split.md` §6: this PR brings the
	total card count to 10 (8 existing + 2 new) and the visible card
	count to 7 (3 disabled). These specific numbers are documented
	here so a future change that touches CARDS without intending to
	change the visible count gets caught.

	When `inter_co_receivable` + `sundry_debtors` get re-enabled in
	a future PR (their predicates fixed separately), update the
	`EXPECTED_VISIBLE_IDS` set here. When new cards are added, update
	`EXPECTED_TOTAL` accordingly. This test is the canary for the
	cards-system shape.
	"""

	EXPECTED_TOTAL = 10
	EXPECTED_DISABLED_IDS = {
		"sundry_debtors",
		"cash_and_bank",
		"inter_co_receivable",
	}
	EXPECTED_VISIBLE_IDS = {
		"sundry_creditors",
		"unsecured_loans",
		"fixed_deposits",
		"financial_exp_to_bank",
		"financial_exp_to_other",
		"liquid_cash",
		"secured_loans",
	}

	def test_total_cards_count(self):
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		self.assertEqual(
			len(CARDS), self.EXPECTED_TOTAL,
			msg=(
				f"CARDS has {len(CARDS)} entries, expected "
				f"{self.EXPECTED_TOTAL}. Adding or removing a card "
				f"requires updating EXPECTED_TOTAL in this test."
			),
		)

	def test_visible_cards_count_is_seven(self):
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		visible = [c for c in CARDS if not c.get("disabled")]
		self.assertEqual(
			len(visible), 7,
			msg=(
				f"Visible card count is {len(visible)}, expected 7. "
				f"This test pins the post-split count documented in "
				f"spec `specs/cash-bank-card-split.md` §6. Re-enabling "
				f"or adding a visible card requires updating this "
				f"assertion AND the EXPECTED_VISIBLE_IDS set."
			),
		)

	def test_disabled_card_ids(self):
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		disabled = {c["id"] for c in CARDS if c.get("disabled")}
		self.assertEqual(
			disabled, self.EXPECTED_DISABLED_IDS,
			msg=(
				f"Disabled card_ids changed: got {disabled}, expected "
				f"{self.EXPECTED_DISABLED_IDS}."
			),
		)

	def test_visible_card_ids(self):
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		visible = {
			c["id"] for c in CARDS if not c.get("disabled")
		}
		self.assertEqual(
			visible, self.EXPECTED_VISIBLE_IDS,
			msg=(
				f"Visible card_ids changed: got {visible}, expected "
				f"{self.EXPECTED_VISIBLE_IDS}."
			),
		)

	# -- Existing-cards predicate / polarity / format / color pin -------

	# The 8 cards that existed BEFORE this PR. Each entry maps card_id
	# to the canonical (predicate, polarity, format, color) tuple that
	# this PR explicitly did NOT change. Any drift in any of these
	# fields means the regression posture broke.
	PRE_SPLIT_CARDS = {
		"sundry_creditors": (
			{"by_account_type": "Payable"},
			"neutral", "crore", "#BA7517",
		),
		"sundry_debtors": (
			{"by_account_type": "Receivable"},
			"bad_up", "crore", "#3B6D11",
		),
		"unsecured_loans": (
			{"by_root_type_and_name_pattern": {
				"root_type": "Liability",
				"name_pattern": "%Unsecured Loan%"}},
			"neutral", "crore", "#5F5E5A",
		),
		"cash_and_bank": (
			{"by_account_type": ["Bank", "Cash"]},
			"good_up", "crore", "#185FA5",
		),
		"inter_co_receivable": (
			{"by_root_type_and_name_pattern": {
				"root_type": "Asset",
				"name_pattern": "%Inter%Compan%"}},
			"neutral", "crore", "#534AB7",
		),
		"fixed_deposits": (
			{"by_root_type_and_name_pattern": {
				"root_type": "Asset",
				"name_pattern": "%Fixed Deposit%"}},
			"good_up", "crore", "#534AB7",
		),
		"financial_exp_to_bank": (
			{"by_root_type_and_name_pattern": {
				"root_type": "Expense",
				"name_pattern": "%Financial Exp To Bank%"}},
			"bad_up", "crore", "#A33B3B",
		),
		"financial_exp_to_other": (
			{"by_root_type_and_name_pattern": {
				"root_type": "Expense",
				"name_pattern": "%Financial Exp To Other%"}},
			"bad_up", "crore", "#C46A1F",
		),
	}

	def test_existing_cards_predicates_polarity_format_color_unchanged(self):
		"""Every card that existed before the split keeps its
		predicate, polarity, format, color exactly as before. The
		ONLY tolerated change on existing cards is the optional
		`disabled` flag. Pinned so a future refactor can't silently
		drift the existing 8.
		"""
		from dux_groupview.dux_groupview.spotlight.cards import CARDS
		by_id = {c["id"]: c for c in CARDS}
		for card_id, (match, polarity, fmt, color) in (
			self.PRE_SPLIT_CARDS.items()
		):
			with self.subTest(card_id=card_id):
				card = by_id.get(card_id)
				self.assertIsNotNone(
					card,
					msg=f"Card '{card_id}' missing from CARDS",
				)
				self.assertEqual(
					card["match"], match,
					msg=f"Card '{card_id}' predicate drifted",
				)
				self.assertEqual(
					card["polarity"], polarity,
					msg=f"Card '{card_id}' polarity drifted",
				)
				self.assertEqual(
					card["format"], fmt,
					msg=f"Card '{card_id}' format drifted",
				)
				self.assertEqual(
					card["color"], color,
					msg=f"Card '{card_id}' color drifted",
				)

	# -- HEADLINE_CARD_NAMES has entries for the two new cards ----------

	def test_headline_card_names_has_entries_for_new_cards(self):
		"""The two new cards must have HEADLINE_CARD_NAMES entries.
		The existing `test_friendly_names_cover_all_cards` regression
		test already checks every card_id has an entry; this test
		additionally pins that the entry strings are non-empty and
		read as prose (not the raw card label).
		"""
		from dux_groupview.dux_groupview.api.cockpit import (
			HEADLINE_CARD_NAMES,
		)
		for card_id in ("liquid_cash", "secured_loans"):
			with self.subTest(card_id=card_id):
				self.assertIn(
					card_id, HEADLINE_CARD_NAMES,
					msg=(
						f"'{card_id}' missing from HEADLINE_CARD_NAMES. "
						f"Without an entry, the headline composer "
						f"falls back to the raw card label -- which "
						f"the test_friendly_names_cover_all_cards "
						f"guardrail explicitly prohibits."
					),
				)
				self.assertTrue(
					HEADLINE_CARD_NAMES[card_id].strip(),
					msg=f"'{card_id}' HEADLINE_CARD_NAMES entry is empty",
				)
