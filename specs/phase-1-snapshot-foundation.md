# Phase 1 — Snapshot Foundation

Archived spec for Phase 1, as agreed with Aditya on 2026-05-02.
Linkifier artifacts from the original chat message have been
normalised in-place; the substance is unchanged.

---

## Goal

Build the snapshot cache layer that the entire cockpit will read from.
No UI work this phase — only data layer plus an admin health page to
verify the layer is alive.

End state: every 30 minutes during business hours (every 4 hours
overnight), a background job computes one row per (date, company,
account) into `DGV TB Snapshot Row`. The cockpit health page shows
when the last snapshot ran, how long it took, and how many rows were
written. A manual refresh trigger exists. A backfill script exists for
one-shot historical population. Unit tests prove the SQL math matches
what `tabGL Entry` says.

## Hard rule

> "Never query `tabGL Entry` directly from any UI code path."

This phase ESTABLISHES the layer that makes that rule possible.
`dux_groupview/dux_groupview/snapshots/refresh.py` is the ONLY place
in the entire app that touches `tabGL Entry`. No exceptions, even for
tests (the gold-standard test queries `tabGL Entry` for verification
but is itself outside the UI code path).

## Resolved ambiguities

- **Q-A — `debit_total` / `credit_total` scope:** lifetime cumulative
  (option 2). `WHERE gl.posting_date <= snapshot_date` applies to all
  three aggregations. Period totals are derived later by subtracting
  two snapshots' values. Invariant: `balance = debit_total - credit_total`.
- **Q-B — `is_opening = 'Yes'` entries:** included. Standard accounting
  convention; critical for migrated entities. Filter is
  `WHERE gl.is_cancelled = 0 AND gl.docstatus = 1 AND gl.posting_date <= %(snapshot_date)s`.
- **Backfill addendum:** after each backfilled date completes, set
  `is_immutable = 1` if the date is strictly less than today. Closes
  the window between backfill and the next nightly finalize.
- **Refresh button polling timeout:** 60 seconds. After that, JS shows
  "Refresh taking longer than expected. Check the snapshot list below
  for status." rather than spinning indefinitely.

## Doctypes

### DGV TB Snapshot (parent)

| Field | Type | Notes |
|---|---|---|
| snapshot_date | Date | required, indexed, unique, set_only_once |
| generated_at | Datetime | required |
| duration_seconds | Float | read-only |
| row_count | Int | read-only |
| status | Select | Generating / Complete / Failed |
| error_message | Long Text | optional |
| is_immutable | Check | default 0 |

- Naming: `format:SNAPSHOT-{snapshot_date}` — one snapshot per date.
- Permissions: System Manager (full), GroupView Owner (read), GroupView
  Viewer (read).
- Sort: snapshot_date DESC.

### DGV TB Snapshot Row

| Field | Type | Notes |
|---|---|---|
| parent_snapshot | Link → DGV TB Snapshot | required, indexed |
| snapshot_date | Date | denormalised, required, indexed |
| company | Link → Company | required |
| account | Link → Account | required |
| account_type | Data | denormalised from Account.account_type |
| root_type | Data | denormalised from Account.root_type |
| balance | Currency | required; raw `SUM(debit) - SUM(credit)` |
| debit_total | Currency | lifetime cumulative debits up to snapshot_date |
| credit_total | Currency | lifetime cumulative credits up to snapshot_date |

- Naming: `hash` (Frappe convention; actual names come from `MD5(...)`
  in the bulk INSERT).
- Composite indexes (added via patch since Frappe doctype JSON has no
  native composite-index declaration):
  - `(snapshot_date, company, account)` — pivot grid reads (primary)
  - `(account, snapshot_date)` — account drill view
  - `(company, snapshot_date)` — entity drill view

## Refresh function

`dux_groupview.dux_groupview.snapshots.refresh.refresh_tb_snapshot(snapshot_date=None)`

- Default snapshot_date = today (site timezone).
- Idempotent: if a `DGV TB Snapshot` for this date exists, deletes its
  rows and recomputes. If `is_immutable=1`, raises.
- Performance: single `INSERT...SELECT` against `tabGL Entry`. No ORM
  for the row inserts. Parameterised SQL via `frappe.db.sql()`.
- Sign convention: stores raw `Dr - Cr` in `balance`. UI flips sign for
  Liability / Equity / Income based on `root_type`.
- Transaction shape: parent record is committed first (so a Failed
  record always remains visible); the row INSERT runs in a second
  transaction; on failure, parent is updated to `status=Failed` with
  the error message in a third commit.

`refresh_tb_snapshot_with_progress(snapshot_date=None)` — emits
`frappe.publish_realtime` events at start / complete / failed boundaries.
The single-INSERT design has no per-row progress to emit; per-entity
progress would require chunking refresh per company.

`finalize_past_snapshots()` — runs nightly. Marks any snapshot with
`snapshot_date < today_in_site_tz()` as `is_immutable=1`.

## Backfill function

`dux_groupview.dux_groupview.snapshots.backfill.backfill_snapshots(months_back=12, force=False)`

- Computes month-end of each of the last N months.
- For each, calls `refresh_tb_snapshot(snapshot_date=date)`.
- After successful refresh on a past date (date < today), immediately
  sets `is_immutable=1`.
- Skips already-immutable dates unless `force=True`.
- Safety: refuses if `12 * COUNT(tabGL Entry)` exceeds 10M rows (rough
  upper bound for total rows scanned). `force=True` overrides.

## Scheduler hooks

```python
scheduler_events = {
  "cron": {
    "*/30 8-22 * * *": ["dux_groupview.dux_groupview.snapshots.refresh.refresh_tb_snapshot"],
    "0 0-7,23 * * *":  ["dux_groupview.dux_groupview.snapshots.refresh.refresh_tb_snapshot"],
    "1 0 * * *":       ["dux_groupview.dux_groupview.snapshots.refresh.finalize_past_snapshots"],
  }
}
```

Site timezone confirmed `Asia/Kolkata`; cron times fire in IST.

## Health page

Route `/groupview-health`. System Manager only. Shows latest snapshot,
last 7 snapshots, "Refresh now" button (enqueue + 60s polling timeout),
"Backfill 12 months" button (with confirmation dialog), scheduler
heartbeat from `tabScheduled Job Log`, and a perf-warning banner if any
of the last 5 snapshots took > 30 seconds.

## API

`dux_groupview.dux_groupview.api.health` — whitelisted, System Manager
only:

- `get_snapshot_health()` — JSON for the health page.
- `trigger_manual_refresh()` — `frappe.enqueue(refresh_tb_snapshot)`,
  returns `job_id`.
- `trigger_backfill(months_back=12)` — `frappe.enqueue(backfill_snapshots)`.

## Tests

`dux_groupview.dux_groupview.tests.test_refresh`:

1. `test_refresh_creates_snapshot`
2. `test_refresh_aggregations_match_gl_entry` ← gold-standard correctness
3. `test_refresh_idempotent`
4. `test_refresh_immutable_protection`
5. `test_backfill_creates_n_snapshots`
6. `test_backfill_skips_immutable`
7. `test_backfill_force_override`

## Performance target

`refresh_tb_snapshot()` must complete in < 15 sec on the seeded 50K-row
dataset. Stop condition at > 30 sec — indicates architecture is wrong.

## Verification SQL (correctness)

```sql
SELECT * FROM `tabDGV TB Snapshot Row`
WHERE balance != (
  SELECT COALESCE(SUM(debit - credit), 0)
  FROM `tabGL Entry` gl
  WHERE gl.company = `tabDGV TB Snapshot Row`.company
    AND gl.account = `tabDGV TB Snapshot Row`.account
    AND gl.posting_date <= `tabDGV TB Snapshot Row`.snapshot_date
    AND gl.is_cancelled = 0
    AND gl.docstatus = 1
);
```

Must return zero rows.
