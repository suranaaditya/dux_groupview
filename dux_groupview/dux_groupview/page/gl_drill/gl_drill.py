"""GL drill -- full page controller.

Phase 4 commit 4 spec v0.3 §6.1. The page renders entirely client-side
from URL query parameters; this controller is intentionally minimal,
mirroring the account-drill page pattern. All data fetches go through
the existing whitelisted APIs:

  - cards_v1.resolve_match_to_accounts          (card scope path)
  - gl_drill_v1.get_gl_entries                  (data)

Permissions: page-level role gate (`GroupView Viewer`, `GroupView
Owner`, `System Manager`) is set in gl_drill.json. Per-company scope
is enforced by `_resolve_scope` inside `get_gl_entries`; the page
cannot widen the user's visibility through query parameters.
"""

import frappe


@frappe.whitelist()
def ping():
	return "pong"
