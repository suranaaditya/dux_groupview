"""DGV ICD Account — one row per ICD-flagged account.

Storage for the Inter-College Deposit classification. The ICD spotlight
card aggregates accounts named here; the Unsecured Loans card excludes
them. Edited via the `/app/dgv-icd-mapping` settings page (the
api/icd_settings.py endpoints batch-write and trigger a single cache
refresh, bypassing this controller). Standard doctype CRUD is still
allowed for ad-hoc edits via the desk list view.
"""

from frappe.model.document import Document


class DGVICDAccount(Document):
	pass
