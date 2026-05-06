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
				dimAffectedSections(true);
				loadCards(currentDate);
				loadHeadline(currentDate);
				loadPivot(currentDate);
			},
			onCancel: function() { /* selector handles state */ },
		});
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
};
