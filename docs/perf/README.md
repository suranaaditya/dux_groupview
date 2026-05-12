# Performance baselines + reusable harness

This folder holds long-lived perf artifacts: measured baselines from
past scale events and the harness that produced them. Add a new
baseline JSON each time the project crosses a meaningful data-volume
threshold or adds new endpoints to the cockpit surface.

## What's here

| File | What it is |
|---|---|
| `perf_harness.py` | Self-contained harness. Times every cockpit + drill endpoint × 3 scope variants (small / medium / large) × N iterations (adaptive: 100 for read-path, 20 for CSV exports, 1 for slow probes). Dumps JSON with p50 / p95 / p99 + first 10 raw timings per cell + scale context (gl_entry row count, snapshot row count). |
| `commit_9_baseline_5M.json` | Canonical v0.9 perf reference. RGI full seed (5,015,000 GL rows, 65 companies). All 14 endpoints under spec §10 targets. Generated 2026-05-11 on `erp.jewonline.in` (KVM 4). PHASE_LOG commit 9 entry has the Phase A pre-fix numbers in a table for comparison. |

## When to use this folder

- **New scale event** (e.g. RGI hits 10M GL rows, or a new dev box with different IO characteristics). Re-run the harness, save the JSON as `commit_<N>_baseline_<scale>.json`, write a brief comment in PHASE_LOG referencing the file.
- **New endpoint** added to the cockpit surface (Phase 5 cards editor, future drills). Extend `perf_harness.py`'s `_build_matrix()` with the new endpoint × 3 scope variants and re-run to establish a baseline before merging.
- **Spec target adjustments**. If a perf target changes, capture the new measurement here so future regressions can be detected.

## How to re-run

1. Copy `perf_harness.py` to the dev box's app `test_data/` directory:
   ```
   scp docs/perf/perf_harness.py \
       frappe@<dev>:/home/frappe/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/test_data/commit_9_perf_harness.py
   ```
   (Note Frappe's double-nested `apps/dux_groupview/dux_groupview/dux_groupview/` — see CLAUDE.md gotcha.)
2. Run with `bench execute` — fresh Python process picks up new module code without a gunicorn restart:
   ```
   nohup bench --site <site> execute \
       dux_groupview.dux_groupview.test_data.commit_9_perf_harness.run \
       > /tmp/perf-harness.log 2>&1 &
   ```
   Expect 60-90 seconds on a clean 5M-row dev box. Use `nohup` + redirect so an SSH disconnect doesn't kill the run (CLAUDE.md gotcha #4).
3. Pull the JSON back and save it here:
   ```
   scp frappe@<dev>:/tmp/commit_9_perf_baseline.json \
       docs/perf/commit_<N>_baseline_<scale>.json
   ```

## Reading a baseline JSON

Each `results[]` entry has:
- `endpoint`, `variant` — which cell
- `p50_ms`, `p95_ms`, `p99_ms`, `mean_ms`, `max_ms` — wall-clock stats
- `n_iters` — how many timed samples (may be < target if `stopped_early` fired)
- `stopped_early` — `True` when the adaptive probe bailed (probe > 1000 ms → 1 sample only) or when the cell wall budget tripped
- `error` — exception message + truncated traceback if the call raised; `n_iters` is 0 in that case
- `raw_ms_first_10` — first 10 raw timings for sanity-checking tail latency

Top-level `fixtures` echoes what scope shapes the harness resolved
(snapshot date, small_company picked, medium trust id, etc.) so the
JSON is self-documenting.

## Reference

Full Phase A → Phase B narrative for commit 9 — including the three
spec amendments (v0.7 → v0.8 → v0.9), EXPLAIN diagnoses, the
optimizer-cost-model finding, and the per-company reframing — is in
[PHASE_LOG.md](../../PHASE_LOG.md) under "Phase 4 commit 9 —
Performance verification at 5M-row scale".

## What NOT to put here

- One-off EXPLAIN scratchwork (commit them into `.claude/tmp/` or
  delete; they're snapshots of a specific moment, not reusable).
- Per-deploy timing logs (`/tmp/perf-harness.log` style).
- Slow-query post-mortems that belong in PHASE_LOG.
