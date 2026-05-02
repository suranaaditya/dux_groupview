"""Add a composite (card_id, snapshot_date) index to tabDGV Spotlight Cache.

Frappe doctype JSON has no native composite-index syntax. The cockpit's
primary read pattern is "give me all cards' data for date X", so we
index on (card_id, snapshot_date). The single-column search_index flags
on each field cover the secondary patterns.
"""

import frappe


DOCTYPE = "DGV Spotlight Cache"
TABLE = f"tab{DOCTYPE}"

INDEXES = [
	("dgv_spotlight_lookup", ["card_id", "snapshot_date"]),
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
