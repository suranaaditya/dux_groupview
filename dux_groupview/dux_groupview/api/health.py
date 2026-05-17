"""Whitelisted endpoints for the /groupview-health admin page.

All endpoints are System Manager only -- the health page is operations
infrastructure, not user-facing.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime


SLOW_REFRESH_THRESHOLD_SECONDS = 30


def _require_system_manager():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("/groupview-health is restricted to System Managers."),
			frappe.PermissionError,
		)


def _serialise(value):
	"""JSON-serialise datetimes / dates as ISO strings."""
	if value is None:
		return None
	if hasattr(value, "isoformat"):
		return value.isoformat()
	return value


@frappe.whitelist()
def get_snapshot_health():
	"""Return everything the /groupview-health page renders."""
	_require_system_manager()

	last_seven = frappe.db.sql(
		"""
		SELECT name, snapshot_date, generated_at, status,
		       duration_seconds, row_count, is_immutable, error_message
		FROM `tabDGV TB Snapshot`
		ORDER BY snapshot_date DESC, generated_at DESC
		LIMIT 7
		""",
		as_dict=True,
	)
	for row in last_seven:
		row["snapshot_date"] = _serialise(row["snapshot_date"])
		row["generated_at"] = _serialise(row["generated_at"])

	latest = last_seven[0] if last_seven else None

	# Performance warning if any of the last 5 took too long.
	last_five_durations = [
		row["duration_seconds"] for row in last_seven[:5]
		if row["duration_seconds"] is not None
	]
	slow_warning = any(
		d > SLOW_REFRESH_THRESHOLD_SECONDS for d in last_five_durations
	)

	scheduler = _scheduler_status()

	gl_count = frappe.db.count("GL Entry")
	row_table_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabDGV TB Snapshot Row`"
	)[0][0]

	return {
		"latest": latest,
		"last_seven": last_seven,
		"slow_warning": slow_warning,
		"slow_threshold_seconds": SLOW_REFRESH_THRESHOLD_SECONDS,
		"scheduler": scheduler,
		"gl_entry_count": gl_count,
		"snapshot_row_count": row_table_count,
	}


def _scheduler_status():
	"""Best-effort scheduler heartbeat info."""
	from frappe.utils.scheduler import is_scheduler_inactive

	# The Scheduled Job Log is the most reliable signal of a recent run.
	last_log = frappe.db.sql(
		"""
		SELECT scheduled_job_type, status, modified
		FROM `tabScheduled Job Log`
		ORDER BY modified DESC
		LIMIT 1
		""",
		as_dict=True,
	)
	last_log_row = last_log[0] if last_log else None
	last_seen = (
		_serialise(last_log_row["modified"]) if last_log_row else None
	)

	stale = False
	if last_log_row:
		age = now_datetime() - get_datetime(last_log_row["modified"])
		stale = age > timedelta(hours=2)
	else:
		stale = True

	return {
		"enabled": not is_scheduler_inactive(verbose=False),
		"last_seen_at": last_seen,
		"last_seen_job": last_log_row["scheduled_job_type"] if last_log_row else None,
		"last_seen_status": last_log_row["status"] if last_log_row else None,
		"stale": stale,
	}


@frappe.whitelist()
def trigger_manual_refresh():
	"""Enqueue a refresh for today; return immediately with the job id.

	Points at `_refresh_with_spotlight` rather than `refresh_tb_snapshot`
	directly so the manual button matches the scheduled cron: TB refresh
	is followed by spotlight cache refresh. Hitting `refresh_tb_snapshot`
	alone leaves `tabDGV Spotlight Cache` stale -- the cockpit cards keep
	rendering the previous run's values until the next scheduled cron
	fires. Discovered post-deploy of PR #19 (supplier-advances display +
	exclude fixes): the manual button on the Snapshot Health page
	refreshed TB but cards still showed pre-deploy sign.
	"""
	_require_system_manager()
	job = frappe.enqueue(
		"dux_groupview.dux_groupview.snapshots.refresh._refresh_with_spotlight",
		queue="default",
		timeout=600,
		job_name="dgv-manual-refresh",
	)
	return {"job_id": job.id, "queue": "default"}


@frappe.whitelist()
def trigger_backfill(months_back=12):
	"""Enqueue a 12-month (or N-month) backfill."""
	_require_system_manager()
	months_back = int(months_back)
	job = frappe.enqueue(
		"dux_groupview.dux_groupview.snapshots.backfill.backfill_snapshots",
		queue="long",
		timeout=3600,
		job_name=f"dgv-backfill-{months_back}m",
		months_back=months_back,
	)
	return {"job_id": job.id, "queue": "long", "months_back": months_back}
