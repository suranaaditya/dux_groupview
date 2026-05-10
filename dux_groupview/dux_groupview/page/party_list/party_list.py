"""Party list -- full page controller.

Phase 4 commit 4 HALT 4 (master spec §6.2). The page renders entirely
client-side from URL query parameters; this controller is intentionally
minimal, mirroring the gl-drill page pattern. Data fetches go through
the existing whitelisted APIs:

  - cards_v1.resolve_match_to_accounts          (card scope path)
  - party_drill_v1.get_party_breakdown          (mode='page')
  - party_drill_v1.export_party_list_csv        (CSV)

Permissions: page-level role gate (`GroupView Viewer`, `GroupView
Owner`, `System Manager`) is set in party_list.json. Per-company
scope is enforced by `_resolve_scope` inside the API; the page cannot
widen the user's visibility through query parameters.
"""

import frappe


@frappe.whitelist()
def ping():
	return "pong"
