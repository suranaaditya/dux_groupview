"""ICD settings — list, save, and auto-suggest Inter-College Deposit accounts.

Three whitelisted endpoints power the `/app/dgv-icd-mapping` settings
page:

  - get_icd_candidates  -- every leaf under the Unsecured Loans subtree
                           with its current ICD status, owning companies,
                           and latest snapshot balance.
  - save_icd_list       -- diff-based update of `tabDGV ICD Account`,
                           then a single spotlight cache refresh.
  - suggest_icd_candidates -- runs the name/abbreviation matching
                           algorithm and returns high-confidence ICD
                           candidates (full-name or abbreviation match
                           against an internal Company, excluding the
                           leaf's own owning company).

The classification is by stripped `account_name` -- one row per name,
not per (name, company) pair. If `"Cerebral Tech Ventures"` appears in
ten companies and the user toggles it as ICD, all ten instances are
treated as ICD. Matches how DGV ICD Account stores values.

Reads `tabDGV TB Snapshot Row`, `tabAccount`, `tabCompany`,
`tabDGV ICD Account`. Never `tabGL Entry` (Rule 1).
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import flt

from dux_groupview.dux_groupview.api.pivot import _require_cockpit_role
from dux_groupview.dux_groupview.api.utils import FLIP_ROOT_TYPES


# Tokens that aren't useful as ICD-matching signal on their own.
_STOP_WORDS = {
	"of", "and", "the", "for", "a", "an",
	"college", "school", "society", "trust", "foundation",
	"university", "institute", "group", "sanstha", "shikshan",
}


def _require_owner_role():
	"""Only GroupView Owner / System Manager can edit ICD settings."""
	roles = set(frappe.get_roles())
	if not (roles & {"System Manager", "GroupView Owner"}):
		frappe.throw(
			_("ICD settings are restricted to GroupView Owner."),
			frappe.PermissionError,
		)


def _latest_snapshot_date():
	row = frappe.db.sql(
		"""
		SELECT MAX(snapshot_date) FROM `tabDGV TB Snapshot`
		WHERE status = 'Complete'
		"""
	)
	return row[0][0] if row and row[0][0] else None


@frappe.whitelist()
def get_icd_candidates():
	"""Return every Unsecured Loans leaf with its current ICD status.

	Shape:
	    {
	      "snapshot_date": "YYYY-MM-DD" | None,
	      "rows": [
	        {
	          "account_name": "<stripped>",
	          "is_icd": <bool>,
	          "balance": <float, natural-side signed>,
	          "company_count": <int>,
	          "companies": "<comma-joined sample of owning companies>",
	        },
	        ...
	      ],
	      "orphans": [
	        {"account_name": "<flagged-but-no-longer-a-leaf>"}
	      ]
	    }

	Sort order: |balance| desc so the largest material accounts surface
	first. Rolls up by `account_name` so each row classifies once for
	all its company-suffixed instances.
	"""
	_require_cockpit_role()
	snap = _latest_snapshot_date()

	rows = frappe.db.sql(
		"""
		SELECT
		  a.account_name AS account_name,
		  COUNT(DISTINCT a.company) AS company_count,
		  GROUP_CONCAT(DISTINCT a.company ORDER BY a.company SEPARATOR ', ')
		    AS companies,
		  COALESCE(SUM(
		    CASE WHEN a.root_type IN ('Liability', 'Equity', 'Income')
		         THEN -COALESCE(r.balance, 0)
		         ELSE COALESCE(r.balance, 0)
		    END
		  ), 0) AS balance,
		  IF(MAX(IF(icd.account_name IS NULL, 0, 1)) = 1, 1, 0) AS is_icd
		FROM `tabAccount` a
		LEFT JOIN `tabDGV TB Snapshot Row` r
		  ON r.account = a.name AND r.snapshot_date = %(snap)s
		LEFT JOIN `tabDGV ICD Account` icd
		  ON icd.account_name = a.account_name
		WHERE a.is_group = 0
		  AND a.disabled = 0
		  AND a.root_type = 'Liability'
		  AND SUBSTRING_INDEX(a.parent_account, ' - ', 1) = 'Unsecured Loans'
		GROUP BY a.account_name
		ORDER BY ABS(COALESCE(SUM(
		  CASE WHEN a.root_type IN ('Liability', 'Equity', 'Income')
		       THEN -COALESCE(r.balance, 0)
		       ELSE COALESCE(r.balance, 0)
		  END
		), 0)) DESC
		""",
		{"snap": snap},
		as_dict=True,
	)

	# Detect orphans: names flagged ICD in DGV ICD Account that no
	# longer match any current Unsecured Loans leaf (account renamed,
	# moved out of the subtree, or disabled). User can clean these up
	# from the settings page.
	live_names = {r["account_name"] for r in rows}
	flagged = frappe.db.sql_list(
		"SELECT account_name FROM `tabDGV ICD Account` ORDER BY account_name"
	)
	orphans = [{"account_name": n} for n in flagged if n not in live_names]

	return {
		"snapshot_date": str(snap) if snap else None,
		"rows": [
			{
				"account_name": r["account_name"],
				"is_icd": bool(r["is_icd"]),
				"balance": float(flt(r["balance"])),
				"company_count": int(r["company_count"]),
				"companies": r["companies"] or "",
			}
			for r in rows
		],
		"orphans": orphans,
	}


@frappe.whitelist()
def save_icd_list(account_names):
	"""Replace `tabDGV ICD Account` with the given account_name list.

	`account_names` may arrive as a Python list or a JSON-stringified
	one (frappe.call serialises arrays via JSON). Diff-based update:
	delete entries no longer present, insert ones not yet stored. All
	via raw SQL so the doctype's per-row hooks don't fire (we trigger
	the cache refresh ONCE at the end). Returns counts so the UI can
	confirm what changed.
	"""
	_require_owner_role()

	if isinstance(account_names, str):
		try:
			account_names = json.loads(account_names)
		except (ValueError, TypeError):
			frappe.throw(_("account_names must be a JSON list or array"))
	if not isinstance(account_names, list):
		frappe.throw(_("account_names must be a list"))
	desired = {str(n).strip() for n in account_names if isinstance(n, str) and n.strip()}

	existing = set(frappe.db.sql_list(
		"SELECT account_name FROM `tabDGV ICD Account`"
	))
	to_add = desired - existing
	to_remove = existing - desired

	if to_remove:
		placeholders = ", ".join(["%s"] * len(to_remove))
		frappe.db.sql(
			f"DELETE FROM `tabDGV ICD Account` "
			f"WHERE account_name IN ({placeholders})",
			tuple(to_remove),
		)
	if to_add:
		# autoname is `field:account_name`, so `name == account_name`.
		# Direct INSERT bypasses Document.insert overhead -- safe here
		# because the table has one Data field with no naming series
		# and no doctype-side validation beyond uniqueness (enforced
		# by the column index). Per row: (name, account_name, owner,
		# modified_by) parameterised; creation, modified, idx literal.
		user = frappe.session.user
		values_sql = ", ".join(
			["(%s, %s, %s, NOW(), NOW(), %s, 0)"] * len(to_add)
		)
		params = []
		for name in sorted(to_add):
			params.extend([name, name, user, user])
		frappe.db.sql(
			f"INSERT INTO `tabDGV ICD Account` "
			f"(name, account_name, owner, creation, modified, modified_by, idx) "
			f"VALUES {values_sql}",
			tuple(params),
		)
	frappe.db.commit()

	# Single cache refresh after all writes. Target the latest completed
	# snapshot date (what the dashboard reads), not today() -- today
	# may not have a snapshot yet on dev or between scheduler runs, and
	# refreshing the cache against an empty row set would just write
	# zeros. The latest snapshot is what the user sees on the cockpit.
	if to_add or to_remove:
		from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
			_latest_complete_snapshot_date,
			refresh_spotlight_cache,
		)
		try:
			target = _latest_complete_snapshot_date()
			if target is not None:
				refresh_spotlight_cache(target)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="ICD save: spotlight refresh failed",
			)

	return {
		"added": sorted(to_add),
		"removed": sorted(to_remove),
		"total": len(desired),
	}


@frappe.whitelist()
def suggest_icd_candidates():
	"""Return a list of suggested ICD account_names.

	High-confidence only: a leaf's stripped account_name matches another
	Company's full name (exact, case-insensitive) or abbreviation (word
	boundary). The leaf's own owning company is excluded from candidates
	to avoid self-reference (an account literally named after its own
	company).

	Shape:
	    {
	      "suggested": [
	        {
	          "account_name": "<stripped>",
	          "match_kind": "name" | "abbr",
	          "matched_company": "<company name>",
	        },
	        ...
	      ]
	    }
	"""
	_require_cockpit_role()

	companies = frappe.db.sql(
		"SELECT name, abbr FROM `tabCompany`",
		as_dict=True,
	)
	# Pre-compile patterns: full name (case-insensitive equality) and
	# abbreviation (word-boundary match, case-sensitive since abbrs are
	# usually uppercase).
	candidates = []
	for co in companies:
		nm = (co["name"] or "").strip()
		ab = (co["abbr"] or "").strip()
		if nm:
			candidates.append({
				"company": co["name"],
				"kind": "name",
				"weight": 10,
				"pattern": re.compile(
					r"^" + re.escape(nm) + r"$", re.IGNORECASE),
			})
		if ab and len(ab) >= 2:
			candidates.append({
				"company": co["name"],
				"kind": "abbr",
				"weight": 8,
				"pattern": re.compile(
					r"\b" + re.escape(ab) + r"\b"),
			})

	# Pull leaves under Unsecured Loans with their distinct owning
	# companies (one row per stripped name). We need owning_company
	# info so we can exclude self-reference matches.
	leaves = frappe.db.sql(
		"""
		SELECT a.account_name,
		       GROUP_CONCAT(DISTINCT a.company SEPARATOR '||') AS companies
		FROM `tabAccount` a
		WHERE a.is_group = 0
		  AND a.disabled = 0
		  AND a.root_type = 'Liability'
		  AND SUBSTRING_INDEX(a.parent_account, ' - ', 1) = 'Unsecured Loans'
		GROUP BY a.account_name
		""",
		as_dict=True,
	)

	suggested = []
	for leaf in leaves:
		name = leaf["account_name"] or ""
		own_set = set((leaf["companies"] or "").split("||"))
		best = None  # (weight, kind, matched_company)
		for c in candidates:
			if c["company"] in own_set:
				continue  # self-reference
			if c["pattern"].search(name):
				if best is None or c["weight"] > best[0]:
					best = (c["weight"], c["kind"], c["company"])
		if best is not None:
			suggested.append({
				"account_name": name,
				"match_kind": best[1],
				"matched_company": best[2],
			})

	# Stable order: high-weight matches first, then alphabetical.
	suggested.sort(key=lambda s: (s["match_kind"] != "name", s["account_name"]))
	return {"suggested": suggested}
