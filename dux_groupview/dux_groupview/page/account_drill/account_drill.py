"""Account drill — full page controller.

Phase 4 commit 3 Phase 2. The page renders entirely client-side from
URL query parameters; this controller is intentionally minimal. All
data fetches go through the existing whitelisted APIs:

  - cards_v1.resolve_match_to_accounts          (card scope path)
  - account_drill_v1.get_account_breakdown      (data)
  - party_drill_v1.get_party_breakdown          (when party-trackable)

Permissions: page-level role gate (`GroupView Viewer`, `GroupView Owner`,
`System Manager`) is set in account_drill.json. Per-company scope is
enforced by `_resolve_scope` inside each API; the page can't widen the
user's visibility through query parameters.
"""

import frappe


@frappe.whitelist()
def ping():
	return "pong"
