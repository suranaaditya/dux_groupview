# Feature Requests

Tracks user-requested features deferred to future phases. Each entry should include: requester, date, description, predicate sketch (if known), target phase.

## Phase 5 — Cards editor seed requests

Items to validate the Phase 5 cards editor against when it ships. These are real user requests deferred until self-service is available.

### Interest Paid card
- **Requester:** RGI (via Aditya / Dux DigiTech)
- **Date:** 2026-05-10
- **Description:** Visible in cockpit cards row alongside existing Sundry Creditors, Cash & Bank, etc. RGI wants to track total interest paid across all companies. Account head exists in GHRCACS Pune (and likely the same name across all RGI companies given symmetric COA).
- **Predicate sketch:** account_type='Expense' AND (account_name LIKE 'Interest%' OR account_name LIKE '%Interest Paid%'). Verify against GHRCACS Pune COA before implementing.
- **Status:** Deferred to Phase 5 cards editor (not adding hardcoded; cards editor will let RGI build this themselves)
