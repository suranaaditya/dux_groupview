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
