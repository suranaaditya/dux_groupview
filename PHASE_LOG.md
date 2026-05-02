# dux_groupview — Phase Log

Running record of what was built, what was decided, and what bit us
in each phase. Updated at the end of every Claude Code session.

---

## Phase 0 — Scaffolding

**Goal:** Empty app installed on dev, /groupview route loads.

**Deliverables:**
- [ ] App created via `bench new-app dux_groupview`
- [ ] Installed on `erp.jewonline.in`
- [ ] GitHub repo wired up, version-1 branch
- [ ] hooks.py with app_include_js / app_include_css
- [ ] Frappe Page at /groupview rendering placeholder HTML
- [ ] CLAUDE.md, PHASE_LOG.md, OPEN_QUESTIONS.md in repo
- [ ] Light synthetic test data seeded on dev

**Decisions made:**

**Gotchas:**

---

## Phase 1 — Snapshot Foundation

**Goal:** TB snapshot refreshes every 30 min, health page shows status.

**Deliverables:**
- [ ] `Dux GV TB Snapshot` (parent) doctype
- [ ] `Dux GV TB Snapshot Row` (child) doctype with proper indexes
- [ ] `refresh_tb_snapshot(snapshot_date)` function using raw SQL
- [ ] `bench execute` command for manual refresh
- [ ] Scheduler hook in hooks.py (every 30 min, business hours)
- [ ] `Dux GV Cockpit Health` page showing snapshot age + last duration
- [ ] Backfill script for historical snapshots
- [ ] Unit tests on refresh logic

**Decisions made:**

**Gotchas:**

---

## Phase 2 — Spotlight Cards

(scaffold continues for Phases 2–6...)