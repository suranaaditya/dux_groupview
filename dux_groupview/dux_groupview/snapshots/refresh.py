"""Snapshot refresh -- the ONLY module in dux_groupview that reads tabGL Entry.

CLAUDE.md hard rule: no UI code path may query tabGL Entry directly. All
cockpit reads come from tabDGV TB Snapshot Row, which this module
populates.

Sign convention
---------------
The `balance` column stores raw `SUM(debit) - SUM(credit)` in company
base currency. UI code is responsible for flipping the sign for
Liability / Equity / Income accounts based on `root_type`. We deliberately
do not bake the sign flip into storage so that the invariant
`balance = debit_total - credit_total` always holds.

Filter
------
Only entries with `is_cancelled = 0 AND docstatus = 1 AND
posting_date <= snapshot_date` are summed. `is_opening = 'Yes'` entries
are included -- they represent migrated opening balances and are part
of any honest TB.

Period totals (`debit_total` / `credit_total`) are lifetime cumulative,
not FY-scoped. Phase-2 spotlight cards and Phase-3 reports derive any
window (MTD, FYTD, custom) by subtracting two snapshots' values.

Transaction shape
-----------------
The parent `DGV TB Snapshot` record is committed *before* the row INSERT
runs, so a Failed record always remains visible. The row INSERT runs in
a second transaction; on failure, parent is updated to status=Failed in
a third commit.

Idempotency
-----------
If a snapshot for the requested date exists and is not immutable, its
rows are deleted and recomputed in place (same parent name, new
generated_at). Immutable snapshots cannot be regenerated -- the function
raises.
"""

import time

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, today


# ---------------------------------------------------------------------------
# Bulk INSERT...SELECT: the one place in the entire app that touches
# tabGL Entry.
# ---------------------------------------------------------------------------

INSERT_ROWS_SQL = """
INSERT INTO `tabDGV TB Snapshot Row`
  (name, parent_snapshot, snapshot_date, company, account,
   account_type, root_type, balance, debit_total, credit_total,
   creation, modified, owner, modified_by, docstatus, idx)
SELECT
  MD5(CONCAT(%(snapshot_date)s, agg.company, agg.account, RAND())),
  %(parent_snapshot)s,
  %(snapshot_date)s,
  agg.company,
  agg.account,
  COALESCE(a.account_type, ''),
  COALESCE(a.root_type, ''),
  agg.balance,
  agg.debit_total,
  agg.credit_total,
  NOW(), NOW(), 'Administrator', 'Administrator', 0, 0
FROM (
  SELECT
    gl.company,
    gl.account,
    ROUND(SUM(gl.debit) - SUM(gl.credit), 2) AS balance,
    ROUND(SUM(gl.debit), 2) AS debit_total,
    ROUND(SUM(gl.credit), 2) AS credit_total
  FROM `tabGL Entry` gl
  WHERE gl.is_cancelled = 0
    AND gl.docstatus = 1
    AND gl.posting_date <= %(snapshot_date)s
  GROUP BY gl.company, gl.account
  -- Skip company/account pairs whose aggregated debit, credit, and
  -- balance ALL round to zero. These rows would carry no information
  -- in the snapshot and writing them wastes space. A non-zero on any
  -- one of the three columns is enough to include the row. Defensive
  -- filter against floating-point noise; on clean data it's a no-op.
  HAVING ABS(ROUND(SUM(gl.debit) - SUM(gl.credit), 2)) > 0
      OR ABS(ROUND(SUM(gl.debit), 2)) > 0
      OR ABS(ROUND(SUM(gl.credit), 2)) > 0
) AS agg
INNER JOIN `tabAccount` a ON a.name = agg.account
"""


def refresh_tb_snapshot(snapshot_date=None):
	"""Refresh the TB snapshot for a given date.

	Args:
		snapshot_date: Date or string. Defaults to today (site timezone).

	Returns:
		dict with keys: snapshot_name, snapshot_date, row_count,
		duration_seconds, status.

	Raises:
		frappe.ValidationError if the existing snapshot for this date is
		immutable, or if the underlying SQL fails.
	"""
	snapshot_date = _resolve_date(snapshot_date)
	t_start = time.time()

	parent_name = _ensure_parent(snapshot_date)
	# Commit parent record so it survives even if the INSERT below fails.
	frappe.db.commit()

	try:
		frappe.db.sql(
			INSERT_ROWS_SQL,
			{"snapshot_date": snapshot_date, "parent_snapshot": parent_name},
		)

		row_count = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabDGV TB Snapshot Row` "
			"WHERE parent_snapshot = %s",
			(parent_name,),
		)[0][0]

		duration = time.time() - t_start

		frappe.db.set_value(
			"DGV TB Snapshot",
			parent_name,
			{
				"status": "Complete",
				"duration_seconds": round(duration, 3),
				"row_count": row_count,
				"error_message": None,
			},
		)
		frappe.db.commit()

		return {
			"snapshot_name": parent_name,
			"snapshot_date": str(snapshot_date),
			"row_count": row_count,
			"duration_seconds": round(duration, 3),
			"status": "Complete",
		}

	except Exception as e:
		# Roll back the failed INSERT, then mark the parent Failed in a
		# fresh transaction so the failure is visible in the UI.
		frappe.db.rollback()
		try:
			frappe.db.set_value(
				"DGV TB Snapshot",
				parent_name,
				{
					"status": "Failed",
					"error_message": str(e)[:1000],
					"duration_seconds": round(time.time() - t_start, 3),
					"row_count": 0,
				},
			)
			frappe.db.commit()
		except Exception:
			# If even the failure-marking commit fails, give up silently --
			# the original exception is more useful to the caller.
			pass
		raise


def refresh_tb_snapshot_with_progress(snapshot_date=None):
	"""Wraps refresh_tb_snapshot() with realtime progress events.

	Emits the following events on the realtime channel
	`dgv_tb_snapshot_progress`:

	  * {phase: "starting", snapshot_date}
	  * {phase: "complete", snapshot_date, duration_seconds, row_count}
	  * {phase: "failed", snapshot_date, error}

	The single-INSERT design means there is no per-row progress to emit;
	this wrapper only fires start / complete / failed boundaries. Per-entity
	progress (the "Refreshing 23/59 entities" UX) would require chunking
	refresh per company, which would also slow it down -- the current
	8-second total runtime on 50K rows is well below the threshold where a
	progress bar adds value.
	"""
	snapshot_date = _resolve_date(snapshot_date)
	user = frappe.session.user

	frappe.publish_realtime(
		"dgv_tb_snapshot_progress",
		{"phase": "starting", "snapshot_date": str(snapshot_date)},
		user=user,
	)
	try:
		result = refresh_tb_snapshot(snapshot_date)
		frappe.publish_realtime(
			"dgv_tb_snapshot_progress",
			{
				"phase": "complete",
				"snapshot_date": str(snapshot_date),
				"duration_seconds": result["duration_seconds"],
				"row_count": result["row_count"],
			},
			user=user,
		)
		return result
	except Exception as e:
		frappe.publish_realtime(
			"dgv_tb_snapshot_progress",
			{
				"phase": "failed",
				"snapshot_date": str(snapshot_date),
				"error": str(e),
			},
			user=user,
		)
		raise


def refresh_tb_snapshot_business_hours():
	"""Scheduler entry point for the business-hours cron (every 30 min, 8-22 IST).

	Frappe's scheduler keys Scheduled Job Type uniquely by method, so two cron
	entries pointing at refresh_tb_snapshot would collide and only the last
	one declared in hooks.py would survive. We expose business-hours and
	off-hours runs as distinct method names; both delegate to
	refresh_tb_snapshot() and then refresh the spotlight cache.
	"""
	return _refresh_with_spotlight()


def refresh_tb_snapshot_off_hours():
	"""Scheduler entry point for the off-hours cron (hourly 23:00-07:00 IST).

	See refresh_tb_snapshot_business_hours for the why behind two methods.
	"""
	return _refresh_with_spotlight()


def _refresh_with_spotlight():
	"""Run refresh_tb_snapshot, then refresh_spotlight_cache for the same date.

	Spotlight failures do not roll back the TB snapshot -- TB is the canonical
	data, spotlight is derivative. A failed spotlight refresh is logged via
	frappe.log_error and the TB result is returned unchanged.
	"""
	# Local import avoids a hard dependency on the spotlight module at the
	# top of refresh.py (e.g. during the Phase 2 patch run, before the
	# spotlight cache table exists).
	from dux_groupview.dux_groupview.snapshots.spotlight_refresh import (
		refresh_spotlight_cache,
	)

	result = refresh_tb_snapshot()
	if result and result.get("status") == "Complete":
		try:
			refresh_spotlight_cache(snapshot_date=result["snapshot_date"])
		except Exception as e:
			frappe.log_error(
				message=f"refresh_spotlight_cache failed for "
				f"{result.get('snapshot_date')}: {e}",
				title="DGV spotlight refresh",
			)
	return result


def finalize_past_snapshots():
	"""Mark every snapshot with snapshot_date < today as immutable.

	Run nightly at 12:01 AM (site timezone) via scheduler. Safe to call
	manually any number of times -- already-immutable snapshots are a no-op.
	"""
	today_d = getdate(today())
	frappe.db.sql(
		"""
		UPDATE `tabDGV TB Snapshot`
		   SET is_immutable = 1, modified = NOW()
		 WHERE snapshot_date < %s
		   AND is_immutable = 0
		""",
		(today_d,),
	)
	frappe.db.commit()
	count_immutable = frappe.db.count(
		"DGV TB Snapshot",
		filters={"is_immutable": 1},
	)
	print(f"finalize_past_snapshots: {count_immutable} snapshots are now immutable")
	return {"immutable_count": count_immutable}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_date(d):
	"""Coerce to a datetime.date in site timezone; default to today."""
	if d is None:
		return getdate(today())
	return getdate(d)


def _ensure_parent(snapshot_date):
	"""Find or create the DGV TB Snapshot parent record for a date.

	Refuses to proceed if an existing snapshot is marked immutable.
	Otherwise resets the parent to status=Generating and clears any prior
	rows. Returns the parent name.
	"""
	existing = frappe.db.get_value(
		"DGV TB Snapshot",
		{"snapshot_date": snapshot_date},
		["name", "is_immutable"],
		as_dict=True,
	)

	if existing and existing.is_immutable:
		frappe.throw(
			_(
				"Snapshot for {0} is marked immutable; cannot regenerate. "
				"Clear the is_immutable flag manually if you really need to."
			).format(snapshot_date),
			title=_("Immutable snapshot"),
		)

	if existing:
		# Drop any prior rows for this snapshot.
		frappe.db.sql(
			"DELETE FROM `tabDGV TB Snapshot Row` WHERE parent_snapshot = %s",
			(existing.name,),
		)
		frappe.db.set_value(
			"DGV TB Snapshot",
			existing.name,
			{
				"generated_at": now_datetime(),
				"status": "Generating",
				"duration_seconds": 0,
				"row_count": 0,
				"error_message": None,
			},
		)
		return existing.name

	parent = frappe.new_doc("DGV TB Snapshot")
	parent.snapshot_date = snapshot_date
	parent.generated_at = now_datetime()
	parent.status = "Generating"
	parent.duration_seconds = 0
	parent.row_count = 0
	parent.is_immutable = 0
	parent.flags.ignore_permissions = True
	parent.insert()
	return parent.name
