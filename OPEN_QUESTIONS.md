# dux_groupview — Open Questions

Things to ask Kumar Sir, Mr. Raisoni, or test on production before
they become blockers. Closed questions move to PHASE_LOG.md.

---

## Open

### Q1 — Backfill window for historical snapshots
**Asked by:** architecture
**Blocking phase:** Phase 1 (production deploy)
**Question:** When can we run the one-time historical snapshot
backfill on ghraisoni.frappe.cloud? It will run 1–2 hours and hit
tabGL Entry heavily. Sunday night is the obvious window, but need to
confirm no batch jobs collide.
**Status:** Open

### Q2 — Frappe Cloud staging clone
**Asked by:** architecture
**Blocking phase:** Phase 3 (pivot grid)
**Question:** Should we provision a paid Frappe Cloud clone of
ghraisoni.frappe.cloud for performance testing before Phase 3? Cost is
typically <₹2,000 for a month. Reduces production deployment risk.
**Status:** Open

### Q3 — Default spotlight card set for owner role
**Asked by:** Phase 2 design
**Blocking phase:** Phase 2
**Question:** Confirm the default 6 spotlight cards with Kumar Sir
before hardcoding them: Sundry Creditors, Sundry Debtors, Unsecured
Loans, Cash & Bank, Inter-Co Receivable, Fixed Deposits. Anything to
swap?
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

(closed questions move here with their resolution)