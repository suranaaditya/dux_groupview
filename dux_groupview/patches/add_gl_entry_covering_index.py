"""Add a covering composite index on tabGL Entry for snapshot aggregation.

Without this index, refresh_tb_snapshot does a full posting_date-range
scan + temporary table + filesort for the GROUP BY (verified via
EXPLAIN). On a 5M-entry production-shaped seed, that takes ~8.6 minutes.
With this covering index, MariaDB does an index-only scan + index-driven
group-by, dropping refresh time to seconds.

Index columns ordered for our access pattern:
  - is_cancelled: equality filter in WHERE
  - docstatus:    equality filter in WHERE
  - company:      first GROUP BY column
  - account:      second GROUP BY column
  - posting_date: range filter in WHERE (placed last so the range scan
                  doesn't disrupt the GROUP BY's ability to use index
                  ordering directly -- if posting_date came before
                  company/account, MariaDB would still need a temp
                  table + filesort because the range scan returns rows
                  unsorted with respect to the GROUP BY columns).

Per CLAUDE.md rule 2 (refined Phase 3): adding helper indexes to stock
ERPNext tables is permitted when justified by aggregation perf,
documented in patches.txt, and reversible. To remove this index:

    DROP INDEX dgv_snapshot_aggregation ON `tabGL Entry`;

Idempotent: skips creation if the index already exists.
"""

import frappe


INDEX_NAME = "dgv_snapshot_aggregation"
TABLE = "tabGL Entry"
COLUMNS = ["is_cancelled", "docstatus", "company", "account", "posting_date"]


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
		f"Created covering index {INDEX_NAME} on {TABLE} "
		f"({', '.join(COLUMNS)})"
	)
