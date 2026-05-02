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
