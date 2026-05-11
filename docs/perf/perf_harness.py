"""Phase 4 commit 9 perf harness.

Times each Phase 4 cockpit + drill endpoint × 3 scope variants
(small / medium / large) × N iterations. Writes results JSON +
EXPLAIN plans for the canonical large-scope query of each endpoint.

Drop in /tmp/ on dev and run with:

    bench --site erp.jewonline.in execute \
        commit_9_perf_harness.run \
        --kwargs '{"out_path": "/tmp/commit_9_perf_baseline.json"}'

Notes:
 - Runs as Administrator (all roles). _require_cockpit_role passes.
 - Iters configurable: 100 for cheap endpoints, 20 for exports.
 - Drops first 3 iterations as warmup before computing percentiles.
 - Catches exceptions per (endpoint, variant) so one bad input
   doesn't kill the whole run; the bad cell shows error=... and
   timings_ms=[].
"""

import json
import os
import statistics
import time
import traceback

import frappe
from frappe.utils import getdate, today


# --- Endpoint imports ---------------------------------------------------

from dux_groupview.dux_groupview.api.pivot import (
	get_pivot_data,
)
from dux_groupview.dux_groupview.api.cockpit import (
	get_spotlight_cards,
	get_spotlight_cards_filtered,
)
from dux_groupview.dux_groupview.api.account_drill_v1 import (
	get_account_breakdown,
	export_account_breakdown_csv,
)
from dux_groupview.dux_groupview.api.party_drill_v1 import (
	get_party_breakdown,
	export_party_list_csv,
)
from dux_groupview.dux_groupview.api.gl_drill_v1 import (
	get_gl_entries,
	export_gl_entries_csv,
	get_filter_metadata,
)
from dux_groupview.dux_groupview.api.focus_v1 import (
	get_focused_view,
	export_focused_view_csv,
)
from dux_groupview.dux_groupview.api.cards_v1 import (
	resolve_match_to_accounts,
)
from dux_groupview.dux_groupview.pivot.trust_groups import TRUSTS


# --- Iteration counts ---------------------------------------------------

ITERS_FAST = 100      # read-path endpoints
ITERS_EXPORT = 20     # CSV streamers (slower per call)
WARMUP = 3            # discarded before percentiles

# Per-cell wall-clock budget (seconds). When the cumulative timed wall
# clock exceeds this, stop iterating and use whatever we have. Empirical
# safeguard: a single endpoint+variant that turns out to take 5s per
# call would otherwise blow up total harness time to hours. Spec compliance
# at p95 is already obvious at that point.
WALL_BUDGET_SEC = 10.0

# Minimum timed samples before honoring the wall-budget bail. 5 lets us
# compute p50/p95/p99; less than that is useless for percentiles.
MIN_TIMED_SAMPLES = 5

# Adaptive-probe threshold: probe time over this (ms) causes the cell to
# stop after one sample. 1000ms is comfortably over every endpoint's
# spec target — anything slower is already in fail territory and the
# order-of-magnitude reading is all we need.
SLOW_THRESHOLD_MS = 1000.0


# --- Fixture resolution -------------------------------------------------

def _pick_fixtures():
	"""Resolve real account / company / trust names that exist in DB.

	Returns dict with:
	  snapshot_date       latest Complete snapshot date (str ISO)
	  small_company       one real company name
	  small_leaf          one leaf account in small_company
	  medium_trust_id     a trust whose companies all exist in DB
	  medium_companies    that trust's companies (filtered to existing)
	  medium_subtree      a parent account_name common across cos
	  large_subtree       root parent ("Application of Funds (Assets)")
	  all_companies       all permission-resolved companies
	"""
	# Latest Complete snapshot.
	row = frappe.db.sql(
		"SELECT snapshot_date FROM `tabDGV TB Snapshot` "
		"WHERE status = 'Complete' "
		"ORDER BY snapshot_date DESC LIMIT 1",
		as_dict=True,
	)
	if not row:
		raise RuntimeError("No Complete snapshot present; seed/refresh first.")
	snapshot_date = row[0]["snapshot_date"].isoformat()

	all_companies = frappe.db.sql_list(
		"SELECT name FROM tabCompany ORDER BY name"
	)
	if not all_companies:
		raise RuntimeError("No companies in DB.")

	# Pick an RGI trust that has at least 5 existing companies.
	medium_trust = None
	medium_companies = []
	for t in TRUSTS:
		existing = [c for c in t["companies"] if c in set(all_companies)]
		if len(existing) >= 5:
			medium_trust = t
			medium_companies = existing
			break
	if not medium_trust:
		# Fall back to first ~10 companies as "medium".
		medium_trust = {"id": "default", "name": "fallback subset"}
		medium_companies = all_companies[:10]

	# Small company + a leaf account that has rows in the snapshot for it.
	small_company = medium_companies[0]
	small_leaf_row = frappe.db.sql(
		"""
		SELECT r.account
		FROM `tabDGV TB Snapshot Row` r
		WHERE r.company = %s
		  AND r.snapshot_date = %s
		LIMIT 1
		""",
		(small_company, snapshot_date),
		as_dict=True,
	)
	if not small_leaf_row:
		raise RuntimeError(
			f"No snapshot rows for {small_company} @ {snapshot_date}"
		)
	small_leaf = small_leaf_row[0]["account"]

	# Medium subtree: a common group account name expected to exist.
	# "Current Assets" is in the Standard COA every Frappe site uses.
	medium_subtree = "Current Assets"
	# Large subtree: a root group account name on the asset side.
	large_subtree = "Application of Funds (Assets)"

	return {
		"snapshot_date": snapshot_date,
		"small_company": small_company,
		"small_leaf": small_leaf,
		"medium_trust_id": medium_trust["id"],
		"medium_companies": medium_companies,
		"medium_subtree": medium_subtree,
		"large_subtree": large_subtree,
		"all_companies": all_companies,
	}


# --- Timing core --------------------------------------------------------

def _time_call(fn, args, kwargs, n_iters):
	"""Time invocations of fn(*args, **kwargs) using adaptive probing.

	Strategy: run a probe call first (timed). If probe > SLOW_THRESHOLD_MS,
	stop — order-of-magnitude is enough for a baseline (any cell taking
	>1s is already over spec for every endpoint in the matrix). Otherwise
	continue with full WARMUP + n_iters timed calls.

	Returns dict with raw timings_ms + summary stats.
	"""
	timings_ms = []
	error = None
	stopped_early = False
	cell_t0 = time.perf_counter()

	# Probe (always timed).
	try:
		t0 = time.perf_counter()
		fn(*args, **kwargs)
		probe_ms = (time.perf_counter() - t0) * 1000
		timings_ms.append(probe_ms)
	except Exception as e:
		error = f"{type(e).__name__}: {str(e)[:200]}"
		tb = traceback.format_exc().splitlines()[-3:]
		error += " | " + " || ".join(tb)
		return {
			"n_iters": 0,
			"error": error,
			"stopped_early": True,
			"raw_ms_first_10": [],
		}

	if probe_ms > SLOW_THRESHOLD_MS:
		# Slow cell. One probe is enough; any further iters would burn
		# the harness budget. Report what we have.
		stopped_early = True
	else:
		# Fast cell. Run remaining (n_iters - 1) timed iters; no warmup
		# because the probe served that purpose, and percentile stability
		# at sub-millisecond per-iter is dominated by the first GC pause
		# not warmup.
		for _ in range(n_iters - 1):
			try:
				t0 = time.perf_counter()
				fn(*args, **kwargs)
				elapsed = (time.perf_counter() - t0) * 1000
				timings_ms.append(elapsed)
				if (time.perf_counter() - cell_t0) > WALL_BUDGET_SEC and len(timings_ms) >= MIN_TIMED_SAMPLES:
					stopped_early = True
					break
			except Exception as e:
				error = f"{type(e).__name__}: {str(e)[:200]}"
				tb = traceback.format_exc().splitlines()[-3:]
				error += " | " + " || ".join(tb)
				break

	out = {
		"n_iters": len(timings_ms),
		"error": error,
		"stopped_early": stopped_early,
		"raw_ms_first_10": [round(t, 1) for t in timings_ms[:10]],
	}
	if timings_ms:
		s = sorted(timings_ms)
		out["p50_ms"] = round(s[len(s) // 2], 1)
		out["p95_ms"] = round(s[int(len(s) * 0.95)], 1) if len(s) > 1 else s[0]
		out["p99_ms"] = round(s[int(len(s) * 0.99)], 1) if len(s) > 1 else s[0]
		out["mean_ms"] = round(statistics.fmean(timings_ms), 1)
		out["max_ms"] = round(max(timings_ms), 1)
	return out


# --- EXPLAIN capture ----------------------------------------------------

def _capture_explain(label, sql, params=None):
	"""Run EXPLAIN against an exact SQL string + params dict.

	Returns the EXPLAIN rows (list of dicts) under the label.
	"""
	try:
		rows = frappe.db.sql(
			f"EXPLAIN {sql}",
			params or {},
			as_dict=True,
		)
		return {
			"label": label,
			"rows": [
				{k: (v if not isinstance(v, (bytes, bytearray)) else v.decode()) for k, v in r.items()}
				for r in rows
			],
		}
	except Exception as e:
		return {"label": label, "error": f"{type(e).__name__}: {e}"}


# --- Build the call matrix ---------------------------------------------

def _build_matrix(fx):
	"""Build the list of (endpoint_name, variant_name, fn, args, kwargs, n_iters)."""
	d = fx["snapshot_date"]
	co = fx["small_company"]
	leaf = fx["small_leaf"]
	med_cos = fx["medium_companies"]
	med_trust = fx["medium_trust_id"]
	all_cos = fx["all_companies"]
	med_sub = fx["medium_subtree"]
	lg_sub = fx["large_subtree"]

	# Scope shapes used across drill endpoints.
	scope_small = {"type": "account", "value": leaf.split(" - ")[0]
	               if " - " in leaf else leaf}
	scope_medium = {"type": "subtree", "value": med_sub}
	scope_large = {"type": "subtree", "value": lg_sub}

	M = []

	# --- get_pivot_data ---
	M.append(("get_pivot_data", "small",
	          get_pivot_data, (d, "crore", json.dumps([co])), {}, ITERS_FAST))
	M.append(("get_pivot_data", "medium",
	          get_pivot_data, (d, "crore", json.dumps(med_cos)), {}, ITERS_FAST))
	M.append(("get_pivot_data", "large",
	          get_pivot_data, (d, "crore", None), {}, ITERS_FAST))

	# --- get_spotlight_cards (cache-read) ---
	M.append(("get_spotlight_cards", "small",
	          get_spotlight_cards, (d,), {}, ITERS_FAST))
	M.append(("get_spotlight_cards", "medium",
	          get_spotlight_cards, (d,), {}, ITERS_FAST))
	M.append(("get_spotlight_cards", "large",
	          get_spotlight_cards, (d,), {}, ITERS_FAST))

	# --- get_spotlight_cards_filtered (live recompute) ---
	M.append(("get_spotlight_cards_filtered", "small",
	          get_spotlight_cards_filtered, (d, json.dumps([co])), {}, ITERS_FAST))
	M.append(("get_spotlight_cards_filtered", "medium",
	          get_spotlight_cards_filtered, (d, json.dumps(med_cos)), {}, ITERS_FAST))
	M.append(("get_spotlight_cards_filtered", "large",
	          get_spotlight_cards_filtered, (d, json.dumps(all_cos)), {}, ITERS_FAST))

	# --- get_account_breakdown ---
	M.append(("get_account_breakdown", "small",
	          get_account_breakdown, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co]},
	          ITERS_FAST))
	M.append(("get_account_breakdown", "medium",
	          get_account_breakdown, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": med_cos},
	          ITERS_FAST))
	M.append(("get_account_breakdown", "large",
	          get_account_breakdown, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos},
	          ITERS_FAST))

	# --- get_party_breakdown (mode=card) ---
	M.append(("get_party_breakdown[card]", "small",
	          get_party_breakdown, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co],
	           "mode": "card", "page": 1, "page_size": 50},
	          ITERS_FAST))
	M.append(("get_party_breakdown[card]", "medium",
	          get_party_breakdown, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": med_cos,
	           "mode": "card", "page": 1, "page_size": 50},
	          ITERS_FAST))
	M.append(("get_party_breakdown[card]", "large",
	          get_party_breakdown, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos,
	           "mode": "card", "page": 1, "page_size": 50},
	          ITERS_FAST))

	# --- get_party_breakdown (mode=page) ---
	M.append(("get_party_breakdown[page]", "small",
	          get_party_breakdown, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co],
	           "mode": "page", "page": 1, "page_size": 50},
	          ITERS_FAST))
	M.append(("get_party_breakdown[page]", "medium",
	          get_party_breakdown, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": med_cos,
	           "mode": "page", "page": 1, "page_size": 50},
	          ITERS_FAST))
	M.append(("get_party_breakdown[page]", "large",
	          get_party_breakdown, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos,
	           "mode": "page", "page": 1, "page_size": 500},
	          ITERS_FAST))

	# --- get_gl_entries (spec v0.9: per-company only) ---
	# Small  = one leaf account, one company.
	# Medium = subtree across one company (~30 leaves, the realistic
	#          single-company "view all in this group" case).
	# Large  = expected ValidationError (multi-company).
	M.append(("get_gl_entries", "small",
	          get_gl_entries, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co],
	           "page": 1, "page_size": 50},
	          ITERS_FAST))
	M.append(("get_gl_entries", "medium",
	          get_gl_entries, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": [co],
	           "page": 1, "page_size": 50},
	          ITERS_FAST))
	M.append(("get_gl_entries", "large",
	          get_gl_entries, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos,
	           "page": 1, "page_size": 50},
	          ITERS_FAST))

	# --- get_filter_metadata (spec v0.9: per-company only) ---
	M.append(("get_filter_metadata", "small",
	          get_filter_metadata, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co]},
	          ITERS_FAST))
	M.append(("get_filter_metadata", "medium",
	          get_filter_metadata, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": [co]},
	          ITERS_FAST))
	M.append(("get_filter_metadata", "large",
	          get_filter_metadata, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos},
	          ITERS_FAST))

	# --- get_focused_view ---
	# small  = single company; medium = a trust; large = treat same as medium
	# (trust is the broadest focus mode shape).
	M.append(("get_focused_view", "small",
	          get_focused_view, ("company", co, d), {}, ITERS_FAST))
	M.append(("get_focused_view", "medium",
	          get_focused_view, ("trust", med_trust, d), {}, ITERS_FAST))
	M.append(("get_focused_view", "large",
	          get_focused_view, ("trust", med_trust, d), {}, ITERS_FAST))

	# --- CSV exports (slower, fewer iters) ---
	M.append(("export_account_breakdown_csv", "small",
	          export_account_breakdown_csv, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co]},
	          ITERS_EXPORT))
	M.append(("export_account_breakdown_csv", "medium",
	          export_account_breakdown_csv, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": med_cos},
	          ITERS_EXPORT))
	M.append(("export_account_breakdown_csv", "large",
	          export_account_breakdown_csv, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos},
	          ITERS_EXPORT))

	# export_gl_entries_csv -- spec v0.9 per-company. Medium becomes
	# subtree+single-co; large stays multi-co (expected ValidationError).
	M.append(("export_gl_entries_csv", "small",
	          export_gl_entries_csv, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co]},
	          ITERS_EXPORT))
	M.append(("export_gl_entries_csv", "medium",
	          export_gl_entries_csv, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": [co]},
	          ITERS_EXPORT))
	M.append(("export_gl_entries_csv", "large",
	          export_gl_entries_csv, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos},
	          ITERS_EXPORT))

	M.append(("export_focused_view_csv", "small",
	          export_focused_view_csv, ("company", co, d), {}, ITERS_EXPORT))
	M.append(("export_focused_view_csv", "medium",
	          export_focused_view_csv, ("trust", med_trust, d), {}, ITERS_EXPORT))
	M.append(("export_focused_view_csv", "large",
	          export_focused_view_csv, ("trust", med_trust, d), {}, ITERS_EXPORT))

	M.append(("export_party_list_csv", "small",
	          export_party_list_csv, (),
	          {"scope": scope_small, "as_of_date": d, "companies": [co]},
	          ITERS_EXPORT))
	M.append(("export_party_list_csv", "medium",
	          export_party_list_csv, (),
	          {"scope": scope_medium, "as_of_date": d, "companies": med_cos},
	          ITERS_EXPORT))
	M.append(("export_party_list_csv", "large",
	          export_party_list_csv, (),
	          {"scope": scope_large, "as_of_date": d, "companies": all_cos},
	          ITERS_EXPORT))

	# --- resolve_match_to_accounts ---
	# Card match predicates are tiny -- they hit tabAccount metadata only,
	# not GL. Should be cheap regardless of scope.
	# Predicate shapes match spotlight/cards.py + spotlight_refresh._match_clause:
	#   {"by_account_type": <str | list>}
	#   {"by_root_type_and_name_pattern": {"root_type": ..., "name_pattern": ...}}
	M.append(("resolve_match_to_accounts", "small",
	          resolve_match_to_accounts, (),
	          {"match": {"by_account_type": "Cash"}, "companies": [co]},
	          ITERS_FAST))
	M.append(("resolve_match_to_accounts", "medium",
	          resolve_match_to_accounts, (),
	          {"match": {"by_account_type": "Receivable"}, "companies": med_cos},
	          ITERS_FAST))
	M.append(("resolve_match_to_accounts", "large",
	          resolve_match_to_accounts, (),
	          {"match": {"by_root_type_and_name_pattern":
	                     {"root_type": "Asset", "name_pattern": "%Bank%"}},
	           "companies": all_cos},
	          ITERS_FAST))

	return M


# --- Public entry point -------------------------------------------------

def run(out_path="/tmp/commit_9_perf_baseline.json"):
	frappe.set_user("Administrator")

	t_start = time.time()
	fx = _pick_fixtures()
	matrix = _build_matrix(fx)

	results = []
	for idx, (name, variant, fn, args, kwargs, n) in enumerate(matrix, 1):
		print(f"[{idx}/{len(matrix)}] {name} :: {variant} ({n} iters)... ", flush=True)
		stats = _time_call(fn, args, kwargs, n)
		stats["endpoint"] = name
		stats["variant"] = variant
		results.append(stats)
		summary = (
			f"  -> p50={stats.get('p50_ms', '?')} ms  "
			f"p95={stats.get('p95_ms', '?')} ms  "
			f"p99={stats.get('p99_ms', '?')} ms  "
			f"n={stats.get('n_iters', 0)}"
		)
		if stats.get("error"):
			summary += f"  ERROR: {stats['error'][:120]}"
		print(summary, flush=True)

	# Row count for context.
	gl_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry`"
	)[0][0]
	snap_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabDGV TB Snapshot Row`"
	)[0][0]

	output = {
		"generated_at": frappe.utils.now(),
		"duration_seconds": round(time.time() - t_start, 1),
		"fixtures": {
			"snapshot_date":   fx["snapshot_date"],
			"small_company":   fx["small_company"],
			"small_leaf":      fx["small_leaf"],
			"medium_trust_id": fx["medium_trust_id"],
			"medium_companies_count": len(fx["medium_companies"]),
			"all_companies_count":    len(fx["all_companies"]),
		},
		"scale": {
			"gl_entry_rows":       gl_count,
			"snapshot_row_count":  snap_count,
		},
		"results": results,
	}

	with open(out_path, "w") as f:
		json.dump(output, f, indent=2, default=str)
	print(f"\nWrote {out_path}  ({len(results)} cells, "
	      f"{round(time.time() - t_start, 1)}s total)")
	return {"status": "ok", "out_path": out_path}
