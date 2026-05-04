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
					<button type="button" class="dgv-scope-pill" id="dgv-scope-pill" aria-haspopup="true" aria-expanded="false">
						<span class="dgv-scope-pill-prefix">Showing:</span>
						<span class="dgv-scope-pill-summary">All companies</span>
						<span class="dgv-scope-pill-caret" aria-hidden="true">▾</span>
					</button>
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
					<div class="dgv-depth-toggle" role="group" aria-label="Account depth">
						<span class="dgv-depth-label">Depth</span>
						<button class="dgv-depth-btn" data-depth="1">1</button>
						<button class="dgv-depth-btn" data-depth="2">2</button>
						<button class="dgv-depth-btn" data-depth="3">3</button>
						<button class="dgv-depth-btn" data-depth="all">All</button>
					</div>
					<div class="dgv-format-toggle" role="group" aria-label="Number format">
						<span class="dgv-format-label">Format</span>
						<button class="dgv-format-btn" data-format="crore">Cr</button>
						<button class="dgv-format-btn" data-format="lakh">L</button>
						<button class="dgv-format-btn" data-format="full">Full</button>
					</div>
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

	const SCOPE_STORAGE_KEY = 'dgv_cockpit_scope_v1';
	const SCOPE_STORAGE_VERSION = 1;
	const DEPTH_STORAGE_KEY = 'dgv_cockpit_depth_v1';
	const DEPTH_DEFAULT = 3;
	const FORMAT_STORAGE_KEY = 'dgv_cockpit_format_v1';
	const FORMAT_DEFAULT = 'crore';
	const FORMAT_VALUES = ['crore', 'lakh', 'full'];

	let currentDate = null;
	let agePollHandle = null;
	let pivotGrid = null;
	let heatmapOn = false;
	let trustSelector = null;
	let depthSetting = loadDepthFromStorage();
	let formatSetting = loadFormatFromStorage();
	// `scopeCompanies` holds the currently-applied scope as an array of
	// company names, OR null to mean "all companies the user can see"
	// (the cockpit then uses the cached spotlight endpoint, which is
	// fast). Loaded from localStorage on boot, repopulated on every
	// Apply.
	let scopeCompanies = loadScopeFromStorage();
	let scopeUniverse = []; // every company this user can see (from get_scope_options)

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
		// Fetch dates and scope options in parallel; both are cheap and
		// the page can't render until both resolve.
		const datesPromise = new Promise((resolve) => {
			frappe.call({
				method: 'dux_groupview.dux_groupview.api.cockpit.get_available_snapshot_dates',
				callback: function(r) { resolve((r && r.message) || []); },
			});
		});
		const scopePromise = new Promise((resolve) => {
			frappe.call({
				method: 'dux_groupview.dux_groupview.api.pivot.get_scope_options',
				callback: function(r) { resolve((r && r.message) || { trusts: [] }); },
			});
		});

		Promise.all([datesPromise, scopePromise]).then(([dates, scopeOptions]) => {
			if (!dates.length) {
				$('#dgv-card-grid').html(emptyState('No snapshots yet. Run a refresh first.'));
				return;
			}

			// Build the universe of all companies this user can see.
			scopeUniverse = [];
			(scopeOptions.trusts || []).forEach(t => {
				(t.companies || []).forEach(c => scopeUniverse.push(c));
			});

			// Reconcile any persisted scope against the current universe
			// (companies the user no longer has permission for, or that
			// were renamed, get pruned). An empty result coerces to
			// "all companies" (== null).
			scopeCompanies = reconcileScope(scopeCompanies, scopeUniverse);

			// First-visit smart default: if no scope is persisted, land
			// on the largest trust by company count rather than dump 59
			// columns on a fresh user. We do NOT save this to localStorage
			// -- the user's first explicit Apply is what becomes their
			// "remembered" scope. If they never interact, they get the
			// smart default again next time (and if the universe shifts,
			// the default tracks it).
			if (scopeCompanies === null) {
				scopeCompanies = applySmartDefaultScope(
					scopeOptions.trusts || [], scopeUniverse
				);
			}

			// Mount the trust selector against the header pill.
			mountTrustSelector(scopeOptions.trusts || []);

			// Populate date dropdown and render initial data.
			populateDateSelect(dates);
			currentDate = dates[0];
			$('.dgv-date-select').val(currentDate);
			loadCards(currentDate);
			loadAge(currentDate);
			loadPivot(currentDate);
			wirePivotControls();
			if (agePollHandle) clearInterval(agePollHandle);
			agePollHandle = setInterval(() => loadAge(currentDate), 30000);
		});
	}

	function mountTrustSelector(trusts) {
		const triggerEl = document.getElementById('dgv-scope-pill');
		if (!triggerEl || !window.DuxTrustSelector) return;
		// Tear down a previous instance (e.g. on reload).
		if (trustSelector) {
			try { trustSelector.destroy(); } catch (e) { /* swallow */ }
		}
		trustSelector = new window.DuxTrustSelector(triggerEl, {
			trusts: trusts,
			initialSelection: scopeCompanies || scopeUniverse,
			onApply: function(selected, isAll) {
				scopeCompanies = isAll ? null : selected;
				saveScopeToStorage(scopeCompanies);
				dimAffectedSections(true);
				loadCards(currentDate);
				loadPivot(currentDate);
				// Cards / pivot fetches each clear their own dim on
				// success; nothing else to do here.
			},
			onCancel: function() { /* no-op; selector handles state */ },
		});
	}

	function dimAffectedSections(on) {
		$('#dgv-card-grid').toggleClass('dgv-loading-dim', !!on);
		$('#pivot-grid').toggleClass('dgv-loading-dim', !!on);
	}

	function reconcileScope(saved, universe) {
		if (!saved || !saved.length) return null;  // null == all
		const universeSet = new Set(universe);
		const reconciled = saved.filter(c => universeSet.has(c));
		if (!reconciled.length) return null;
		// If the persisted scope == the user's full universe, normalize
		// to null so the cached spotlight endpoint takes over.
		if (reconciled.length === universe.length) return null;
		return reconciled;
	}

	function pickLargestTrust(trusts) {
		// "Largest" = most companies. Tie-break: trust id alphabetically
		// (deterministic, no surprise on a re-render). Returns null if
		// no trust has any companies.
		let best = null;
		(trusts || []).forEach(t => {
			const count = (t.companies || []).length;
			if (count === 0) return;
			if (!best) { best = t; return; }
			if (count > best.companies.length) { best = t; return; }
			if (count === best.companies.length && t.id < best.id) best = t;
		});
		return best;
	}

	function applySmartDefaultScope(trusts, universe) {
		// Called when there's no persisted scope. Land first-time users
		// on the largest trust by company count, so they don't face a
		// 59-column wall of data on first paint. If the largest trust IS
		// the full universe (e.g. dev seed where all companies fall under
		// "Other"), fall back to null (= all companies, cached path).
		const largest = pickLargestTrust(trusts);
		if (!largest) return null;
		if (largest.companies.length >= universe.length) return null;
		return [...largest.companies];
	}

	function loadScopeFromStorage() {
		try {
			const raw = window.localStorage.getItem(SCOPE_STORAGE_KEY);
			if (!raw) return null;
			const parsed = JSON.parse(raw);
			if (!parsed || parsed.version !== SCOPE_STORAGE_VERSION) return null;
			if (!Array.isArray(parsed.selected_companies)) return null;
			return parsed.selected_companies;
		} catch (e) {
			// Incognito / blocked storage / parse error -- silent fallback.
			return null;
		}
	}

	function saveScopeToStorage(companies) {
		try {
			if (companies === null) {
				window.localStorage.removeItem(SCOPE_STORAGE_KEY);
				return;
			}
			window.localStorage.setItem(SCOPE_STORAGE_KEY, JSON.stringify({
				version: SCOPE_STORAGE_VERSION,
				selected_companies: companies,
				saved_at: new Date().toISOString(),
			}));
		} catch (e) {
			// Storage unavailable -- accept that scope won't persist.
		}
	}

	function loadDepthFromStorage() {
		try {
			const raw = window.localStorage.getItem(DEPTH_STORAGE_KEY);
			if (!raw) return DEPTH_DEFAULT;
			if (raw === 'all') return 'all';
			const parsed = parseInt(raw, 10);
			if (!isNaN(parsed) && parsed >= 1 && parsed <= 99) return parsed;
		} catch (e) { /* fall through */ }
		return DEPTH_DEFAULT;
	}

	function saveDepthToStorage(value) {
		try {
			window.localStorage.setItem(DEPTH_STORAGE_KEY, String(value));
		} catch (e) { /* ignore */ }
	}

	function syncDepthButtons() {
		const $btns = $('.dgv-depth-btn');
		$btns.removeClass('dgv-depth-active');
		const target = String(depthSetting);
		$btns.filter(function() {
			return String($(this).data('depth')) === target;
		}).addClass('dgv-depth-active');
	}

	function loadFormatFromStorage() {
		try {
			const raw = window.localStorage.getItem(FORMAT_STORAGE_KEY);
			if (raw && FORMAT_VALUES.indexOf(raw) !== -1) return raw;
		} catch (e) { /* fall through */ }
		return FORMAT_DEFAULT;
	}

	function saveFormatToStorage(value) {
		try {
			window.localStorage.setItem(FORMAT_STORAGE_KEY, String(value));
		} catch (e) { /* ignore */ }
	}

	function syncFormatButtons() {
		const $btns = $('.dgv-format-btn');
		$btns.removeClass('dgv-format-active');
		$btns.filter(function() {
			return String($(this).data('format')) === formatSetting;
		}).addClass('dgv-format-active');
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
		const args = { snapshot_date: snapshotDate, format: 'crore' };
		// Only attach `companies` when scope is narrower than full --
		// keeps the default request shape identical to Phase 3 and lets
		// the server skip the JSON.loads + intersection work.
		if (scopeCompanies !== null) {
			args.companies = JSON.stringify(scopeCompanies);
		}
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.pivot.get_pivot_data',
			args: args,
			callback: function(r) {
				$('#pivot-grid').removeClass('dgv-loading-dim');
				if (!r || !r.message) return;
				if (!pivotGrid) {
					pivotGrid = new window.DuxPivotGrid(containerEl, {
						format: formatSetting, height: 600, depth: depthSetting,
					});
				}
				pivotGrid.render(r.message);
				pivotGrid.setHeatmap(heatmapOn);
				pivotGrid.setDepth(depthSetting);
				pivotGrid.setFormat(formatSetting);
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

		// Depth toggle: pill group, last-clicked wins, persisted to
		// localStorage. The active state is also restored on page boot
		// (see syncDepthButtons() below).
		syncDepthButtons();
		$('.dgv-depth-btn').off('click').on('click', function() {
			const value = $(this).data('depth');
			depthSetting = (value === 'all') ? 'all' : parseInt(value, 10);
			saveDepthToStorage(depthSetting);
			syncDepthButtons();
			if (pivotGrid) pivotGrid.setDepth(depthSetting);
		});

		// Number format toggle: Cr / L / Full. Affects pivot cells only;
		// spotlight cards always render in Cr (no toggle wiring there).
		// State persisted in localStorage so a returning user keeps
		// their last view.
		syncFormatButtons();
		$('.dgv-format-btn').off('click').on('click', function() {
			const value = String($(this).data('format'));
			if (FORMAT_VALUES.indexOf(value) === -1) return;
			formatSetting = value;
			saveFormatToStorage(formatSetting);
			syncFormatButtons();
			if (pivotGrid) pivotGrid.setFormat(formatSetting);
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
		// First load (empty grid) shows the loading skeleton; subsequent
		// loads (date change, scope change) dim the existing cards in
		// place to avoid a jarring blank flash.
		const $grid = $('#dgv-card-grid');
		if (!$grid.children().length) {
			$grid.html(loadingState());
		}
		// Full-scope -> cached endpoint (fast). Narrow scope -> filtered
		// endpoint that re-aggregates from snapshot rows.
		const isFullScope = scopeCompanies === null;
		const method = isFullScope
			? 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards'
			: 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards_filtered';
		const args = { snapshot_date: snapshotDate };
		if (!isFullScope) args.companies = JSON.stringify(scopeCompanies);
		frappe.call({
			method: method,
			args: args,
			callback: function(r) {
				$('#dgv-card-grid').removeClass('dgv-loading-dim');
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
		.dgv-depth-toggle {
			display: inline-flex; align-items: center;
			background: #fff; border: 1px solid #e2e8f0;
			border-radius: 8px; overflow: hidden;
		}
		.dgv-depth-label {
			padding: 0 10px 0 12px;
			font-size: 11px; font-weight: 600;
			letter-spacing: 0.06em; text-transform: uppercase;
			color: #94a3b8;
		}
		.dgv-depth-btn {
			background: transparent; border: none; padding: 6px 12px;
			font-family: 'Geist', sans-serif; font-size: 12px; color: #475569;
			cursor: pointer; border-left: 1px solid #e2e8f0;
			transition: background 0.1s ease;
		}
		.dgv-depth-btn:hover:not(.dgv-depth-active) { background: #f1f5f9; }
		.dgv-depth-btn.dgv-depth-active {
			background: #0f172a; color: #fff; cursor: default;
		}
		.dgv-format-toggle {
			display: inline-flex; align-items: center;
			background: #fff; border: 1px solid #e2e8f0;
			border-radius: 8px; overflow: hidden;
		}
		.dgv-format-label {
			padding: 0 10px 0 12px;
			font-size: 11px; font-weight: 600;
			letter-spacing: 0.06em; text-transform: uppercase;
			color: #94a3b8;
		}
		.dgv-format-btn {
			background: transparent; border: none; padding: 6px 12px;
			font-family: 'Geist', sans-serif; font-size: 12px; color: #475569;
			cursor: pointer; border-left: 1px solid #e2e8f0;
			transition: background 0.1s ease;
		}
		.dgv-format-btn:hover:not(.dgv-format-active) { background: #f1f5f9; }
		.dgv-format-btn.dgv-format-active {
			background: #0f172a; color: #fff; cursor: default;
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
