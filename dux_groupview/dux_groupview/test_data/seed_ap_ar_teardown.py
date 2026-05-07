"""Reverse the AP/AR seed augmentation.

Idempotent and safe. Filters strictly by voucher_no LIKE
'AP-AR-SEED-%' so the existing trust-subset seed (RGI-DEMO-* prefix)
and any production-shape seed (PROD-TEST-*) are untouched.

By default this only deletes GL entries — Supplier and Customer docs
created by seed_ap_ar are LEFT IN PLACE because:
  - leaving party docs preserves any user-created GL entries against
    them (rare, but possible if a developer manually transacted against
    a synthetic supplier),
  - re-running seed_ap_ar after teardown will skip existing party docs
    via the supplier_name / customer_name idempotency check, so leaving
    them costs nothing.

Pass `delete_party_docs=True` to also remove the synthetic party docs.
This requires no GL entries to exist against them (Frappe blocks
party deletion with linked transactions); the function checks first.

Usage:

    bench --site <site> execute \\
      dux_groupview.dux_groupview.test_data.seed_ap_ar_teardown.teardown_ap_ar
    bench --site <site> execute \\
      dux_groupview.dux_groupview.test_data.seed_ap_ar_teardown.teardown_ap_ar \\
      --kwargs '{"delete_party_docs": true}'
"""

import frappe

from dux_groupview.dux_groupview.test_data.seed_ap_ar import (
	CUSTOMER_TEMPLATES,
	SUPPLIER_TEMPLATES,
)
from dux_groupview.dux_groupview.test_data.seed_ap_ar_generator import (
	VOUCHER_PREFIX,
)


def teardown_ap_ar(delete_party_docs=False):
	"""Remove all AP/AR seed data added by seed_ap_ar.

	Returns dict with deletion counts.
	"""
	# --- Step 1: GL entries ---
	gl_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no LIKE %s",
		(f"{VOUCHER_PREFIX}%",),
	)[0][0]
	# Defence-in-depth: a row that matches the prefix but somehow
	# slipped past the LIKE pattern (shouldn't be possible) would abort.
	stray = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` "
		"WHERE voucher_no LIKE %s AND voucher_no NOT LIKE %s",
		(f"{VOUCHER_PREFIX}%", f"{VOUCHER_PREFIX}%"),
	)[0][0]
	if stray > 0:
		print(f"ABORT: {stray} rows match prefix but failed safety check.")
		return {"status": "aborted"}

	if gl_count:
		frappe.db.sql(
			"DELETE FROM `tabGL Entry` WHERE voucher_no LIKE %s",
			(f"{VOUCHER_PREFIX}%",),
		)
		frappe.db.commit()
	print(f"Deleted {gl_count:,} GL entries with voucher_no LIKE {VOUCHER_PREFIX}%")

	# --- Step 2: party docs (optional) ---
	suppliers_deleted = customers_deleted = 0
	suppliers_kept = customers_kept = 0
	if delete_party_docs:
		for tpl in SUPPLIER_TEMPLATES:
			n = tpl["name"]
			if not frappe.db.exists("Supplier", n):
				continue
			# Block deletion if any non-AP-AR-SEED GL entries exist
			# (defensive against accidental orphan transactions).
			leftover = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabGL Entry` "
				"WHERE party_type='Supplier' AND party=%s "
				"  AND voucher_no NOT LIKE %s",
				(n, f"{VOUCHER_PREFIX}%"),
			)[0][0]
			if leftover:
				suppliers_kept += 1
				continue
			try:
				frappe.delete_doc("Supplier", n, force=1, ignore_permissions=True)
				suppliers_deleted += 1
			except Exception as e:
				print(f"WARN: could not delete Supplier {n!r}: {e}")
				suppliers_kept += 1

		for tpl in CUSTOMER_TEMPLATES:
			n = tpl["name"]
			if not frappe.db.exists("Customer", n):
				continue
			leftover = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabGL Entry` "
				"WHERE party_type='Customer' AND party=%s "
				"  AND voucher_no NOT LIKE %s",
				(n, f"{VOUCHER_PREFIX}%"),
			)[0][0]
			if leftover:
				customers_kept += 1
				continue
			try:
				frappe.delete_doc("Customer", n, force=1, ignore_permissions=True)
				customers_deleted += 1
			except Exception as e:
				print(f"WARN: could not delete Customer {n!r}: {e}")
				customers_kept += 1

		frappe.db.commit()
		print(f"Deleted {suppliers_deleted} suppliers ({suppliers_kept} kept)")
		print(f"Deleted {customers_deleted} customers ({customers_kept} kept)")

	return {
		"status": "complete",
		"gl_entries_deleted": gl_count,
		"suppliers_deleted": suppliers_deleted,
		"customers_deleted": customers_deleted,
		"suppliers_kept": suppliers_kept,
		"customers_kept": customers_kept,
	}
