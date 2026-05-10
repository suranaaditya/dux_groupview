frappe.pages['groupview'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'GroupView',
		single_column: true,
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$body.html(`
		<div class="dux-synthetic-banner" id="dux-synthetic-banner" hidden>
			Synthetic preview data — not for distribution
		</div>

		<section class="rgi-focus" id="rgi-focus" hidden aria-label="Focused view">
			<header class="dgv-focus-header">
				<button type="button" class="dgv-focus-exit"
				        id="dgv-focus-exit"
				        aria-label="Back to cockpit">
					← Back to cockpit
				</button>
				<div class="dgv-focus-scope">
					<div class="dgv-focus-scope-title">
						<span class="dgv-focus-scope-prefix">Focusing:</span>
						<span class="dgv-focus-scope-name" id="dgv-focus-scope-name"></span>
					</div>
					<div class="dgv-focus-scope-sub" id="dgv-focus-scope-sub"></div>
				</div>
				<div class="dgv-focus-toolbar">
					<button type="button" class="dgv-focus-export"
					        id="dgv-focus-export"
					        title="Download the focused view as CSV">
						Export CSV
					</button>
					<button type="button" class="dgv-focus-close"
					        id="dgv-focus-close"
					        aria-label="Exit focus mode">
						×
					</button>
				</div>
			</header>

			<div class="dgv-focus-tiles" id="dgv-focus-tiles"></div>
			<div class="dgv-focus-trust-strip" id="dgv-focus-trust-strip" hidden></div>
			<div class="dgv-focus-accounts" id="dgv-focus-accounts"></div>
		</section>

		<div class="dgv-cockpit-shell" id="dgv-cockpit-shell">

			<header class="rgi-header" role="banner">
				<div class="rgi-header-brand">
					<img class="rgi-header-logo"
					     src="/assets/dux_groupview/images/raisoni_education_navy.svg"
					     alt="Raisoni Education" />
					<div class="rgi-header-rule" aria-hidden="true"></div>
					<div class="rgi-header-tagline">
						Group financial<br>administration
					</div>
				</div>

				<div class="rgi-header-controls">
					<button type="button" class="dgv-scope-pill" id="dgv-scope-pill"
					        aria-haspopup="true" aria-expanded="false">
						<span class="dgv-scope-pill-prefix">Scope</span>
						<span class="dgv-scope-pill-summary">All companies</span>
						<span class="dgv-scope-pill-caret" aria-hidden="true">▾</span>
					</button>
					<select class="dgv-date-select" aria-label="Snapshot date"></select>
					<span class="dgv-age-pill" id="dgv-age-pill">…</span>
				</div>

				<div class="rgi-header-meta">
					<div class="rgi-header-date" id="rgi-header-date">—</div>
					<div class="rgi-header-caption" id="rgi-header-caption">—</div>
				</div>
			</header>

			<main class="rgi-body">

				<section class="rgi-headline" id="rgi-headline" hidden>
					<div class="rgi-headline-eyebrow">Headline</div>
					<div class="rgi-headline-text" id="rgi-headline-text"></div>
				</section>

				<section class="rgi-spotlight" id="rgi-spotlight" aria-label="Spotlight">
					<div class="rgi-spotlight-tier-primary" id="rgi-tier-primary"></div>
					<div class="rgi-spotlight-tier-secondary" id="rgi-tier-secondary"></div>
				</section>

				<section class="rgi-tb" aria-label="Trial balance">
					<div class="rgi-tb-heading">Trial balance</div>
					<div class="rgi-tb-subtitle" id="rgi-tb-subtitle">
						Account-level detail across the selected scope
					</div>

					<div class="rgi-tb-toolbar">
						<div class="dgv-view-toggle" role="tablist">
							<button class="dgv-view-btn dgv-view-active" data-view="balance">Balance</button>
							<button class="dgv-view-btn" data-view="movement"
							        disabled title="Coming in Phase 4">Movement</button>
							<button class="dgv-view-btn" data-view="compare"
							        disabled title="Coming in Phase 4">Compare</button>
						</div>

						<div class="dgv-depth-toggle" role="group" aria-label="Account depth">
							<span class="dgv-depth-label">Depth</span>
							<div class="dgv-depth-pill-group">
								<button class="dgv-depth-btn" data-depth="1">1</button>
								<button class="dgv-depth-btn" data-depth="2">2</button>
								<button class="dgv-depth-btn" data-depth="3">3</button>
								<button class="dgv-depth-btn" data-depth="all">All</button>
							</div>
						</div>

						<div class="dgv-format-toggle" role="group" aria-label="Number format">
							<span class="dgv-format-label">Format</span>
							<div class="dgv-format-pill-group">
								<button class="dgv-format-btn" data-format="crore">Cr</button>
								<button class="dgv-format-btn" data-format="lakh">L</button>
								<button class="dgv-format-btn" data-format="full">Full</button>
								<button class="dgv-format-btn" data-format="plain">Plain</button>
							</div>
						</div>

						<input type="text" class="dgv-pivot-search"
						       placeholder="Search account…" />
					</div>

					<div id="pivot-grid"></div>
				</section>

			</main>

			<footer class="rgi-footer" role="contentinfo">
				<div class="rgi-footer-confidential">
					Confidential — for internal management use only
				</div>
				<div class="rgi-footer-provider">
					<span class="rgi-footer-eyebrow">Provided by</span>
					<img class="rgi-footer-logo"
					     src="/assets/dux_groupview/images/dux_digitech_navy.svg"
					     alt="Dux Digitech" />
				</div>
			</footer>

		</div>
	`);

	const SCOPE_STORAGE_KEY = 'dgv_cockpit_scope_v1';
	const SCOPE_STORAGE_VERSION = 1;
	const DEPTH_STORAGE_KEY = 'dgv_cockpit_depth_v1';
	const DEPTH_DEFAULT = 3;
	const FORMAT_STORAGE_KEY = 'dgv_cockpit_format_v1';
	const FORMAT_DEFAULT = 'crore';
	// Phase 4 commit 2.5: format pill grew a 'plain' option which replaces
	// the standalone heatmap toggle (removed). 'plain' renders raw numbers
	// without the Cr/L abbreviation suffix.
	const FORMAT_VALUES = ['crore', 'lakh', 'full', 'plain'];

	let currentDate = null;
	let lastAgeSeconds = null;       // last polled age, used to compose caption
	let agePollHandle = null;
	let pivotGrid = null;
	let trustSelector = null;
	let depthSetting = loadDepthFromStorage();
	let formatSetting = loadFormatFromStorage();
	// `scopeCompanies` holds the currently-applied scope as an array of
	// company names, OR null to mean "all companies the user can see".
	let scopeCompanies = loadScopeFromStorage();
	let scopeUniverse = [];          // every company this user can see
	let scopeTrustAbbrs = [];        // abbreviations of the trusts in scope (for caption)
	let trustList = [];              // cached trust definitions from get_scope_options

	// Focus mode (Phase 4 commit 5). null = cockpit pivot view; an
	// object means focus mode is active. depthBeforeFocus stashes the
	// pre-entry depth so exit restores it (per spec §4.3 / §7.1).
	// pivotHeaderObserver re-injects "Focus →" buttons when pivot
	// re-renders headers (e.g. on trust collapse/expand or depth change).
	let focusMode = null;            // {type: 'company'|'trust', value, depthBeforeFocus}
	let pivotHeaderObserver = null;

	bootstrap();
	checkSyntheticPreview();

	function checkSyntheticPreview() {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.cockpit.get_seed_state',
			callback: function(r) {
				const banner = document.getElementById('dux-synthetic-banner');
				if (!banner) return;
				banner.hidden = !(r && r.message && r.message.is_synthetic_preview);
			},
		});
	}

	function bootstrap() {
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
				$('#rgi-tier-primary').html(emptyState('No snapshots yet. Run a refresh first.'));
				return;
			}

			trustList = scopeOptions.trusts || [];
			scopeUniverse = [];
			trustList.forEach(t => {
				(t.companies || []).forEach(c => scopeUniverse.push(c));
			});

			scopeCompanies = reconcileScope(scopeCompanies, scopeUniverse);

			if (scopeCompanies === null) {
				scopeCompanies = applySmartDefaultScope(
					scopeOptions.trusts || [], scopeUniverse
				);
			}

			updateScopeCaption(scopeOptions.trusts || []);
			mountTrustSelector(scopeOptions.trusts || []);

			populateDateSelect(dates);
			currentDate = dates[0];
			$('.dgv-date-select').val(currentDate);
			updateHeaderDate(currentDate);

			loadCards(currentDate);
			loadAge(currentDate);
			loadHeadline(currentDate);
			loadPivot(currentDate);
			wirePivotControls();
			wireFocusModeChrome();

			// Direct-URL entry: a /app/groupview?focus=GHRCE link boots
			// straight into focus mode after the cockpit's normal load
			// (so the underlying state is intact for exit). Per spec §3.3.
			const focusFromUrl = parseFocusFromUrl();
			if (focusFromUrl) {
				enterFocusMode(focusFromUrl.type, focusFromUrl.value, true);
			}

			if (agePollHandle) clearInterval(agePollHandle);
			agePollHandle = setInterval(() => loadAge(currentDate), 30000);
		});
	}

	// -----------------------------------------------------------------
	// Trust selector mount
	// -----------------------------------------------------------------

	function mountTrustSelector(trusts) {
		const triggerEl = document.getElementById('dgv-scope-pill');
		if (!triggerEl || !window.DuxTrustSelector) return;
		if (trustSelector) {
			try { trustSelector.destroy(); } catch (e) { /* swallow */ }
		}
		trustSelector = new window.DuxTrustSelector(triggerEl, {
			trusts: trusts,
			initialSelection: scopeCompanies || scopeUniverse,
			onApply: function(selected, isAll) {
				scopeCompanies = isAll ? null : selected;
				saveScopeToStorage(scopeCompanies);
				updateScopeCaption(trustList);
				// Spec §8.5: trust selector during company focus auto-
				// exits focus mode (the focused company may no longer be
				// in the new trust scope, leaving the focus banner
				// incoherent). Trust focus is locked, so this branch
				// never fires for trust focus.
				if (focusMode && focusMode.type === 'company') {
					exitFocusMode(false);
				}
				dimAffectedSections(true);
				loadCards(currentDate);
				loadHeadline(currentDate);
				loadPivot(currentDate);
			},
			onCancel: function() { /* selector handles state */ },
		});
		applyFocusModeToTrustSelector();
	}

	function dimAffectedSections(on) {
		$('#rgi-tier-primary, #rgi-tier-secondary').toggleClass('dgv-loading-dim', !!on);
		$('#pivot-grid').toggleClass('dgv-loading-dim', !!on);
	}

	function reconcileScope(saved, universe) {
		if (!saved || !saved.length) return null;
		const universeSet = new Set(universe);
		const reconciled = saved.filter(c => universeSet.has(c));
		if (!reconciled.length) return null;
		if (reconciled.length === universe.length) return null;
		return reconciled;
	}

	function pickLargestTrust(trusts) {
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
		const largest = pickLargestTrust(trusts);
		if (!largest) return null;
		if (largest.companies.length >= universe.length) return null;
		return [...largest.companies];
	}

	// -----------------------------------------------------------------
	// Storage helpers (scope, depth, format)
	// -----------------------------------------------------------------

	function loadScopeFromStorage() {
		try {
			const raw = window.localStorage.getItem(SCOPE_STORAGE_KEY);
			if (!raw) return null;
			const parsed = JSON.parse(raw);
			if (!parsed || parsed.version !== SCOPE_STORAGE_VERSION) return null;
			if (!Array.isArray(parsed.selected_companies)) return null;
			return parsed.selected_companies;
		} catch (e) { return null; }
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
		} catch (e) { /* storage unavailable; accept */ }
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
		try { window.localStorage.setItem(DEPTH_STORAGE_KEY, String(value)); }
		catch (e) { /* ignore */ }
	}

	function syncDepthButtons() {
		const $btns = $('.dgv-depth-btn');
		$btns.removeClass('active');
		const target = String(depthSetting);
		$btns.filter(function() {
			return String($(this).data('depth')) === target;
		}).addClass('active');
	}

	function loadFormatFromStorage() {
		try {
			const raw = window.localStorage.getItem(FORMAT_STORAGE_KEY);
			if (raw && FORMAT_VALUES.indexOf(raw) !== -1) return raw;
		} catch (e) { /* fall through */ }
		return FORMAT_DEFAULT;
	}

	function saveFormatToStorage(value) {
		try { window.localStorage.setItem(FORMAT_STORAGE_KEY, String(value)); }
		catch (e) { /* ignore */ }
	}

	function syncFormatButtons() {
		const $btns = $('.dgv-format-btn');
		$btns.removeClass('active');
		$btns.filter(function() {
			return String($(this).data('format')) === formatSetting;
		}).addClass('active');
	}

	// -----------------------------------------------------------------
	// Header rendering
	// -----------------------------------------------------------------

	function populateDateSelect(dates) {
		const $sel = $('.dgv-date-select').empty();
		dates.forEach(d => $sel.append(`<option value="${d}">${formatDate(d)}</option>`));
		$sel.on('change', function() {
			currentDate = $(this).val();
			updateHeaderDate(currentDate);
			loadCards(currentDate);
			loadAge(currentDate);
			loadHeadline(currentDate);
			loadPivot(currentDate);
		});
	}

	function updateHeaderDate(iso) {
		const el = document.getElementById('rgi-header-date');
		if (!el) return;
		el.textContent = formatDateLong(iso);
	}

	function updateScopeCaption(trusts) {
		// Compute the trust abbreviations represented by the current scope.
		const inScope = new Set(scopeCompanies || scopeUniverse);
		const abbrs = [];
		let companiesInScope = 0;
		(trusts || []).forEach(t => {
			const matches = (t.companies || []).filter(c => inScope.has(c)).length;
			if (matches > 0) {
				abbrs.push(t.abbr || t.id || '?');
				companiesInScope += matches;
			}
		});
		scopeTrustAbbrs = abbrs;
		// Update the trust-pill summary too.
		const summary = abbrs.length === 0
			? 'All companies'
			: (abbrs.length <= 3
				? `${abbrs.join(', ')}`
				: `${abbrs.length} trusts`);
		$('.dgv-scope-pill-summary').text(summary);

		// Caption on the right: "<abbrs> · N companies · synced X ago"
		renderHeaderCaption(companiesInScope || (scopeUniverse || []).length);
	}

	function renderHeaderCaption(companyCount) {
		const el = document.getElementById('rgi-header-caption');
		if (!el) return;
		const parts = [];
		if (scopeTrustAbbrs && scopeTrustAbbrs.length) {
			parts.push(scopeTrustAbbrs.length <= 3
				? scopeTrustAbbrs.join(', ')
				: `${scopeTrustAbbrs.length} trusts`);
		} else {
			parts.push('All companies');
		}
		parts.push(`${companyCount} ${companyCount === 1 ? 'company' : 'companies'}`);
		parts.push(syncedAgeText());
		el.textContent = parts.join(' · ');
	}

	function syncedAgeText() {
		if (lastAgeSeconds === null || lastAgeSeconds === undefined) {
			return 'sync pending';
		}
		const min = Math.round(lastAgeSeconds / 60);
		if (lastAgeSeconds < 60) return 'synced just now';
		if (min < 60) return `synced ${min} ${min === 1 ? 'minute' : 'minutes'} ago`;
		const hr = Math.round(min / 60);
		if (hr < 24) return `synced ${hr} ${hr === 1 ? 'hour' : 'hours'} ago`;
		const d = Math.round(hr / 24);
		return `synced ${d} ${d === 1 ? 'day' : 'days'} ago`;
	}

	// -----------------------------------------------------------------
	// Headline section (Phase 4 commit 2.5)
	// -----------------------------------------------------------------

	function loadHeadline(snapshotDate) {
		const args = { as_of_date: snapshotDate };
		if (scopeCompanies !== null) {
			args.companies = JSON.stringify(scopeCompanies);
		}
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.cockpit.get_cockpit_headline',
			args: args,
			callback: function(r) {
				const headlineEl = document.getElementById('rgi-headline');
				const textEl = document.getElementById('rgi-headline-text');
				if (!headlineEl || !textEl) return;
				const text = (r && r.message && r.message.headline) || '';
				if (!text) {
					headlineEl.hidden = true;
					textEl.textContent = '';
					return;
				}
				textEl.textContent = text;
				headlineEl.hidden = false;
			},
		});
	}

	// -----------------------------------------------------------------
	// Pivot
	// -----------------------------------------------------------------

	function loadPivot(snapshotDate) {
		const containerEl = document.getElementById('pivot-grid');
		if (!containerEl) return;
		const args = { snapshot_date: snapshotDate, format: 'crore' };
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
				pivotGrid.setDepth(depthSetting);
				pivotGrid.setFormat(formatSetting);
				updateTbSubtitle(r.message);
			},
		});
	}

	function updateTbSubtitle(pivotData) {
		const el = document.getElementById('rgi-tb-subtitle');
		if (!el) return;
		const trusts = (pivotData && pivotData.trusts) || [];
		const cos = trusts.reduce((acc, t) => acc + ((t.companies || []).length), 0);
		el.textContent = `Account-level detail across ${cos} ${cos === 1 ? 'company' : 'companies'}.`;
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

		// Depth pill: 1 / 2 / 3 / All
		syncDepthButtons();
		$('.dgv-depth-btn').off('click').on('click', function() {
			const value = $(this).data('depth');
			depthSetting = (value === 'all') ? 'all' : parseInt(value, 10);
			saveDepthToStorage(depthSetting);
			syncDepthButtons();
			if (pivotGrid) pivotGrid.setDepth(depthSetting);
		});

		// Format pill: Cr / L / Full / Plain.
		// 'plain' renders raw numbers without the Cr/L suffix; replaces the
		// standalone heatmap toggle removed in Phase 4 commit 2.5 (Q22).
		syncFormatButtons();
		$('.dgv-format-btn').off('click').on('click', function() {
			const value = String($(this).data('format'));
			if (FORMAT_VALUES.indexOf(value) === -1) return;
			formatSetting = value;
			saveFormatToStorage(formatSetting);
			syncFormatButtons();
			if (pivotGrid) pivotGrid.setFormat(formatSetting);
		});

		// Disabled view buttons (Movement / Compare): styled but do nothing.
		$('.dgv-view-btn[disabled]').off('click').on('click', function(e) {
			e.preventDefault();
		});

		document.removeEventListener('dux-pivot-cell-click', _cellHandler);
		document.addEventListener('dux-pivot-cell-click', _cellHandler);

		// Pivot leaf-row account-name click → open the drill panel scoped
		// to that account across all in-scope companies. Group rows keep
		// their existing collapse/expand caret behaviour; this delegated
		// handler only fires on .pivot-row-leaf rows.
		const $pivot = $('#pivot-grid');
		$pivot.off('click.dgv-drill').on('click.dgv-drill',
			'tr.pivot-row-leaf .pivot-cell-label', function(e) {
			// Don't intercept clicks on the caret (collapse/expand) or
			// numeric cells (which already dispatch dux-pivot-cell-click).
			if (e.target.closest('.pivot-account-caret')) return;
			if (e.target.closest('.pivot-cell-num'))      return;
			const $row = $(this).closest('tr');
			const acctId = $row.data('account-id');
			const acctName = $row.find('.pivot-account-name').text().trim();
			if (!acctId) return;
			openDrillFromPivot(acctId, acctName);
		});
	}

	function _cellHandler(e) {
		// Numeric cell click: drill into that account × company. We open
		// the panel scoped to the single company the user clicked on.
		if (!e.detail) return;
		openDrillFromPivot(e.detail.account, e.detail.account, [e.detail.company]);
	}

	function openDrillFromPivot(accountId, accountName, companiesOverride) {
		if (!window.dgvOpenAccountDrillPanel) return;
		window.dgvOpenAccountDrillPanel({
			source: 'pivot',
			scope: { type: 'account', value: accountId },
			scope_label: accountName || accountId,
			as_of_date: currentDate,
			companies: companiesOverride
				|| (scopeCompanies === null ? null : scopeCompanies),
		});
	}

	// -----------------------------------------------------------------
	// Spotlight cards (two-tier render)
	// -----------------------------------------------------------------

	function loadCards(snapshotDate) {
		// Skeletons in both tiers on first load; subsequent loads dim in place.
		const $primary = $('#rgi-tier-primary');
		const $secondary = $('#rgi-tier-secondary');
		if (!$primary.children().length) {
			$primary.html(loadingState());
			$secondary.empty();
		}
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
				$('#rgi-tier-primary, #rgi-tier-secondary').removeClass('dgv-loading-dim');
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
				if (!r || !r.message) return;
				lastAgeSeconds = (r.message.age_seconds === undefined)
					? null : r.message.age_seconds;
				renderAgePill(r.message);
				// Refresh caption to update the synced-X-ago suffix.
				if (trustList && trustList.length) {
					updateScopeCaption(trustList);
				}
			},
		});
	}

	function renderAgePill(data) {
		const $pill = $('#dgv-age-pill');
		$pill.removeClass('fresh stale very-old');
		if (!data || data.age_seconds === null || data.age_seconds === undefined) {
			$pill.addClass('stale').text('Not synced yet');
			return;
		}
		const klass = data.age_seconds < 1800 ? 'fresh'
			: data.age_seconds < 3600 ? 'stale'
			: 'very-old';
		const ageMin = Math.round(data.age_seconds / 60);
		let label;
		if (data.age_seconds < 60) label = 'Fresh';
		else if (ageMin < 60) label = `${ageMin}m ago`;
		else if (ageMin < 1440) label = `${Math.round(ageMin / 60)}h ago`;
		else label = `${Math.round(ageMin / 1440)}d ago`;
		$pill.addClass(klass).text(label);
	}

	// -----------------------------------------------------------------
	// Tier classification + card render
	// -----------------------------------------------------------------

	function classifyTiers(cards) {
		// Primary = top 3 by abs(value) where value != 0.
		// Secondary = remaining 3.
		// If fewer than 3 cards have non-zero values, the empty cards
		// always go to secondary (per spec §Tier classification).
		const indexed = cards.map((c, i) => ({ card: c, idx: i, abs: Math.abs(Number(c.value) || 0) }));
		const nonZero = indexed.filter(e => e.abs > 0);
		const zero = indexed.filter(e => e.abs === 0);
		nonZero.sort((a, b) => b.abs - a.abs);
		const primaryEntries = nonZero.slice(0, 3);
		const secondaryEntries = nonZero.slice(3).concat(zero);
		// Preserve original card order within each tier so a returning
		// user doesn't see cards visually jump around when underlying
		// numbers change marginally.
		primaryEntries.sort((a, b) => a.idx - b.idx);
		secondaryEntries.sort((a, b) => a.idx - b.idx);
		return {
			primary: primaryEntries.map(e => e.card),
			secondary: secondaryEntries.map(e => e.card),
		};
	}

	function renderCards(cards) {
		const tiers = classifyTiers(cards);
		const $primary = $('#rgi-tier-primary').empty();
		const $secondary = $('#rgi-tier-secondary').empty();
		tiers.primary.forEach(c => $primary.append(renderCard(c, 'primary')));
		tiers.secondary.forEach(c => $secondary.append(renderCard(c, 'secondary')));
	}

	function renderCard(card, tier) {
		const isEmpty = (Number(card.value) || 0) === 0
		             && (Number(card.delta) || 0) === 0;
		const eyebrow = escape(card.label || '');
		const tierClass = tier === 'primary' ? 'rgi-tier-primary' : 'rgi-tier-secondary';
		// Chevron SVG: 14x14 right-arrow, fades in on hover via CSS.
		const chevron = `
			<span class="rgi-card-chevron" aria-hidden="true">
				<svg viewBox="0 0 14 14">
					<path d="M5 3 L9 7 L5 11" />
				</svg>
			</span>
		`;
		const $card = $(`
			<article class="rgi-spotlight-card ${tierClass}"
			         data-card-id="${escape(card.card_id)}"
			         tabindex="0"
			         role="button"
			         aria-label="Open drill panel for ${eyebrow}">
				${chevron}
				<div class="rgi-spotlight-eyebrow">${eyebrow}</div>
				<div class="rgi-spotlight-figure-row"></div>
				<div class="rgi-spotlight-delta-row"></div>
			</article>
		`);
		const $fig = $card.find('.rgi-spotlight-figure-row');
		const $delta = $card.find('.rgi-spotlight-delta-row');

		if (isEmpty) {
			$fig.replaceWith(`<div class="rgi-spotlight-empty">No activity recorded</div>`);
		} else {
			const fig = formatFigure(card.value);
			$fig.replaceWith(`
				<div class="rgi-spotlight-figure">
					<span class="rgi-spotlight-amount">₹${escape(fig.amount)}</span>
					<span class="rgi-spotlight-unit">${escape(fig.unit)}</span>
				</div>
			`);
		}

		$delta.replaceWith(renderDelta(card));

		// Click handler: stub for now -- real drill panel wires in commit 5.
		// "No activity recorded" cards are clickable too; commit 5's panel
		// renders the empty state ("no transactions in this scope").
		$card.on('click', () => dgvSpotlightCardClick(card));
		$card.on('keydown', function(e) {
			if (e.key === 'Enter' || e.key === ' ') {
				e.preventDefault();
				dgvSpotlightCardClick(card);
			}
		});
		return $card;
	}

	// Spotlight card click: open the account drill panel scoped to the
	// card's `match` predicate. account_drill.js owns the panel; this
	// dispatch just hands it the inputs.
	function dgvSpotlightCardClick(card) {
		if (!window.dgvOpenAccountDrillPanel) {
			console.warn('[dux_groupview] account_drill.js not loaded');
			return;
		}
		window.dgvOpenAccountDrillPanel({
			source: 'card',
			card_id: card.card_id,
			match: card.match,
			scope_label: card.label,
			as_of_date: currentDate,
			companies: scopeCompanies,  // null = all
		});
	}

	function formatFigure(rupeeValue) {
		// Always Cr in the spotlight figure — spec §spotlight grid.
		const v = Number(rupeeValue) || 0;
		const cr = Math.abs(v) / 10000000;
		// 1 decimal for tidy display; '−' prefix for negatives.
		const sign = v < 0 ? '−' : '';
		return { amount: `${sign}${cr.toFixed(1)}`, unit: 'Cr' };
	}

	function renderDelta(card) {
		const v = Number(card.delta) || 0;
		const prevMonthLabel = monthOfPrior(currentDate);

		// Phase 4 commit 2.5 fix 3: distinguish "prior month had ₹0"
		// from "no prior data exists at all". The API sets
		// has_prior_baseline=false when no prior cache row exists for
		// this card+scope. In that case, show "First reported this
		// month" instead of a misleading "Up ₹X Cr from <Month>".
		if (card.has_prior_baseline === false) {
			return $(`<div class="rgi-spotlight-delta flat">First reported this month</div>`);
		}

		if (v === 0) {
			return $(`<div class="rgi-spotlight-delta flat">Unchanged from ${escape(prevMonthLabel)}</div>`);
		}
		const cr = Math.abs(v) / 10000000;
		const direction = v > 0 ? 'Up' : 'Down';
		const klass = v > 0 ? 'up' : 'down';
		return $(`
			<div class="rgi-spotlight-delta ${klass}">
				${direction} ₹${cr.toFixed(1)} Cr from ${escape(prevMonthLabel)}
			</div>
		`);
	}

	function monthOfPrior(iso) {
		// iso is a snapshot date; "prior month" is the month before it.
		if (!iso) return '';
		try {
			const d = new Date(iso);
			d.setDate(1);
			d.setMonth(d.getMonth() - 1);
			return d.toLocaleDateString(undefined, { month: 'long' });
		} catch (e) { return ''; }
	}

	// -----------------------------------------------------------------
	// Misc
	// -----------------------------------------------------------------

	function loadingState() {
		return '<div class="dgv-cockpit-empty">Loading…</div>';
	}

	function emptyState(text) {
		return `<div class="dgv-cockpit-empty">${escape(text)}</div>`;
	}

	function formatDate(iso) {
		// Used for the <select> options. Long form: "6 May 2026".
		return formatDateLong(iso);
	}

	function formatDateLong(iso) {
		if (!iso) return '';
		try {
			const d = new Date(iso);
			return d.toLocaleDateString(undefined, {
				day: 'numeric', month: 'long', year: 'numeric',
			});
		} catch (e) { return iso; }
	}

	function escape(s) {
		if (s === null || s === undefined) return '';
		return frappe.utils.escape_html(String(s));
	}

	// =====================================================================
	// Focus mode (Phase 4 commit 5)
	// =====================================================================
	//
	// Triggered by:
	//   - "Focus →" button on a company column header → company focus
	//   - "Focus →" button on a trust group header     → trust focus
	//   - Direct URL with ?focus=<co> or ?focus_trust=<trust>
	//
	// Reads from the new api.focus_v1.get_focused_view endpoint (snapshot
	// only, no GL Entry access). Renders 5 summary tiles, an account
	// hierarchy at full depth, and (for trust focus) a per-company strip.
	// Account-row clicks open the existing drill panel.
	//
	// State: `focusMode` is null (cockpit) or {type, value, depthBeforeFocus}.
	// Pre-focus depth is restored on exit.

	function parseFocusFromUrl() {
		try {
			const params = new URLSearchParams(window.location.search);
			const focusTrust = params.get('focus_trust');
			const focus = params.get('focus');
			// Mutually exclusive per spec §5; prefer trust on conflict.
			if (focusTrust && focus) {
				console.warn('[dux_groupview] focus and focus_trust both present; using focus_trust');
				return { type: 'trust', value: focusTrust };
			}
			if (focusTrust) return { type: 'trust', value: focusTrust };
			if (focus) return { type: 'company', value: focus };
		} catch (e) { /* IE-no-URLSearchParams; surface nothing */ }
		return null;
	}

	function buildFocusUrl(type, value) {
		const params = new URLSearchParams(window.location.search);
		params.delete('focus');
		params.delete('focus_trust');
		if (type === 'trust') params.set('focus_trust', value);
		else if (type === 'company') params.set('focus', value);
		const qs = params.toString();
		return window.location.pathname + (qs ? '?' + qs : '');
	}

	function buildExitUrl() {
		const params = new URLSearchParams(window.location.search);
		params.delete('focus');
		params.delete('focus_trust');
		const qs = params.toString();
		return window.location.pathname + (qs ? '?' + qs : '');
	}

	function buildFocusedViewCsvUrl(scopeType, scopeValue, asOfDate) {
		const base = '/api/method/dux_groupview.dux_groupview.api.focus_v1.export_focused_view_csv';
		const params = new URLSearchParams({
			scope_type: scopeType,
			scope_value: scopeValue,
			as_of_date: asOfDate,
		});
		return `${base}?${params.toString()}`;
	}

	function enterFocusMode(scopeType, scopeValue, fromUrl) {
		focusMode = {
			type: scopeType,
			value: scopeValue,
			depthBeforeFocus: depthSetting,
		};

		// pushState only when the entry was user-initiated (a button
		// click). Direct-URL entry: the URL already matches; skip
		// pushState so back-button doesn't bounce to a phantom
		// pre-cockpit state.
		if (!fromUrl) {
			try {
				history.pushState(
					{ focus: { type: scopeType, value: scopeValue } },
					'',
					buildFocusUrl(scopeType, scopeValue)
				);
			} catch (e) { /* swallow */ }
		}

		applyFocusModeToCockpit(true);
		applyFocusModeToTrustSelector();
		loadFocusedView(scopeType, scopeValue);

		// Scroll to the top of the focused view. Frappe Desk pages
		// scroll inside an internal container -- not window/documentElement
		// -- so window.scrollTo(0, 0) is a no-op here. scrollIntoView
		// walks up the DOM to find whichever ancestor is the actual
		// scroller, so it works regardless of the desk's chrome.
		const focusEl = document.getElementById('rgi-focus');
		if (focusEl) focusEl.scrollIntoView({ block: 'start', behavior: 'auto' });
	}

	function exitFocusMode(viaPopState) {
		if (!focusMode) return;
		const restored = focusMode.depthBeforeFocus;

		focusMode = null;

		if (!viaPopState) {
			// User clicked × or back-to-cockpit. Drop focus params from
			// URL via replaceState so we don't add a redundant history
			// entry. (pushState would mean the next back-button press
			// re-enters focus mode -- confusing.)
			try {
				history.replaceState(
					{},
					'',
					buildExitUrl()
				);
			} catch (e) { /* swallow */ }
		}

		// Restore the pre-focus depth (per spec §4.3 / §7.1).
		depthSetting = restored;
		saveDepthToStorage(depthSetting);
		syncDepthButtons();
		if (pivotGrid) pivotGrid.setDepth(depthSetting);

		applyFocusModeToCockpit(false);
		applyFocusModeToTrustSelector();

		// Drop the user back at the trial balance section so they land
		// near where they came from (the pivot column they clicked
		// Focus on). Exact-pixel restoration would require capturing
		// the desk's internal scroll container's scrollTop on entry --
		// out of scope; scrollIntoView on rgi-tb is close enough for v1.
		// requestAnimationFrame defers until rgi-tb has laid out.
		window.requestAnimationFrame(() => {
			const tb = document.querySelector('.rgi-tb');
			if (tb) tb.scrollIntoView({ block: 'start', behavior: 'auto' });
		});
	}

	function applyFocusModeToCockpit(focused) {
		// Hide cockpit body sections during focus; show focus container.
		// Header (logo + scope pill + date + age) stays visible always
		// per spec §7.1.
		const $headline = $('#rgi-headline');
		const $spotlight = $('#rgi-spotlight');
		const $tb = $('.rgi-tb');
		const $focus = $('#rgi-focus');

		if (focused) {
			$headline.attr('data-was-hidden', $headline.prop('hidden') ? '1' : '0');
			$headline.prop('hidden', true);
			$spotlight.prop('hidden', true);
			$tb.prop('hidden', true);
			$focus.prop('hidden', false);
			$('body').addClass('dgv-focus-active');
		} else {
			// Restore: headline only un-hides if it was visible before.
			const headlineWasHidden = $headline.attr('data-was-hidden') === '1';
			$headline.prop('hidden', !!headlineWasHidden);
			$headline.removeAttr('data-was-hidden');
			$spotlight.prop('hidden', false);
			$tb.prop('hidden', false);
			$focus.prop('hidden', true);
			$('body').removeClass('dgv-focus-active');
		}
	}

	function applyFocusModeToTrustSelector() {
		// Spec §8.5:
		//   trust focus -> selector LOCKED (greyed, tooltip)
		//   company focus -> selector LIVE (auto-exit on change handled
		//                    in mountTrustSelector callback)
		//   no focus -> selector LIVE
		const $pill = $('#dgv-scope-pill');
		if (focusMode && focusMode.type === 'trust') {
			$pill.prop('disabled', true)
			     .attr('aria-disabled', 'true')
			     .attr('title', 'Trust selector locked in focus mode')
			     .addClass('dgv-locked');
		} else {
			$pill.prop('disabled', false)
			     .removeAttr('aria-disabled')
			     .removeAttr('title')
			     .removeClass('dgv-locked');
		}
	}

	function loadFocusedView(scopeType, scopeValue) {
		// Skeleton state.
		const $tiles = $('#dgv-focus-tiles').html(
			'<div class="dgv-focus-loading">Loading…</div>'
		);
		$('#dgv-focus-trust-strip').prop('hidden', true).empty();
		$('#dgv-focus-accounts').empty();

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.focus_v1.get_focused_view',
			args: {
				scope_type: scopeType,
				scope_value: scopeValue,
				as_of_date: currentDate,
			},
			callback: function(r) {
				if (!r || !r.message) {
					$tiles.html(
						'<div class="dgv-focus-error">Unable to load focused view.</div>'
					);
					return;
				}
				renderFocusedView(r.message);
			},
			error: function() {
				$tiles.html(
					'<div class="dgv-focus-error">Unable to load focused view.</div>'
				);
			},
		});
	}

	function renderFocusedView(payload) {
		// Update banner labels.
		const isTrust = payload.scope.type === 'trust';
		$('#dgv-focus-scope-name').text(payload.scope.value);
		const coCount = (payload.scope.companies || []).length;
		const sub = isTrust
			? `${coCount} ${coCount === 1 ? 'company' : 'companies'} · as of ${formatDateLong(payload.as_of_date)}`
			: `as of ${formatDateLong(payload.as_of_date)}`;
		$('#dgv-focus-scope-sub').text(sub);

		// Tiles.
		renderFocusTiles(payload.summary_tiles);

		// Per-company strip (trust focus only).
		if (isTrust) {
			renderFocusTrustStrip(payload);
			$('#dgv-focus-trust-strip').prop('hidden', false);
		} else {
			$('#dgv-focus-trust-strip').prop('hidden', true).empty();
		}

		// Accounts table.
		renderFocusAccounts(payload.accounts || [], payload.scope.companies);
	}

	function renderFocusTiles(tiles) {
		const order = [
			['ASSETS',      'assets',      tiles.assets],
			['LIABILITIES', 'liabilities', tiles.liabilities],
			['INCOME',      'income',      tiles.income],
			['EXPENSES',    'expenses',    tiles.expenses],
			['NET SURPLUS', 'net_surplus', tiles.net_surplus],
		];
		const $row = $('#dgv-focus-tiles').empty();
		order.forEach(([label, key, value]) => {
			const v = Number(value) || 0;
			const klass = v > 0 ? 'dgv-focus-tile-positive'
			           : v < 0 ? 'dgv-focus-tile-negative'
			           : 'dgv-focus-tile-zero';
			$row.append(`
				<div class="dgv-focus-tile ${klass}" data-tile-key="${escape(key)}">
					<div class="dgv-focus-tile-label">${escape(label)}</div>
					<div class="dgv-focus-tile-value">${escape(formatTileValue(v))}</div>
				</div>
			`);
		});
	}

	function renderFocusTrustStrip(payload) {
		// HALT 5.2's get_focused_view does not yet return per-company
		// breakdowns inline (per the brief: "Companies list returned in
		// response so UI can render the per-company strip"). For HALT
		// 5.3 we render company-name pills for each company in the
		// trust; per-company balance is fetched in HALT 5.4 along with
		// CSV export, OR resolved lazily here via per-company calls.
		//
		// To stay within HALT 5.3's scope and avoid blocking on a HALT
		// 5.2 response-shape change, we kick off one
		// get_focused_view(company) call per company in the trust and
		// render the Net Surplus contribution per company. Cost is
		// bounded (≤13 companies × <400ms = <5s end-to-end, parallelised
		// via Promise.all so wall-clock is one round-trip).
		const $strip = $('#dgv-focus-trust-strip').empty();
		const companies = payload.scope.companies || [];
		if (!companies.length) return;

		// Loading placeholders so the strip occupies space immediately.
		companies.forEach(co => {
			$strip.append(`
				<div class="dgv-focus-strip-cell" data-company="${escape(co)}">
					<div class="dgv-focus-strip-name">${escape(co)}</div>
					<div class="dgv-focus-strip-value">…</div>
				</div>
			`);
		});

		// Click handler to swap to per-company focus.
		$strip.off('click.dgv-focus').on('click.dgv-focus',
			'.dgv-focus-strip-cell', function() {
				const co = $(this).data('company');
				if (!co) return;
				exitFocusMode(false);
				enterFocusMode('company', String(co), false);
			});

		// Fetch per-company net surplus in parallel.
		const promises = companies.map(co => new Promise((resolve) => {
			frappe.call({
				method: 'dux_groupview.dux_groupview.api.focus_v1.get_focused_view',
				args: {
					scope_type: 'company',
					scope_value: co,
					as_of_date: currentDate,
				},
				callback: (r) => {
					const ns = (r && r.message && r.message.summary_tiles &&
					            r.message.summary_tiles.net_surplus) || 0;
					resolve({ company: co, net_surplus: ns });
				},
				error: () => resolve({ company: co, net_surplus: 0 }),
			});
		}));

		Promise.all(promises).then(results => {
			results.forEach(({ company, net_surplus }) => {
				const v = Number(net_surplus) || 0;
				const klass = v > 0 ? 'dgv-focus-strip-positive'
				           : v < 0 ? 'dgv-focus-strip-negative'
				           : 'dgv-focus-strip-zero';
				const $cell = $strip.find(
					`.dgv-focus-strip-cell[data-company="${cssEscape(company)}"]`
				);
				$cell.addClass(klass);
				$cell.find('.dgv-focus-strip-value').text(formatTileValue(v));
			});
		});
	}

	function renderFocusAccounts(accounts, companies) {
		const $body = $('#dgv-focus-accounts').empty();
		if (!accounts.length) {
			$body.append(`
				<div class="dgv-focus-empty">
					No data for this date.
				</div>
			`);
			return;
		}
		const $table = $(`
			<table class="dgv-focus-table">
				<thead>
					<tr>
						<th class="dgv-focus-th-account">Account</th>
						<th class="dgv-focus-th-type">Type</th>
						<th class="dgv-focus-th-balance">Balance</th>
					</tr>
				</thead>
				<tbody></tbody>
			</table>
		`);
		const $tbody = $table.find('tbody');
		accounts.forEach(a => {
			const depth = Math.max(0, Number(a.depth) || 0);
			const padPx = 12 + depth * 12;
			const isGroup = !!a.is_group;
			const value = Number(a.balance) || 0;
			const klass = isGroup ? 'dgv-focus-row-group' : 'dgv-focus-row-leaf';
			const balanceCell = isGroup
				? '<td class="dgv-focus-balance"></td>'
				: `<td class="dgv-focus-balance ${value < 0 ? 'dgv-focus-negative' : ''}">${escape(formatRupeesIndianLocal(value))}</td>`;
			const $row = $(`
				<tr class="${klass}"
				    data-account-name="${escape(a.account_name)}"
				    data-full-name="${escape(a.name)}">
					<td class="dgv-focus-account" style="padding-left: ${padPx}px;">
						${a.has_children ? '<span class="dgv-focus-chevron">▾</span>' : '<span class="dgv-focus-chevron-spacer"></span>'}
						<span class="dgv-focus-account-name">${escape(a.account_name)}</span>
					</td>
					<td class="dgv-focus-type">${escape(a.root_type || '')}</td>
					${balanceCell}
				</tr>
			`);
			$tbody.append($row);
		});
		$body.append($table);

		// Wire leaf-row click → drill panel. The drill panel's
		// `scope.value` is the stripped `account_name` form (matches
		// the pivot's existing drill-from-row contract); the
		// `_resolve_scope_to_leaves` helper resolves it back to full
		// company-suffixed names via JOIN against tabAccount.
		$body.off('click.dgv-focus-drill').on('click.dgv-focus-drill',
			'tr.dgv-focus-row-leaf', function() {
				const acctName = $(this).data('account-name');
				if (!acctName) return;
				if (!window.dgvOpenAccountDrillPanel) return;
				window.dgvOpenAccountDrillPanel({
					source: 'pivot',
					scope: { type: 'account', value: acctName },
					scope_label: acctName,
					as_of_date: currentDate,
					companies: companies,
				});
			});
	}

	function formatTileValue(rupees) {
		// Respects the cockpit's current format setting. Crore (default),
		// Lakh, Full (Indian-grouped raw), Plain (Indian-grouped raw -
		// same shape as Full at this level). Negatives render with a
		// leading Unicode minus sign; the terracotta tile colour
		// reinforces the sign visually.
		const v = Number(rupees) || 0;
		const sign = v < 0 ? '−' : '';
		const abs = Math.abs(v);
		if (formatSetting === 'crore') {
			return `₹${sign}${(abs / 10000000).toFixed(2)} Cr`;
		}
		if (formatSetting === 'lakh') {
			return `₹${sign}${(abs / 100000).toFixed(2)} L`;
		}
		// 'full' or 'plain' -> Indian grouping
		return `₹${sign}${formatRupeesIndianLocal(abs)}`;
	}

	function formatRupeesIndianLocal(value) {
		// Mirrors pivot_grid.js's formatIndian (Indian thousands grouping
		// with 2 decimal places). Inlined here so the focus rendering
		// has zero coupling to the pivot's internals.
		const v = Number(value) || 0;
		if (v === 0) return '0.00';
		const abs = Math.abs(v).toFixed(2);
		const [whole, decimal] = abs.split('.');
		const lastThree = whole.slice(-3);
		const rest = whole.slice(0, -3);
		const restWithCommas = rest
			? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')
			: '';
		const formatted = restWithCommas
			? `${restWithCommas},${lastThree}.${decimal}`
			: `${lastThree}.${decimal}`;
		return v < 0 ? `(${formatted})` : formatted;
	}

	function cssEscape(s) {
		// CSS.escape isn't in older browsers; fall back to a basic
		// quoting for attribute selectors. Conservative: only quote a
		// minimal character set used in company / trust names.
		if (window.CSS && typeof window.CSS.escape === 'function') {
			return window.CSS.escape(String(s));
		}
		return String(s).replace(/(["\\])/g, '\\$1');
	}

	// ---------------------------------------------------------------------
	// Focus-mode chrome wiring (banner buttons, popstate, header injection)
	// ---------------------------------------------------------------------

	function wireFocusModeChrome() {
		// Exit affordances.
		$('#dgv-focus-exit, #dgv-focus-close').off('click.dgv-focus-exit')
			.on('click.dgv-focus-exit', function() { exitFocusMode(false); });

		// CSV export. Browser-level navigation triggers the download
		// directly because the endpoint sets type=binary +
		// content-disposition (see _set_csv_response).
		$('#dgv-focus-export').off('click.dgv-focus-export')
			.on('click.dgv-focus-export', function() {
				if (!focusMode) return;
				window.location.href = buildFocusedViewCsvUrl(
					focusMode.type, focusMode.value, currentDate
				);
			});

		// Browser back: if the prior entry was the cockpit, popstate
		// arrives with state===null (or our own pre-focus snapshot).
		// Either way, we want to drop into cockpit view. We also want
		// pushState/forward to re-enter focus.
		window.addEventListener('popstate', function() {
			const fromUrl = parseFocusFromUrl();
			if (fromUrl && !focusMode) {
				enterFocusMode(fromUrl.type, fromUrl.value, true);
			} else if (!fromUrl && focusMode) {
				exitFocusMode(true);
			} else if (fromUrl && focusMode &&
			           (fromUrl.type !== focusMode.type ||
			            fromUrl.value !== focusMode.value)) {
				// Forward/back across two different focus URLs.
				exitFocusMode(true);
				enterFocusMode(fromUrl.type, fromUrl.value, true);
			}
		});

		// Watch the pivot container for re-renders so we can re-inject
		// "Focus →" buttons. The pivot grid lazily creates its <thead>
		// only after the first frappe.call returns, so attaching to
		// #pivot-head directly would race the initial render. Watching
		// the always-present #pivot-grid container's subtree catches
		// the initial mount AND every subsequent re-render (trust
		// collapse/expand, depth change, full refresh).
		const containerEl = document.getElementById('pivot-grid');
		if (containerEl && window.MutationObserver) {
			if (pivotHeaderObserver) pivotHeaderObserver.disconnect();
			pivotHeaderObserver = new MutationObserver(() => injectFocusButtons());
			pivotHeaderObserver.observe(containerEl, {
				childList: true,
				subtree: true,
			});
		}
		// Initial inject (covers the case where the pivot is already
		// rendered before this wiring runs).
		injectFocusButtons();
	}

	function injectFocusButtons() {
		// Idempotent: skip a th that already has a button.
		const headEl = document.getElementById('pivot-head');
		if (!headEl) return;

		// Trust headers: only when the trust is expanded (colspan > 1).
		// Collapsed trusts show as a 1-col abbr badge -- not enough
		// space for the Focus button, and the user typically expands
		// before focusing.
		headEl.querySelectorAll('.pivot-trust-head').forEach(el => {
			if (el.querySelector('.dgv-focus-trigger')) return;
			const colspan = parseInt(el.getAttribute('colspan') || '1', 10);
			if (colspan <= 1) return;
			const trustId = el.getAttribute('data-trust-id') || '';
			const trust = (trustList || []).find(
				t => String(t.id) === String(trustId)
			);
			if (!trust) return;
			const btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'dgv-focus-trigger dgv-focus-trigger-trust';
			btn.textContent = 'Focus →';
			btn.title = 'View this trust at full depth';
			btn.setAttribute('data-focus-type', 'trust');
			btn.setAttribute('data-focus-value', trust.name || trust.id);
			btn.addEventListener('click', function(e) {
				e.stopPropagation();
				enterFocusMode('trust', trust.name || trust.id, false);
			});
			el.appendChild(btn);
		});

		// Company headers: simpler. The company name is in the title
		// attribute (set by pivot_grid._renderHeader at line ~278).
		headEl.querySelectorAll('.pivot-company-head').forEach(el => {
			if (el.classList.contains('pivot-collapsed-cell')) return;
			if (el.querySelector('.dgv-focus-trigger')) return;
			const company = el.getAttribute('title') || el.textContent.trim();
			if (!company) return;
			const btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'dgv-focus-trigger dgv-focus-trigger-company';
			btn.textContent = 'Focus →';
			btn.title = 'View this company at full depth';
			btn.setAttribute('data-focus-type', 'company');
			btn.setAttribute('data-focus-value', company);
			btn.addEventListener('click', function(e) {
				e.stopPropagation();
				enterFocusMode('company', company, false);
			});
			el.appendChild(btn);
		});
	}
};
