frappe.pages['groupview'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'GroupView',
		single_column: true
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$('head').append(
		'<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">'
	);

	$body.html(`
		<div class="dux-synthetic-banner" id="dux-synthetic-banner" hidden>
			<strong>SYNTHETIC PREVIEW DATA</strong>
			<span>Numbers shown are random and not actual RGI figures. For UI/UX review only.</span>
		</div>
		<div class="dgv-cockpit-shell" id="dgv-cockpit-shell">
			<div class="dgv-cockpit-topbar">
				<div class="dgv-cockpit-brand">
					<div class="dgv-cockpit-eyebrow">dux_groupview</div>
					<div class="dgv-cockpit-title">Group cockpit</div>
				</div>
				<div class="dgv-cockpit-controls">
					<select class="dgv-date-select"></select>
					<span class="dgv-age-pill" id="dgv-age-pill">…</span>
				</div>
			</div>

			<div class="dgv-section-row">
				<div class="dgv-section-title">Spotlight</div>
				<button class="dgv-edit-pill" disabled title="Card editor lands in Phase 5">Edit</button>
			</div>

			<div class="dgv-card-grid" id="dgv-card-grid"></div>

			<div class="dgv-section-row dgv-pivot-section-row">
				<div class="dgv-section-title">Pivot</div>
				<div class="dgv-pivot-controls">
					<div class="dgv-view-toggle" role="tablist">
						<button class="dgv-view-btn dgv-view-active" data-view="balance">Balance</button>
						<button class="dgv-view-btn" data-view="movement" disabled title="Coming in Phase 4">Movement</button>
						<button class="dgv-view-btn" data-view="compare" disabled title="Coming in Phase 4">Compare</button>
					</div>
					<input type="text" class="dgv-pivot-search" placeholder="Search account…" />
					<button class="dgv-heatmap-toggle" id="dgv-heatmap-toggle">
						<span class="dgv-heatmap-state">Plain</span>
					</button>
				</div>
			</div>
			<div id="pivot-grid"></div>

			<div class="dgv-cockpit-footer">
				Powered by Dux DigiTech &middot; Phase 3
			</div>
		</div>
		${dgvCockpitStyles()}
	`);

	let currentDate = null;
	let agePollHandle = null;
	let pivotGrid = null;
	let heatmapOn = false;

	bootstrap();
	checkSyntheticPreview();

	function checkSyntheticPreview() {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.cockpit.get_seed_state',
			callback: function(r) {
				if (r && r.message && r.message.is_synthetic_preview) {
					document.getElementById('dux-synthetic-banner').hidden = false;
					document.getElementById('dgv-cockpit-shell').classList.add('dgv-with-banner');
				} else {
					document.getElementById('dux-synthetic-banner').hidden = true;
					document.getElementById('dgv-cockpit-shell').classList.remove('dgv-with-banner');
				}
			},
		});
	}

	function bootstrap() {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.cockpit.get_available_snapshot_dates',
			callback: function(r) {
				if (!r || !r.message || !r.message.length) {
					$('#dgv-card-grid').html(emptyState('No snapshots yet. Run a refresh first.'));
					return;
				}
				const dates = r.message;
				populateDateSelect(dates);
				currentDate = dates[0];
				$('.dgv-date-select').val(currentDate);
				loadCards(currentDate);
				loadAge(currentDate);
				loadPivot(currentDate);
				wirePivotControls();
				if (agePollHandle) clearInterval(agePollHandle);
				agePollHandle = setInterval(() => loadAge(currentDate), 30000);
			},
		});
	}

	function populateDateSelect(dates) {
		const $sel = $('.dgv-date-select').empty();
		dates.forEach(d => $sel.append(`<option value="${d}">${formatDate(d)}</option>`));
		$sel.on('change', function() {
			currentDate = $(this).val();
			loadCards(currentDate);
			loadAge(currentDate);
			loadPivot(currentDate);
		});
	}

	function loadPivot(snapshotDate) {
		const containerEl = document.getElementById('pivot-grid');
		if (!containerEl) return;
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.pivot.get_pivot_data',
			args: { snapshot_date: snapshotDate, format: 'crore' },
			callback: function(r) {
				if (!r || !r.message) return;
				if (!pivotGrid) {
					pivotGrid = new window.DuxPivotGrid(containerEl, {
						format: 'crore', height: 600,
					});
				}
				pivotGrid.render(r.message);
				pivotGrid.setHeatmap(heatmapOn);
			},
		});
	}

	function wirePivotControls() {
		const $search = $('.dgv-pivot-search');
		let searchTimer = null;
		$search.off('input').on('input', function() {
			const v = $(this).val();
			clearTimeout(searchTimer);
			searchTimer = setTimeout(() => {
				if (pivotGrid) pivotGrid.setSearch(v);
			}, 80);
		});

		const $heatBtn = $('#dgv-heatmap-toggle');
		$heatBtn.off('click').on('click', function() {
			heatmapOn = !heatmapOn;
			$heatBtn.toggleClass('dgv-heatmap-on', heatmapOn);
			$heatBtn.find('.dgv-heatmap-state').text(heatmapOn ? 'Heatmap' : 'Plain');
			if (pivotGrid) pivotGrid.setHeatmap(heatmapOn);
		});

		// Disabled view-mode buttons just show a tooltip; no handler needed.
		$('.dgv-view-btn[disabled]').off('click').on('click', function(e) {
			e.preventDefault();
		});

		// Listen for cell clicks bubbled from the pivot.
		document.removeEventListener('dux-pivot-cell-click', _cellHandler);
		document.addEventListener('dux-pivot-cell-click', _cellHandler);
	}

	function _cellHandler(e) {
		if (!e.detail) return;
		frappe.show_alert({
			message: `Drill into ${e.detail.account} × ${e.detail.company} coming in Phase 4`,
			indicator: 'blue',
		}, 5);
	}

	function loadCards(snapshotDate) {
		$('#dgv-card-grid').html(loadingState());
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards',
			args: { snapshot_date: snapshotDate },
			callback: function(r) {
				if (r && r.message) {
					renderCards(r.message);
				}
			},
		});
	}

	function loadAge(snapshotDate) {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.cockpit.get_snapshot_age',
			args: { snapshot_date: snapshotDate },
			callback: function(r) {
				if (r && r.message) {
					renderAgePill(r.message);
				}
			},
		});
	}

	function renderAgePill(data) {
		const $pill = $('#dgv-age-pill');
		if (!data || data.age_seconds === null || data.age_seconds === undefined) {
			$pill.removeClass('dgv-age-green dgv-age-amber dgv-age-red')
				.addClass('dgv-age-amber')
				.text('Not synced yet');
			return;
		}
		const ageMin = Math.round(data.age_seconds / 60);
		let label;
		if (data.age_seconds < 60) label = 'Synced just now';
		else if (ageMin < 60) label = `Synced ${ageMin} min ago`;
		else if (ageMin < 1440) label = `Synced ${Math.round(ageMin / 60)} h ago`;
		else label = `Synced ${Math.round(ageMin / 1440)} d ago`;

		const klass = data.age_seconds < 1800 ? 'dgv-age-green'
			: data.age_seconds < 3600 ? 'dgv-age-amber'
			: 'dgv-age-red';

		$pill.removeClass('dgv-age-green dgv-age-amber dgv-age-red')
			.addClass(klass)
			.text(label);
	}

	function renderCards(cards) {
		const $grid = $('#dgv-card-grid').empty();
		cards.forEach(card => {
			const deltaClass = deltaColorClass(card.polarity, card.delta);
			const deltaPercentText = card.delta_percent ?
				` (${card.delta_percent > 0 ? '+' : ''}${card.delta_percent.toFixed(1)}%)` : '';

			const sparkline = renderSparkline(card.sparkline_data || [], card.color);

			const $card = $(`
				<div class="dgv-card" data-card-id="${escape(card.card_id)}">
					<div class="dgv-card-header">
						<div class="dgv-card-label">${escape(card.label)}</div>
						<div class="dgv-card-spark">${sparkline}</div>
					</div>
					<div class="dgv-card-value" style="color: ${escape(card.color)};">
						${escape(card.formatted_value)}
					</div>
					<div class="dgv-card-meta">
						<span class="dgv-card-delta ${deltaClass}">
							${escape(card.formatted_delta)}${escape(deltaPercentText)}
						</span>
						<span class="dgv-card-meta-sub">vs prev month</span>
					</div>
				</div>
			`);
			$card.on('click', () => onCardClick(card));
			$grid.append($card);
		});
	}

	function renderSparkline(values, color) {
		const points = values.filter(v => v !== null && v !== undefined);
		if (points.length < 2) {
			return '<svg width="60" height="18"></svg>';
		}
		const min = Math.min(...points);
		const max = Math.max(...points);
		const range = max - min || 1;
		const width = 60, height = 18, pad = 1;
		const dx = (width - 2 * pad) / (values.length - 1);
		const path = values.map((v, i) => {
			if (v === null || v === undefined) return null;
			const x = pad + i * dx;
			const y = height - pad - ((v - min) / range) * (height - 2 * pad);
			return `${x.toFixed(1)},${y.toFixed(1)}`;
		}).filter(Boolean);
		return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
			<polyline fill="none" stroke="${escape(color)}" stroke-width="1.5"
				points="${path.join(' ')}" />
		</svg>`;
	}

	function deltaColorClass(polarity, delta) {
		if (polarity === 'neutral' || delta === 0) return 'dgv-delta-neutral';
		const up = delta > 0;
		if (polarity === 'good_up') return up ? 'dgv-delta-good' : 'dgv-delta-bad';
		if (polarity === 'bad_up') return up ? 'dgv-delta-bad' : 'dgv-delta-good';
		return 'dgv-delta-neutral';
	}

	function onCardClick(card) {
		frappe.show_alert({
			message: `Drill into ${card.label} coming in Phase 4.`,
			indicator: 'blue',
		}, 5);
	}

	function loadingState() {
		return '<div class="dgv-cockpit-empty">Loading…</div>';
	}

	function emptyState(text) {
		return `<div class="dgv-cockpit-empty">${escape(text)}</div>`;
	}

	function formatDate(iso) {
		if (!iso) return '';
		try { return frappe.datetime.global_date_format(iso); }
		catch (e) { return iso; }
	}

	function escape(s) {
		if (s === null || s === undefined) return '';
		return frappe.utils.escape_html(String(s));
	}
};

function dgvCockpitStyles() {
	return `<style>
		.dux-synthetic-banner {
			position: sticky;
			top: 0;
			z-index: 100;
			background: #f59e0b;
			color: #422006;
			padding: 0 24px;
			height: 36px;
			display: flex;
			align-items: center;
			gap: 12px;
			font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
			font-size: 13px;
			border-bottom: 2px solid #b45309;
			box-shadow: 0 2px 4px rgba(15, 23, 42, 0.08);
		}
		.dux-synthetic-banner strong {
			font-weight: 700;
			letter-spacing: 0.05em;
		}
		.dux-synthetic-banner span {
			font-weight: 500;
			opacity: 0.9;
		}
		.dgv-cockpit-shell {
			font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
			color: #0f172a;
			background: #f8fafc;
			min-height: calc(100vh - 60px);
			padding: 32px 40px 56px 40px;
		}
		.dgv-cockpit-shell.dgv-with-banner {
			min-height: calc(100vh - 60px - 36px);
		}
		.dgv-cockpit-topbar {
			display: flex; justify-content: space-between; align-items: flex-end;
			margin-bottom: 28px;
		}
		.dgv-cockpit-eyebrow {
			font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
			text-transform: uppercase; color: #64748b;
		}
		.dgv-cockpit-title {
			font-size: 26px; font-weight: 600; margin: 4px 0 0 0; color: #0f172a;
		}
		.dgv-cockpit-controls {
			display: flex; gap: 12px; align-items: center;
		}
		.dgv-date-select {
			background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
			padding: 8px 12px; font-family: 'Geist', sans-serif; font-size: 13px;
			color: #0f172a; cursor: pointer;
		}
		.dgv-age-pill {
			padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 500;
			border: 1px solid;
		}
		.dgv-age-green { background: #dcfce7; border-color: #86efac; color: #166534; }
		.dgv-age-amber { background: #fef3c7; border-color: #fcd34d; color: #92400e; }
		.dgv-age-red   { background: #fee2e2; border-color: #fca5a5; color: #991b1b; }

		.dgv-section-row {
			display: flex; justify-content: space-between; align-items: center;
			margin-bottom: 16px;
		}
		.dgv-pivot-section-row { margin-top: 36px; }
		.dgv-pivot-controls {
			display: flex; gap: 10px; align-items: center;
		}
		.dgv-view-toggle {
			display: inline-flex; background: #fff; border: 1px solid #e2e8f0;
			border-radius: 8px; overflow: hidden;
		}
		.dgv-view-btn {
			background: transparent; border: none; padding: 6px 14px;
			font-family: 'Geist', sans-serif; font-size: 12px; color: #475569;
			cursor: pointer; transition: background 0.1s ease;
		}
		.dgv-view-btn + .dgv-view-btn { border-left: 1px solid #e2e8f0; }
		.dgv-view-btn:hover:not([disabled]) { background: #f1f5f9; }
		.dgv-view-btn.dgv-view-active {
			background: #0f172a; color: #fff; cursor: default;
		}
		.dgv-view-btn[disabled] { color: #cbd5e1; cursor: not-allowed; }
		.dgv-pivot-search {
			background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
			padding: 6px 12px; font-family: 'Geist', sans-serif; font-size: 12px;
			color: #0f172a; width: 200px;
		}
		.dgv-pivot-search:focus { outline: none; border-color: #94a3b8; }
		.dgv-heatmap-toggle {
			background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
			padding: 6px 14px; font-family: 'Geist', sans-serif; font-size: 12px;
			color: #475569; cursor: pointer; transition: all 0.1s ease;
		}
		.dgv-heatmap-toggle:hover { background: #f1f5f9; }
		.dgv-heatmap-toggle.dgv-heatmap-on {
			background: #0f172a; color: #fff; border-color: #0f172a;
		}
		.dgv-section-title {
			font-size: 12px; font-weight: 700; letter-spacing: 0.1em;
			text-transform: uppercase; color: #475569;
		}
		.dgv-edit-pill {
			background: #fff; border: 1px solid #e2e8f0; border-radius: 999px;
			padding: 4px 14px; font-family: 'Geist', sans-serif; font-size: 12px;
			color: #94a3b8; cursor: not-allowed;
		}

		.dgv-card-grid {
			display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px;
		}
		@media (max-width: 900px) {
			.dgv-card-grid { grid-template-columns: repeat(2, 1fr); }
		}
		@media (max-width: 600px) {
			.dgv-card-grid { grid-template-columns: 1fr; }
		}

		.dgv-card {
			background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
			padding: 18px 20px; cursor: pointer; transition: all 0.15s ease;
		}
		.dgv-card:hover {
			border-color: #cbd5e1;
			box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05), 0 4px 12px rgba(15, 23, 42, 0.06);
			transform: translateY(-1px);
		}
		.dgv-card-header {
			display: flex; justify-content: space-between; align-items: center;
			margin-bottom: 12px;
		}
		.dgv-card-label {
			font-size: 13px; font-weight: 500; color: #475569;
		}
		.dgv-card-spark { display: flex; align-items: center; }
		.dgv-card-value {
			font-family: 'Geist Mono', ui-monospace, SFMono-Regular, monospace;
			font-size: 24px; font-weight: 600;
			font-variant-numeric: tabular-nums;
			margin-bottom: 8px;
			letter-spacing: -0.01em;
		}
		.dgv-card-meta {
			display: flex; justify-content: space-between; align-items: baseline;
			font-size: 10px;
		}
		.dgv-card-delta {
			font-family: 'Geist Mono', ui-monospace, SFMono-Regular, monospace;
			font-weight: 600;
			font-variant-numeric: tabular-nums;
		}
		.dgv-delta-good    { color: #16a34a; }
		.dgv-delta-bad     { color: #dc2626; }
		.dgv-delta-neutral { color: #94a3b8; }
		.dgv-card-meta-sub { color: #94a3b8; }

		.dgv-cockpit-empty {
			grid-column: 1 / -1;
			padding: 40px; text-align: center; color: #94a3b8;
			background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
		}

		.dgv-cockpit-footer {
			margin-top: 32px; text-align: center;
			font-size: 11px; color: #94a3b8; letter-spacing: 0.06em;
		}
	</style>`;
}
