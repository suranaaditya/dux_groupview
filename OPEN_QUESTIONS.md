# dux_groupview — Open Questions

Things to ask Kumar Sir, Mr. Raisoni, or test on production before
they become blockers. Closed questions move to PHASE_LOG.md.

---

## Open

### Q1 — Backfill window for historical snapshots
**Asked by:** architecture
**Blocking phase:** Phase 1 (production deploy)
**Question:** When can we run the one-time historical snapshot
backfill on ghraisoni.frappe.cloud? Updated estimate after Phase 1
dev verification: a 12-month backfill on the dev seed (50K GL entries)
took **1.5 seconds total**, not 1-2 hours. Linear projection to
production (~5M GL entries) puts it at ~150 sec for a 12-month run,
still under 3 minutes. Sunday night is no longer load-critical; could
plausibly run any low-traffic window. Caveat: the 10M-row safety check
in `backfill_snapshots` will trip on production (12 × 5M = 60M >
threshold) and require `force=True`. Need Aditya / Kumar Sir sign-off
on the actual window and the force flag.
**Status:** Open

### Q2 — Frappe Cloud staging clone
**Asked by:** architecture
**Blocking phase:** Phase 3 (pivot grid)
**Question:** Should we provision a paid Frappe Cloud clone of
ghraisoni.frappe.cloud for performance testing before Phase 3? Cost is
typically <₹2,000 for a month. Reduces production deployment risk.
**Status:** Open

### Q4 — GHR CACS Pune GST Settings inconsistency
**Asked by:** Phase 0 seed
**Blocking phase:** Anything that creates a new Company on this site,
and possibly production deploys.
**Question:** GHR CACS Pune (CACSPU) has dangling tax-account references
that fail validation when any new Company is inserted. Phase 0 seed
worked around this with a local monkey-patch of
india_compliance.update_gst_settings. The underlying data inconsistency
remains. Need to: (a) identify which tabSingles or tabGST Settings rows
are stale, (b) clean them up, (c) verify Company creation works without
the patch. Possibly also exists on ghraisoni.frappe.cloud — must check
before any backfill there.
**Status:** Open

### Q5 — uv not installed on dev bench venv
**Asked by:** Phase 0 scaffolding
**Blocking phase:** None directly; future bench new-app calls would fail.
**Question:** uv pip install errored during bench new-app. Recovered
with pip install -e directly. Low priority — install uv on the bench
when convenient.
**Status:** Open

---

## Closed

### Q3 — Default spotlight card set for owner role
**Asked by:** Phase 2 design
**Blocking phase:** Phase 2
**Question:** Confirm the default 6 spotlight cards with Kumar Sir
before hardcoding them: Sundry Creditors, Sundry Debtors, Unsecured
Loans, Cash & Bank, Inter-Co Receivable, Fixed Deposits. Anything to
swap?
**Resolution:** Closed in Phase 2 — default 6 cards (Sundry Creditors,
Sundry Debtors, Unsecured Loans, Cash & Bank, Inter-co Receivable,
Fixed Deposits) shipped as hardcoded definitions in
`dux_groupview/dux_groupview/spotlight/cards.py`. Kumar Sir to validate
via post-Phase-2 demo. Editor in Phase 5 will allow adjustment.