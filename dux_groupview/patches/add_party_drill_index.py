"""Supplementary index on tabGL Entry for the party drill GROUP BY.

The Phase 3 covering index `dgv_snapshot_aggregation` is ordered
`(is_cancelled, docstatus, company, account, posting_date)` for the
snapshot refresh's per-(company, account) GROUP BY. Phase 4's party
drill (`api/party_drill_v1.get_party_breakdown`) GROUPs BY
`party_type, party` instead, with the same equality filters on
`is_cancelled`/`docstatus` and an IN list on `account` and `company`.

Without an index whose leading columns include `account` plus the
GROUP BY columns, MariaDB falls back to either a temp-table sort or a
nested loop, both of which scale poorly with row count. This index
gives the optimiser a path that uses the leading equality filters
(via the existing covering index) for row selection, then groups
in-index for `party_type, party` aggregation.

Index columns ordered for the access pattern:
  - account:      first IN filter (high selectivity for the bounded
                  leaf list a drill scope resolves to)
  - party_type:   first GROUP BY column
  - party:        second GROUP BY column
  - posting_date: range filter in WHERE

Per CLAUDE.md rule 2 (refined Phase 3): adding helper indexes to stock
ERPNext tables is permitted when justified by aggregation perf,
documented in patches.txt, and reversible. To remove this index:

    DROP INDEX dgv_party_drill ON `tabGL Entry`;

Idempotent: skips creation if the index already exists.
"""

import frappe


INDEX_NAME = "dgv_party_drill"
TABLE = "tabGL Entry"
COLUMNS = ["account", "party_type", "party", "posting_date"]


def execute():
	existing = frappe.db.sql(
		f"SHOW INDEX FROM `{TABLE}` WHERE Key_name = %s",
		(INDEX_NAME,),
	)
	if existing:
		print(f"{INDEX_NAME} already exists on {TABLE}; skipping.")
		return

	col_list = ", ".join(f"`{c}`" for c in COLUMNS)
	frappe.db.sql(
		f"CREATE INDEX `{INDEX_NAME}` ON `{TABLE}` ({col_list})"
	)
	frappe.db.commit()
	print(
		f"Created supplementary index {INDEX_NAME} on {TABLE} "
		f"({', '.join(COLUMNS)})"
	)
