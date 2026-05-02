frappe.pages['groupview-health'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'GroupView Health',
		single_column: true
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$('head').append(
		'<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">'
	);

	$body.html(`
		<div class="dgv-health-shell">
			<div class="dgv-health-header">
				<div>
					<div class="dgv-health-eyebrow">dux_groupview</div>
					<h1 class="dgv-health-title">Snapshot Health</h1>
				</div>
				<div class="dgv-health-actions">
					<button class="btn btn-primary dgv-btn-refresh">Refresh now</button>
					<button class="btn btn-default dgv-btn-backfill">Backfill 12 months</button>
				</div>
			</div>
			<div id="dgv-health-banner"></div>
			<div id="dgv-health-grid"></div>
			<div id="dgv-health-list"></div>
		</div>
		${dgvHealthStyles()}
	`);

	loadHealth();

	$body.on('click', '.dgv-btn-refresh', onRefreshClick);
	$body.on('click', '.dgv-btn-backfill', onBackfillClick);

	function loadHealth() {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.health.get_snapshot_health',
			callback: function(r) {
				if (r && r.message) {
					renderHealth(r.message);
				}
			},
		});
	}

	function renderHealth(data) {
		const banner = $('#dgv-health-banner').empty();
		if (data.slow_warning) {
			banner.html(`
				<div class="dgv-banner dgv-banner-warn">
					One of the last 5 snapshots took longer than ${data.slow_threshold_seconds}s.
					Investigate before next phase work.
				</div>
			`);
		}
		if (data.scheduler && data.scheduler.stale) {
			banner.append(`
				<div class="dgv-banner dgv-banner-error">
					Scheduler last seen ${data.scheduler.last_seen_at || 'never'}.
					Cron jobs may not be firing.
				</div>
			`);
		}

		const grid = $('#dgv-health-grid').empty();
		const latest = data.latest;
		grid.html(`
			<div class="dgv-card-grid">
				${statCard('Latest snapshot', latest ? latest.snapshot_date : '—',
					latest ? `Status: ${latest.status}` : 'No snapshots yet')}
				${statCard('Last duration',
					latest ? `${formatNumber(latest.duration_seconds)} sec` : '—',
					latest ? `${formatNumber(latest.row_count, 0)} rows written` : '')}
				${statCard('GL Entry rows', formatNumber(data.gl_entry_count, 0), 'Source size')}
				${statCard('Snapshot rows', formatNumber(data.snapshot_row_count, 0),
					'Across all snapshots')}
				${statCard('Scheduler',
					data.scheduler.enabled ? 'Enabled' : 'Disabled',
					data.scheduler.last_seen_at ? `Last seen ${data.scheduler.last_seen_at}` : 'Never seen')}
			</div>
		`);

		const list = $('#dgv-health-list').empty();
		const rows = (data.last_seven || []).map(snapshot => `
			<tr>
				<td class="dgv-mono">${escape(snapshot.snapshot_date)}</td>
				<td><span class="dgv-status dgv-status-${snapshot.status.toLowerCase()}">${escape(snapshot.status)}</span></td>
				<td>${escape(snapshot.generated_at || '')}</td>
				<td class="dgv-mono dgv-num">${formatNumber(snapshot.duration_seconds)}</td>
				<td class="dgv-mono dgv-num">${formatNumber(snapshot.row_count, 0)}</td>
				<td>${snapshot.is_immutable ? 'Locked' : ''}</td>
			</tr>
		`).join('');
		list.html(`
			<div class="dgv-section-title">Last 7 snapshots</div>
			<table class="dgv-table">
				<thead>
					<tr>
						<th>Date</th><th>Status</th><th>Generated at</th>
						<th class="dgv-num">Duration (s)</th>
						<th class="dgv-num">Rows</th>
						<th>Immutable</th>
					</tr>
				</thead>
				<tbody>${rows || '<tr><td colspan="6" class="dgv-empty">No snapshots yet.</td></tr>'}</tbody>
			</table>
		`);
	}

	function onRefreshClick() {
		const $btn = $(this);
		const originalLabel = $btn.text();
		$btn.prop('disabled', true).text('Refreshing…');

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.health.trigger_manual_refresh',
			callback: function(r) {
				if (!r || !r.message) {
					$btn.prop('disabled', false).text(originalLabel);
					return;
				}
				pollUntilFresh($btn, originalLabel);
			},
		});
	}

	function pollUntilFresh($btn, originalLabel) {
		const startedAt = Date.now();
		const baselineGeneratedAt = $('#dgv-health-list .dgv-mono').first().text();

		const interval = setInterval(() => {
			const elapsed = Date.now() - startedAt;
			if (elapsed > 60000) {
				clearInterval(interval);
				$btn.prop('disabled', false).text(originalLabel);
				frappe.show_alert({
					message: 'Refresh taking longer than expected. Check the snapshot list below for status.',
					indicator: 'orange',
				}, 8);
				loadHealth();
				return;
			}
			frappe.call({
				method: 'dux_groupview.dux_groupview.api.health.get_snapshot_health',
				callback: function(r) {
					if (!r || !r.message || !r.message.latest) return;
					const latest = r.message.latest;
					if (latest.status === 'Complete' || latest.status === 'Failed') {
						clearInterval(interval);
						$btn.prop('disabled', false).text(originalLabel);
						renderHealth(r.message);
						frappe.show_alert({
							message: `Snapshot ${latest.status.toLowerCase()} in ${formatNumber(latest.duration_seconds)} sec.`,
							indicator: latest.status === 'Complete' ? 'green' : 'red',
						}, 5);
					}
				},
			});
		}, 2000);
	}

	function onBackfillClick() {
		frappe.confirm(
			'This will compute 12 monthly snapshots and may take several minutes. Continue?',
			function() {
				const $btn = $('.dgv-btn-backfill');
				const originalLabel = $btn.text();
				$btn.prop('disabled', true).text('Enqueuing…');
				frappe.call({
					method: 'dux_groupview.dux_groupview.api.health.trigger_backfill',
					args: { months_back: 12 },
					callback: function(r) {
						$btn.prop('disabled', false).text(originalLabel);
						if (r && r.message) {
							frappe.show_alert({
								message: `Backfill enqueued (job ${r.message.job_id}). Watch the snapshot list for progress.`,
								indicator: 'blue',
							}, 8);
							loadHealth();
						}
					},
				});
			}
		);
	}

	function statCard(label, value, sublabel) {
		return `
			<div class="dgv-stat">
				<div class="dgv-stat-label">${escape(label)}</div>
				<div class="dgv-stat-value">${escape(value)}</div>
				<div class="dgv-stat-sub">${escape(sublabel || '')}</div>
			</div>
		`;
	}

	function formatNumber(n, decimals) {
		if (n === null || n === undefined || n === '') return '—';
		const d = decimals === undefined ? 3 : decimals;
		const num = Number(n);
		if (isNaN(num)) return '—';
		return num.toLocaleString(undefined, {
			minimumFractionDigits: d,
			maximumFractionDigits: d,
		});
	}

	function escape(s) {
		if (s === null || s === undefined) return '';
		return frappe.utils.escape_html(String(s));
	}

	function dgvHealthStyles() {
		return `<style>
			.dgv-health-shell {
				font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
				color: #0f172a;
				background: #f8fafc;
				min-height: calc(100vh - 60px);
				padding: 32px 40px;
			}
			.dgv-health-header {
				display: flex; justify-content: space-between; align-items: flex-end;
				margin-bottom: 24px;
			}
			.dgv-health-eyebrow {
				font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
				text-transform: uppercase; color: #64748b;
			}
			.dgv-health-title { font-size: 24px; font-weight: 600; margin: 4px 0 0 0; }
			.dgv-health-actions { display: flex; gap: 8px; }
			.dgv-banner {
				padding: 12px 16px; border-radius: 8px; margin-bottom: 16px;
				font-size: 13px; border: 1px solid;
			}
			.dgv-banner-warn { background: #fef3c7; border-color: #fcd34d; color: #92400e; }
			.dgv-banner-error { background: #fee2e2; border-color: #fca5a5; color: #991b1b; }
			.dgv-card-grid {
				display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
				gap: 12px; margin-bottom: 28px;
			}
			.dgv-stat {
				background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
				padding: 16px;
			}
			.dgv-stat-label {
				font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
				text-transform: uppercase; color: #64748b; margin-bottom: 8px;
			}
			.dgv-stat-value {
				font-size: 22px; font-weight: 600; color: #0f172a;
				font-variant-numeric: tabular-nums;
			}
			.dgv-stat-sub { font-size: 12px; color: #94a3b8; margin-top: 4px; }
			.dgv-section-title {
				font-size: 13px; font-weight: 600; color: #475569;
				margin: 24px 0 12px 0;
				text-transform: uppercase; letter-spacing: 0.06em;
			}
			.dgv-table {
				width: 100%; background: #fff; border: 1px solid #e2e8f0;
				border-radius: 12px; overflow: hidden; border-collapse: separate;
				border-spacing: 0;
			}
			.dgv-table th, .dgv-table td {
				padding: 10px 14px; font-size: 13px; text-align: left;
				border-bottom: 1px solid #f1f5f9;
			}
			.dgv-table th {
				background: #f8fafc; font-weight: 600; color: #475569;
				text-transform: uppercase; letter-spacing: 0.05em; font-size: 11px;
			}
			.dgv-table tr:last-child td { border-bottom: none; }
			.dgv-mono { font-family: 'Geist Mono', ui-monospace, SFMono-Regular, monospace; }
			.dgv-num { text-align: right; font-variant-numeric: tabular-nums; }
			.dgv-empty { color: #94a3b8; padding: 20px; text-align: center; }
			.dgv-status {
				display: inline-block; padding: 2px 8px; border-radius: 4px;
				font-size: 11px; font-weight: 600;
			}
			.dgv-status-complete { background: #dcfce7; color: #166534; }
			.dgv-status-generating { background: #dbeafe; color: #1e40af; }
			.dgv-status-failed { background: #fee2e2; color: #991b1b; }
		</style>`;
	}
};
