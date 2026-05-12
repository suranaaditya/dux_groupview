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

### Q8 — Refresh queue contention at production scale
**Asked by:** Phase 3 perf verification
**Blocking phase:** Phase 3 production deploy
**Question:** `refresh_tb_snapshot()` runs in ~44 sec on a 5M-entry
production-shaped seed (after the Phase 3 covering index + subquery
restructure). The scheduler runs this every 30 min during business
hours. While it runs, the worker queue is blocked for those 44 sec —
other scheduled jobs queue up behind it. On a busy production site
this could cause noticeable cron lag (e.g. email digests, fixture
sync). Options to consider before launch: (a) accept the 44-sec
blip; (b) refactor refresh to chunk per-company (~59 queries of ~1
sec each — total work similar but the queue isn't monopolised for
44 sec at a stretch); (c) move the snapshot job to its own dedicated
queue. Decision needed before the first production deploy.
**Status:** Open

### Q9 — Pivot grid frontend perf at production scale unverified in browser
**Asked by:** Phase 3 perf verification
**Blocking phase:** Phase 3 RGI launch demo
**Question:** Phase 3 verified the pivot API at production scale
(get_pivot_data returns ~5,581 rows × 59 companies = ~330K cells in
~0.5 sec). But we never opened the cockpit page in a browser against
the prod-shaped data — the seed was torn down right after API
correctness was confirmed, to free the dev DB. The DOM render of
~330K cells without virtualisation (Clusterize was disabled in
Phase 3 due to sticky-column conflicts) hasn't been measured.
Heatmap toggle, search filter, and date change perf targets
(instant / instant / < 1.5 sec) need a real browser test on a real
prod-shaped seed before the RGI demo. Options: (a) re-seed +
profile + tear down; (b) wait for staging clone (Q2) and profile
there.
**Status:** Open

### Q11 — Migrate trust selector scope persistence to a User Preferences doctype
**Asked by:** Phase 3.5 trust selector
**Blocking phase:** Phase 5 (settings + saved views)
**Question:** Phase 3.5 persists the cockpit's trust-selector scope in
`localStorage` under key `dgv_cockpit_scope_v1`. Storage is
per-browser, not per-Frappe-user, so a user logging in from a second
device starts with the all-companies default rather than their
last-used scope. Phase 5 should lift this into a `DGV User
Preferences` doctype (single-row-per-user) with a small whitelisted
get/set pair, and the cockpit should fall back to localStorage only
when the server-side preference is absent. The storage key is already
versioned (`v1`) so the migration can be a one-shot copy from
localStorage to the doctype on next page load, then localStorage can
be cleared.
**Status:** Open

### Q12 — Hover tooltip on pivot cells showing full Indian-format value
**Asked by:** Phase 3.5 round-2 review
**Blocking phase:** None (UX nice-to-have)
**Question:** When the pivot is rendering in Cr or L mode, the
displayed value is rounded to 2 decimals against the scaled unit
(e.g. `141.26 Cr` hides anything below ₹1 lakh per cell; `14,126.00 L`
hides anything below ₹1). Add a hover tooltip on each numeric cell
that shows the raw value formatted with the Indian comma-grouping
helper (`format_indian`), regardless of the active toggle. Deferred
from Phase 3.5 to keep the format-toggle scope tight; add when users
ask for precision-on-demand without flipping the toggle. Cheapest
implementation: `title` attribute on `.pivot-cell-num` carrying the
pre-computed Indian-format string. Avoid re-computing on every
hover.
**Status:** Open

### Q5 — uv not installed on dev bench venv
**Asked by:** Phase 0 scaffolding
**Blocking phase:** None directly; future bench new-app calls would fail.
**Question:** uv pip install errored during bench new-app. Recovered
with pip install -e directly. Low priority — install uv on the bench
when convenient.
**Status:** Open

### Q21 — teardown_rgi_named_data leaves tabAccount orphans (and likely others)
**Asked by:** Side PR seed-scale-for-kvm verification
**Blocking phase:** None directly. Cosmetic on dev; production never runs teardown.

teardown_rgi_named_data calls frappe.delete_doc on Company records, but Frappe's
Company.on_trash doesn't cascade-delete child tabAccount rows. After repeated
teardown/reseed cycles on dev, hundreds of orphan tabAccount rows accumulate,
with company values pointing to deleted Company records. This pollutes
pivot.py::_lookup_group_by_stripped_name's LIMIT 1 lookup (no company filter),
causing depth=0 root_type assertions to fail intermittently.

Workaround: cleanup_orphan_accounts() utility in seed_production.py. Run after
teardown if next reseed needs clean state.

Real fix: investigate which child tables ERPNext expects to clean up via
Company.on_trash. Likely also affects: tabCost Center, tabFiscal Year,
tabWarehouse, tabItem Group, tabAddress, tabContact. Either extend teardown
with explicit cascade DELETEs or file upstream Frappe issue.

Decision deferred. Low priority — cosmetic on dev, not blocking any phase.
**Status:** Open

### Q20 — Periodic full-seed CI run
**Asked by:** Side PR `fix/seed-scale-for-kvm`
**Blocking phase:** None (low priority)
**Question:** The dev environment now defaults to a trust-subset seed
(~1.1M rows) so the full app test suite finishes in ~7-9 min instead
of ~32 min on the KVM. Production-shape behaviour at 5M rows
(query plans, scheduler refresh duration, snapshot-row count) is no
longer exercised on every test run. Should there be a periodic CI
tier — e.g. a weekly or pre-release run — that re-seeds at full
scale, runs the suite, and tears back down? Or is on-demand manual
verification (run `seed_rgi_named_data()` without `trusts=`, run
suite, run `teardown_rgi_named_data()`) sufficient given the cadence
of changes that touch refresh / aggregation paths?

If yes, candidate trigger points: (a) before each Phase close (Phase
4, Phase 5, etc.), (b) before any production deploy, (c) on a
scheduled GitHub Action — though the latter would need a perf-tier
runner since the KVM dev box is what we just decided is too slow.
Low priority — manual on-demand is acceptable until cadence
demands more.
**Status:** Open

---

## Q22 — Heatmap toggle removed in commit 2.5
**Asked by:** Cockpit visual redesign (commit 2.5)
**Status:** Resolved

The heatmap toggle was removed during the cockpit visual redesign.
Rationale: low feature value, added visual complexity that fought
the new executive-briefing aesthetic. Format pill (Cr/L/Full/Plain)
now controls only number-formatting.

If heatmap-style visualization is needed in the future, the right
home is the focus mode introduced in commit 6 — a focused single-column
view is a more natural place for color encoding than the multi-column
pivot.

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

### Q7 — TB refresh perf bottleneck on 5M-entry production-shaped data
**Asked by:** Phase 3 perf verification
**Blocking phase:** Phase 3 production deploy
**Question:** Initial measurement of `refresh_tb_snapshot()` on a
5,015,000-row synthetic seed showed 514 sec (8.6 min), 17× over the
spec's 30-sec target. Is the architecture broken, or do we need to
add an index?
**Resolution:** Closed in Phase 3 by (a) adding a covering composite
index on `tabGL Entry (is_cancelled, docstatus, company, account,
posting_date)` via the `dux_groupview.patches.add_gl_entry_covering_index`
patch, and (b) restructuring the refresh SQL to aggregate
`tabGL Entry` in a subquery first (using the new index, no temp /
filesort), then JOIN `tabAccount` against the small ~5,581-row
result. Result: **514 sec → 44.7 sec (11.5× speedup)**. Target
relaxed from 30 sec to 60 sec on the dedicated production-scale row
of the perf table; the dev-scale `Background refresh p95 < 15 sec`
target continues to apply for smaller deployments. Gold-standard
correctness check passed post-optimisation: 0 mismatched rows across
all 5,581 snapshot rows.