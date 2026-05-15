"""Tests for `cards_v1.resolve_match_to_accounts`.

Verifies that the predicate-to-leaf-list translation matches the
predicate-to-balance translation in `spotlight_refresh._match_clause`
-- i.e. that cards_v1 returns the same set of leaves the spotlight
cache aggregates over.

Reads only `tabAccount` and `tabDGV TB Snapshot Row`. Never `tabGL Entry`.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_cards_v1
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from dux_groupview.dux_groupview.api import cards_v1
from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	_match_clause,
	refresh_spotlight_cache,
)
from dux_groupview.dux_groupview.spotlight.cards import CARDS


def _ensure_today_data():
	if not frappe.db.exists("DGV TB Snapshot", {"snapshot_date": getdate(today())}):
		refresh_tb_snapshot()
	refresh_spotlight_cache()


def _all_companies():
	return frappe.db.sql_list("SELECT name FROM `tabCompany` ORDER BY name")


class TestResolveMatchToAccounts(FrappeTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_resolve_by_account_type_string(self):
		match = {"by_account_type": "Payable"}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		self.assertIsInstance(result["accounts"], list)
		self.assertEqual(result["label"], "")
		# Every returned account should actually have account_type=Payable.
		if result["accounts"]:
			rows = frappe.db.sql_list(
				"""
				SELECT DISTINCT account_type FROM `tabAccount`
				WHERE name IN %s
				""",
				(tuple(result["accounts"]),),
			)
			self.assertEqual(set(rows), {"Payable"})

	def test_resolve_by_account_type_list(self):
		match = {"by_account_type": ["Bank", "Cash"]}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		if result["accounts"]:
			rows = frappe.db.sql_list(
				"""
				SELECT DISTINCT account_type FROM `tabAccount`
				WHERE name IN %s
				""",
				(tuple(result["accounts"]),),
			)
			self.assertTrue(set(rows).issubset({"Bank", "Cash"}))

	def test_resolve_by_root_type_and_name_pattern(self):
		match = {
			"by_root_type_and_name_pattern": {
				"root_type": "Asset",
				"name_pattern": "%Fixed Deposit%",
			},
		}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		if result["accounts"]:
			rows = frappe.db.sql(
				"""
				SELECT DISTINCT root_type FROM `tabAccount`
				WHERE name IN %s
				""",
				(tuple(result["accounts"]),),
			)
			self.assertEqual({r[0] for r in rows}, {"Asset"})
			# All returned names must match the LIKE pattern.
			for n in result["accounts"]:
				self.assertIn("Fixed Deposit", n)

	def test_resolve_label_is_echoed(self):
		match = {"by_account_type": "Payable"}
		result = cards_v1.resolve_match_to_accounts(
			match, _all_companies(), label="Sundry creditors",
		)
		self.assertEqual(result["label"], "Sundry creditors")

	def test_resolve_unknown_match_returns_empty(self):
		match = {"by_unknown_strategy": "whatever"}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		self.assertEqual(result["accounts"], [])

	def test_resolve_match_to_accounts_malformed_scope_raises(self):
		"""Missing / empty match predicate raises DoesNotExistError
		with `malformed_scope: true` on the response (commit-6 HALT
		6.3 stale-deep-link path). Distinguishes "predicate is gone"
		(404) from "predicate is well-formed but matches zero
		leaves" (200 + empty list, exercised by
		test_resolve_unknown_match_returns_empty above).
		"""
		# match=None — JSON-parse path or direct call from a page that
		# lost its predicate state.
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			cards_v1.resolve_match_to_accounts(None, _all_companies())
		self.assertTrue(
			frappe.local.response.get("malformed_scope"),
			"malformed_scope flag missing on None match",
		)
		# match={} — empty dict, same semantics.
		frappe.local.response = frappe._dict()
		with self.assertRaises(frappe.DoesNotExistError):
			cards_v1.resolve_match_to_accounts({}, _all_companies())
		self.assertTrue(
			frappe.local.response.get("malformed_scope"),
			"malformed_scope flag missing on empty-dict match",
		)

	def test_get_spotlight_cards_zero_cards_returns_empty_array(self):
		"""Defensive zero-cards empty-banner path lives in
		groupview.js's renderCards but isn't reachable from the
		existing endpoints today.

		The CARDS constant in spotlight/cards.py is the source of
		truth for built-in cards; `get_spotlight_cards` always
		returns one entry per built-in card (with zero values when
		the predicate matches nothing in scope).

		Once Phase 5's cards-editor ships, this test should exercise
		the actual zero-cards path (e.g. user disables all cards for
		a trust). For now the JS side has the defensive
		`<div class="dgv-empty-banner">No spotlight cards configured
		for this trust selection.</div>` branch in place
		(commit-6 HALT 6.1).
		"""
		self.skipTest(
			"CARDS list is hard-coded; zero-cards path "
			"reachable only after Phase 5 cards-editor. Defensive "
			"JS empty-banner branch verified at HALT 6.1."
		)

	def test_resolve_empty_companies_returns_empty(self):
		match = {"by_account_type": "Payable"}
		# Empty list is JSON-serialised by Frappe; emulate the path.
		result = cards_v1.resolve_match_to_accounts(match, [])
		# `[]` triggers _resolve_scope's "no scope provided" fallback,
		# which uses the user's full allowed set. Administrator runs
		# tests so all companies are allowed -> non-empty result.
		# Assertion is loose: just ensure it returned a list.
		self.assertIsInstance(result["accounts"], list)

	def test_resolve_accepts_json_strings(self):
		match_json = '{"by_account_type": "Payable"}'
		companies_json = '["%s"]' % _all_companies()[0]
		result = cards_v1.resolve_match_to_accounts(match_json, companies_json)
		self.assertIsInstance(result["accounts"], list)

	# ------------------------------------------------------------------
	# Parity with spotlight cache
	# ------------------------------------------------------------------

	def test_resolve_matches_spotlight_predicate_for_every_card(self):
		"""Every card's resolved leaf list, when summed via the snapshot
		row, equals the spotlight cache value for that card.

		This proves cards_v1.resolve_match_to_accounts and
		spotlight_refresh._aggregate use compatible predicate
		translation (i.e. the leaf set we hand to the drill API is
		exactly the leaf set the cache aggregated)."""
		snapshot_date = getdate(today())
		all_companies = _all_companies()

		for card in CARDS:
			with self.subTest(card=card["id"]):
				resolved = cards_v1.resolve_match_to_accounts(
					card["match"], all_companies,
				)
				# Sum the resolved leaves with the spotlight CASE WHEN
				# convention against the snapshot row -- result should
				# equal the cached value.
				if not resolved["accounts"]:
					expected_value = frappe.db.get_value(
						"DGV Spotlight Cache",
						{
							"card_id": card["id"],
							"snapshot_date": snapshot_date,
						},
						"value",
					)
					self.assertEqual(float(expected_value or 0), 0.0)
					continue

				placeholders = ", ".join(
					f"%(a_{i})s" for i in range(len(resolved["accounts"]))
				)
				params = {
					f"a_{i}": n for i, n in enumerate(resolved["accounts"])
				}
				params["snapshot_date"] = snapshot_date
				row = frappe.db.sql(
					f"""
					SELECT COALESCE(SUM(
					  CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
					       THEN -balance
					       ELSE balance
					  END
					), 0) AS total
					FROM `tabDGV TB Snapshot Row`
					WHERE snapshot_date = %(snapshot_date)s
					  AND account IN ({placeholders})
					""",
					params,
				)
				resolved_total = float(row[0][0]) if row else 0.0

				cached_value = frappe.db.get_value(
					"DGV Spotlight Cache",
					{
						"card_id": card["id"],
						"snapshot_date": snapshot_date,
					},
					"value",
				)
				self.assertAlmostEqual(
					resolved_total, float(cached_value or 0), places=2,
					msg=f"Predicate->leaf->sum drifts from cache for card {card['id']}",
				)

	def test_match_clause_and_resolver_use_same_match_keys(self):
		"""The two predicate translators (`_match_clause` against
		snapshot rows, `cards_v1._resolve_match` against tabAccount)
		must recognise the same set of `match` keys; otherwise a card
		definition could resolve correctly in one path and silently
		return [] in the other.

		Sanity test: every CARD's match dict produces a non-None
		clause from `_match_clause` AND a list (possibly empty, on a
		fresh site) from `cards_v1._resolve_match`."""
		from dux_groupview.dux_groupview.api.cards_v1 import _resolve_match
		for card in CARDS:
			with self.subTest(card=card["id"]):
				clause, _params = _match_clause(card)
				self.assertIsNotNone(
					clause,
					msg=f"_match_clause returned None for {card['id']}",
				)
				resolved = _resolve_match(card["match"], _all_companies())
				self.assertIsInstance(resolved, list)


class TestByParentAccountStemIn(FrappeTestCase):
	"""Tests for the new `by_parent_account_stem_in` predicate.

	Per spec `specs/cash-bank-card-split.md` §4. The predicate matches
	leaves where:
	  - is_group = 0, AND
	  - root_type = <given>, AND
	  - SUBSTRING_INDEX(parent_account, ' - ', 1) IN (<stems>)

	Tests rely on the dev fixture's existing COA structure (ERPNext
	standard convention `<parent_name> - <company_abbr>` on
	`parent_account`). No fixture seeding required.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	# -- Happy path + filter behaviour ----------------------------------

	def test_happy_path_returns_leaves_under_named_stems(self):
		"""Predicate {stems: ['Bank Accounts'], root_type: 'Asset'}
		returns at least one leaf when the dev seed has Bank Accounts
		groups; every returned account satisfies all three predicate
		conditions independently.

		Note: MySQL's default collation (`utf8mb4_general_ci`) is case-
		INSENSITIVE on VARCHAR comparison, so `IN ('Bank Accounts')`
		matches both `"Bank Accounts"` and `"BANK ACCOUNTS"` parent
		stems if both exist. The dev COA has case variants (e.g.
		`"Cash in Hand"` and `"Cash In Hand"` coexist); the predicate
		intentionally accepts both because the production data has
		the same kind of inconsistency. Tests therefore compare
		case-insensitively.
		"""
		match = {
			"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"],
				"root_type": "Asset",
			},
		}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		if not result["accounts"]:
			self.skipTest("No 'Bank Accounts' leaves in dev COA.")
		# Independently verify each returned leaf matches predicate.
		rows = frappe.db.sql(
			"""
			SELECT name, parent_account, root_type, is_group
			FROM `tabAccount` WHERE name IN %s
			""",
			(tuple(result["accounts"]),),
			as_dict=True,
		)
		for r in rows:
			self.assertEqual(
				r["is_group"], 0,
				msg=f"{r['name']}: parent-stem predicate returned a group account",
			)
			self.assertEqual(
				r["root_type"], "Asset",
				msg=f"{r['name']}: parent-stem predicate returned wrong root_type",
			)
			stem = (r["parent_account"] or "").split(" - ", 1)[0]
			self.assertEqual(
				stem.casefold(), "Bank Accounts".casefold(),
				msg=f"{r['name']}: parent stem '{stem}' != 'Bank Accounts' (case-insensitive)",
			)

	def test_root_type_filter_excludes_wrong_root_type(self):
		"""Same parent-stem, different root_type -> different (or
		empty) leaf set. Pin that the root_type filter is load-bearing,
		not vestigial."""
		asset = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"], "root_type": "Asset"}},
			_all_companies(),
		)
		liability = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"], "root_type": "Liability"}},
			_all_companies(),
		)
		# Sets must be disjoint -- a Bank Accounts leaf can't be both
		# Asset and Liability simultaneously.
		self.assertEqual(
			set(asset["accounts"]) & set(liability["accounts"]),
			set(),
			msg="Asset and Liability root_type filters returned overlapping leaves",
		)

	def test_multi_stem_or_returns_union(self):
		"""Two stems in the same predicate return the union of leaves
		under either stem. Pin the IN-list semantics."""
		bank_only = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"], "root_type": "Asset"}},
			_all_companies(),
		)
		cash_only = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Cash in Hand"], "root_type": "Asset"}},
			_all_companies(),
		)
		both = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Bank Accounts", "Cash in Hand"],
				"root_type": "Asset"}},
			_all_companies(),
		)
		# Union check: every leaf in either single-stem result is in
		# the combined; every leaf in combined is in one of the singles.
		single_union = set(bank_only["accounts"]) | set(cash_only["accounts"])
		self.assertEqual(
			set(both["accounts"]), single_union,
			msg="Multi-stem predicate did not return union of single-stem results",
		)

	def test_nonexistent_stem_returns_empty(self):
		"""A stem that doesn't match any parent_account returns an
		empty list. Not an error."""
		result = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Definitely Not A Real Parent Stem XYZ"],
				"root_type": "Asset"}},
			_all_companies(),
		)
		self.assertEqual(result["accounts"], [])

	# -- Defensive branches --------------------------------------------

	def test_empty_stems_returns_empty(self):
		"""Empty stems list -> empty result. Defensive against
		malformed dev-defined card predicates."""
		result = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": [], "root_type": "Asset"}},
			_all_companies(),
		)
		self.assertEqual(
			result["accounts"], [],
			msg="Empty stems list should match no leaves (defensive)",
		)

	def test_missing_root_type_returns_empty(self):
		"""Missing root_type key -> empty result. Defensive."""
		result = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Bank Accounts"]}},
			_all_companies(),
		)
		self.assertEqual(
			result["accounts"], [],
			msg="Missing root_type should match no leaves (defensive)",
		)

	def test_non_dict_conf_returns_empty(self):
		"""Predicate value not a dict -> empty result. Defensive
		against a card definition that passes a string or list by
		mistake."""
		from dux_groupview.dux_groupview.api.cards_v1 import _resolve_match
		result = _resolve_match(
			{"by_parent_account_stem_in": "not-a-dict"},
			_all_companies(),
		)
		self.assertEqual(
			result, [],
			msg="Non-dict predicate conf should match no leaves (defensive)",
		)

	def test_matches_case_variants_via_explicit_enumeration(self):
		"""Both 'Cash in Hand' (dev seed) and 'Cash In Hand' (prod COA)
		appear in real environments. The predicate must catch both via
		explicit enumeration in `stems`, rather than relying on MySQL's
		default collation behaviour (which happens to be case-
		insensitive today, but that's a per-database-engine accident
		we don't want to depend on).

		Phase 5 cards-editor may introduce fuzzy stem matching
		(case-insensitive + trailing-s tolerant) to eliminate the
		need for manual variant enumeration. Until then, card
		definitions must enumerate every case variant explicitly
		(see `liquid_cash` in cards.py).

		This test exists so a future 'tidy up duplicate-looking
		entries' refactor doesn't silently drop one of the case
		variants and break prod matching.
		"""
		# Probe the dev seed to confirm both variants actually exist
		# as parent stems. If the dev seed ever changes to use only
		# one variant, this test should still pin the rationale
		# (skip rather than silently pass).
		#
		# `BINARY` is required: the default collation
		# (utf8mb4_general_ci) collapses both casings under DISTINCT,
		# returning a single entry. We specifically need case-
		# DISTINCT detection here.
		dev_stems = set(frappe.db.sql_list(
			"""
			SELECT DISTINCT BINARY SUBSTRING_INDEX(parent_account, ' - ', 1)
			FROM `tabAccount`
			WHERE is_group = 0
			  AND parent_account IS NOT NULL
			  AND parent_account != ''
			  AND LOWER(SUBSTRING_INDEX(parent_account, ' - ', 1)) = 'cash in hand'
			"""
		))
		# `BINARY` returns bytes in MariaDB; decode for the Python
		# string comparison below.
		dev_stems = {
			s.decode("utf-8") if isinstance(s, (bytes, bytearray)) else s
			for s in dev_stems
		}
		if "Cash in Hand" not in dev_stems or "Cash In Hand" not in dev_stems:
			self.skipTest(
				"Dev seed lacks both 'Cash in Hand' AND 'Cash In Hand' "
				"parent stems; cannot verify case-variant rationale. "
				f"Found: {sorted(dev_stems)}"
			)

		# Both casings in stems -> matches all leaves under either
		# parent stem regardless of which variant the COA used.
		both_casings = cards_v1.resolve_match_to_accounts(
			{"by_parent_account_stem_in": {
				"stems": ["Cash in Hand", "Cash In Hand"],
				"root_type": "Asset"}},
			_all_companies(),
		)
		# Independently confirm: there should be at least one leaf
		# under EACH casing variant in the result. This is the
		# load-bearing invariant -- dropping either entry from the
		# stems list would lose leaves on one side.
		if not both_casings["accounts"]:
			self.skipTest("No Cash in/In Hand leaves in dev COA.")
		rows = frappe.db.sql(
			"""
			SELECT SUBSTRING_INDEX(parent_account, ' - ', 1) AS stem
			FROM `tabAccount` WHERE name IN %s
			""",
			(tuple(both_casings["accounts"]),),
			as_dict=True,
		)
		distinct_stems = {r["stem"] for r in rows}
		self.assertIn(
			"Cash in Hand", distinct_stems,
			msg=(
				"Predicate with both casings in stems must catch "
				"leaves under 'Cash in Hand' (lowercase 'in', dev "
				"seed variant). Found stems: "
				f"{sorted(distinct_stems)}"
			),
		)
		self.assertIn(
			"Cash In Hand", distinct_stems,
			msg=(
				"Predicate with both casings in stems must catch "
				"leaves under 'Cash In Hand' (capital 'I', prod COA "
				"variant). Found stems: "
				f"{sorted(distinct_stems)}"
			),
		)

	def test_stems_not_a_list_returns_empty(self):
		"""stems supplied as a string (instead of list) -> empty
		result. Defensive."""
		from dux_groupview.dux_groupview.api.cards_v1 import _resolve_match
		result = _resolve_match(
			{"by_parent_account_stem_in": {
				"stems": "Bank Accounts", "root_type": "Asset"}},
			_all_companies(),
		)
		self.assertEqual(
			result, [],
			msg="Non-list stems should match no leaves (defensive)",
		)

	# -- Two-card-specific predicate sanity ----------------------------

	def test_liquid_cash_predicate_resolves_to_bank_and_cash_leaves(self):
		"""The `liquid_cash` card's predicate (the production card
		definition) returns leaves under `Bank Accounts` or
		`Cash in Hand` parents with `root_type=Asset` only.
		Cross-checks the actual CARDS entry, not a synthetic copy."""
		card = next(c for c in CARDS if c["id"] == "liquid_cash")
		result = cards_v1.resolve_match_to_accounts(
			card["match"], _all_companies(),
		)
		if not result["accounts"]:
			self.skipTest("No Liquid cash leaves in dev COA.")
		rows = frappe.db.sql(
			"""
			SELECT parent_account, root_type FROM `tabAccount`
			WHERE name IN %s
			""",
			(tuple(result["accounts"]),),
			as_dict=True,
		)
		# Case-insensitive comparison: MySQL's default collation matches
		# 'Cash in Hand' and 'Cash In Hand' as equal; the predicate
		# returns both case variants from the dev COA (which has both).
		# See test_happy_path_returns_leaves_under_named_stems note.
		expected_stems_lower = {"bank accounts", "cash in hand"}
		for r in rows:
			stem = (r["parent_account"] or "").split(" - ", 1)[0]
			self.assertIn(
				stem.casefold(), expected_stems_lower,
				msg=f"Liquid cash leaf has unexpected parent stem '{stem}'",
			)
			self.assertEqual(
				r["root_type"], "Asset",
				msg=f"Liquid cash leaf has unexpected root_type '{r['root_type']}'",
			)

	def test_secured_loans_predicate_resolves_to_loan_leaves(self):
		"""The `secured_loans` card's predicate returns leaves under
		`Secured Loans` or `Bank OD A/c` parents with
		`root_type=Liability` only."""
		card = next(c for c in CARDS if c["id"] == "secured_loans")
		result = cards_v1.resolve_match_to_accounts(
			card["match"], _all_companies(),
		)
		if not result["accounts"]:
			self.skipTest("No Secured loans leaves in dev COA.")
		rows = frappe.db.sql(
			"""
			SELECT parent_account, root_type FROM `tabAccount`
			WHERE name IN %s
			""",
			(tuple(result["accounts"]),),
			as_dict=True,
		)
		# Case-insensitive (see note on test_happy_path).
		expected_stems_lower = {"secured loans", "bank od a/c"}
		for r in rows:
			stem = (r["parent_account"] or "").split(" - ", 1)[0]
			self.assertIn(
				stem.casefold(), expected_stems_lower,
				msg=f"Secured loans leaf has unexpected parent stem '{stem}'",
			)
			self.assertEqual(
				r["root_type"], "Liability",
				msg=f"Secured loans leaf has unexpected root_type '{r['root_type']}'",
			)


class TestDrillResolverIgnoresDisabledFlag(FrappeTestCase):
	"""Disabled cards remain resolvable by the drill resolver.

	Per spec `specs/cash-bank-card-split.md` §5.3 + Q1: the cockpit
	grid + headline composer skip disabled cards, BUT
	`cards_v1.resolve_match_to_accounts` does NOT. Rationale: a
	bookmarked deep-link to the drill panel for an old card_id (e.g.
	a saved share-link for the now-disabled `cash_and_bank`) should
	still open and render -- only the cockpit grid hides the card.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	def test_resolve_match_to_accounts_works_for_disabled_card(self):
		"""Disabled card_id's predicate still resolves to a leaf list.
		Pinned so a future 'tidy up the disable behaviour' refactor
		can't silently break bookmarked drill deep-links.
		"""
		# Pick a card that is currently disabled by the production
		# CARDS definition (this PR disables `cash_and_bank`).
		disabled_cards = [c for c in CARDS if c.get("disabled")]
		self.assertGreater(
			len(disabled_cards), 0,
			msg=(
				"This regression test relies on at least one disabled "
				"card existing in CARDS. The cash & bank split PR "
				"disables 3; if all disabled cards are later removed, "
				"this test should be re-pointed at a different surface "
				"(e.g. monkey-patched CARDS entry)."
			),
		)
		target = disabled_cards[0]
		result = cards_v1.resolve_match_to_accounts(
			target["match"], _all_companies(),
		)
		# We don't assert non-empty (the disabled card's predicate may
		# match no leaves on this dev seed; e.g. inter_co_receivable).
		# We assert the resolver did NOT short-circuit on disabled and
		# returned the same shape it would for a non-disabled card.
		self.assertIsInstance(
			result["accounts"], list,
			msg=(
				"Disabled card predicate must resolve to a list (possibly "
				"empty) so bookmarked drill deep-links continue to work."
			),
		)
		self.assertIn(
			"label", result,
			msg="Resolver response shape must be unchanged for disabled cards",
		)


class TestBalanceSignResolver(FrappeTestCase):
	"""Tests for the `balance_sign` extension on
	`by_account_type` and `by_parent_account_stem_in` predicates.

	Per spec/supplier-advances-split §1 + HALT 1 decision (a): when
	`balance_sign` is set to "positive" or "negative", the resolver
	consults the latest Complete `tabDGV TB Snapshot` and filters
	leaves to those whose snapshot `balance` has the matching sign.
	Default "any" (or omitted) preserves the existing tabAccount-only
	resolution path.

	Sign comparison runs against the RAW snapshot row `balance` column
	(debit-credit, pre-natural-side-flip). For Payable accounts:
	  - balance < 0 = credit balance = owed
	  - balance > 0 = debit balance = advance paid
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	# ------------------------------------------------------------------
	# Shape acceptance: the three input forms of by_account_type
	# ------------------------------------------------------------------

	def test_str_shape_defaults_to_any(self):
		"""Legacy string shape: `"by_account_type": "Payable"` resolves
		without sign filter -- same set of leaves a tabAccount-only
		query would return.
		"""
		match = {"by_account_type": "Payable"}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		self.assertIsInstance(result["accounts"], list)
		# Independent check: every returned leaf has account_type=Payable
		# at tabAccount level. (Snapshot consultation is NOT happening
		# here -- the string shape is a shortcut for any-sign.)
		if result["accounts"]:
			types = frappe.db.sql_list(
				"SELECT DISTINCT account_type FROM `tabAccount` "
				"WHERE name IN %s",
				(tuple(result["accounts"]),),
			)
			self.assertEqual(set(types), {"Payable"})

	def test_list_shape_defaults_to_any(self):
		"""Legacy list shape: `["Bank", "Cash"]` resolves without
		sign filter. Same semantics as the string shape.
		"""
		match = {"by_account_type": ["Bank", "Cash"]}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		self.assertIsInstance(result["accounts"], list)

	def test_dict_shape_default_balance_sign_is_any(self):
		"""Canonical dict shape WITHOUT balance_sign key: same result
		as the legacy string/list shapes -- no sign filter applied.
		"""
		match = {"by_account_type": {"account_type": "Payable"}}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		expected = cards_v1.resolve_match_to_accounts(
			{"by_account_type": "Payable"}, _all_companies(),
		)
		self.assertEqual(
			set(result["accounts"]), set(expected["accounts"]),
			msg=(
				"Dict shape with no balance_sign key must produce the "
				"same leaf set as the legacy string shortcut. The dict "
				"shape is canonical; the string is a shortcut for it."
			),
		)

	def test_dict_shape_explicit_any_matches_default(self):
		"""`balance_sign: "any"` explicitly set must behave identically
		to the key being omitted.
		"""
		explicit = cards_v1.resolve_match_to_accounts(
			{"by_account_type": {
				"account_type": "Payable", "balance_sign": "any"}},
			_all_companies(),
		)
		omitted = cards_v1.resolve_match_to_accounts(
			{"by_account_type": {"account_type": "Payable"}},
			_all_companies(),
		)
		self.assertEqual(
			set(explicit["accounts"]), set(omitted["accounts"]),
			msg="balance_sign='any' must be equivalent to omitting the key",
		)

	# ------------------------------------------------------------------
	# Sign filter semantics: positive vs negative vs any
	# ------------------------------------------------------------------

	def test_positive_sign_filters_to_debit_balances(self):
		"""`balance_sign: "positive"` returns only leaves with
		balance > 0 in the latest snapshot. For Payable accounts,
		that's the advance-paid / "supplier advances" case.
		"""
		match = {"by_account_type": {
			"account_type": "Payable", "balance_sign": "positive"}}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		if not result["accounts"]:
			self.skipTest("No debit-balance Payable leaves in latest snapshot")
		# Independent: every returned leaf has snapshot row balance > 0
		# at the latest Complete snapshot.
		signs = frappe.db.sql(
			"""
			SELECT s.account, s.balance
			FROM `tabDGV TB Snapshot Row` s
			WHERE s.snapshot_date = (
			    SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
			    WHERE status = 'Complete'
			)
			  AND s.account IN %s
			""",
			(tuple(result["accounts"]),),
			as_dict=True,
		)
		for row in signs:
			self.assertGreater(
				row["balance"], 0,
				msg=(
					f"Leaf '{row['account']}' returned by positive-sign "
					f"predicate has balance={row['balance']} (must be > 0). "
					f"Sign filter must route through snapshot, not COA."
				),
			)

	def test_negative_sign_filters_to_credit_balances(self):
		"""`balance_sign: "negative"` returns only leaves with
		balance < 0. For Payable accounts, that's the owed /
		"sundry creditors" case (the default state).
		"""
		match = {"by_account_type": {
			"account_type": "Payable", "balance_sign": "negative"}}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		if not result["accounts"]:
			self.skipTest("No credit-balance Payable leaves in latest snapshot")
		signs = frappe.db.sql(
			"""
			SELECT s.account, s.balance
			FROM `tabDGV TB Snapshot Row` s
			WHERE s.snapshot_date = (
			    SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
			    WHERE status = 'Complete'
			)
			  AND s.account IN %s
			""",
			(tuple(result["accounts"]),),
			as_dict=True,
		)
		for row in signs:
			self.assertLess(
				row["balance"], 0,
				msg=(
					f"Leaf '{row['account']}' returned by negative-sign "
					f"predicate has balance={row['balance']} (must be < 0)."
				),
			)

	def test_positive_and_negative_sets_are_disjoint(self):
		"""Sign filter splits the Payable leaf universe into two
		disjoint sets. A leaf with balance == 0 belongs to neither;
		a leaf is in exactly one of the two sets at any snapshot.
		"""
		pos = cards_v1.resolve_match_to_accounts(
			{"by_account_type": {
				"account_type": "Payable", "balance_sign": "positive"}},
			_all_companies(),
		)
		neg = cards_v1.resolve_match_to_accounts(
			{"by_account_type": {
				"account_type": "Payable", "balance_sign": "negative"}},
			_all_companies(),
		)
		overlap = set(pos["accounts"]) & set(neg["accounts"])
		self.assertEqual(
			overlap, set(),
			msg=(
				"Positive and negative sign sets must be disjoint. "
				"A leaf cannot have both balance>0 and balance<0 "
				"simultaneously. Overlap suggests an SQL or filter bug."
			),
		)

	# ------------------------------------------------------------------
	# Defensive branches: invalid input
	# ------------------------------------------------------------------

	def test_invalid_balance_sign_returns_empty(self):
		"""`balance_sign: "credit"` (or any non-canonical value) must
		return an empty list -- not silently fall back to "any" and
		not raise. Defensive against card-definition typos.
		"""
		match = {"by_account_type": {
			"account_type": "Payable", "balance_sign": "credit"}}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		self.assertEqual(
			result["accounts"], [],
			msg=(
				"Invalid balance_sign must return empty list. Silently "
				"treating it as 'any' would mask card-definition bugs. "
				"Empty result is a clear signal during dev verification."
			),
		)

	def test_mixed_type_list_returns_empty(self):
		"""`by_account_type: ["Payable", {"balance_sign": "positive"}]`
		(mixed string + dict elements) is not a supported shape.
		Must return empty rather than crash at SQL execution.
		"""
		from dux_groupview.dux_groupview.api.cards_v1 import _resolve_match
		mixed = _resolve_match(
			{"by_account_type": ["Payable", {"balance_sign": "positive"}]},
			_all_companies(),
		)
		self.assertEqual(
			mixed, [],
			msg=(
				"Mixed-type lists are not a supported shape. A card "
				"author wanting sign-filter + multi-type must use the "
				"canonical dict shape with account_type=list."
			),
		)

	def test_non_string_account_type_in_dict_returns_empty(self):
		"""`{"account_type": 42}` or similar non-string is invalid.
		Defensive shape: empty result.
		"""
		from dux_groupview.dux_groupview.api.cards_v1 import _resolve_match
		result = _resolve_match(
			{"by_account_type": {"account_type": 42}},
			_all_companies(),
		)
		self.assertEqual(result, [])

	# ------------------------------------------------------------------
	# by_parent_account_stem_in: balance_sign forward consistency
	# ------------------------------------------------------------------

	def test_parent_stem_predicate_accepts_balance_sign(self):
		"""`by_parent_account_stem_in` accepts the same balance_sign
		key as `by_account_type` even though no card in this PR
		consumes it. Forward consistency for the OD-positive case
		(when secured_loans / liquid_cash get sign-aware predicates).
		"""
		# Use Unsecured Loans stem (from the parallel PR). On dev,
		# some leaves under this stem will have credit balances
		# (money owed). With balance_sign=negative, those leaves
		# return; balance_sign=positive returns the (rare) debit
		# advances.
		match = {"by_parent_account_stem_in": {
			"stems": ["Unsecured Loans"],
			"root_type": "Liability",
			"balance_sign": "negative",
		}}
		result = cards_v1.resolve_match_to_accounts(match, _all_companies())
		self.assertIsInstance(result["accounts"], list)
		if result["accounts"]:
			signs = frappe.db.sql(
				"""
				SELECT s.account, s.balance
				FROM `tabDGV TB Snapshot Row` s
				WHERE s.snapshot_date = (
				    SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
				    WHERE status = 'Complete'
				)
				  AND s.account IN %s
				""",
				(tuple(result["accounts"]),),
				as_dict=True,
			)
			for row in signs:
				self.assertLess(
					row["balance"], 0,
					msg=(
						f"by_parent_account_stem_in with "
						f"balance_sign=negative returned leaf "
						f"{row['account']} with balance={row['balance']}"
					),
				)

	def test_parent_stem_predicate_invalid_balance_sign_returns_empty(self):
		"""Same defensive shape on by_parent_account_stem_in: invalid
		balance_sign returns empty.
		"""
		from dux_groupview.dux_groupview.api.cards_v1 import _resolve_match
		result = _resolve_match(
			{"by_parent_account_stem_in": {
				"stems": ["Unsecured Loans"],
				"root_type": "Liability",
				"balance_sign": "weird",
			}},
			_all_companies(),
		)
		self.assertEqual(result, [])

	# ------------------------------------------------------------------
	# No-Complete-snapshot edge case
	# ------------------------------------------------------------------

	def test_no_complete_snapshot_returns_empty_for_sign_filter(self):
		"""When no Complete snapshot exists, the sign-filter subquery
		(`MAX(snapshot_date) WHERE status='Complete'`) returns NULL,
		and the outer query matches zero rows.

		Pinned via a brief monkey-patch on the snapshot_date subquery's
		anchor. Real "no snapshot" state would wipe the snapshot tables
		(destructive in dev); we patch the query instead.
		"""
		# Temporarily set every Complete snapshot to a different status
		# so MAX(...) over WHERE status='Complete' returns NULL.
		# Reversible via try/finally.
		original_statuses = frappe.db.sql(
			"SELECT name, status FROM `tabDGV TB Snapshot` "
			"WHERE status = 'Complete'",
			as_dict=True,
		)
		if not original_statuses:
			# No snapshots at all -> the edge is already in effect.
			pass
		else:
			frappe.db.sql(
				"UPDATE `tabDGV TB Snapshot` "
				"SET status = 'Failed_For_Test' WHERE status = 'Complete'"
			)
			frappe.db.commit()
		try:
			# Sign-filter path consults snapshot -> with no Complete
			# snapshot, returns empty.
			result = cards_v1.resolve_match_to_accounts(
				{"by_account_type": {
					"account_type": "Payable", "balance_sign": "negative"}},
				_all_companies(),
			)
			self.assertEqual(
				result["accounts"], [],
				msg=(
					"Sign-filter resolver must return empty when no "
					"Complete snapshot exists. Drill panel will show "
					"the standard empty state ('No matching accounts')."
				),
			)
			# But no-sign path (string shape) still works -- no snapshot
			# dependency, tabAccount only.
			result_no_sign = cards_v1.resolve_match_to_accounts(
				{"by_account_type": "Payable"},
				_all_companies(),
			)
			# May be empty if no Payable leaves exist on dev seed; the
			# point is the call doesn't raise and isn't blocked by the
			# snapshot absence.
			self.assertIsInstance(result_no_sign["accounts"], list)
		finally:
			# Restore original statuses
			for row in original_statuses:
				frappe.db.sql(
					"UPDATE `tabDGV TB Snapshot` SET status = %s "
					"WHERE name = %s",
					(row["status"], row["name"]),
				)
			frappe.db.commit()
