"""Tests for the Phase 3.5 trust-selector backend filter.

Verifies that:
1. `get_pivot_data` honours the `companies` parameter and excludes
   non-listed companies from the response.
2. The `companies` parameter is intersected with User Permissions on
   Company -- a user cannot widen their visibility through the filter.
3. `get_spotlight_cards_filtered` returns the same shape as
   `get_spotlight_cards` and produces values that agree with the cache
   when the filter equals the user's full allowed set.
4. `get_spotlight_cards_filtered` against a strict subset of companies
   matches an independent SQL aggregation over the same subset (the
   gold-standard correctness check for this phase).

Reads only `tabDGV TB Snapshot Row`, `tabAccount`, `tabCompany`, and
`tabDGV Spotlight Cache`. Never `tabGL Entry`.

Run with:
    bench --site erp.jewonline.in run-tests --module \\
        dux_groupview.dux_groupview.tests.test_pivot_filter
"""

from collections import defaultdict

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, today

from dux_groupview.dux_groupview.api import cockpit as cockpit_api
from dux_groupview.dux_groupview.api import pivot as pivot_api
from dux_groupview.dux_groupview.pivot.format import format_indian
from dux_groupview.dux_groupview.snapshots.refresh import refresh_tb_snapshot
from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
	refresh_spotlight_cache,
)
from dux_groupview.dux_groupview.spotlight.cards import CARDS


def _ensure_today_data():
	if not frappe.db.exists("DGV TB Snapshot", {"snapshot_date": getdate(today())}):
		refresh_tb_snapshot()
	# Cache may be stale or missing if Phase 2 was never run on this
	# site -- harmless to refresh it here.
	refresh_spotlight_cache()


def _two_test_companies():
	"""Pick any two distinct companies that have rows in today's snapshot.

	Falls back to dev's "Test Company %" set first (Phase 0 seed) before
	considering production-style names. Returns a tuple of names.
	"""
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT company FROM `tabDGV TB Snapshot Row`
		WHERE snapshot_date = %s
		ORDER BY company
		""",
		(today(),),
	)
	companies = [r[0] for r in rows]
	if len(companies) < 2:
		return None
	test_set = [c for c in companies if c.startswith("Test Company ")]
	if len(test_set) >= 2:
		return tuple(test_set[:2])
	return tuple(companies[:2])


class TestPivotFilter(FrappeTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_today_data()

	# ------------------------------------------------------------------
	# 1 -- companies filter on get_pivot_data
	# ------------------------------------------------------------------

	def test_get_pivot_data_filters_by_companies(self):
		pair = _two_test_companies()
		if not pair:
			self.skipTest("Need at least 2 companies in today's snapshot.")
		c1, c2 = pair

		data = pivot_api.get_pivot_data(today(), companies=[c1, c2])

		# Visible-company set: from the trusts list returned.
		visible = set()
		for trust in data["trusts"]:
			visible.update(trust["companies"])
		self.assertEqual(
			visible, {c1, c2},
			f"Pivot data trusts list contained companies outside the requested scope: {visible}",
		)

		# Balances dict should not contain any other company either.
		for account_name, company_map in data["balances"].items():
			for company in company_map:
				self.assertIn(
					company, {c1, c2},
					f"Pivot balance for {account_name} included {company}, outside scope.",
				)

	def test_get_pivot_data_companies_intersected_with_user_permissions(self):
		"""A user permitted on 2 companies who passes 3 (one outside their
		permissions) should see exactly the 2 they're allowed."""
		pair = _two_test_companies()
		if not pair:
			self.skipTest("Need at least 2 companies in today's snapshot.")
		permitted = list(pair)

		# Pick a 3rd company that exists but is NOT in the permitted pair.
		third_row = frappe.db.sql(
			"""
			SELECT name FROM tabCompany
			WHERE name NOT IN %s
			ORDER BY name LIMIT 1
			""",
			(tuple(permitted),),
		)
		if not third_row:
			self.skipTest("Need a 3rd company to construct an over-scope request.")
		over_scope_company = third_row[0][0]

		user_email = "dgv_filter_perm@example.invalid"
		try:
			frappe.delete_doc("User", user_email, force=True, ignore_missing=True)
		except Exception:
			pass

		try:
			user = frappe.get_doc({
				"doctype": "User",
				"email": user_email,
				"first_name": "DGV Filter Perm",
				"send_welcome_email": 0,
				"new_password": "dgv-filter-pw-7q3m",
				"roles": [{"role": "GroupView Viewer"}],
			})
			user.flags.ignore_permissions = True
			user.insert()

			for c in permitted:
				perm = frappe.get_doc({
					"doctype": "User Permission",
					"user": user_email,
					"allow": "Company",
					"for_value": c,
					"apply_to_all_doctypes": 1,
				})
				perm.flags.ignore_permissions = True
				perm.insert()
			frappe.db.commit()

			original_user = frappe.session.user
			try:
				frappe.set_user(user_email)
				# Request all three companies; only the two permitted
				# should come back.
				data = pivot_api.get_pivot_data(
					today(),
					companies=[*permitted, over_scope_company],
				)
			finally:
				frappe.set_user(original_user)

			visible = set()
			for trust in data["trusts"]:
				visible.update(trust["companies"])
			self.assertEqual(
				visible, set(permitted),
				f"User saw companies outside their User Permissions: {visible}",
			)
			# Defensive: the over-scope company must not appear anywhere
			# in the balances dict either.
			for account_name, company_map in data["balances"].items():
				self.assertNotIn(
					over_scope_company, company_map,
					f"Over-scope company {over_scope_company} appeared "
					f"in balances for {account_name}.",
				)
		finally:
			frappe.db.sql(
				"DELETE FROM `tabUser Permission` WHERE user = %s",
				(user_email,),
			)
			frappe.delete_doc("User", user_email, force=True, ignore_missing=True)
			frappe.db.commit()

	# ------------------------------------------------------------------
	# 2 -- spotlight filtered correctness
	# ------------------------------------------------------------------

	def test_get_spotlight_cards_filtered_returns_same_shape_as_cached(self):
		"""When called with the user's full allowed company set, the
		filtered endpoint must agree with the cached endpoint up to the
		round-to-2-decimal precision used by both."""
		# Full set = every company in today's snapshot (System Manager
		# scope here, so no User Permission narrowing).
		all_companies = frappe.db.sql_list(
			"""
			SELECT DISTINCT company FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %s
			""",
			(today(),),
		)
		if not all_companies:
			self.skipTest("No snapshot rows for today.")

		cached = cockpit_api.get_spotlight_cards(today())
		filtered = cockpit_api.get_spotlight_cards_filtered(
			today(), companies=list(all_companies)
		)

		self.assertEqual(len(cached), len(filtered))
		# Compare card-by-card on (id, value, delta).
		cached_by_id = {c["card_id"]: c for c in cached}
		for f in filtered:
			c = cached_by_id.get(f["card_id"])
			self.assertIsNotNone(
				c, f"Filtered output contains unknown card {f['card_id']}"
			)
			self.assertAlmostEqual(
				float(f["value"]), float(c["value"]), places=2,
				msg=f"Card {f['card_id']} value drift: filtered={f['value']} cached={c['value']}",
			)
			self.assertAlmostEqual(
				float(f["delta"]), float(c["delta"]), places=2,
				msg=f"Card {f['card_id']} delta drift",
			)
			# Shape check.
			for key in ("polarity", "format", "color", "label",
			            "formatted_value", "formatted_delta", "sparkline_data"):
				self.assertIn(key, f, f"Filtered card {f['card_id']} missing {key}")

	def test_get_spotlight_cards_filtered_subset(self):
		"""For each card, the filtered value over a chosen subset must
		match an independent sign-corrected SUM over the same subset.
		"""
		pair = _two_test_companies()
		if not pair:
			self.skipTest("Need at least 2 companies in today's snapshot.")
		subset = list(pair)

		filtered = cockpit_api.get_spotlight_cards_filtered(
			today(), companies=subset
		)
		filtered_by_id = {c["card_id"]: c for c in filtered}

		# Iterate VISIBLE cards only -- the API response excludes
		# disabled cards (per PR #15 disabled flag semantics), so
		# pulling `filtered_by_id[card["id"]]` for a disabled card
		# raises KeyError. Mirror the API's visibility filter here.
		for card in [c for c in CARDS if not c.get("disabled")]:
			expected = self._aggregate_card_directly(card, today(), subset)
			got = filtered_by_id[card["id"]]["value"]
			self.assertAlmostEqual(
				float(got), float(expected), places=2,
				msg=(
					f"Filtered card {card['id']} value: "
					f"got {got}, expected {expected} (subset={subset})."
				),
			)

	# ------------------------------------------------------------------
	# 3 -- group total aggregation (Phase 3.5 additions)
	# ------------------------------------------------------------------

	def test_get_pivot_data_includes_group_totals(self):
		"""Every group account in the response must have a balances
		entry (even an empty dict for groups with no descendants in
		scope), so the frontend can render the row at the right level
		of the hierarchy without a missing-data fallback."""
		data = pivot_api.get_pivot_data(today())
		accounts = data["accounts"]
		balances = data["balances"]

		group_accounts = [a for a in accounts if a["is_group"]]
		self.assertGreater(
			len(group_accounts), 0,
			"Expected at least one group account on dev (chart of accounts has groups)",
		)
		for a in group_accounts:
			self.assertIn(
				a["id"], balances,
				f"Group account {a['id']} missing from balances dict; "
				f"frontend row would render with no data.",
			)

	def test_get_pivot_data_group_totals_match_descendants_recursively(self):
		"""Gold-standard recursive invariant:

		    balance[node][company]
		      == own_balance[node][company]
		         + sum(balance[child][company] for child in children(node))

		holds at EVERY level of the hierarchy. By induction this implies
		each group's balance equals the sum over all its descendants'
		direct snapshot-row balances -- exactly the spec's requirement.

		Checking the immediate-children form (rather than walking down to
		leaves) keeps the assertion simple and also exposes any
		intermediate node where the bubble-up dropped a contribution.
		"""
		snapshot_date = today()
		data = pivot_api.get_pivot_data(snapshot_date)
		accounts = data["accounts"]
		balances = data["balances"]

		# Per-account, per-company "own" snapshot-row balance, indexed
		# by stripped account_name -- the same key the API uses to
		# build its balances dict.
		own_balances = defaultdict(lambda: defaultdict(float))
		rows = frappe.db.sql(
			"""
			SELECT r.company, r.balance,
			       COALESCE(a.account_name, r.account) AS name
			FROM `tabDGV TB Snapshot Row` r
			LEFT JOIN `tabAccount` a ON a.name = r.account
			WHERE r.snapshot_date = %s
			""",
			(snapshot_date,),
			as_dict=True,
		)
		for r in rows:
			own_balances[r["name"]][r["company"]] += float(flt(r["balance"]))

		children_of = defaultdict(list)
		for a in accounts:
			if a["parent"]:
				children_of[a["parent"]].append(a["id"])

		checked = 0
		for a in accounts:
			node_bal = balances.get(a["id"], {}) or {}
			own = own_balances.get(a["id"], {})
			children = children_of.get(a["id"], [])

			# Build expected per-company total from own + sum(children).
			expected = defaultdict(float)
			for c, v in own.items():
				expected[c] += v
			for child in children:
				child_bal = balances.get(child, {}) or {}
				for c, v in child_bal.items():
					expected[c] += v

			# Verify per-company.
			companies = set(node_bal.keys()) | set(expected.keys())
			for c in companies:
				self.assertAlmostEqual(
					float(node_bal.get(c, 0)), float(expected.get(c, 0)),
					places=2,
					msg=(
						f"Account {a['id']!r} × {c!r}: "
						f"API balance = {node_bal.get(c, 0)}, "
						f"expected own ({own.get(c, 0)}) "
						f"+ sum-of-{len(children)}-children "
						f"({expected.get(c, 0) - own.get(c, 0)}) "
						f"= {expected.get(c, 0)}"
					),
				)
			checked += 1
		self.assertGreater(
			checked, 0,
			"Expected at least one account in the response to verify",
		)

	def test_get_pivot_data_group_balance_obeys_companies_filter(self):
		"""Group totals must be filtered to the same scope as leaf
		balances. If we ask for only company A, the group's per-company
		map must not contain company B, even if B's accounts are part
		of the same trust hierarchy."""
		pair = _two_test_companies()
		if not pair:
			self.skipTest("Need at least 2 companies in today's snapshot.")
		c1, c2 = pair

		data = pivot_api.get_pivot_data(today(), companies=[c1])

		for a in data["accounts"]:
			if not a["is_group"]:
				continue
			balance_map = data["balances"].get(a["id"], {}) or {}
			# The map should contain at most company c1 -- never c2.
			self.assertNotIn(
				c2, balance_map,
				f"Group {a['id']!r} balance contained out-of-scope company {c2!r} "
				f"when scope = [{c1!r}]",
			)

	# ------------------------------------------------------------------
	# 4 -- depth filter (Phase 3.5 additions, post-fix)
	# ------------------------------------------------------------------

	def test_depth_filter_works_across_all_root_types(self):
		"""Simulate the JS visibility filter against the API response
		and verify it behaves uniformly across every root_type.

		Mirrors the post-fix `pivot_grid.js` semantics exactly:
		    Depth=N shows N levels (depth=1 -> roots only;
		    depth=2 -> roots + first-level children; etc.).
		"""
		data = pivot_api.get_pivot_data(today())
		accounts = data["accounts"]
		by_id = {a["id"]: a for a in accounts}

		def is_group_expanded(node, setting):
			# Mirrors _isGroupExpanded post-fix.
			if setting == "all":
				return True
			return (node["depth"] + 1) < setting

		def is_visible(account, setting):
			cur = account
			while cur and cur.get("parent"):
				parent = by_id.get(cur["parent"])
				if not parent:
					break
				if not is_group_expanded(parent, setting):
					return False
				cur = parent
			return True

		# Group all accounts by root_type so we can spot-check uniformity.
		by_root = defaultdict(list)
		for a in accounts:
			by_root[a["root_type"] or "<none>"].append(a)
		root_types = list(by_root)
		self.assertGreaterEqual(
			len(root_types), 2,
			"Expected at least 2 root types in the dev seed",
		)

		# Depth = 1 -> only depth-0 rows visible across every root_type.
		# We don't assert exactly one visible per root_type because the
		# RGI-DEMO seed has a typo'd duplicate Asset root
		# (`Application Of Funds(Assets)` + `Application of Funds
		# (Assets)`) which would fail an "exactly one" assertion for
		# the wrong reason -- the filter is fine, the data has two
		# roots. We do require at least one, and we require all visible
		# rows to be roots.
		for rt in root_types:
			visible = [a for a in by_root[rt] if is_visible(a, 1)]
			self.assertGreaterEqual(
				len(visible), 1,
				f"Depth=1 for root_type={rt!r}: expected at least one "
				f"visible row (a root), got 0",
			)
			depths_visible = {a["depth"] for a in visible}
			self.assertEqual(
				depths_visible, {0},
				f"Depth=1 for root_type={rt!r}: only depth-0 rows "
				f"should be visible, got depths {sorted(depths_visible)} "
				f"— names: {[v['id'] for v in visible]}"
			)

		# Depth = 2 -> roots + their depth-1 children visible; depth-2
		# nodes (and deeper) hidden.
		for rt in root_types:
			visible = [a for a in by_root[rt] if is_visible(a, 2)]
			depths_visible = {a["depth"] for a in visible}
			self.assertTrue(
				depths_visible.issubset({0, 1}),
				f"Depth=2 for root_type={rt!r}: expected only "
				f"depths {{0,1}} visible, got {sorted(depths_visible)}"
			)

		# Depth = 3 -> roots + depth-1 + depth-2 visible; depth-3+ hidden.
		for rt in root_types:
			visible = [a for a in by_root[rt] if is_visible(a, 3)]
			depths_visible = {a["depth"] for a in visible}
			self.assertTrue(
				depths_visible.issubset({0, 1, 2}),
				f"Depth=3 for root_type={rt!r}: expected only "
				f"depths {{0,1,2}} visible, got {sorted(depths_visible)}"
			)

		# Depth = 'all' -> every account visible.
		for rt in root_types:
			visible = [a for a in by_root[rt] if is_visible(a, "all")]
			self.assertEqual(len(visible), len(by_root[rt]))

	# ------------------------------------------------------------------
	# 5 -- Indian number format (Phase 3.5 additions)
	# ------------------------------------------------------------------

	def test_indian_format_function(self):
		"""Verify the Indian comma grouping spec, including parens for
		negatives. The JS `formatIndian` in pivot_grid.js mirrors this
		Python implementation -- if either changes the other must
		follow."""
		cases = [
			(0, "0.00"),
			(0.0, "0.00"),
			(1234.56, "1,234.56"),
			(1234567.89, "12,34,567.89"),
			(12345678901.23, "12,34,56,78,901.23"),
			# Whole-number padding to 2 decimals.
			(100, "100.00"),
			(99, "99.00"),
			(7, "7.00"),
			# Negatives in parens; the minus sign is dropped in favour
			# of surrounding parentheses (accounting convention).
			(-100.5, "(100.50)"),
			(-1234567.89, "(12,34,567.89)"),
			(-1, "(1.00)"),
			# 8-digit boundary (verifies the regex matches the by-hand
			# Indian grouping for "12345678" -> "12,34,56,78").
			(12345678.0, "1,23,45,678.00"),
		]
		for value, expected in cases:
			got = format_indian(value)
			self.assertEqual(
				got, expected,
				f"format_indian({value!r}): got {got!r}, expected {expected!r}",
			)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	def _aggregate_card_directly(self, card, snapshot_date, companies):
		"""Independent reference aggregation -- mirrors the SQL in
		spotlight_refresh._aggregate but written from scratch here so a
		bug introduced into _aggregate would not slip through.
		"""
		match = card.get("match", {})
		clauses = []
		params = {"sd": snapshot_date}

		if "by_account_type" in match:
			v = match["by_account_type"]
			# Handle all three input shapes introduced by PR #18
			# (supplier-advances-split) and PR #19 (display-and-exclude-
			# fixes). The dict shape is the canonical form; str/list are
			# legacy shortcuts. Without this handling the helper binds
			# the whole dict as a parameter and MariaDB raises
			# `TypeError: dict can not be used as parameter`.
			balance_sign = "any"
			exclude_parent_stems = None
			if isinstance(v, dict):
				at = v["account_type"]
				balance_sign = v.get("balance_sign", "any")
				exclude_parent_stems = v.get("exclude_parent_stems")
			else:
				at = v
			if isinstance(at, (list, tuple)):
				placeholders = ", ".join(f"%(at{i})s" for i in range(len(at)))
				for i, x in enumerate(at):
					params[f"at{i}"] = x
				clauses.append(f"account_type IN ({placeholders})")
			else:
				clauses.append("account_type = %(at)s")
				params["at"] = at
			if balance_sign == "positive":
				clauses.append("balance > 0")
			elif balance_sign == "negative":
				clauses.append("balance < 0")
			if (
				isinstance(exclude_parent_stems, list)
				and exclude_parent_stems
				and all(isinstance(x, str) for x in exclude_parent_stems)
			):
				ex_placeholders = ", ".join(
					f"%(ex{i})s" for i in range(len(exclude_parent_stems))
				)
				for i, x in enumerate(exclude_parent_stems):
					params[f"ex{i}"] = x
				clauses.append(
					f"account NOT IN ("
					f"SELECT name FROM `tabAccount` "
					f"WHERE is_group = 0 "
					f"AND SUBSTRING_INDEX(parent_account, ' - ', 1) "
					f"IN ({ex_placeholders})"
					f")"
				)
		elif "by_root_type_and_name_pattern" in match:
			conf = match["by_root_type_and_name_pattern"]
			clauses.append(
				"root_type = %(rt)s AND account LIKE %(np)s"
			)
			params["rt"] = conf["root_type"]
			params["np"] = conf["name_pattern"]
		elif "by_parent_account_stem_in" in match:
			# Added by PR #15 (Cash & Bank split) but never wired
			# into this helper. Catches up with the predicate set
			# the production aggregator supports.
			conf = match["by_parent_account_stem_in"]
			stems = conf["stems"]
			placeholders = ", ".join(
				f"%(st{i})s" for i in range(len(stems))
			)
			for i, x in enumerate(stems):
				params[f"st{i}"] = x
			params["rt"] = conf["root_type"]
			clauses.append(
				f"account IN ("
				f"SELECT name FROM `tabAccount` "
				f"WHERE is_group = 0 "
				f"AND root_type = %(rt)s "
				f"AND SUBSTRING_INDEX(parent_account, ' - ', 1) "
				f"IN ({placeholders})"
				f")"
			)
			# Per PR #18 (supplier-advances-split): optional balance_sign
			# on this predicate too.
			stem_balance_sign = conf.get("balance_sign", "any")
			if stem_balance_sign == "positive":
				clauses.append("balance > 0")
			elif stem_balance_sign == "negative":
				clauses.append("balance < 0")
		else:
			return 0.0

		# Explicit company subset.
		co_placeholders = ", ".join(f"%(c{i})s" for i in range(len(companies)))
		for i, c in enumerate(companies):
			params[f"c{i}"] = c
		clauses.append(f"company IN ({co_placeholders})")

		where = " AND ".join(clauses)
		row = frappe.db.sql(
			f"""
			SELECT COALESCE(SUM(
				CASE WHEN root_type IN ('Liability', 'Equity', 'Income')
				     THEN -balance
				     ELSE balance
				END
			), 0)
			FROM `tabDGV TB Snapshot Row`
			WHERE snapshot_date = %(sd)s AND ({where})
			""",
			params,
		)
		raw = round(flt(row[0][0]), 2) if row else 0.0
		# Mirror the production-side `display_sign` transform applied
		# in spotlight_refresh._aggregate so this independent helper's
		# return value compares apples-to-apples with the API's
		# (already-transformed) cache value.
		sign = card.get("display_sign", "natural")
		if sign == "absolute":
			return abs(raw)
		if sign == "negated":
			return -raw
		return raw
