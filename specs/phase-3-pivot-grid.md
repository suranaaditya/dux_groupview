# Phase 3 — Pivot Grid

Archived spec for Phase 3, as agreed with Aditya on 2026-05-03.

---

## Goal

Replace the spotlight-only cockpit with a full pivot grid below the
spotlight cards. Account hierarchy down rows, companies across columns
grouped by trust, balance values in cells, virtualized scrolling,
sticky first column and group total column, account search, heatmap
toggle, trust column collapse / expand. Reads exclusively from
`tabDGV TB Snapshot Row` plus `tabAccount` and `tabCompany` for
metadata.

## Hard rule (further tightened)

- Phase 3 reads ONLY from `tabDGV TB Snapshot Row`,
  `tabAccount`, `tabCompany`.
- The pivot API never reads `tabGL Entry`. Verified by `grep`.
- The pivot API also never reads `tabDGV Spotlight Cache` -- spotlight
  is a separate concern, lives in its own API.
- Trust group definitions are hardcoded in Phase 3 (`pivot/trust_groups.py`).
  Phase 5 lifts them into `DGV Cockpit Settings`.

## Resolved interpretive choices

- **Pivot rows are keyed by `account_name` (no company suffix).** ERPNext
  stores each company's accounts with a unique full name like
  `"Sundry Creditors - TCA"`; the pivot groups them by `account_name`
  (`"Sundry Creditors"`) so one row can show values across all companies.
- **Hierarchy is reconstructed from `tabAccount` once per request** by
  taking the most-common `parent_account.account_name` per
  `account_name`. Conflicting parents (rare) log a warning and pick the
  most common.
- **Vendored Clusterize.js v1.0.0** (MIT, Denis Lukov 2015) at
  `public/vendor/clusterize/`. Loaded before `cockpit.js` via the
  `app_include_js` list in `hooks.py`.
- **Trust assignment fallback.** Companies not in any trust map to
  `"default"` (label "Other", color `#888780`). Dev seed companies
  ("Test Company A--E") and prod-test seed companies ("Prod Co N1--N59")
  all fall to default; that's expected.
- **Production seed safety gate.** `seed_production_data()` requires
  the env var `PROD_SEED_CONFIRM=yes` so it can't be triggered
  accidentally. Companion `teardown_production_data()` purges the
  PROD-TEST voucher prefix and synthetic Prod Co companies, then
  refreshes TB + spotlight to restore dev to its prior state.
- **User Permissions on Company.** The pivot API uses `frappe.get_list("Company", ...)`
  which automatically applies User Permissions. Trusts and balance
  cells for restricted companies are filtered out of the response.

## API

`dux_groupview.dux_groupview.api.pivot.get_pivot_data(snapshot_date, format="crore")`

Returns a single JSON payload with:

- `snapshot_date`, `snapshot_age_seconds`, `format`
- `trusts`: list of `{id, name, abbr, color, companies}` (only trusts
  with at least one allowed company)
- `accounts`: list of `{id, name, parent, depth, is_group, root_type, account_type}`
  ordered depth-first
- `balances`: dict of `account_name -> {company_name -> raw Dr-Cr balance}`

Targets: < 500 ms on dev, < 1500 ms on production.

`get_pivot_summary(snapshot_date)` -> `{snapshot_date, generated_at,
row_count, company_count, trust_count}`. Used by cockpit metadata,
not the grid itself.

## Frontend

`window.DuxPivotGrid` class in `public/js/pivot_grid.js`. Constructor
takes `(containerEl, options)` and exposes `render`, `updateData`,
`setHeatmap`, `setSearch`, `collapseTrust`, `expandTrust`,
`collapseAccount`, `expandAccount`, `destroy`.

Internals: Clusterize for row virtualisation. Sticky first column
(account label) via `position: sticky; left: 0`. Sticky group-total
column on the right. Trust headers use `colspan` over their company
columns and have a left border in trust colour. Cell click dispatches
`dux-pivot-cell-click` with `{account, company, value, snapshot_date}`.

Heatmap mode regenerates row HTML with `rgba(...)` cell backgrounds
whose opacity is `abs(value) / row_max(abs(value))`, tinted with the
trust colour. Toggle is client-side and instant.

Search mode rebuilds the visible row array via `clusterize.update`,
filtering by `account.name` substring (case-insensitive).

## CSS

`public/css/pivot_grid.css`. Geist Mono for numbers, Geist for labels.
Reuses cockpit.css colour tokens. Hover rows light-grey, search hits
get a subtle yellow row tint, negative numbers in red parentheses,
zeros muted.

## Tests

`dux_groupview.dux_groupview.tests.test_pivot`:

1. `test_get_pivot_data_structure` -- response shape sanity
2. `test_pivot_data_matches_snapshot` -- gold-standard correctness
3. `test_get_pivot_data_respects_user_permissions` -- restricted user
   sees only their companies
4. `test_trust_assignment_function` -- known company name -> known
   trust id; unknown -> default; all 59 RGI names map correctly
5. `test_get_pivot_data_performance` -- < 500 ms smoke check on dev

## Performance verification (post-review)

After Aditya signs off the dev pivot UI:

1. `seed_production_data()` (PROD_SEED_CONFIRM=yes) -- ~30-40 min
2. `refresh_tb_snapshot` -- target < 30 sec
3. `refresh_spotlight_cache` -- target < 10 sec
4. /groupview first paint -- target < 2 sec
5. Heatmap toggle, search filter -- instant
6. Date change -- < 1.5 sec
7. Record actuals in `PHASE_LOG.md`; update `CLAUDE.md` perf table
8. `teardown_production_data()` to restore dev state

Stop conditions: API > 1 sec on dev, render > 3 sec on prod, gold-
standard test fail, perm leak, sticky positioning broken in any major
browser, prod seed > 60 min.
