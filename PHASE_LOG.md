# dux_groupview — Phase Log

Running record of what was built, what was decided, and what bit us
in each phase. Updated at the end of every Claude Code session.

---

## Phase 0 — Scaffolding

**Status:** Done — 2026-05-02. Branch `phase-0-scaffolding` ready for review (not yet merged to main).

**Goal:** Empty app installed on dev, /groupview route loads.

**Deliverables:**
- [x] App created via `bench new-app dux_groupview`
- [x] Installed on `erp.jewonline.in`
- [x] GitHub repo wired up, `phase-0-scaffolding` branch
- [x] hooks.py with `app_include_js` / `app_include_css`
- [x] Frappe Page at /groupview rendering placeholder HTML (sidebar-less, Geist font, light theme)
- [x] CLAUDE.md, PHASE_LOG.md, OPEN_QUESTIONS.md preserved in repo
- [x] Light synthetic test data seeded on dev (5 companies, 50K GL entries)

**Decisions made:**

- **App scaffolded server-side, then merged with seed-doc main.** Ran `bench new-app dux_groupview` on the dev server, added `https://github.com/suranaaditya/dux_groupview` as `origin`, fetched `origin/main` (which already had the 3 seed docs), renamed the auto-created `version-16` branch to `phase-0-scaffolding`, and merged `origin/main` in with `--allow-unrelated-histories`. No conflicts since the seed docs and bench-generated files don't overlap.
- **Frappe Page lives at /app/groupview (i.e., /desk/groupview).** The spec text said "Visiting http://erp.jewonline.in/groupview" but also said "Frappe Page (NOT a Web Page)". Resolved in favor of the Frappe Page route — a Page named `groupview` is reachable at `/app/groupview` (or the legacy `/desk/groupview`). No www-level redirect was added; visual confirmed by Aditya in browser.
- **Synthetic GL entries use voucher_type `DGV Test Seed` and voucher_no `DGV-TEST-NNNNNN`.** Lets the seeder safely purge-and-reseed via `WHERE voucher_no LIKE 'DGV-TEST-%'`, with a safety check that aborts if any matching row's voucher_no doesn't actually start with the prefix.
- **Bulk insert via `frappe.db.bulk_insert`.** 50K rows in ~8 sec on this dev box. ORM-level inserts would have been ~5+ min. Acceptable for seed; production-scale snapshot inserts will use raw SQL per CLAUDE.md rule 4.
- **Diagnostic discipline note (caught after Phase 0 main work).** When an error trace looks like a recurring problem (e.g. filename corruption from earlier in the session), verify the actual file state on disk before any destructive operation. `ls -la` / `git ls-tree` output may render misleadingly in chat transports due to markdown linkification.

**Gotchas:**

- `bench new-app` tries to install via `uv pip install`; this dev's venv has only `pip`. The new-app step finished file generation but exited 167 on install. Recovered by running `pip install -e apps/dux_groupview` against the bench venv directly.
- After the failed `uv` install, `dux_groupview` was missing from `sites/apps.txt`, so `bench install-app` initially errored with "App not in apps.txt". Appended manually and proceeded. (The existing apps.txt had no trailing newline — first append concatenated to the prior entry; fixed with `sed`.)
- Creating any new Company on this dev site triggers `india_compliance.update_gst_settings`, which re-saves GST Settings and validates GST tax-account references. Pre-existing company "GHR CACS Pune" (abbr CACSPU) has dangling references, so the validation fails and the new Company insert rolls back. Worked around in `seed_light.py` with a `_suppress_gst_settings_revalidation` context manager that monkey-patches `update_gst_settings` to a no-op for the duration of seed company creation only. Real GST wiring on existing companies is untouched.
- Seed `_split_amount` rounds at 2 decimals per leg, leaving ~₹24K total Dr/Cr drift across 50K rows (~0.0025%). Trial Balance will be slightly off. Acceptable for synthetic data; fixable later if Phase 1 demands bit-exact balance.
- Frappe v16's `bench new-app` prompts for: Title, Description, Publisher, Email, License, GitHub workflow Y/N, Branch — 7 prompts total (one more than older versions; the GitHub-workflow prompt is new).
- DB backup taken before the first seed run: `/home/frappe/frappe-bench/sites/erp.jewonline.in/private/backups/20260502_221932-erp_jewonline_in-database.sql.gz`.
- Initial `bench execute` attempt failed with `ModuleNotFoundError` because the spec used a single-level dotted path. Correct path is double-nested: `dux_groupview.dux_groupview.test_data.seed_light`. CLAUDE.md updated to document this for future phases.

**Verification:**

- `curl http://localhost:8000/api/method/ping` (with Host: erp.jewonline.in) → `{"message":"pong"}`
- `bench --site erp.jewonline.in list-apps` shows `dux_groupview 0.0.1 phase-0-scaffolding`
- /desk/groupview renders the placeholder card (screenshot confirmed by Aditya)
- `SELECT COUNT(*) FROM tabGL Entry WHERE voucher_no LIKE 'DGV-TEST-%'` → 50,000 rows across 19,211 vouchers, 5 companies, dates 2025-05-02 → 2026-05-02

---

## Phase 1 — Snapshot Foundation

**Status:** Done — 2026-05-03. Branch `phase-1-snapshots` ready for review (not yet merged to main).

**Goal:** TB snapshot refreshes every 30 min, health page shows status.

**Deliverables:**
- [x] `DGV TB Snapshot` (parent) doctype
- [x] `DGV TB Snapshot Row` (child) doctype with composite indexes
- [x] `refresh_tb_snapshot(snapshot_date)` function using raw `INSERT...SELECT`
- [x] `bench execute` command for manual refresh
- [x] Scheduler hooks in hooks.py (every 30 min business hours, hourly off hours, nightly finalize)
- [x] `/groupview-health` admin page (System Manager only) showing latest snapshot, last 7, scheduler heartbeat, slow-refresh warning
- [x] Whitelisted API at `dux_groupview.dux_groupview.api.health` (`get_snapshot_health`, `trigger_manual_refresh`, `trigger_backfill`)
- [x] Backfill script with 10M-row safety check
- [x] All 7 unit tests pass (gold-standard correctness included)

**Decisions made:**

- **PTD totals = lifetime cumulative** (Q-A). Both `debit_total` and `credit_total` use the same `posting_date <= snapshot_date` filter as `balance`. Window calculations (MTD / FYTD / arbitrary) are derived later by subtracting two snapshots' values. Maintains the invariant `balance = debit_total - credit_total` (verified zero violations across all 13 snapshots in dev).
- **`is_opening = 'Yes'` entries included** (Q-B). Standard accounting convention; critical for migrated entities.
- **Filter is `is_cancelled = 0 AND docstatus = 1 AND posting_date <= snapshot_date`.** Defence in depth against draft / cancelled rows.
- **Sign convention:** raw `Dr - Cr` stored in `balance`. UI flips sign for Liability / Equity / Income based on `root_type`. Keeps `balance = debit_total - credit_total` exact.
- **Composite indexes via patch.** Frappe doctype JSON has no native composite-index syntax; added `dgv_pivot (snapshot_date, company, account)`, `dgv_account_drill (account, snapshot_date)`, `dgv_company_drill (company, snapshot_date)` via `dux_groupview.patches.add_dgv_snapshot_row_indexes`.
- **Two scheduler entry points (`refresh_tb_snapshot_business_hours` and `_off_hours`)** rather than one `refresh_tb_snapshot` referenced from both crons. Frappe's scheduler de-dupes Scheduled Job Type by `method` only (see `frappe/.../scheduled_job_type.py:267`); two crons pointing at the same method collide and the later one wins. Two thin wrapper methods sidestep this without changing semantics.
- **Backfill auto-locks past dates.** After `refresh_tb_snapshot()` succeeds for a date strictly < today, the snapshot is immediately marked `is_immutable=1` so the next scheduler tick can't silently overwrite it. Closes the window between backfill completion and the nightly `finalize_past_snapshots`.
- **Backfill `force=True` clears `is_immutable` before re-running** so `refresh_tb_snapshot()`'s own immutable check doesn't trip. The lock is then re-applied by the auto-lock step above. Without this, force-mode backfill would fail on every previously-locked date (caught by `test_backfill_force_override`).
- **Backfill 10M-row safety threshold** = `12 * COUNT(tabGL Entry)`. On dev (50K rows): 600K, well under threshold. On production (~5M rows): 60M would trip → must use `force=True` on first prod backfill (see Q1).
- **Health page uses Geist + light theme** matching the Phase 0 cockpit. Refresh button enqueues to `default` queue; backfill enqueues to `long` queue with 1-hour timeout. Polling timeout 60 seconds before showing "taking longer than expected".

**Gotchas:**

- **`bench --site mariadb -e "..."` does not commit by default** -- the wrapper opens a transaction that is rolled back on shell exit. UPDATE statements appear to succeed but the change vanishes. Cost an hour during immutable-protection testing. Workaround: use `frappe.db.set_value` via a `bench execute` or a small inline `python -c` (run from `~/frappe-bench/sites/`), which goes through the proper Frappe transaction layer with explicit `frappe.db.commit()`.
- **`frappe.db.table_exists()` expects the doctype name, not the prefixed table name.** Passing `"tabDGV TB Snapshot Row"` produces a check for `"tabtabDGV TB Snapshot Row"` and silently returns False, so the index patch on first run did nothing. Fixed by passing `"DGV TB Snapshot Row"` to `table_exists` and using the prefixed form only for the SQL.
- **`Role` doctype has no `description` field** in this Frappe v16 build. The first version of `ensure_groupview_roles` patch tried to set one and aborted the migrate with `(1054, "Unknown column 'description'")`. Removed the line.
- **`bench new-app` install via `uv` failure pattern repeats** (carried over from Phase 0): `bench install-app` requires `dux_groupview` in `sites/apps.txt`. Phase 0's earlier fix is already in place; called out here for future apps.
- **`bench execute` renders bubbled exceptions as a misleading `NameError: name '<app>' is not defined`** at the bottom of the traceback. The real exception (from `frappe.throw` in our case) is higher up. When debugging, scroll to the top of the traceback rather than trusting the bottom line.
- **Frappe scheduler dedup by method** (see Decisions): the spec's original cron map with two entries pointing to `refresh_tb_snapshot` would silently lose one entry. Documented and worked around with two wrapper methods.
- The three Frappe quirks documented above are also captured in `CLAUDE.md` under "Frappe gotchas to remember" so they're visible to all future Claude Code sessions, not just this phase log.

**Performance:**

| Operation | Source rows | Output rows | Duration | Target |
|---|---|---|---|---|
| Single refresh (today) | 50,726 | 507 | **0.15--0.32 sec** | < 15 sec ✅ |
| 12-month backfill (12 refreshes + locks) | 50,726 | ~5,800 | **~1.5 sec total** | < 60 sec ✅ |
| Unit test suite (7 tests) | -- | -- | **3.8 sec** | -- |

Gold-standard correctness check (`SELECT COUNT(*) ... WHERE balance != live SUM`): **0 mismatched rows** across all 13 snapshots × ~500 rows each (~6,500 rows checked). Invariant `balance = debit_total - credit_total`: **0 violations**.

Production projection: with ~5M GL entries instead of 50K, refresh duration scales roughly linearly to ~30 sec. Under the 30-second stop threshold but tight; will revisit perf if Phase 3 reads need finer cadence.

---

## Phase 2 — Spotlight Cards

**Status:** Done — 2026-05-03. Branch `phase-2-spotlight` ready for review (not yet merged to main).

**Goal:** Replace the placeholder /groupview page with a real cockpit landing screen showing 6 live spotlight cards backed by a pre-aggregated cache.

**Deliverables:**
- [x] `DGV Spotlight Cache` doctype with composite `(card_id, snapshot_date)` index
- [x] `spotlight/cards.py` — 6 hardcoded card definitions (sundry creditors / debtors, unsecured loans, cash & bank, inter-co receivable, fixed deposits)
- [x] `snapshots/spotlight_refresh.py` — single-transaction refresh, sign-corrected aggregation, sparkline + delta computation
- [x] Wired into both scheduler wrappers and the backfill loop; spotlight failures swallowed-and-logged so they never roll back a TB snapshot
- [x] `/groupview` cockpit page rewritten — date selector, snapshot age pill (color-coded by freshness), 3-column card grid with sparklines, click-to-toast Phase 4 placeholder
- [x] `api/cockpit.py` — `get_available_snapshot_dates`, `get_spotlight_cards`, `get_snapshot_age`, all GroupView Viewer or higher
- [x] All 7 unit tests pass including the gold-standard `test_spotlight_value_matches_direct_aggregation`
- [x] Q3 closed in `OPEN_QUESTIONS.md`

**Decisions made:**

- **`by_account_type` accepts string OR list.** The spec note "by_account_type = 'Bank' or 'Cash' (handle multi-value match)" was implemented by allowing the strategy value to be either a single string or a list -- the matcher emits `=` or `IN (...)` accordingly. Existing single-value cards keep the string form.
- **Delta period definition.** `delta = current value - value at the latest snapshot strictly before the first day of this month`. With month-end backfill in place, this resolves to the prior month-end on dev. If no prior snapshot exists, delta = 0. `delta_percent` is 0 when the prior value is 0 (avoids divide-by-zero / infinity).
- **Sparkline composition.** 6 most recent month-end snapshot dates with `snapshot_date <= target snapshot_date`. Padded with `null` at the front if fewer than 6 historical month-ends exist. Re-aggregates from `tabDGV TB Snapshot Row` (not from prior `DGV Spotlight Cache` rows) so a card-definition change re-bases the whole sparkline.
- **Sign convention -- spotlight stores natural-side value.** Phase 1 stores `Dr - Cr` raw; spotlight applies `CASE WHEN root_type IN ('Liability','Equity','Income') THEN -balance ELSE balance` per row before summing. UI consumes this as positive = healthy direction. Polarity (`good_up` / `bad_up` / `neutral`) is purely UI metadata for delta colouring -- never affects the stored value (`test_polarity_does_not_affect_value`).
- **One transaction per refresh.** All 6 cards upserted in a single `frappe.db` transaction. On any failure, full rollback so we never end with a partial cache.
- **Spotlight failure does not roll back TB snapshot.** Both `_business_hours` / `_off_hours` wrappers and the backfill loop catch spotlight exceptions and log via `frappe.log_error`. TB is canonical, spotlight is derivative.
- **Server-rendered `formatted_value` / `formatted_delta`.** API returns pre-formatted strings (e.g. `"4.5 Cr"`) so JS can't drift on locale / rounding bugs.
- **Snapshot age pill colour bands:** green < 30 min, amber 30-60 min, red > 60 min. Polled client-side every 30 sec.

**Gotchas:**

- **`bench --site mariadb -e CURDATE()` returns the server's local date, not the site timezone date.** Cost a few minutes when a `WHERE snapshot_date = CURDATE()` query unexpectedly returned nothing right after a refresh that wrote `2026-05-03` rows (the server's CURDATE was a different date than the site-tz today). Workaround: pass the literal date string in test queries.
- **`pkill -HUP -f gunicorn` causes SSH to exit 255.** The signal still fires and workers reload (visible via `ps aux`); the SSH client just bails because pkill targets the parent and the connection is briefly disrupted. Cosmetic; the reload itself succeeds.
- **Frappe's "not whitelisted" error message is misleading.** An unauthenticated curl against a `@frappe.whitelist()` (no `allow_guest`) returns "Function ... is not whitelisted" rather than something like "authentication required". Function IS whitelisted; HTTP-level testing requires a session cookie. Verified via `bench execute` that the function is reachable in-process; browser as Administrator works.
- **Real dev data has accounts matching `%Unsecured Loan%` and `%Inter%Compan%`.** The spec's expectation that cards 3, 5, 6 would be zero on dev was off -- only fixed_deposits (card 6) is truly zero. Test `test_zero_match_card_returns_zero` updated to use that card. The cards still show real numbers from jewonline live data, including a pathological `Trial Bank - DD` account in Dux Digitech with a -111 billion Cr balance that makes `cash_and_bank` look absurd on dev. Production RGI data won't have this.

**Performance:**

| Operation | Source | Duration | Target |
|---|---|---|---|
| `refresh_spotlight_cache` (cold, 1 date, 6 cards × 6 sparkline aggregations + 6 prior-month deltas) | 6 cards × ~13 snapshot dates | **0.18 sec** | < 2 sec ✅ |
| `refresh_spotlight_cache` (warm, just upserts) | same | **0.034 sec** | -- |
| `get_spotlight_cards` API (cold, via `bench execute` so includes Frappe bootstrap) | -- | **~0.5 sec** | -- |
| `get_spotlight_cards` API (HTTP, would be substantially less without bootstrap overhead) | -- | TBD via browser | < 500 ms first paint |
| Unit test suite (7 tests) | -- | **1.9 sec** | -- |

Gold-standard test: cached value vs independent SQL aggregation -- exact match on all 6 cards.

**`tabGL Entry` audit:** `grep -rn "tabGL Entry"` across all Phase 2 code (`spotlight/`, `api/cockpit.py`, `snapshots/spotlight_refresh.py`, `page/groupview/groupview.js`) returns only docstring mentions explaining the rule; no actual queries. Hard architectural rule preserved.

---

## Phase 3 — Pivot Grid

**Status:** Done — 2026-05-03. Branch `phase-3-pivot` ready for review (not yet merged to main).

**Goal:** Replace the spotlight-only cockpit with a full pivot grid below the cards. Account hierarchy down rows, companies grouped by trust across columns, sticky first/last columns, account search, heatmap toggle, trust collapse / expand. Reads exclusively from `tabDGV TB Snapshot Row` plus `tabAccount` and `tabCompany` for metadata.

**Deliverables:**
- [x] Clusterize.js v1.0.0 vendored at `dux_groupview/public/vendor/clusterize/` (MIT, Denis Lukov)
- [x] `pivot/trust_groups.py` with all 10 RGI trusts (verbatim names from Aditya) + synthetic `default` trust
- [x] `api/pivot.py` — `get_pivot_data` and `get_pivot_summary`, GroupView Viewer or higher, respects User Permissions on Company
- [x] `public/js/pivot_grid.js` — `DuxPivotGrid` class with render / updateData / setHeatmap / setSearch / collapseTrust / expandTrust / collapseAccount / expandAccount / destroy
- [x] `public/css/pivot_grid.css` — sticky columns, heatmap, hover, search hit, total row in sticky `<tfoot>`
- [x] Cockpit page wired (view toggle, search, heatmap, pivot below spotlight)
- [x] All 5 unit tests pass (0.83 sec) including the gold-standard `test_pivot_data_matches_snapshot`
- [x] Visual verification on dev confirmed by Aditya (post scroll-bounce fix below)
- [x] `seed_production.py` with `seed_production_data` + `teardown_production_data`, gated by `PROD_SEED_CONFIRM=yes`
- [x] Production-shaped perf verification on 5M-entry synthetic data (numbers below)

**Decisions made:**

- **Pivot grouping by `account_name`.** ERPNext stores per-company copies of each account with a unique full name (`"Sundry Creditors - TCA"`, etc.); the pivot groups them by `account_name` so one row shows values across all companies' matching accounts.
- **Hierarchy reconstructed at request time.** Walk up `tabAccount.parent_account` for each leaf in the snapshot to add ancestor groups (which have no snapshot rows of their own) so the UI can render an expand/collapse tree. Most-common-parent wins on conflicts.
- **`_allowed_companies()` bypasses Company doctype role check** and applies User Permissions directly. The GroupView Viewer / Owner roles don't have read on Company (it's a stock ERPNext doctype we deliberately don't modify); User Permissions are the real authorisation mechanism here. System Manager always sees everything.
- **Total row pulled out of `<tbody>` into sticky `<tfoot>`.** Originally rendered as the last Clusterize-managed row; that broke the virtualization spacer math (Clusterize assumes uniform row heights, the total row's `border-top: 2px` doesn't comply). Sticky `<tfoot>` keeps it pinned to the bottom of the scroll container with no virtualization interference.
- **Clusterize disabled for Phase 3.** Even with the total row out, sticky-column cells inside Clusterize-managed virtualized rows produce a "snap back at end of scroll" UX bug (the bottom spacer recalculates as the last cluster comes into view, shrinking the scrollable area, snapping the scrollbar up). Direct DOM rendering ships in Phase 3 — fine for ~500 rows on dev and the projected ~700 on production. The Clusterize files stay vendored and loaded so a Phase 3.5 frozen-column virtualization library can swap in if production scale demands it.
- **Production seed safety gate.** `seed_production_data()` requires the env var `PROD_SEED_CONFIRM=yes` to proceed. Companion `teardown_production_data()` purges the `PROD-TEST-` voucher prefix and the synthetic `Prod Co N1..N59` companies, then refreshes TB + spotlight to restore the prior dev state. Strictly safe on real data.
- **Cell click event surface.** `dux-pivot-cell-click` `CustomEvent` with `{account, company, value, snapshot_date}` detail. Phase 4 listens and shows the drill panel; for now `groupview.js` shows a "coming in Phase 4" toast.

**Gotchas:**

- **Local repo had a duplicated `public/` directory tree at the wrong nesting depth.** Phase 3 files initially landed at `dux_groupview/dux_groupview/dux_groupview/public/` (3 dux levels) instead of `dux_groupview/dux_groupview/public/` (2 dux levels) because I instinctively wrote them next to other Phase 3 module-dir files. Frappe's `sites/assets/` symlink points at the 2-dux level, so the assets returned 404 for the first build. Moved everything to the 2-dux level and dropped the empty 3-dux tree on both local and server. `find . -path "*public*" -type f` is the quickest sanity check after any new vendor / asset add.
- **`bench mariadb -e "LIKE 'X %'"` parses `%` as a printf format specifier when args are empty.** Cost a test failure on the user-permissions test until I rewrote with a parameterised query (`LIKE %s` + `("X %",)`).
- **GroupView roles can't `frappe.get_list("Company", ...)`** because Company is a stock ERPNext doctype and we don't add a Custom DocPerm for it. `_allowed_companies()` queries `tabCompany` directly with `frappe.db.sql_list` and applies User Permissions manually. This is documented at the function's docstring.
- **Clusterize + sticky columns is a known-bad combo.** Spec required Clusterize; we vendored it but disabled use for Phase 3 (see Decisions). If Phase 3.5 needs virtualization at 700+ accounts, the right approach is a frozen-column library (two synchronised tables) rather than Clusterize + `position: sticky`.
- **Curl to `localhost:8000/assets/` returns 404 on the dev server.** Static assets are served by nginx at port 443, not by gunicorn. Use `curl -sk -H "Host: erp.jewonline.in" --resolve erp.jewonline.in:443:127.0.0.1 https://erp.jewonline.in/assets/...` to verify asset URLs from the server.

**Performance:**

| Operation | Source / Scale | Duration | Target |
|---|---|---|---|
| `get_pivot_data` (dev: ~500 accounts × 8 companies) | snapshot + Account join | < 100 ms (test passes < 500 ms) | < 500 ms ✅ |
| Unit test suite (5 tests) | -- | **0.83 sec** | -- |
| `seed_production_data` | 5,015,000 GL entries created (no extras) | **21.5 min** | -- |
| `refresh_tb_snapshot` (production-shaped, BEFORE optimisation) | 5M `tabGL Entry` rows → 5,581 snapshot rows | **514 sec / 8.6 min** | ❌ < 30 sec target |
| `refresh_tb_snapshot` (production-shaped, AFTER covering index + subquery restructure) | same | **44.7 sec** | < 60 sec (revised) ✅ |
| `refresh_spotlight_cache` on prod-shaped data | 5,581 snapshot rows | **0.044 sec** | < 10 sec ✅ |
| `get_pivot_data` on prod-shaped data | 5,581 snapshot rows + 7,123 Account rows | **~0.5-0.6 sec** wall (incl. bench bootstrap) | < 1.5 sec ✅ |
| Heatmap toggle | client-side | instant | instant |
| Search filter (debounced 80 ms) | client-side | instant | instant |

**Performance optimisation story (refresh):**

1. First measurement on production seed: 514 sec / 8.6 min — 17× over the original 30 sec target.
2. EXPLAIN on the refresh's `INSERT...SELECT FROM tabGL Entry GROUP BY ...` showed `type: range` + `Using temporary; Using filesort` — 2.2 M rows scanned, in-memory grouping, no helpful index.
3. **Optimisation 1 — covering index on `tabGL Entry`**: a new patch `add_gl_entry_covering_index` added `(is_cancelled, docstatus, company, account, posting_date)` index. Resulted in `Using index` (no row data fetch) — but the actual refresh-time SQL `GROUP BY`s on `a.account_type, a.root_type` (joined from `tabAccount`), so the optimiser switched to a tabAccount-driven nested-loop plan and the win was lost (552 sec, slightly worse than baseline). EXPLAIN-fidelity gotcha: the simplified test query showed the wrong plan.
4. **Optimisation 2 — restructure refresh SQL**: aggregate `tabGL Entry` in a subquery first (which uses our covering index cleanly with no temp / filesort), then JOIN `tabAccount` against the small ~5,581-row result. Final EXPLAIN: inner aggregation `Using where` with our index, outer JOIN on `tabAccount.PRIMARY`. Result: **44.7 sec** — 11.5× speedup, 14.7 sec over the 30 sec target but well within the revised 60 sec.
5. CLAUDE.md rule 2 was refined to permit operational helper indexes on stock ERPNext tables (commit `6bc51ad`); the index lands via `dux_groupview.patches.add_gl_entry_covering_index` and is reversible via `DROP INDEX dgv_snapshot_aggregation ON \`tabGL Entry\``.
6. The perf restructure also added a `HAVING` clause to the inner aggregation that drops all-zero `(company, account)` pairs. Reduces row count slightly without semantic change. Safe no-op on clean data.

**Gold-standard test on production-shaped data (post-optimisation):**

```
mismatched_rows: 0
invariant_violations: 0
```

Every one of the 5,581 snapshot rows derived from the 5,015,000 GL entries matches an independent SQL aggregation against `tabGL Entry`, AND the `balance = debit_total - credit_total` invariant holds across the entire row set. The SQL restructure preserved correctness.

**`tabGL Entry` audit:** `grep -rn "tabGL Entry"` across `pivot/`, `api/pivot.py`, `public/js/pivot_grid.js` returns only docstring mentions of the architectural rule; no real queries. Two-layer cache rule preserved. (`refresh.py` itself is the one permitted reader of `tabGL Entry` per CLAUDE.md rule 1.)

---

## Phase 3.5 — Trust Selector

**Branch:** `feat/trust-selector`
**Status:** _to fill in on merge_

**Goal:** Header-pill popover that scopes the cockpit (spotlight cards
+ pivot grid) to a chosen subset of trusts and companies. Default is
the last-used scope (or all-companies for first-time users). Persisted
in `localStorage`. Server-side intersection with User Permissions on
Company is the security boundary -- a user cannot widen their own
visibility through the selector.

**Deliverables:**
- [x] `specs/phase-3.5-trust-selector.md` archived
- [x] `dux_groupview/public/js/trust_selector.js` -- self-contained
  `DuxTrustSelector` class with tri-state checkboxes, search,
  click-outside / Esc cancel, localStorage-friendly Apply / Cancel API
- [x] `dux_groupview/public/css/trust_selector.css` -- popover + pill
  styling, plus a `dgv-loading-dim` utility for the affected sections
  during a scope-change fetch
- [x] `hooks.py` updated to ship the new JS/CSS in the right order
- [x] `api/pivot.py`: `get_pivot_data` and `get_pivot_summary` accept
  optional `companies` arg; new `_resolve_scope` helper does the
  User-Permission intersection in one place; new `get_scope_options`
  endpoint returns the trust × company universe for the popover
- [x] `api/cockpit.py`: new `get_spotlight_cards_filtered` endpoint;
  reuses `aggregate_card_value` / `prior_month_snapshot_date` /
  `historical_month_end_dates` from `spotlight_refresh.py` (now
  exported as public aliases) so the filtered path goes through the
  same SQL-level aggregation as the cache refresh
- [x] `snapshots/spotlight_refresh.py`: `_aggregate` accepts an
  optional `companies` iterable. Default `None` keeps the cache-refresh
  semantics unchanged.
- [x] `page/groupview/groupview.js`: header pill, selector mount, scope
  persistence, scoped card / pivot fetches, smooth re-render (existing
  cards dim in place rather than blank-and-rebuild)
- [x] `tests/test_pivot_filter.py`: 4 tests including gold-standard
  filtered-vs-direct-SQL aggregation
- [ ] Visual verification on dev (RGI-DEMO seed loaded) -- _Aditya_
- [ ] Performance: scope-change re-render < 1 sec on dev seed -- _Aditya_

**Decisions made:**

- **`null` scope means "all companies".** When the cockpit has the user's
  full allowed set selected, `scopeCompanies` is normalised to `null`
  client-side and `companies` is omitted from API calls. This keeps the
  default fetch shape identical to Phase 3 and lets the server skip the
  JSON parsing + intersection work, AND lets the spotlight code path
  hit the cached endpoint (`get_spotlight_cards`) instead of the
  filtered one.
- **Empty selection coerces to "all".** A blank scope renders a useless
  cockpit; both client-side (`_handleApply`) and via the storage
  reconciliation (`reconcileScope`) we promote empty back to the full
  universe rather than show an empty page. The user's intent in
  hitting Apply on an empty draft is almost certainly "reset" rather
  than "show nothing".
- **Storage key versioned (`dgv_cockpit_scope_v1`).** Lets us bump the
  schema in Phase 5 (when scope migrates to `DGV User Preferences`)
  without orphaning stale entries silently. Version mismatch -> ignore,
  fall back to all-companies.
- **Scope persistence is per-browser, not per-Frappe-user.** Logged as
  Q11. Phase 5 lifts this into a server-side doctype.
- **Single-trust summary distinguishes full vs partial.** "ASS (16
  companies)" vs "ASS (14 of 16)". 4+ trusts collapses to "N trusts, M
  companies" for compactness; below that threshold the pill keeps
  trust abbreviations so the user can read the scope at a glance.
- **`tabGL Entry` audit holds.** `grep -rn "tabGL Entry"` across the
  Phase 3.5 surface (`trust_selector.js`, `pivot.py`, the new code
  path in `cockpit.py`) returns only docstring mentions; no real
  queries. The filtered spotlight path reads only
  `tabDGV TB Snapshot Row`.

**Gotchas surfaced:**

- **Frappe whitelist serialises array args as JSON strings.** Calling
  `frappe.call({args: {companies: ["a", "b"]}})` arrives at the server
  as a string. `_resolve_scope` accepts either a Python list OR a
  JSON-stringified list and decodes via `json.loads` if needed. Tests
  exercise the Python-list path; the JS path was hand-traced.
- **Single-element `IN %s` placeholder needs a duplicated tuple.**
  Carried over from Phase 3's pattern but factored into a tiny
  `_sql_in_tuple` helper in `pivot.py` to avoid the inline ternary
  noise repeated across the two queries.
- **MariaDB `IN ()` with named placeholders works fine.** The filtered
  spotlight aggregator builds `co_0, co_1, ...` placeholders and
  populates them from the `companies` list -- avoids the duplicate-the-
  lone-value workaround when there's exactly one company.

**Performance:**

| Operation | Source / Scale | Duration | Target |
|---|---|---|---|
| `get_scope_options` (dev) | tabCompany only | TBD on dev | < 200 ms |
| `get_pivot_data` with full scope (dev) | unchanged from Phase 3 | < 100 ms | < 500 ms ✅ |
| `get_pivot_data` with subset scope (dev) | + `WHERE company IN ()` | TBD on dev | < 500 ms |
| `get_spotlight_cards_filtered` (dev, 6 cards × 13 hist dates) | snapshot rows | TBD on dev | < 500 ms |
| Scope-change client re-render (dev) | spotlight + pivot | TBD | < 1 sec |

Numbers fill in after the dev verification pass.

**Open follow-ups:**

- Q11 added to OPEN_QUESTIONS.md: migrate scope persistence from
  localStorage to `DGV User Preferences` doctype in Phase 5.

**Additions (post-initial-review, 2026-05-04):**

Issues raised in initial visual review:
1. Default load showed all 59 companies (overwhelming on
   production scale and slow to render).
2. Group / sub-group totals were missing on the pivot rows -- only
   leaf accounts had numbers.
3. Full account hierarchy expanded by default -- on production the
   pivot becomes a wall of leaves with no sub-totals to skim.

Resolutions, all on the same `feat/trust-selector` branch:

- **Smart default scope.** First-time users (no `dgv_cockpit_scope_v1`
  in localStorage) now land on the largest trust by company count --
  ASS for RGI (16 companies), falling back to all-companies if the
  largest trust is itself the universe (e.g. dev seed where every
  company is in the synthetic "Other" trust). Tie-broken by trust id
  alphabetically. The smart default is NOT persisted to localStorage
  -- the user's first explicit Apply becomes their "remembered"
  scope; if they never interact, the default tracks the trust set
  next time.
- **Group totals at request time (B-lazy).** `get_pivot_data` now
  bubbles every direct snapshot row's per-company balance up through
  the ancestor chain in `account_meta`, populating every non-leaf
  account's entry in the `balances` dict. Aggregation runs from a
  snapshot of the pre-mutation values so an account's own direct row
  never gets added to its ancestors twice. Empty-balance accounts are
  still included in the response (with `{}`) so the hierarchy
  structure renders intact. Cost is ~O(accounts × depth) Python-side,
  bounded under 5 ms on the dev seed.

  **Aggregation invariant**: `balance[node] = own_row +
  sum(balance[child] for child in children(node))` at every level of
  the response tree. The aggregator deliberately bubbles to ALL
  ancestors regardless of `is_group` -- real-world charts of accounts
  contain mid-tree accounts flagged `is_group=False` in the snapshot
  but still parents to other accounts (e.g. an "Unsecured Loans" leaf
  with its own balance plus child sub-accounts hanging under it). If
  the aggregator skipped these, descendants would jump past them to
  the next `is_group=True` ancestor and the intermediate's stored
  balance would be just its own row, breaking the recursive
  invariant. Discovered while running the gold-standard test on the
  RGI-DEMO seed -- a `Source of Funds (Liabilities)` total was
  -787 M but the test's leaf-walk only found -610 M; the missing
  -177 M was descendants under a `is_group=False` "Unsecured Loans"
  intermediate.
- **Depth control toggle.** New toolbar pill group `Depth | 1 | 2 |
  3 | All` next to the search box. Default 3. State persisted in
  `dgv_cockpit_depth_v1` (separate key from the scope storage so
  the two preferences evolve independently). The pivot grid grew a
  `setDepth(n)` method and a new visibility model that merges the
  depth-driven default with the user's manual expand / collapse
  intent (separate `userExpanded` and `collapsedAccounts` sets).
  Manual expand still works regardless of depth, and depth changes
  preserve the user's manual choices.

**Decisions made for the additions:**

- **Group totals at the API boundary, not in the cache.** The
  alternative was extending `tabDGV TB Snapshot Row` to also hold
  group rows, doubling the cache size and forcing every refresh to
  recompute group sums even when nobody's reading them. The B-lazy
  approach pays the aggregation cost only on read paths that need it,
  and reads are already two-layer-cached (the snapshot rows are
  themselves a cache of `tabGL Entry`).
- **Spotlight cards stay flat.** The filtered spotlight endpoint
  deliberately does NOT do hierarchy aggregation -- spotlight matches
  are predicate-based (`account_type = 'Receivable'`) rather than
  tree-based, so a hierarchy roll-up would risk double-counting once
  a parent group also matches the predicate. Documented in a comment
  in `get_spotlight_cards_filtered` so future maintainers don't
  retrofit that behaviour.
- **Manual expand state survives depth changes.** Toggling depth
  doesn't clear `userExpanded` / `collapsedAccounts`. Rationale:
  someone who's drilled into a specific deep group expects to keep
  seeing it when they zoom out. The merge rule (`_isGroupExpanded`)
  consults the depth default LAST, so manual choices take priority.
- **Smart default is computed, not saved.** Saving it would muddy the
  storage semantics ("did the user pick this, or did the system?")
  and means future tweaks to the rule (e.g. "largest trust by total
  balance") wouldn't apply to existing browsers. Recomputing on
  every first-visit boot is cheap.

**Tests added:**

- `test_get_pivot_data_includes_group_totals` -- every group account
  has a balances dict entry (even if `{}`).
- `test_get_pivot_data_group_totals_match_descendants_recursively`
  -- gold-standard equality between every pure group's per-company
  balance and the sum of its leaf descendants' balances. Skips
  groups with their own direct snapshot rows (rare) to keep the
  equality unambiguous.
- `test_get_pivot_data_group_balance_obeys_companies_filter` --
  group totals respect the same scope intersection as leaf balances;
  out-of-scope companies never appear in a group's balance map.

**Performance for the additions:** TBD on dev verification pass.
Expected:
- `get_pivot_data` (full scope, depth 3): < 1.5 sec on dev, < 2 sec
  on prod-shaped (the aggregation overhead is ~ms; the dominating
  cost remains the Phase 3 SQL).
- Depth toggle re-render: instant (DOM filter on the existing rows).
- First-visit smart-default on RGI: same fetch shape as Phase 3.5
  initial scoped fetch.

**Round-2 fixes after first visual review (2026-05-04):**

- **Depth filter off-by-one fix.** Initial implementation read
  `Depth=N` as "show every account whose depth ≤ N", which on RGI-DEMO
  meant `Depth=3` displayed depths 0..3 inclusive (4 levels of rows).
  Aditya's mental model is `Depth=N` = "N levels visible", so
  `Depth=1` should collapse every tree to its root. Fix is one
  character: `<=` → `<` in `_visibleByDepth`. Net effect: every tree
  collapses uniformly at every Depth setting, default Depth=3 now
  shows roots + sub-categories + sub-sub-categories (no leaves on the
  default), and the user clicks individual chevrons or `Depth=All` to
  drill into the leaf rows. Confirmed against the simulation in
  `test_depth_filter_works_across_all_root_types`.

- **Why it had looked Asset-only.** A second observation that
  surfaced during diagnosis: on RGI-DEMO seed, only the Asset and
  Liability trees go past depth 3 in the data; Equity, Expense, and
  Income max out at depths 2-3. Even with the buggy `<=`, the toggle
  IS firing across all rows -- but the visible delta between
  `Depth=3` and `Depth=All` only shows up on the two trees that have
  anything past depth 3 to hide. After the off-by-one fix, the
  user-visible labels line up with the user's expectation regardless
  of tree shape. (Documented in case future Claude Code sessions hit
  the same "looks broken but isn't" pattern on a fresh seed.)

- **Group / leaf differentiation refresh.** Dropped the subtle
  background-tint difference for group rows -- it was too easy to
  miss against the white surrounding cockpit. Replaced with two
  layered cues:
  1. A small inline-SVG icon (two horizontal stripes evoking a
     "summary line") prefixed to the account name on group rows.
  2. A 1 px top border on every numeric cell of a group row,
     mimicking the accounting-traditional underline above a subtotal.
  Background now matches leaves, so the heatmap toggle is the only
  background-color signal in the grid (less visual noise when
  heatmap is on). Bold name + bold number weight stays.

- **Number format toggle.** New toolbar pill group `Format | Cr | L
  | Full` next to the depth toggle. Persisted in
  `dgv_cockpit_format_v1` (separate key from scope/depth). Default
  Cr. Spotlight cards always render Cr regardless of the toggle --
  they're the headline; compactness wins over precision. Pivot cells
  in `Full` mode use Indian comma grouping (the canonical 17-char
  form `1,41,26,00,000.00`); the table grows numeric columns to
  150 px in Full mode via a `[data-format="full"]` CSS rule so cells
  don't clip or wrap. Negatives are wrapped in parens across all
  three formats. The Indian format is implemented twice -- a Python
  `format_indian` in `dux_groupview/pivot/format.py` (executable
  spec, used by `test_indian_format_function`) and a hand-translated
  JS `formatIndian` in `pivot_grid.js`. Both must move together.

- **Open follow-up:** Q12 added to OPEN_QUESTIONS.md -- hover
  tooltip showing full Indian-format value on every cell regardless
  of active toggle. Deferred from Phase 3.5 to keep the format
  toggle scope tight.

---

## Option A — RGI-named synthetic seed (post-Phase-3 follow-up)

**Branch:** `feat/rgi-named-seed`
**Status:** _to fill in on merge_

**What was added:**

- `seed_rgi_named_data()` — variant of `seed_production_data` using RGI's actual 59 company names from `pivot/trust_groups.py`. Voucher prefix `RGI-DEMO-` (vs `PROD-TEST-` for the generic seed). Same 5M-entry shape; same UUID-based name gen; same safety gate, separate env var (`RGI_DEMO_SEED_CONFIRM=yes`).
- `teardown_rgi_named_data()` — paired teardown with two defensive guards: (a) only deletes companies whose only GL entries are `RGI-DEMO-*` (skips any company that has real / non-DEMO entries — protection against accidental run on production); (b) pre-cleans orphan `tabMode of Payment Account` rows for the deleted companies before dropping them, so the next Company.insert() doesn't trip `_validate_links` on stale singletons (see Gotcha below).
- `get_seed_state()` API endpoint at `dux_groupview.api.cockpit.get_seed_state` — returns `{is_synthetic_preview, synthetic_entry_count}`. GroupView Viewer or higher.
- "SYNTHETIC PREVIEW DATA" banner at the top of `/groupview` — sticky to viewport, amber background, automatically appears when `RGI-DEMO-*` data is present and disappears when torn down.
- `_generate_gl_entries` refactored to accept `voucher_prefix=` (default `PROD-TEST-`); both seeds reuse the same generator.
- `_purge_synthetic_gl_entries()` purges BOTH `PROD-TEST-*` and `RGI-DEMO-*` so the RGI seed acts as a clean reset (the generic seed only purges its own prefix; running it after RGI without teardown would mix data).

**Purpose:** visually preview the cockpit's 10-trust grouping on dev without touching production. Allows screenshots and previews to be shared with Kumar Sir / Mr. Raisoni before production deploy.

**Gotcha surfaced and fixed:**

- **Orphan `tabMode of Payment Account` refs.** Phase 3's `teardown_production_data` deleted Companies but didn't clean their child-table rows in singletons. Mode of Payment held 59 rows pointing at the deleted Prod Co companies. The next Company.insert() in `seed_rgi_named_data` then tried to save Mode of Payment, which validated all child links and raised `frappe.exceptions.LinkValidationError: Could not find Row #N: Company: Prod Co N1...` for all 59 orphan refs. Same family of issue as Phase 0's GHR CACS Pune GST Settings inconsistency. Fixed by adding a `DELETE FROM tabMode of Payment Account WHERE company LIKE ...` to BOTH teardown functions before company deletion. One-time orphans on dev cleaned up manually too.
- **Performance.** Both seeds reuse the Phase 3 `_generate_gl_entries` (5M rows in ~21 min) and `refresh_tb_snapshot` (~44 sec post-index). No regression introduced.

**Notes:**

- Both seeds are mutually exclusive: `seed_rgi_named_data` purges any pre-existing `PROD-TEST-*`, but `seed_production_data` only purges its own prefix (asymmetry documented in `_purge_synthetic_gl_entries` docstring).
- Banner appears purely from server-side data state (`get_seed_state` queries `tabGL Entry` LIKE 'RGI-DEMO-%' LIMIT 1). No UI flag to toggle, no risk of drift.
- Defensive teardown filter on RGI side: a company is only deleted if it has zero GL entries OUTSIDE the `RGI-DEMO-` prefix. Protects against accidental runs on a misconfigured production environment where a real RGI company exists.

---

## Side PR — seed scale for KVM (post-Phase-3.5, mid-Phase-4)

**Branch:** `fix/seed-scale-for-kvm`
**Status:** _to fill in on merge_

**Why this exists:**

Mid-Phase-4 commit 2 review surfaced that the full RGI-DEMO 5M-row seed
saturates the dev server (Hostinger KVM1) during the test suite. Each
`refresh_tb_snapshot` call against 5M rows takes ~50 sec; the suite
triggers ~25 such refreshes (backfill tests, idempotency tests, sparkline
tests), totalling ~24 min of refresh work. Combined with KVM CPU
throttling, the suite was running 32+ min and risking concurrent-scheduler
collisions on the today() snapshot. This PR introduces a smaller
trust-subset seed for routine test runs while keeping the 5M shape
available on demand.

**What was added:**

- **`seed_rgi_named_data(trusts: list[str] | None = None)`** — optional
  parameter scoping the seed to a subset of trusts from `pivot/
  trust_groups.py:TRUSTS`. Default `None` preserves the full 59-company
  / 5M-row behaviour (no change for existing callers). When a list is
  passed, only matching trusts' companies are seeded; per-company row
  count (85K) is unchanged so total rows scale linearly.
- **`_select_trusts_to_seed(trusts)`** — pure helper that filters
  TRUSTS by id list. Case-insensitive, raises `ValueError` on unknown
  ids before any DB writes. Pulled out so the filter logic is
  unit-testable without touching tabCompany or tabAccount.
- **`tests/test_seed_production.py`** — 10 tests covering the filter
  in isolation: default-returns-all, subset-filtering, order-preservation,
  case-insensitive matching, error messages on unknown / empty / non-list
  inputs, single-trust path, and a pinning test that asserts the
  `["ghremf", "cbs", "sgr"]` subset = 13 companies (so future
  trust_groups.py changes surface here rather than silently shifting
  the row count).
- **`tests/test_refresh.py::test_refresh_creates_snapshot` threshold**
  retuned from 2-tier (>1M → 60s, else 15s) to 3-tier:
  - `>2,000,000` rows → 60s (production-scale, post Phase 3 covering
    index)
  - `>100,000` rows → 30s (dev/staging-scale, e.g. trust-subset
    RGI seed)
  - `<=100,000` rows → 15s (synthetic/CI-tiny, seed_light or unseeded)
  The middle tier is new: a 1.1M-row trust-subset seed needs more than
  the synthetic 15s but less than the prod-scale 60s.

**Untouched:**

- `seed_production_data()` — generic prod-scale seed unchanged. Both
  the full RGI mode and seed_production_data still produce ~5M rows
  by default.
- `teardown_rgi_named_data()` — unchanged. Iterates the full TRUSTS
  list and silently skips companies that were never created by a
  subset seed (`frappe.db.exists` returns False). Tested implicitly
  by running teardown after a subset seed in the dev verification
  step below.
- `_generate_gl_entries`, `_purge_synthetic_gl_entries`, `_build_rgi_company_specs`
  internals — only the entry point and abbr-collision walk changed
  to take pre-filtered trusts.

**Trust subset chosen for dev: `["ghremf", "cbs", "sgr"]` — 13 companies, ~1.1M rows.**

Rationale:
- **GHREMF** (8 cos) — second-tier size; includes "GHR CACS Pune"
  which has the Q4 GST Settings inconsistency. Keeping it in dev
  means Phase 4 commit 2's `audit_group_co_name_match` (Q19) and any
  future Q4 fix can be tested against a real edge case.
- **CBS** (3 cos) — small mid-tier. Provides trust-row collapse
  variety without inflating row count.
- **SGR** (2 cos) — smallest non-singleton trust. Distinct
  abbreviation shape ("SGR Foundation") for testing the trust-pill
  rendering at the small end.

ASS (16 cos) intentionally excluded from the dev default to keep the
KVM CPU comfortable. Restore the full seed (run `seed_rgi_named_data()`
without `trusts=`) when capturing demo screenshots that need the
10-trust visual or when re-validating production-scale perf.

**Dev verification (filled in at merge):** _row count, single-test
timing, full-suite runtime to be captured during PR test run._

**Performance impact (expected):**

| Operation | 5M-row seed | 1.1M-row subset |
|---|---|---|
| `refresh_tb_snapshot` (single) | ~50s | ~10-12s |
| `test_refresh_creates_snapshot` | ~50s | ~10s (threshold 30s) |
| `test_backfill_creates_n_snapshots` | ~159s (3 cycles) | ~35s |
| Full app test suite | ~32 min | ~7-9 min (estimated) |
| RGI seed run time | ~30-40 min | ~7 min |

**Why a side PR rather than a Phase 4 commit:**

The change is unrelated to Phase 4's drill-API logic; it's pure
infrastructure for the test environment. Bundling it into commit 2
would mix concerns and bloat the diff. Separating means the test
threshold change and seed parameter can be reviewed on their own
merits, and Phase 4 commit 2 keeps its focused scope when it resumes.

**Open follow-ups:**

- Q20 added to OPEN_QUESTIONS.md — should there be a CI tier that runs
  on the full 5M seed periodically (e.g. weekly), or is on-demand
  manual runs sufficient? Low priority.
- Once Phase 4 commit 2 lands, re-verify that the trust-subset seed
  surfaces all the data shapes the spotlight cards / pivot grid /
  drill panels exercise. The card → leaf-count table in
  `PHASE_4_COMMIT_1_FINDINGS.md` may need a subset-specific revision.

**Addendum — orphan tabAccount discovery during PR verification:**

The first full-suite re-run on the trust-subset seed surfaced one
unrelated test failure: `test_pivot_filter::test_depth_filter_works_across_all_root_types`
hit `Depth=1 for root_type='Liability': expected at least one visible
row, got 0`. Root cause was *not* the side PR's logic but a
pre-existing dev-hygiene issue: `teardown_rgi_named_data` calls
`frappe.delete_doc("Company", ...)` but Frappe's Company.on_trash does
not cascade-delete child `tabAccount` rows, leaving 105 orphan
companies' worth of accounts (525+ rows) over multiple
teardown/reseed cycles. Pivot's `_lookup_group_by_stripped_name`
(LIMIT 1, no company filter) then picked an orphan row whose
`account_name` had a company suffix embedded in the field rather
than the stripped form, with `root_type=''`, masking the properly-
typed Liability roots from the response.

Action taken in this PR:
- Added `cleanup_orphan_accounts()` utility in `seed_production.py`
  (bench-execute-only, not whitelisted) that deletes `tabAccount`
  rows whose `company` is no longer in `tabCompany`. One-off dev
  hygiene; production never needs it.
- Filed Q21 in OPEN_QUESTIONS.md for proper investigation of which
  child tables Frappe's Company.on_trash *should* cascade and
  whether to extend teardown explicitly or file an upstream issue.
  Likely candidates to also leak: tabCost Center, tabFiscal Year,
  tabWarehouse, tabItem Group, tabAddress, tabContact.

After running `cleanup_orphan_accounts` on dev, the full suite ran
clean (91/91 pass; runtime documented in dev verification).

---

## Side PR — augment dev seed with AP/AR data (post-Phase-4-commit-3)

**Branch:** `fix/seed-augment-ap-ar`
**Status:** _to fill in on merge_

**Why this exists:**

Phase 4 commit 3 review surfaced that the dev seed has only 8 unique
parties total across all payable accounts, all with near-zero
balances. The synthetic seed generator (`seed_rgi_named_data`) was
designed for snapshot/refresh load testing, not party data realism --
`_generate_gl_entries` recycles 8-9 supplier names across many
vouchers with legs that net to near zero. The account drill panel
worked correctly but had nothing meaningful to display in its
"By party" section, and commits 4 (GL drill), 5 (focus mode), 7
(walkthrough) would hit the same problem. Fixing now as a side PR
keeps Phase 4 commits focused on their actual deliverables.

Same pattern as side PR #10 (`fix/seed-scale-for-kvm`): focused
single-purpose change, branched off main, halt-point review, merged
before resuming Phase 4 work.

**What was added:**

- `dux_groupview/test_data/seed_ap_ar.py` -- pure data templates for
  50 suppliers + 30 customers + tier-distribution constants. No
  Frappe imports; importable without DB.
- `dux_groupview/test_data/seed_ap_ar_generator.py` -- whitelisted
  `seed_ap_ar(companies=None, dry_run=False)` entry point with
  affiliation planning (1-5 companies per party, weighted), Pareto-
  preserving padding when a company comes up short on the weighted
  random, and bulk GL-entry generation (5K-row chunks, same pattern
  as `seed_production._generate_gl_entries`).
- `dux_groupview/test_data/seed_ap_ar_teardown.py` -- whitelisted
  `teardown_ap_ar(delete_party_docs=False)` deletes by `AP-AR-SEED-`
  voucher prefix; defaults to keeping party docs (re-run friendly),
  with optional party-doc cleanup that refuses to delete a party with
  non-AP-AR-SEED transactions.
- `dux_groupview/tests/test_seed_ap_ar.py` -- 17 pure-logic unit
  tests: template counts, tier distribution, Pareto top-10/top-5
  share, per-company minimums (≥5 suppliers, ≥3 customers), no
  duplicate (party, company) pairs, balance/tx in tier ranges,
  determinism (same seed → same plan), every template has at least
  one affiliation.

**Decisions made:**

- **Pareto-preserving padding.** The weighted random for
  per-supplier company count usually hits "every company has ≥5
  suppliers" but not always. `_ensure_min_per_company` walks
  templates in tier-priority order (bottom-tier first, least balance
  impact) and pads (party, company) pairs without disturbing the
  Pareto distribution. On the dev seed this padded 9 supplier and
  ~25 customer affiliations.
- **Transaction shape: 70% bills + 30% payments.** Each affiliation
  generates `num_tx` vouchers, two-leg each. Bills are
  `Cr Sundry Creditors / Dr Expense` (party stamped on the Cr leg
  per ERPNext convention), payments are `Dr Sundry Creditors / Cr
  Bank` (party on the Dr leg). Net per supplier = bill_total -
  payment_total = target balance. Customer side mirrors with
  invoices and receipts. Counter-leg accounts (Expense, Bank,
  Income) carry no party stamp.
- **Voucher prefix `AP-AR-SEED-`, voucher_type `DGV AP-AR Seed`.**
  Distinct from existing `RGI-DEMO-` / `PROD-TEST-` / `DGV-TEST-`
  prefixes so teardown is unambiguous and existing seeds are
  untouched.
- **Idempotency: refuse-on-existing rather than skip-existing.**
  `seed_ap_ar` aborts if any `AP-AR-SEED-` GL entries exist and
  prints a hint to run teardown first. Cleaner UX than producing
  ambiguous skip counts on partial re-runs.
- **Default trust subset matches side PR #10.** `_default_companies()`
  resolves the ghremf+cbs+sgr trust list against `tabCompany` and
  filters to existing rows. Keeps a fresh dev box without the
  trust-subset seed from inserting orphan affiliations.
- **Posting date weighting: 50% last 3mo / 30% mid-period / 20%
  older.** Realistic concentration in recent months matches actual
  AP/AR ageing patterns -- visual benefit for the trend chart.

**Gotchas surfaced:**

- **`bench execute --kwargs '{...}'` uses `eval()`, not
  `json.loads()`.** Lowercase JSON `true` / `false` raises
  `NameError`. Pass Python-style `True` / `False` instead.
  Documented in CLAUDE.md "Frappe gotchas to remember" if not
  already there.
- **MariaDB `rows` is a reserved word.** A diagnostic
  `SELECT ... AS rows` was rejected with a syntax error mid-PR.
  Trivial; renamed to `n` and moved on.
- **`Supplier` doc creation hits the GST Settings revalidation
  branch even with `flags.ignore_mandatory`.** The `_suppress_gst_
  settings_revalidation` context manager (Phase 0 Q4 workaround)
  covers this -- wrapped supplier and customer creation calls in it
  belt-and-braces, even though customers are not GST-impacted.

**Performance / verification (dev, 13-company trust subset):**

| Operation | Source / Scale | Duration |
|---|---|---|
| `seed_ap_ar` dry-run | data-only, no DB writes | 0.3 sec |
| `seed_ap_ar` real run | 79 party docs + 5,678 GL rows | 2.1 sec |
| `refresh_tb_snapshot` (post-seed) | 1.1M GL rows + new 5.7K | 12.6 sec |
| `refresh_spotlight_cache` | 6 cards, 2,192 snapshot rows | 0.14 sec |
| `test_seed_ap_ar` (17 tests) | pure logic | 0.004 sec |
| Full app suite (`bench run-tests --app dux_groupview`) | 91 + 17 + 19 = 127 tests | _filled in at PR-submit_ |

**Numbers after seed (verified via mariadb):**

- Unique payable parties: 57 (was 8 pre-seed; 8 existed + 49 newly
  created; 1 supplier name collision skipped)
- Total payable balance: ₹10.84 Cr (planned ₹10.83 Cr, sub-rupee
  rounding variance)
- Top supplier: Sun Infotech Solutions, ₹2.48 Cr across 262 GL rows
- Pareto: top 10 suppliers = ~75% of total AP value
- Customers: 30 created, 0 skipped; total AR ~₹6.02 Cr

**Open follow-ups:**

- After this PR merges, the trust-subset dev seed remains the
  baseline. Phase 4 commits 4-7 should run `seed_ap_ar` once on
  dev as part of their setup if they need richer party data.
- The supplier name collision (1 skipped) was "Gulab Hardware" --
  pre-existing in the trust-subset seed under the same name.
  Acceptable noise; the duplicate name shares a single tabSupplier
  row across both seeds.
- Production (RGI books) won't run this seed -- it's dev-only
  augmentation. The `companies` parameter defaults to the dev
  trust subset and the function refuses to run if those companies
  don't exist; production would need an explicit `companies=` list
  to run, which is unlikely.

---

## Phase 4 — Commit 4 — GL drill page + CSV export + filter UI + view all parties

**Status:** Done -- 2026-05-09. Branch `phase-4-drills` (single
bundled commit covering HALT 1 + HALT 2 + HALT 2.5 + HALT 4 of the
master commit-4 sequence).

**Goal:** Wire the three stub buttons left over from commit 3 into
real functionality. After commit 4, every "→" or "View all" affordance
in the account drill panel and full page produces a working
destination: a paginated GL drill page, downloadable CSVs of
account-breakdown / GL-entries / party-list shapes, in-page filter
UI on the GL drill page, and a paginated party list page with
click-row-to-drill into GL filtered by party.

**Deliverables:**
- [x] `api/gl_drill_v1.py` -- new whitelisted `get_gl_entries`
  (paginated GL entries with SQL window function for running balance,
  50K hard cap with `is_truncated` flag), `get_filter_metadata`
  (HALT 2.5 dropdown population), `export_gl_entries_csv` (50K cap,
  raw decimal cells, ISO dates, `_filtered` filename infix when
  HALT 2.5 filters active)
- [x] `page/gl_drill/` -- new Frappe page at `/app/gl-drill?scope=...`,
  toolbar with sort/page-size/Export-CSV/pager, table with party
  cells + voucher links + running-balance column, scope-fanout +
  truncation banners, filter row (Companies / Accounts / Date Range
  / Party / Voucher type) + chips + bottom-sheet on ≤800px
- [x] `api/account_drill_v1.py` -- new `export_account_breakdown_csv`
  (per-(company, account) shape with currency)
- [x] `api/party_drill_v1.py` -- extended `get_party_breakdown` with
  `mode='card'` (default, byte-identical to HALT 1+2 wire shape) and
  new `mode='page'` (max page_size 500, adds `name_desc` sort,
  `total_pages` + scope echo); new `export_party_list_csv` (50K cap)
- [x] `page/party_list/` -- new Frappe page at `/app/party-list?scope=...`,
  paginated party table with click-row-to-drill into GL filtered
  by party (uses the `?party=&party_type=` URL params HALT 1's
  gl-drill page already supports)
- [x] `public/js/account_drill.js` -- all three commit-3 stubs
  (`stubGlDrill`, `stubExportCsv`, `stubViewAllParties`) wired with
  three new URL builders (`buildGlDrillUrl`, `buildAccountBreakdownCsvUrl`,
  `buildPartyListUrl`); each handles both panel-args and page-state
  arg shapes
- [x] `public/css/cockpit.css` -- ~1,850 new lines covering the
  GL drill page chrome, HALT 2.5 filter UI (multi-select + chips +
  bottom sheet), and party-list page chrome
- [x] 30 new tests across `test_gl_drill.py` (new file, 25 tests)
  + `test_account_drill.py` (+2 CSV tests) + `test_party_drill.py`
  (+8 HALT 4 tests). Suite at 163 / 163 green (1 pre-existing skip).
- [x] `specs/phase-4-commit-4-gl-drill.md` (v0.1 → v0.6) +
  `specs/phase-4-commit-4-filters.md` (HALT 2.5 spec). Already
  committed in the spec-evolution sequence preserved on the branch.

**Spec evolution (visible in git log):**

| Version | Commit  | Driver |
|---------|---------|--------|
| v0.1    | (in-chat only, not committed) | Initial draft surfaced at spec halt |
| v0.2    | 4d2bd11 | 5 Q's resolved + 2 mode-args contracts (commit subject doesn't include version; subsequent commits adopted "v0.X" naming) |
| v0.3    | 5ad7f50 | Page-size 100/1000, posting_date_* sort keys, 50K cap on get_gl_entries, EXPLAIN softening |
| v0.4    | 282a226 | Sort default flip (asc), HALT 2.5 insertion, filters open question |
| v0.5    | 016b8bf | Drop running balance partition (scope-wide accumulation) |
| v0.6    | 2f5bca7 | Filter UI spec + account-breakdown CSV column alignment |
| v0.6.1  | 860de01 | Filter spec amendments (EXPLAIN criterion + chip max-width + reset on scope change) |

**Halt-point cadence (visible in commit hashes between halts):**

- HALT 1 -- GL drill page + window function + pagination. Perf: 32-90s
  on huge subtree-of-root scopes (203K-431K rows); accepted as a known
  v1 limitation per the closing decisions (real users drill into
  specific accounts; the fanout banner warns).
- HALT 2 -- CSV export. Three new endpoints, three "Export CSV"
  buttons wired, raw-decimal cell format, slugified filenames.
- HALT 2.5 -- Filter UI (gl-drill page only). Five filters: Companies
  multi-select, Account-name multi-select, Date range, Party
  autocomplete, Voucher type (collapsed under "Advanced"). Filter
  state persists in URL; resets on cross-scope navigation.
- HALT 4 (renumbered from HALT 3 after HALT 2.5 insertion) --
  View All Parties. mode='page' on get_party_breakdown +
  /app/party-list page + export_party_list_csv. stubViewAllParties
  wired in account_drill.js.

**Architectural decisions:**

- **Running balance is scope-wide, not per-(company, account).**
  v0.5 dropped the `PARTITION BY company, account` clause from the
  window function. Reason: every other cockpit surface (pivot, cards,
  account-drill panel) treats a scope as one aggregated thing.
  Per-account-ledger view interleaves curves by date and reads as
  jumble. Per-account ledger is achievable in HALT 2.5 by filtering
  the scope to a single account_name. Mixed-root-type scopes get a
  scope-activity figure rather than a real financial total --
  documented in spec §13.2 known limitation.
- **CSV cells are raw decimals, not Indian-grouped strings.** Locked
  at HALT 2 across all three CSV endpoints. Reason: CSV is data-
  interchange; Indian-grouping forces visual interpretation onto
  every importer and breaks numerical typing in spreadsheet apps.
  Indian grouping stays in the rendered UI surfaces only.
- **Filters reset on cross-scope navigation.** Per spec §5
  amendment 3: filters are per-scope. `account_drill.js`'s
  `buildGlDrillUrl` deliberately doesn't emit any filter params
  even when called from a context that has filter state. A future
  Phase 5 sticky-pref doctype could opt back into carry-over;
  v1 keeps the URL contract simple.
- **`?scope=<scope_id>` URL contract for `/app/party-list`.** HALT 4
  instruction proposed `?account=<id>` but the implementation uses
  `?scope=<scope_id>` to (a) handle card-resolved scopes that span
  multiple accounts cleanly, and (b) reuse the gl-drill page's URL
  parser (`window.dgvParseAccountDrillHash`). Approved on review.
- **`mode='page'` as a parameter extension to `get_party_breakdown`,
  not a new function.** Per spec v0.6 §5.4. card mode (default)
  stays byte-identical for the panel + account-drill page; page
  mode adds the new knobs needed by `/app/party-list`. Pinned by
  the regression test `test_get_party_breakdown_mode_card_defaults_unchanged`.
- **Filter UI: SQL-narrow over client-side post-filter** (spec §4
  Q1). Reason: client-side filter would break pagination math
  (`total_entries` reported by server vs displayed by client) and
  running-balance cumulative semantics. Server-side WHERE folded
  cleanly into the existing JOIN -- HALT 2.5.3 EXPLAIN check
  confirmed no `Using temporary` / `Using filesort` regression
  vs HALT 1 baseline.
- **Filter UI: URL-persisted, not localStorage-sticky** (spec §4
  Q2). Shareability + refresh-safety > stickiness. Phase 5 may
  layer localStorage on top via `DGV User Preferences` doctype.

**Stub wiring (commit 3 → commit 4):**

| Stub | Wired to | URL builder |
|---|---|---|
| `stubGlDrill` | `/app/gl-drill?scope=...` | `buildGlDrillUrl` |
| `stubExportCsv` | `/api/method/...export_account_breakdown_csv` | `buildAccountBreakdownCsvUrl` |
| `stubViewAllParties` | `/app/party-list?scope=...` | `buildPartyListUrl` |

The function NAMES (`stub*`) were retained -- they're consumed by
`window.dgvDrill` exports + `bindActionBar` / `bindPartyViewAll`
wirings + the account-drill page's own click handlers. Renaming
would have rippled with no test gain. Comment block at the top of
the section documents the transition for future maintainers.

**Tests (163 total, 1 pre-existing skip):**

- `test_gl_drill.py` (new) -- 25 tests:
  - 5 pagination + truncation: offset, total_count, truncation cap
  - 2 running balance: correctness on single-account scope, continuous
    across (company, account) partitions (gold-standard for v0.5
    partition removal)
  - 1 sort options: posting_date_asc/desc + amount_asc/desc
  - 1 party filter
  - 5 CSV (HALT 2): columns, 50K cap throw, party filter applied,
    raw decimals, ISO dates
  - 6 filter (HALT 2.5): account_names, from_date, to_date,
    to_date clamp, voucher_types, combined intersection
  - 2 export filter (HALT 2.5): honors filters in CSV body, filename
    `_filtered_` infix marker
  - 3 misc shape (e.g., scope_fanout in response)
- `test_account_drill.py` (+2): account-breakdown CSV columns,
  raw decimals
- `test_party_drill.py` (+8): mode='page' total_pages, max_page_size
  500, name_desc sort, scope echo, mode-invalid raises, card-mode
  regression, party-list CSV columns, party-list raw decimals

EXPLAIN check (HALT 1 baseline + HALT 2.5.3 re-verify): both passed
the spec §10 / §4 Q1 fail criterion. Optimizer picked
`dgv_party_drill` index for the inner JOIN, no `Using temporary` /
`Using filesort`. New `account_names` + `voucher_types` predicates
folded into the existing `Using where` -- byte-identical plan to
HALT 1 baseline.

**Performance:**

| Operation | Scope | Median ms | Notes |
|---|---|---:|---|
| `get_gl_entries` p1 size=100 | account-leaf, single co (~9K rows) | 26-300 | Well within <500ms target |
| `get_gl_entries` p1 size=100 | subtree, all 20 cos (~50K-100K rows) | 25,000-31,000 | Truncated; fanout banner warns. Documented v1 limitation |
| `get_filter_metadata` first paint | SGR Current Liabilities (21 accts × 1 co) | 1,120 | Cached client-side per page load |
| `get_gl_entries` with `account_names` filter | same scope | 68 | Filter narrows; perf improves |
| `export_gl_entries_csv` (1,728 rows) | filtered scope | <2,000 | Single-pass windowed read |
| `get_party_breakdown` mode='page' | subtree, single co | <100 | Same shape as mode='card'; just different knobs |
| `export_party_list_csv` (7 parties) | small scope | <500 | Cap at 50K -- party lists are small |
| Test suite | trust-subset seed | ~430s (8 min) | Snapshot/refresh tests dominate |

**Carryover (visual-only, non-blocking):**

- Filter row alignment + popup wrap of long company names on the
  GL drill page were iterated through several CSS rounds during
  HALT 2.5. Final pass: native date inputs pinned to `height: 30px`
  with `box-sizing: border-box` to match custom dropdowns; popup
  uses `width: max-content` capped at 720px with `white-space:
  nowrap` on option text so long names display single-line.
  Aditya signed off the functionality but didn't have time for a
  final visual confirm; deferred to follow-up. See cockpit.css
  `.dgv-gl-filter-from` / `.dgv-gl-filter-to` (date inputs at
  `height: 30px`), `.dgv-gl-multiselect-popup` (max-content sizing
  + 720px cap), and `.dgv-gl-multiselect-option > span`
  (`white-space: nowrap`) rules.

**Gotchas surfaced:**

- **Page-record registration without `bench migrate`.** The dev's
  `bench migrate` is blocked by an unrelated `vehicle_no` custom-
  field collision on Sales Invoice (from a different installed app).
  New Frappe pages defined by `<page>.json` files are NOT
  auto-registered into `tabPage` from a clear-cache or bench build
  alone -- normally `bench migrate` does the sync. Workaround used
  during HALT 1 + HALT 4: a one-off `bench execute` calling
  `frappe.get_doc({...}).insert()` to insert the Page row directly
  with the same metadata as the JSON file. The unrelated
  `vehicle_no` collision needs to be fixed before the next
  production rollout that adds new doctypes / pages -- track as a
  side issue.
- **`loadFilterMetadata` race for card scopes.** Initial HALT 2.5.2
  implementation called `loadFilterMetadata()` immediately at page
  boot. For card-kind scopes, that fired BEFORE `resolveCardScope`
  populated `state.resolvedAccounts`, so the function returned early
  without making the API call -- filter row stayed hidden. Fix
  moved the call into the same Promise chain as `fetchAndRender`
  so it runs after card resolution.
- **`_count_entries` JOIN missing.** When HALT 2.5 added the
  `account_names` filter (which references `a.account_name` in the
  WHERE clause), the count query (which previously didn't JOIN
  `tabAccount`) started failing with "Unknown column 'a.account_name'".
  Fix added the JOIN to `_count_entries`. Caught by tests on first
  run after the WHERE-clause extension.
- **Frappe response handling for binary downloads.** All three CSV
  endpoints set `frappe.local.response.filename` + `filecontent`
  + `type='binary'`. The `filecontent` must be bytes (encoded
  UTF-8). Frappe sends `Content-Disposition: attachment` based on
  `filename` and the browser triggers the download.

**`tabGL Entry` audit:** `grep -rn "tabGL Entry"` across `gl_drill_v1.py`,
`party_drill_v1.py`, all the page JS, and the new tests returns only
the `_drill`-suffixed APIs as actual queries. Cockpit reads
(`account_drill_v1.export_account_breakdown_csv`, page Python stubs)
read snapshot tables only. Architecture rule preserved.

**Open follow-ups (Phase 5 candidates):**

- Filter UI alignment + popup wrap visual confirmation (carryover).
- Saved filter presets backed by a `DGV User Preferences` doctype.
- Card-id stability across Phase 5 editor changes (see master spec
  §13.1 known limitation; `# TODO(phase-5)` markers in
  `cards_v1.resolve_match_to_accounts` + `account_drill.js` URL
  builders).
- Index choice (`dgv_party_drill` vs `dgv_snapshot_aggregation`)
  perf sweep against real production click data after 6 weeks of
  usage.
- Fanout-banner threshold tuning based on real usage (currently
  `N_accounts > 20 OR N_companies > 5`).
- The unrelated `vehicle_no` migrate block on dev -- fix separately
  before next prod rollout.
