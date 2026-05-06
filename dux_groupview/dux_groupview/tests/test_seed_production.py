"""Tests for `seed_production._select_trusts_to_seed`.

Covers the trust-subset filter introduced by the side PR
"fix/seed-scale-for-kvm". The filter is the only piece of
seed_rgi_named_data that has user-visible behaviour worth
unit-testing; the rest of the seed (companies + GL entry generation)
is integration-tested by running it on dev.

Reads `dux_groupview.dux_groupview.pivot.trust_groups.TRUSTS` only;
no DB writes.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_seed_production
"""

from frappe.tests.utils import FrappeTestCase

from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS
from dux_groupview.dux_groupview.test_data.seed_production import (
	_select_trusts_to_seed,
)


class TestSelectTrustsToSeed(FrappeTestCase):

	def test_default_returns_all_trusts(self):
		"""trusts=None -> full TRUSTS list, original order."""
		result = _select_trusts_to_seed()
		self.assertEqual(len(result), len(TRUSTS))
		self.assertEqual(
			[t["id"] for t in result],
			[t["id"] for t in TRUSTS],
		)

	def test_filter_subset_returns_only_matching_trusts(self):
		"""trusts=[ids] -> only matching trust dicts."""
		result = _select_trusts_to_seed(["ghremf", "cbs", "sgr"])
		ids = sorted(t["id"] for t in result)
		self.assertEqual(ids, ["cbs", "ghremf", "sgr"])

	def test_filter_preserves_original_trust_order(self):
		"""Filter preserves the order in TRUSTS, not the order of
		input ids -- callers shouldn't rely on input ordering."""
		# Pass ids out of TRUSTS order.
		result = _select_trusts_to_seed(["sgr", "ghremf", "cbs"])
		# TRUSTS order is ass, ghremf, ghref, ghrf, ghrus, cbs,
		# ghrua, ghrstu, ghristu, sgr -- so subset is ghremf, cbs, sgr.
		self.assertEqual(
			[t["id"] for t in result],
			["ghremf", "cbs", "sgr"],
		)

	def test_filter_company_count_matches_planned_subset(self):
		"""Side PR target: ["ghremf", "cbs", "sgr"] = 13 companies.

		If trust_groups.py rosters change, this test fails so the
		side PR documentation in PHASE_LOG can be retuned.
		"""
		result = _select_trusts_to_seed(["ghremf", "cbs", "sgr"])
		company_count = sum(len(t["companies"]) for t in result)
		self.assertEqual(company_count, 13)

	def test_unknown_trust_id_raises_valueerror(self):
		with self.assertRaises(ValueError) as cm:
			_select_trusts_to_seed(["not-a-real-trust"])
		# Error message lists valid ids -- ergonomic for the operator.
		self.assertIn("not-a-real-trust", str(cm.exception))

	def test_partial_unknown_id_raises_valueerror(self):
		"""One unknown id in an otherwise-valid list still raises."""
		with self.assertRaises(ValueError):
			_select_trusts_to_seed(["ghremf", "definitely-not-a-trust"])

	def test_case_insensitive_matching(self):
		"""Trust ids in TRUSTS are lowercase, but operators may type
		uppercase abbrs ("GHREMF", "CBS"). Match case-insensitively."""
		result = _select_trusts_to_seed(["GHREMF", "cbs", "Sgr"])
		ids = sorted(t["id"] for t in result)
		self.assertEqual(ids, ["cbs", "ghremf", "sgr"])

	def test_empty_list_raises(self):
		"""Empty list is ambiguous (full vs nothing); refuse explicitly."""
		with self.assertRaises(ValueError):
			_select_trusts_to_seed([])

	def test_non_list_raises(self):
		with self.assertRaises(ValueError):
			_select_trusts_to_seed("ghremf")  # string, not list

	def test_single_trust_works(self):
		result = _select_trusts_to_seed(["ass"])
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["id"], "ass")
