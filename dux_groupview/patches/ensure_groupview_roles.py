"""Ensure the GroupView Owner and GroupView Viewer roles exist.

These roles are referenced in the DGV TB Snapshot and DGV TB Snapshot Row
permissions. Frappe's auto-create-on-migrate behaviour for missing roles
is inconsistent across versions, so we create them explicitly with
desk_access=1.
"""

import frappe


ROLES = ["GroupView Owner", "GroupView Viewer"]


def execute():
	for role_name in ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.disabled = 0
		role.flags.ignore_permissions = True
		role.insert()
		print(f"Created role: {role_name}")
