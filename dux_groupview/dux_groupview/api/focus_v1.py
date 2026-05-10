"""Focus-mode endpoint for the cockpit.

Returns the data for a single-scope vertical view of the cockpit data:
either one company, or one trust (all companies in that trust). Reads
only `tabDGV TB Snapshot Row` joined to `tabAccount` -- no `tabGL Entry`
access, per spec §4.5 / CLAUDE.md hard rule 1.

The cockpit pivot is the wide cross-company view; focus mode is the
single-scope full-depth reflow. See `specs/phase-4-commit-5-focus-mode.md`.
"""

import csv
import io

import frappe
from frappe import _
from frappe.utils import flt, getdate

from dux_groupview.dux_groupview.api.gl_drill_v1 import (
	_csv_filename,
	_set_csv_response,
)
from dux_groupview.dux_groupview.api.pivot import (
	_allowed_companies,
	_sql_in_tuple,
	_strip_company_suffix,
)
from dux_groupview.dux_groupview.api.utils import FLIP_ROOT_TYPES
from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS


ALLOWED_ROLES = {"System Manager", "GroupView Owner", "GroupView Viewer"}


def _require_cockpit_role():
	if not (ALLOWED_ROLES & set(frappe.get_roles())):
		frappe.throw(
			_("Focus mode is restricted to GroupView roles."),
			frappe.PermissionError,
		)


# Root types that participate in the five summary tiles.
TILE_ROOT_TYPES = ("Asset", "Liability", "Income", "Expense")


def _resolve_focused_companies(scope_type, scope_value):
	"""Translate (scope_type, scope_value) to the permission-intersected company list.

	Returns a list of allowed companies for this focus scope. Raises
	ValueError on bad input shape, frappe.PermissionError if the user
	has no access to anything in the scope, frappe.DoesNotExistError if
	the scope_value resolves to nothing.
	"""
	if scope_type not in ("company", "trust"):
		raise ValueError(
			"scope_type must be 'company' or 'trust', got %r" % (scope_type,)
		)
	if not isinstance(scope_value, str) or not scope_value.strip():
		raise ValueError("scope_value must be a non-empty string")

	allowed = set(_allowed_companies())

	if scope_type == "company":
		if not frappe.db.exists("Company", scope_value):
			frappe.throw(
				_("Company {0} does not exist").format(scope_value),
				frappe.DoesNotExistError,
			)
		if scope_value not in allowed:
			frappe.throw(
				_("You don't have permission for company {0}").format(scope_value),
				frappe.PermissionError,
			)
		return [scope_value]

	# scope_type == "trust"
	trust_companies = None
	for trust in TRUSTS:
		if trust["name"] == scope_value or trust["id"] == scope_value:
			trust_companies = list(trust["companies"])
			break
	if trust_companies is None:
		frappe.throw(
			_("Trust {0} does not exist").format(scope_value),
			frappe.DoesNotExistError,
		)
	intersected = [c for c in trust_companies if c in allowed]
	if not intersected:
		frappe.throw(
			_("You don't have permission for any company in trust {0}").format(scope_value),
			frappe.PermissionError,
		)
	return intersected


@frappe.whitelist()
def get_focused_view(scope_type, scope_value, as_of_date):
	"""Return the focused-view payload for one company or one trust.

	Params:
	    scope_type:  'company' | 'trust'
	    scope_value: company name (when 'company') or trust name/id (when 'trust')
	    as_of_date:  ISO date string

	Output:
	    {
	      "scope": {"type", "value", "companies": [...]},
	      "as_of_date": "YYYY-MM-DD",
	      "summary_tiles": {
	        "assets":      <int rupees, sign-flipped>,
	        "liabilities": <int rupees, sign-flipped>,
	        "income":      <int rupees, sign-flipped>,
	        "expenses":    <int rupees, sign-flipped>,
	        "net_surplus": <income - expenses>,
	      },
	      "accounts": [
	        {
	          "name": "<full Frappe name; first by lft when trust focus>",
	          "account_name": "<stripped>",
	          "parent_account": "<stripped or empty>",
	          "root_type": "Asset" | "Liability" | "Income" | "Expense" | "Equity",
	          "account_type": "<may be empty>",
	          "depth": <int, computed from parent walk>,
	          "is_group": <bool>,
	          "has_children": <bool, whether any descendant exists in scope>,
	          "balance": <int rupees, sign-flipped per FLIP_ROOT_TYPES>,
	        },
	        ...
	      ],
	    }

	Reads from `tabDGV TB Snapshot Row` and `tabAccount` only. No
	`tabGL Entry` access.
	"""
	_require_cockpit_role()

	companies = _resolve_focused_companies(scope_type, scope_value)
	snapshot_date = getdate(as_of_date)

	co_tuple = _sql_in_tuple(companies)
	flip_tuple = _sql_in_tuple(FLIP_ROOT_TYPES)

	# Single SQL: every account in scope companies, with snapshot balance
	# left-joined (so zero-activity accounts still appear in the
	# hierarchy). GROUP BY account_name folds same-named accounts across
	# companies into one row -- a no-op for company focus, the
	# aggregation step for trust focus.
	#
	# Sign convention: balance stored as raw Dr-Cr; flipped here for
	# Liability / Equity / Income via the FLIP_ROOT_TYPES tuple from
	# utils.py (single source of truth, pinned by sign-convention tests).
	#
	# Ordering: MIN(a.lft) gives a stable canonical position. For
	# company focus this is exactly the company's own COA traversal;
	# for trust focus across companies it's a best-effort anchor (each
	# company's lft range differs but the relative position of e.g.
	# "Current Assets" within "Application of Funds" is consistent
	# across RGI's COA).
	rows = frappe.db.sql(
		"""
		SELECT
		  a.account_name AS account_name,
		  MIN(a.name) AS name,
		  MIN(a.parent_account) AS parent_account_raw,
		  MAX(CAST(a.is_group AS UNSIGNED)) AS is_group,
		  MIN(a.root_type) AS root_type,
		  MIN(COALESCE(a.account_type, '')) AS account_type,
		  COALESCE(SUM(
		    CASE WHEN a.root_type IN %(flip)s
		         THEN -r.balance
		         ELSE r.balance
		    END
		  ), 0) AS balance
		FROM `tabAccount` a
		LEFT JOIN `tabDGV TB Snapshot Row` r
		  ON r.account = a.name
		  AND r.snapshot_date = %(snapshot_date)s
		WHERE a.company IN %(companies)s
		  AND a.disabled = 0
		GROUP BY a.account_name
		ORDER BY MIN(a.lft)
		""",
		{
			"snapshot_date": snapshot_date,
			"companies": co_tuple,
			"flip": flip_tuple,
		},
		as_dict=True,
	)

	accounts = _shape_accounts(rows)
	summary_tiles = _summary_tiles(snapshot_date, co_tuple, flip_tuple)

	return {
		"scope": {
			"type": scope_type,
			"value": scope_value,
			"companies": companies,
		},
		"as_of_date": str(snapshot_date),
		"summary_tiles": summary_tiles,
		"accounts": accounts,
	}


def _shape_accounts(rows):
	"""Strip parent suffix, compute depth + has_children, finalise shape.

	`rows` are the GROUP BY a.account_name aggregates from the SQL
	above, already in lft order. Returns a list ready for JSON
	serialisation.

	`has_children` is computed from the response's own parent_account
	graph (does any other row name this row as its parent?), not from
	the SQL's MIN(lft)/MAX(rgt) span. The lft/rgt approach is wrong for
	trust focus because each company has its own nested-set range --
	`MAX(rgt) - MIN(lft)` straddles unrelated companies' ranges and
	gives false positives on leaf accounts. The graph-based check is
	exact regardless of scope flavor.
	"""
	# Build a map keyed by stripped account_name for parent-walk lookups.
	by_name = {}
	for r in rows:
		acct_name = r["account_name"]
		parent_stripped = _strip_company_suffix(r["parent_account_raw"]) if r["parent_account_raw"] else ""
		by_name[acct_name] = {
			"name": r["name"],
			"account_name": acct_name,
			"parent_account": parent_stripped,
			"root_type": r["root_type"] or "",
			"account_type": r["account_type"] or "",
			"is_group": bool(int(r["is_group"] or 0)),
			"balance": float(flt(r["balance"])),
		}

	# Build the inverse: which account_names appear as someone's parent
	# in the response? has_children = (account_name appears here).
	parents_with_kids = {
		entry["parent_account"]
		for entry in by_name.values()
		if entry["parent_account"]
	}
	for entry in by_name.values():
		entry["has_children"] = entry["account_name"] in parents_with_kids

	# Walk parent chain to compute depth. Each account's depth = parent's
	# depth + 1; root (no parent in scope) is 0. Memoise.
	depth_cache = {}

	def _depth(acct_name):
		if acct_name in depth_cache:
			return depth_cache[acct_name]
		entry = by_name.get(acct_name)
		if not entry:
			return 0
		parent = entry["parent_account"]
		if not parent or parent not in by_name:
			depth_cache[acct_name] = 0
			return 0
		d = _depth(parent) + 1
		depth_cache[acct_name] = d
		return d

	# Output preserves the SQL's lft-min ordering.
	out = []
	for r in rows:
		acct_name = r["account_name"]
		entry = by_name[acct_name]
		entry["depth"] = _depth(acct_name)
		out.append(entry)
	return out


def _summary_tiles(snapshot_date, co_tuple, flip_tuple):
	"""Aggregate the five summary tile values from snapshot leaf rows.

	Tiles aggregate by `root_type` predicate, not by hierarchy (CLAUDE.md
	hard rule 7). Snapshot rows are leaf-only (CLAUDE.md hard rule 6) so
	the predicate sum is exactly the leaf-level total -- no double
	counting from group-account rollups.

	Reads `root_type` directly from the snapshot row (not via JOIN to
	tabAccount) -- the column was populated at refresh time and avoids
	an avoidable join. Empirical: dropping the JOIN clears
	`Using temporary; Using filesort` from the EXPLAIN plan against
	`tabDGV TB Snapshot Row` (HALT 5.2 fix; original query tripped the
	spec §9.2 fail criterion).

	Net surplus is computed as Income - Expenses, both already
	sign-flipped to positive on their natural side.
	"""
	rows = frappe.db.sql(
		"""
		SELECT
		  r.root_type AS root_type,
		  COALESCE(SUM(
		    CASE WHEN r.root_type IN %(flip)s
		         THEN -r.balance
		         ELSE r.balance
		    END
		  ), 0) AS total
		FROM `tabDGV TB Snapshot Row` r
		WHERE r.snapshot_date = %(snapshot_date)s
		  AND r.company IN %(companies)s
		  AND r.root_type IN %(tile_roots)s
		GROUP BY r.root_type
		""",
		{
			"snapshot_date": snapshot_date,
			"companies": co_tuple,
			"flip": flip_tuple,
			"tile_roots": _sql_in_tuple(TILE_ROOT_TYPES),
		},
		as_dict=True,
	)
	by_root = {r["root_type"]: float(flt(r["total"])) for r in rows}

	assets = by_root.get("Asset", 0.0)
	liabilities = by_root.get("Liability", 0.0)
	income = by_root.get("Income", 0.0)
	expenses = by_root.get("Expense", 0.0)
	net_surplus = round(income - expenses, 2)

	return {
		"assets": assets,
		"liabilities": liabilities,
		"income": income,
		"expenses": expenses,
		"net_surplus": net_surplus,
	}


# ---------------------------------------------------------------------------
# CSV export (HALT 5.4)
# ---------------------------------------------------------------------------
#
# Reuses the same scope-resolution + accounts SQL as get_focused_view, then
# emits a 4-column CSV: Account, Root Type, Depth, Balance. Raw decimal
# balances (no Indian grouping, no Cr/L abbreviation) so spreadsheet
# tools can re-format. Filename:
#   focused_view_<scope_type>_<slug>_<as_of_date>_<HHMMSS>.csv
# Filename slug uses the existing _slugify_for_filename helper from
# gl_drill_v1 (via _csv_filename) for cross-app consistency.
#
# Sub-rupee filter (commit 3.1 convention): leaf rows with abs(balance)
# < 1 are dropped from the output -- they're rounding residuals from
# the seed and would otherwise render as "0" in spreadsheets, polluting
# the export. Group rows pass through regardless because they aggregate
# subtree balances; their own row contribution may legitimately be
# zero even when the subtree has activity.
#
# 50K-row cap from the GL/party CSV exports does NOT apply here -- a
# focused view returns at most ~700 (company) to ~9000 (large trust)
# rows, well below any meaningful cap. Skipping the cap keeps the
# endpoint simple.

CSV_HEADERS = ["Account", "Root Type", "Depth", "Balance"]


@frappe.whitelist(allow_guest=False)
def export_focused_view_csv(scope_type, scope_value, as_of_date):
	"""Stream the focused view as a downloadable CSV.

	Same scope resolution + permission rules as `get_focused_view`.
	Sub-rupee leaf rows are filtered out (commit 3.1 convention);
	group rows pass through regardless.
	"""
	_require_cockpit_role()

	companies = _resolve_focused_companies(scope_type, scope_value)
	snapshot_date = getdate(as_of_date)

	co_tuple = _sql_in_tuple(companies)
	flip_tuple = _sql_in_tuple(FLIP_ROOT_TYPES)

	# Same query as the body of get_focused_view, lined up with that
	# function's contract (lft order, sign-flipped balance, etc.).
	rows = frappe.db.sql(
		"""
		SELECT
		  a.account_name AS account_name,
		  MIN(a.parent_account) AS parent_account_raw,
		  MAX(CAST(a.is_group AS UNSIGNED)) AS is_group,
		  MIN(a.root_type) AS root_type,
		  COALESCE(SUM(
		    CASE WHEN a.root_type IN %(flip)s
		         THEN -r.balance
		         ELSE r.balance
		    END
		  ), 0) AS balance
		FROM `tabAccount` a
		LEFT JOIN `tabDGV TB Snapshot Row` r
		  ON r.account = a.name
		  AND r.snapshot_date = %(snapshot_date)s
		WHERE a.company IN %(companies)s
		  AND a.disabled = 0
		GROUP BY a.account_name
		ORDER BY MIN(a.lft)
		""",
		{
			"snapshot_date": snapshot_date,
			"companies": co_tuple,
			"flip": flip_tuple,
		},
		as_dict=True,
	)

	# Mirror the depth-walk in _shape_accounts so the CSV's Depth
	# column matches what the on-screen view shows.
	by_name = {}
	for r in rows:
		acct = r["account_name"]
		parent = _strip_company_suffix(r["parent_account_raw"]) if r["parent_account_raw"] else ""
		by_name[acct] = {"parent": parent}

	depth_cache = {}

	def _depth(acct):
		if acct in depth_cache:
			return depth_cache[acct]
		entry = by_name.get(acct)
		if not entry:
			return 0
		parent = entry["parent"]
		if not parent or parent not in by_name:
			depth_cache[acct] = 0
			return 0
		d = _depth(parent) + 1
		depth_cache[acct] = d
		return d

	# Build the CSV.
	buf = io.StringIO()
	writer = csv.writer(buf)
	writer.writerow(CSV_HEADERS)

	for r in rows:
		balance = float(flt(r["balance"]))
		is_group = bool(int(r["is_group"] or 0))
		# Sub-rupee filter on leaves only (commit 3.1).
		if not is_group and abs(balance) < 1.0:
			continue
		acct_name = r["account_name"] or ""
		root_type = r["root_type"] or ""
		depth = _depth(acct_name)
		# Group rows: empty Balance cell so spreadsheets show a blank
		# rather than 0.00 (group own-row balance is meaningless --
		# the on-screen UI also leaves it blank).
		balance_cell = "" if is_group else f"{balance:.2f}"
		writer.writerow([acct_name, root_type, depth, balance_cell])

	# Compose the filename from a "<scope_type>_<scope_value>" label
	# so the exported filename clearly identifies what was exported
	# (a user with multiple downloads can tell company-focus exports
	# apart from trust-focus exports at a glance).
	label = f"{scope_type}_{scope_value}"
	filename = _csv_filename("focused_view", label, snapshot_date)
	_set_csv_response(filename, buf.getvalue())
