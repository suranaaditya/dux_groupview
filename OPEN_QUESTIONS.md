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

---

## Closed

(closed questions move here with their resolution)