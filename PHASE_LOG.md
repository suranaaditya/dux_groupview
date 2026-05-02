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
