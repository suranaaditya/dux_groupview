# dux_groupview — Project Context

## What this app is

A multi-entity Trial Balance cockpit for ERPNext v16. Owner-facing,
read-only, performance-first. Sits alongside ERPNext's stock TB report;
does not replace it.

Primary user: the group owner / managing trustee of a multi-entity
holding. Built first for GH Raisoni Group (RGI) — 59 entities across
10 trust groups in the Indian education sector. Designed to generalise
to any multi-entity setup.

The cockpit's distinctive value over stock ERPNext:
1. Cross-entity rollup with collapsible trust groups
2. Spotlight cards: configurable COA totals shown above the pivot
3. Drill: Group → Trust → Entity → Account → GL Entry
4. TB Doctor: integrity checks across all entities at once
5. Inter-Company Matcher: receivables vs payables diff matrix
6. Mobile PWA for owner-on-the-go usage

## Hard architectural rules

These are non-negotiable. They protect the project from drift.

1. **Never query `tabGL Entry` directly from any UI code path.**
   All UI reads go through the snapshot cache layer (`DGV TB Snapshot Row`,
   `DGV Spotlight Cache`, `DGV Aggregate Cache`). The only place that
   touches `tabGL Entry` is the background refresh function in
   `dux_groupview/snapshots/refresh.py`.

2. **Never modify ERPNext doctype schemas** (no custom fields, links,
   child tables, or controller behavior). Adding helper indexes to
   stock tables IS permitted when justified by aggregation perf,
   documented in `patches.txt`, and reversible via `DROP INDEX`. The
   intent of this rule is to prevent app coupling to ERPNext
   internals — indexes are operational, not structural.

3. **Never write to the books.** This app is read-only on accounting data.
   It can write to its own snapshot/cache/settings doctypes, nothing else.

4. **Never use Frappe ORM for bulk snapshot operations.** Snapshot
   generation uses raw SQL via `frappe.db.sql()` with single
   INSERT...SELECT statements. ORM is fine for everything else.

5. **The cockpit is not the stock TB report.** If a feature is "go run
   the stock TB report", it does not belong here. We build pivot,
   drills, mobile, snapshots, owner UX — different product.

## Architecture

### Layers

    Source layer (ERPNext live):
      tabGL Entry, tabAccount, tabCompany — read by refresh job only.

    Refresh layer:
      Background scheduler runs every 30 min during business hours.
      Incremental refresh: only recomputes balances for accounts touched
      since last_snapshot_at.
      Manual refresh button bypasses cache, recomputes for current day.

    Cache layer (our doctypes):
      DGV TB Snapshot          — header per snapshot date
      DGV TB Snapshot Row      — one row per (date, company, account)
      DGV Spotlight Cache      — pre-computed values for spotlight cards
      DGV Aggregate Cache      — trust-level rollups

    UI layer:
      Cockpit page (/groupview) — pivot, spotlight cards, filters
      Drill views — trust, entity, account
      Mobile PWA — separate manifest, service worker
      Settings — spotlight card editor, saved views, schedule III mapping

### Doctype naming

All doctypes are prefixed `DGV ` to disambiguate from `dux_voucher`
and `dux_portal` doctypes. Examples:

- `DGV TB Snapshot`
- `DGV TB Snapshot Row`
- `DGV Spotlight Card` (child table on settings)
- `DGV Spotlight Cache`
- `DGV Aggregate Cache`
- `DGV Cockpit Settings` (single)
- `DGV User Preferences`
- `DGV Saved View`
- `DGV Annotation`

Folder names use snake_case lowercase: `dgv_tb_snapshot`, etc.

### Roles

- `GroupView Owner` — full configuration access, sees TB Doctor,
  edits spotlight cards, can save shared views.
- `GroupView Viewer` — read-only cockpit access, scoped by User
  Permissions on Company.

User Permissions on Company (the existing ERPNext mechanism) are the
sole source of truth for who sees which entities. The app respects them
without any custom scoping logic.

## Performance targets

These numbers are commitments. Regression triggers a fix.

| Operation                                    | Target         |
|----------------------------------------------|----------------|
| Cockpit initial paint                        | 200 ms         |
| Spotlight cards filled                       | 400 ms         |
| Trust list filled                            | 600 ms         |
| Trust drill                                  | 300 ms         |
| Account drill                                | 500 ms         |
| Background refresh p95                       | <15 sec        |
| Manual full refresh                          | <60 sec        |
| Snapshot row read latency                    | <50 ms         |
| Pivot grid initial render (production scale) | <2 sec         |
| Heatmap toggle                               | instant        |
| Search filter                                | instant        |
| Date change (pivot refetch + re-render)      | <1.5 sec       |
| Trust column collapse                        | <100 ms        |

Measure on production-shaped data (59 entities, ~700 accounts each,
5M+ GL entries). Dev site (~thousand entries) is meaningless for perf.

## File layout

    dux_groupview/
    ├── README.md
    ├── CLAUDE.md                    ← this file
    ├── PHASE_LOG.md                 ← what shipped in each phase
    ├── OPEN_QUESTIONS.md            ← things to ask Kumar Sir / test
    ├── LICENSE                      ← MIT
    ├── dux_groupview/
    │   ├── hooks.py                 ← scheduler events, app_include_js
    │   ├── snapshots/
    │   │   ├── __init__.py
    │   │   ├── refresh.py           ← THE ONLY tabGL Entry reader
    │   │   ├── backfill.py          ← one-shot historical seeding
    │   │   └── health.py            ← ops dashboard data
    │   ├── api/
    │   │   ├── cockpit.py           ← whitelisted APIs for cockpit page
    │   │   ├── drill.py             ← whitelisted APIs for drill views
    │   │   └── settings.py          ← whitelisted APIs for editor
    │   ├── doctype/
    │   │   ├── dgv_tb_snapshot/
    │   │   ├── dgv_tb_snapshot_row/
    │   │   ├── dgv_spotlight_cache/
    │   │   ├── dgv_aggregate_cache/
    │   │   ├── dgv_cockpit_settings/
    │   │   ├── dgv_user_preferences/
    │   │   ├── dgv_saved_view/
    │   │   ├── dgv_annotation/
    │   │   └── dgv_spotlight_card/
    │   ├── page/
    │   │   ├── groupview/           ← main cockpit page
    │   │   ├── groupview_drill/     ← drill views
    │   │   └── groupview_health/    ← ops health page
    │   ├── public/
    │   │   ├── css/
    │   │   │   ├── cockpit.css
    │   │   │   └── mobile.css
    │   │   ├── js/
    │   │   │   ├── cockpit.js
    │   │   │   ├── pivot_grid.js    ← virtualised pivot
    │   │   │   ├── spotlight.js
    │   │   │   └── drill.js
    │   │   └── pwa/
    │   │       ├── manifest.json
    │   │       └── service_worker.js
    │   ├── test_data/
    │   │   ├── seed_light.py        ← 5 cos, 100 accts, 50K entries
    │   │   └── seed_production.py   ← 59 cos, ~700 accts, 5M entries
    │   └── tests/
    │       ├── test_refresh.py
    │       ├── test_drill_api.py
    │       └── test_settings.py
    ├── pyproject.toml
    └── setup.py

## Servers

| Site                           | Role             |
|--------------------------------|------------------|
| erp.jewonline.in               | Primary dev      |
| ghraisoni.frappe.cloud         | RGI production   |

Bench path on dev: /home/frappe/frappe-bench
SSH: frappe@187.127.132.58

## Workflow conventions

- One branch per phase: `phase-0-scaffolding`, `phase-1-snapshots`, etc.
- Never push directly to `main` or `version-1` without a merge.
- Spec before code for any non-trivial piece. Specs live in
  `specs/phase-N-<topic>.md` until implemented, then archived.
- Commit hygiene: no PATs in code; ask before pushing; descriptive
  commit messages.
- Three docs stay current: `CLAUDE.md`, `PHASE_LOG.md`, `OPEN_QUESTIONS.md`.
- Bench commands: dev server runs via `bench start`, not supervisor.
  Only `clear-cache` is needed after Python changes; full `bench restart`
  is not required.

### Bench execute paths

Frappe's app layout double-nests the package directory. The correct
dotted path for `bench execute` calls is:

    dux_groupview.dux_groupview.<subpackage>.<module>.<function>

Example:

    bench --site erp.jewonline.in execute \
      dux_groupview.dux_groupview.test_data.seed_light.seed_light_data

Note the doubled `dux_groupview`. Single-level paths will fail with
`ModuleNotFoundError`. This applies to all phases — Phase 1 refresh
functions, Phase 2 spotlight cache rebuilds, etc.

## Frappe gotchas to remember

These are quirks discovered during this project's implementation. Future
phases and future Claude Code sessions should be aware of them before
debugging.

1. **Scheduler events dedupe by method name only.**
   If two cron entries in `scheduler_events` point to the same dotted
   method, Frappe silently keeps only one Scheduled Job Type record
   during `bench migrate`. Workaround: each cron entry must point to a
   distinct method name. Use thin wrapper functions if needed:

       def refresh_tb_snapshot_business_hours():
           return refresh_tb_snapshot()

       def refresh_tb_snapshot_off_hours():
           return refresh_tb_snapshot()

2. **`bench --site SITENAME mariadb -e "SQL"` does not autocommit.**
   `UPDATE`s and `INSERT`s run via this shell appear to succeed but
   the transaction rolls back at shell exit. For data modifications in
   scripts or tests, use `frappe.db.sql()` with `frappe.db.commit()`
   via `bench execute`, or use `frappe.db.set_value()` which handles
   commit correctly. Read-only `SELECT`s through `bench mariadb` are
   fine.

3. **Composite indexes are not declared in doctype JSON.**
   Frappe's doctype schema only supports per-field index flags
   (single-column). For composite indexes (e.g. `(snapshot_date,
   company, account)`), add them via a one-time `patches.txt`
   migration using `frappe.db.add_index()` or raw `ALTER TABLE`.

## How to start a new Claude Code session

1. Open Claude Code in the repo root.
2. First message: "Read CLAUDE.md, PHASE_LOG.md, and OPEN_QUESTIONS.md.
   Then read the spec for the current phase. Confirm you understand
   before writing any code."
3. Work against the spec. If a question comes up not covered by spec
   or CLAUDE.md, add it to OPEN_QUESTIONS.md and ask in chat —
   don't guess.
4. Update PHASE_LOG.md as deliverables complete.

## What this app deliberately does NOT do

- Does not write to tabGL Entry, tabPayment Entry, tabJournal Entry, or
  any other transactional doctype.
- Does not modify any ERPNext doctype's schema.
- Does not handle authentication, payments, document submission,
  inventory, payroll, or any operational function.
- Does not duplicate ERPNext's Trial Balance report — different product.
- Does not handle student fee data (RGI collects fees outside ERPNext).
- Does not run financial calculations beyond aggregating GL balances
  (no forecasting, no ratios that aren't direct sums, no projections).