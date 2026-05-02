"""Add composite indexes to tabDGV TB Snapshot Row.

Frappe's doctype JSON does not support composite indexes natively, so we
add them via a one-time patch. These indexes are critical for cockpit
read performance:

- (snapshot_date, company, account)  -- pivot grid reads (primary)
- (account, snapshot_date)           -- account drill view
                                        (this account across all companies)
- (company, snapshot_date)           -- entity drill view
                                        (this company's full TB at one date)

If the indexes already exist, they are skipped.
"""

import frappe


DOCTYPE = "DGV TB Snapshot Row"
TABLE = f"tab{DOCTYPE}"

INDEXES = [
	("dgv_pivot", ["snapshot_date", "company", "account"]),
	("dgv_account_drill", ["account", "snapshot_date"]),
	("dgv_company_drill", ["company", "snapshot_date"]),
]


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		print(f"Table {TABLE} does not exist yet; skipping index creation.")
		return

	existing_rows = frappe.db.sql(f"SHOW INDEX FROM `{TABLE}`", as_dict=True)
	existing_index_names = {row["Key_name"] for row in existing_rows}

	for index_name, columns in INDEXES:
		if index_name in existing_index_names:
			print(f"Index {index_name} already present; skipping.")
			continue
		col_list = ", ".join(f"`{c}`" for c in columns)
		frappe.db.sql(
			f"CREATE INDEX `{index_name}` ON `{TABLE}` ({col_list})"
		)
		print(f"Created index {index_name} on ({', '.join(columns)})")
