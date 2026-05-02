import frappe
from frappe.model.document import Document


class DGVTBSnapshot(Document):
	def on_trash(self):
		"""Cascade-delete child rows when the parent snapshot is removed."""
		frappe.db.sql(
			"DELETE FROM `tabDGV TB Snapshot Row` WHERE parent_snapshot = %s",
			(self.name,),
		)
