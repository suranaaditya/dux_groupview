/*
 * dux_groupview — GL drill full page (Phase 4 commit 4, HALT 1)
 *
 * URL: /app/gl-drill?scope=<scope_id>&as_of=<iso>&companies=<csv>
 *      &party=<name>&party_type=<type>
 *      &page=<n>&page_size=<n>&sort=<key>
 *
 *   scope_id forms (re-used from /app/account-drill — encoded by
 *   buildDrillUrl in public/js/account_drill.js):
 *     - <card_id>           — spotlight card scope
 *     - account:<acct_name> — pivot leaf row click
 *     - subtree:<acct_name> — subtree drill
 *
 *   sort: posting_date_asc (default — natural ledger order so the
 *         running balance reads as accumulator down the column) |
 *         posting_date_desc | amount_desc | amount_asc
 *   page_size offerings: 50, 100 (default), 250, 1000
 *
 * The page renders entirely from URL parameters. The Python controller
 * is a stub. All data comes from `gl_drill_v1.get_gl_entries`.
 *
 * Component reuse: trend chart, hero, by-company table all live in
 * window.dgvDrill (account_drill.js). This page reuses the format
 * helpers (formatRupeesIndian, formatLongDate, escapeHtml) plus
 * renders its own table + pagination + group-divider chrome.
 *
 * Browser-history pagination: pushState on every prev/next/sort/
 * page-size change so back/forward navigates pagination history.
 */

frappe.pages['gl-drill'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'GL drill',
		single_column: true,
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$body.html(`
		<div class="dgv-gl-page" id="dgv-gl-page">

			<header class="dgv-drill-page-breadcrumb">
				<a href="/app/groupview">Cockpit</a>
				<span class="dgv-bc-sep">/</span>
				<a id="dgv-gl-bc-account" href="#">Account drill</a>
				<span class="dgv-bc-sep">/</span>
				<span class="dgv-bc-current" id="dgv-gl-bc-current">GL entries</span>
			</header>

			<section class="dgv-drill-page-hero" id="dgv-gl-hero">
				<div class="dgv-drill-page-hero-meta">
					<div class="dgv-drill-eyebrow">GL drill</div>
					<h2 class="dgv-drill-title" id="dgv-gl-title">…</h2>
					<div class="dgv-drill-scope-sub" id="dgv-gl-sub"></div>
				</div>
				<div class="dgv-gl-totals" id="dgv-gl-totals"></div>
			</section>

			<div class="dgv-gl-banner" id="dgv-gl-fanout-banner" hidden></div>
			<div class="dgv-gl-banner dgv-gl-banner-truncated" id="dgv-gl-truncate-banner" hidden></div>

			<section class="dgv-gl-toolbar" id="dgv-gl-toolbar">
				<div class="dgv-gl-toolbar-left">
					<label class="dgv-gl-toolbar-field">
						<span>Sort</span>
						<select id="dgv-gl-sort">
							<option value="posting_date_asc">Date (oldest first)</option>
							<option value="posting_date_desc">Date (newest first)</option>
							<option value="amount_desc">Amount (largest first)</option>
							<option value="amount_asc">Amount (smallest first)</option>
						</select>
					</label>
					<label class="dgv-gl-toolbar-field">
						<span>Per page</span>
						<select id="dgv-gl-page-size">
							<option value="50">50</option>
							<option value="100">100</option>
							<option value="250">250</option>
							<option value="1000">1000</option>
						</select>
					</label>
				</div>
				<div class="dgv-gl-toolbar-right">
					<button class="dgv-gl-export-btn" id="dgv-gl-export" type="button" title="Download all matching entries as CSV (50K cap)">
						Export CSV
					</button>
					<span class="dgv-gl-page-info" id="dgv-gl-page-info"></span>
					<button class="dgv-gl-pager-btn" id="dgv-gl-prev" type="button" disabled>← Prev</button>
					<button class="dgv-gl-pager-btn" id="dgv-gl-next" type="button" disabled>Next →</button>
				</div>
			</section>

			<!-- HALT 2.5 filter row + chips. Filters render inline at -->
			<!-- ≥800px; the row turns into a "Filters" button + bottom -->
			<!-- sheet at ≤800px (CSS-driven via media query). -->
			<section class="dgv-gl-filter-row" id="dgv-gl-filter-row" hidden>
				<div class="dgv-gl-filter-row-inline" id="dgv-gl-filter-row-inline">
					<!-- populated by renderFilters() -->
				</div>
				<button class="dgv-gl-filter-mobile-trigger" id="dgv-gl-filter-mobile-trigger" type="button">
					<span>Filters</span>
					<span class="dgv-gl-filter-mobile-badge" id="dgv-gl-filter-mobile-badge" hidden>0</span>
				</button>
			</section>
			<section class="dgv-gl-filter-chips" id="dgv-gl-filter-chips" hidden>
				<!-- populated by renderFilterChips() -->
			</section>

			<!-- Bottom sheet for ≤800px viewport. Hidden by default. -->
			<div class="dgv-gl-bottom-sheet" id="dgv-gl-bottom-sheet" hidden>
				<div class="dgv-gl-bottom-sheet-backdrop" id="dgv-gl-bottom-sheet-backdrop"></div>
				<div class="dgv-gl-bottom-sheet-panel">
					<header class="dgv-gl-bottom-sheet-head">
						<h3>Filters</h3>
						<button class="dgv-gl-bottom-sheet-close" id="dgv-gl-bottom-sheet-close" type="button" aria-label="Close">×</button>
					</header>
					<div class="dgv-gl-bottom-sheet-body" id="dgv-gl-bottom-sheet-body">
						<!-- populated by renderFilters() -->
					</div>
					<footer class="dgv-gl-bottom-sheet-foot">
						<button class="dgv-gl-bottom-sheet-clear" id="dgv-gl-bottom-sheet-clear" type="button">Clear all</button>
						<button class="dgv-gl-bottom-sheet-apply" id="dgv-gl-bottom-sheet-apply" type="button">Apply</button>
					</footer>
				</div>
			</div>

			<section class="dgv-gl-table-wrap" id="dgv-gl-table-wrap">
				<div class="dgv-gl-skeleton" id="dgv-gl-skeleton">
					${(() => {
						// 10-row skeleton (commit-6 HALT 6.1 category 2.b).
						// Reserves the table-row visual rhythm so when
						// real rows load the page doesn't reflow.
						let rows = '';
						for (let i = 0; i < 10; i++) {
							rows += `
								<div class="dgv-skeleton-row">
									<div class="dgv-skeleton-cell narrow"></div>
									<div class="dgv-skeleton-cell wide"></div>
									<div class="dgv-skeleton-cell"></div>
									<div class="dgv-skeleton-cell narrow"></div>
									<div class="dgv-skeleton-cell right-align"></div>
								</div>
							`;
						}
						return rows;
					})()}
				</div>
			</section>

		</div>
	`);

	// -----------------------------------------------------------------
	// State + URL parse
	// -----------------------------------------------------------------

	const state = parseAll(window.location.search);
	if (!state.scope) {
		// Treat as commit-6 HALT 6.2 category (c) "malformed scope" --
		// the URL doesn't carry a valid scope. Route through the
		// error-tile helper so the user gets a [Cockpit] button to
		// recover, not a wall of text.
		const wrap = document.getElementById('dgv-gl-table-wrap');
		if (wrap && window.dgvRenderErrorTile) {
			wrap.innerHTML = '';
			window.dgvRenderErrorTile(
				{ status: 404, responseJSON: { malformed_scope: true } },
				wrap,
				null
			);
		} else {
			showError(
				'Missing scope parameter. Open this page from a drill panel ' +
				'or account-drill page.'
			);
		}
		return;
	}
	state.resolvedAccounts = null;
	state.resolvedLabel = null;
	state.data = null;

	wireBreadcrumbToAccountDrill();

	// Initial sort/page-size dropdown values from URL.
	$('#dgv-gl-sort').val(state.sort);
	$('#dgv-gl-page-size').val(String(state.page_size));

	// -----------------------------------------------------------------
	// Wire toolbar
	// -----------------------------------------------------------------

	$('#dgv-gl-sort').on('change', function () {
		state.sort = $(this).val();
		state.page = 1;
		pushUrl();
		fetchAndRender();
	});
	$('#dgv-gl-page-size').on('change', function () {
		state.page_size = parseInt($(this).val(), 10) || 100;
		state.page = 1;
		pushUrl();
		fetchAndRender();
	});
	$('#dgv-gl-prev').on('click', function () {
		if (state.page > 1) {
			state.page -= 1;
			pushUrl();
			fetchAndRender();
		}
	});
	$('#dgv-gl-next').on('click', function () {
		state.page += 1;
		pushUrl();
		fetchAndRender();
	});
	$('#dgv-gl-export').on('click', function () {
		// Browser handles the download; the server returns a CSV
		// response with Content-Disposition: attachment.
		var url = buildExportCsvUrl();
		window.location.href = url;
	});

	// Browser back/forward integration. popstate re-parses the URL
	// and re-fetches. pushUrl() never fires popstate (only the user
	// does), so we don't loop.
	window.addEventListener('popstate', function () {
		const fresh = parseAll(window.location.search);
		state.scope = fresh.scope;
		state.as_of_date = fresh.as_of_date;
		state.companies = fresh.companies;
		state.party = fresh.party;
		state.party_type = fresh.party_type;
		state.page = fresh.page;
		state.page_size = fresh.page_size;
		state.sort = fresh.sort;
		// HALT 2.5 filter restore on back/forward.
		state.account_names = fresh.account_names;
		state.from_date = fresh.from_date;
		state.to_date = fresh.to_date;
		state.voucher_types = fresh.voucher_types;
		$('#dgv-gl-sort').val(state.sort);
		$('#dgv-gl-page-size').val(String(state.page_size));
		renderFilters();
		renderFilterChips();
		fetchAndRender();
	});

	// HALT 2.5 filter UI init.
	state.filterMetadata = null;     // {account_names, voucher_types, parties, scope_fanout}
	// Stable universe for the Companies dropdown. Captured ONCE
	// from the initial URL state.companies; never shrinks as the
	// user narrows. Bug fix: deriving options from state.companies
	// at render time meant the dropdown lost options as the user
	// applied a selection. Per spec §3.1 the URL's companies param
	// IS the universe -- to widen, the user navigates from a
	// different scope entry point.
	state.companiesUniverse = state.companies ? state.companies.slice() : null;
	bindFilterShellEvents();

	// -----------------------------------------------------------------
	// Initial fetch
	// -----------------------------------------------------------------
	// loadFilterMetadata MUST run after resolveCardScope for card-kind
	// scopes -- card scopes have state.resolvedAccounts=null until
	// resolveCardScope populates it; calling loadFilterMetadata before
	// that returns early without making the API call, leaving the
	// filter row hidden. For account/subtree scopes the metadata call
	// can run immediately (in parallel with fetchAndRender) since
	// scope.id is the lookup key.

	if (state.scope.kind === 'card') {
		resolveCardScope(state.scope.id).then(function () {
			loadFilterMetadata();
			fetchAndRender();
		});
	} else {
		loadFilterMetadata();
		fetchAndRender();
	}


	// =================================================================
	// Implementation
	// =================================================================

	function parseAll(searchString) {
		const parsed = window.dgvParseAccountDrillHash(searchString);
		// parsed: { scope: {kind, id} | null, as_of_date, companies }
		const params = new URLSearchParams(searchString || window.location.search);
		const page = parseInt(params.get('page') || '1', 10) || 1;
		let page_size = parseInt(params.get('page_size') || '100', 10) || 100;
		// Clamp to allowed offerings; silent fall-back to 100 on bad input.
		if (![50, 100, 250, 1000].includes(page_size)) page_size = 100;
		// Default sort flipped to posting_date_asc in spec v0.4 -- the
		// natural ledger order (Tally, ERPNext stock TB, QuickBooks all
		// default this way) so running balance reads as accumulator
		// down the column. posting_date_desc remains available in the
		// toolbar for "what's most recent" queries.
		const sort = params.get('sort') || 'posting_date_asc';

		// HALT 2.5 filter state -- comma-separated lists for
		// account_names / voucher_types; ISO dates for from/to.
		// Empty / missing collapse to null so the SQL branches stay off.
		function _csvOrNull(name) {
			const v = params.get(name);
			if (!v) return null;
			const list = v.split(',').map(s => s.trim()).filter(Boolean);
			return list.length ? list : null;
		}
		function _isoOrNull(name) {
			const v = params.get(name);
			if (!v) return null;
			return /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null;
		}

		return {
			scope: parsed.scope,
			as_of_date: parsed.as_of_date || frappe.datetime.get_today(),
			companies: parsed.companies,
			party: params.get('party') || null,
			party_type: params.get('party_type') || null,
			page: Math.max(1, page),
			page_size: page_size,
			sort: ['posting_date_asc', 'posting_date_desc', 'amount_desc', 'amount_asc']
				.includes(sort) ? sort : 'posting_date_asc',
			// HALT 2.5 filters -- per-scope, reset on cross-scope nav
			// because buildGlDrillUrl never emits these params.
			account_names: _csvOrNull('account_names'),
			from_date: _isoOrNull('from_date'),
			to_date: _isoOrNull('to_date'),
			voucher_types: _csvOrNull('voucher_types'),
		};
	}

	function pushUrl() {
		const params = [];
		const scopeParam = state.scope.kind === 'card'
			? state.scope.id
			: state.scope.kind + ':' + state.scope.id;
		// Param order per spec §5: scope -> envelope -> filters in
		// toolbar order -> display/pagination. Helps users skim
		// shared URLs.
		params.push('scope=' + encodeURIComponent(scopeParam));
		if (state.as_of_date) {
			params.push('as_of=' + encodeURIComponent(state.as_of_date));
		}
		if (state.companies && state.companies.length) {
			params.push('companies=' + encodeURIComponent(state.companies.join(',')));
		}
		// HALT 2.5 filters -- omit when default (null/empty) so URLs
		// stay short for the common no-filter case.
		if (state.account_names && state.account_names.length) {
			params.push('account_names=' + encodeURIComponent(state.account_names.join(',')));
		}
		if (state.from_date) {
			params.push('from_date=' + encodeURIComponent(state.from_date));
		}
		if (state.to_date) {
			params.push('to_date=' + encodeURIComponent(state.to_date));
		}
		if (state.party) {
			params.push('party=' + encodeURIComponent(state.party));
		}
		if (state.party_type) {
			params.push('party_type=' + encodeURIComponent(state.party_type));
		}
		if (state.voucher_types && state.voucher_types.length) {
			params.push('voucher_types=' + encodeURIComponent(state.voucher_types.join(',')));
		}
		// Always emit page/page_size/sort so a back-button restores
		// the prior pagination state byte-for-byte.
		params.push('page=' + state.page);
		params.push('page_size=' + state.page_size);
		params.push('sort=' + state.sort);
		const url = '/app/gl-drill?' + params.join('&');
		window.history.pushState({}, '', url);
	}

	function wireBreadcrumbToAccountDrill() {
		// Clicking "Account drill" in the breadcrumb returns to the
		// account-drill page for the same scope. Same URL builder
		// the panel uses (account_drill.js:buildDrillUrl).
		const params = [];
		const scopeParam = state.scope.kind === 'card'
			? state.scope.id
			: state.scope.kind + ':' + state.scope.id;
		params.push('scope=' + encodeURIComponent(scopeParam));
		if (state.as_of_date) {
			params.push('as_of=' + encodeURIComponent(state.as_of_date));
		}
		if (state.companies && state.companies.length) {
			params.push('companies=' + encodeURIComponent(state.companies.join(',')));
		}
		const url = '/app/account-drill?' + params.join('&');
		document.getElementById('dgv-gl-bc-account').setAttribute('href', url);
	}

	function resolveCardScope(card_id) {
		// Two-step resolution mirrors account_drill page lines 130-174.
		const isFullScope = state.companies === null;
		const method = isFullScope
			? 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards'
			: 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards_filtered';
		const args = { snapshot_date: state.as_of_date };
		if (!isFullScope) args.companies = JSON.stringify(state.companies);

		return new Promise(function (resolve) {
			frappe.call({
				method: method,
				args: args,
				callback: function (r) {
					const cards = (r && r.message) || [];
					const card = cards.find(function (c) { return c.card_id === card_id; });
					if (!card) {
						// Stale deep-link: card_id from URL doesn't
						// match any known card (commit-6 HALT 6.3
						// carryover from 6.2 review). Route through
						// the shared error tile with malformed_scope
						// so the user gets the [Cockpit] button and
						// consistent visual treatment, not plain text.
						const wrap = document.getElementById('dgv-gl-table-wrap');
						if (wrap && window.dgvRenderErrorTile) {
							wrap.innerHTML = '';
							window.dgvRenderErrorTile(
								{ status: 404, responseJSON: { malformed_scope: true } },
								wrap,
								null
							);
						} else {
							showError('Unknown spotlight card: ' + escape(card_id));
						}
						resolve();
						return;
					}
					state.resolvedLabel = card.label;
					frappe.call({
						method: 'dux_groupview.dux_groupview.api.cards_v1.resolve_match_to_accounts',
						args: {
							match: JSON.stringify(card.match),
							companies: state.companies
								? JSON.stringify(state.companies) : null,
							label: card.label,
						},
						callback: function (rr) {
							const m = (rr && rr.message) || {};
							state.resolvedAccounts = m.accounts || [];
							resolve();
						},
					});
				},
			});
		});
	}

	function buildExportCsvUrl() {
		// Build the /api/method/ URL for export_gl_entries_csv
		// honoring the same scope + filters + sort the page is
		// currently rendering. Pagination args (page / page_size)
		// are intentionally omitted -- export streams the full set
		// up to the 50K cap.
		var qs = [];
		if (state.resolvedAccounts !== null) {
			qs.push('accounts=' + encodeURIComponent(JSON.stringify(state.resolvedAccounts)));
			if (state.resolvedLabel) {
				qs.push('scope_label=' + encodeURIComponent(state.resolvedLabel));
			}
		} else if (state.scope.kind === 'account' || state.scope.kind === 'subtree') {
			qs.push('scope=' + encodeURIComponent(JSON.stringify({
				type: state.scope.kind, value: state.scope.id,
			})));
		}
		if (state.as_of_date) {
			qs.push('as_of_date=' + encodeURIComponent(state.as_of_date));
		}
		if (state.companies) {
			qs.push('companies=' + encodeURIComponent(JSON.stringify(state.companies)));
		}
		if (state.party) {
			qs.push('party=' + encodeURIComponent(state.party));
		}
		if (state.party_type) {
			qs.push('party_type=' + encodeURIComponent(state.party_type));
		}
		// HALT 2.5 filter pass-through. Export honors current filter
		// state so the CSV matches the on-screen view.
		if (state.account_names && state.account_names.length) {
			qs.push('account_names=' + encodeURIComponent(state.account_names.join(',')));
		}
		if (state.from_date) qs.push('from_date=' + encodeURIComponent(state.from_date));
		if (state.to_date)   qs.push('to_date=' + encodeURIComponent(state.to_date));
		if (state.voucher_types && state.voucher_types.length) {
			qs.push('voucher_types=' + encodeURIComponent(state.voucher_types.join(',')));
		}
		qs.push('sort=' + encodeURIComponent(state.sort));
		return '/api/method/dux_groupview.dux_groupview.api.gl_drill_v1.export_gl_entries_csv?'
			+ qs.join('&');
	}

	// =================================================================
	// HALT 2.5 -- Filter UI: state mgmt, render, multi-select component
	// =================================================================

	function loadFilterMetadata() {
		// Single fetch per page load (cached client-side until scope
		// changes -- which means a navigation, which reloads the page,
		// so the cache is implicitly per-page-instance).
		const args = {};
		if (state.resolvedAccounts !== null) {
			args.accounts = JSON.stringify(state.resolvedAccounts);
		} else if (state.scope.kind === 'account' || state.scope.kind === 'subtree') {
			args.scope = JSON.stringify({
				type: state.scope.kind, value: state.scope.id,
			});
		} else {
			return;
		}
		if (state.as_of_date) args.as_of_date = state.as_of_date;
		if (state.companies)  args.companies = JSON.stringify(state.companies);

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.gl_drill_v1.get_filter_metadata',
			args: args,
			callback: function (r) {
				state.filterMetadata = (r && r.message) || null;
				renderFilters();
				renderFilterChips();
				updateMobileBadge();
			},
		});
	}

	function bindFilterShellEvents() {
		// Mobile trigger opens the bottom sheet.
		$('#dgv-gl-filter-mobile-trigger').on('click', openBottomSheet);
		$('#dgv-gl-bottom-sheet-close').on('click', closeBottomSheet);
		$('#dgv-gl-bottom-sheet-backdrop').on('click', closeBottomSheet);
		$('#dgv-gl-bottom-sheet-clear').on('click', function () {
			clearAllFilters();
			closeBottomSheet();
		});
		$('#dgv-gl-bottom-sheet-apply').on('click', function () {
			// Drafts in the sheet are committed to URL on Apply.
			closeBottomSheet();
			applyFilterChange({});  // no-op merge -- sheet's onChange already pushed each draft
		});
		// Escape closes the bottom sheet.
		document.addEventListener('keydown', function (e) {
			if (e.key === 'Escape' && !document.getElementById('dgv-gl-bottom-sheet').hidden) {
				closeBottomSheet();
			}
		});
	}

	function openBottomSheet() {
		document.getElementById('dgv-gl-bottom-sheet').hidden = false;
		document.body.style.overflow = 'hidden';
	}
	function closeBottomSheet() {
		document.getElementById('dgv-gl-bottom-sheet').hidden = true;
		document.body.style.overflow = '';
	}

	/**
	 * Render the filter row (inline at desktop, also populates the
	 * mobile bottom sheet). Both surfaces use the same component
	 * functions; CSS media query controls visibility.
	 */
	function renderFilters() {
		const meta = state.filterMetadata;
		if (!meta) {
			document.getElementById('dgv-gl-filter-row').hidden = true;
			return;
		}
		document.getElementById('dgv-gl-filter-row').hidden = false;

		const inline = document.getElementById('dgv-gl-filter-row-inline');
		const sheet  = document.getElementById('dgv-gl-bottom-sheet-body');
		// Render twice -- once for inline (desktop), once for the
		// bottom sheet body. The two surfaces share the same
		// underlying state object, so changes in either propagate.
		inline.innerHTML = '';
		sheet.innerHTML = '';
		[inline, sheet].forEach(function (host) {
			renderFilterControls(host, meta);
		});
	}

	function renderFilterControls(host, meta) {
		// Companies (when scope spans >1 co)
		// Universe is captured ONCE at page boot (state.companiesUniverse);
		// never shrinks as the user applies narrower selections.
		const universe = state.companiesUniverse || state.companies || [];
		if (universe.length > 1) {
			const companyOptions = universe.map(function (c) {
				return { value: c, label: c };
			});
			renderMultiSelect(host, {
				key: 'company',
				label: 'Companies',
				options: companyOptions,
				selected: state.companies || universe,
				onApply: function (next) {
					// Empty -> all (per spec §3.1 "empty selection
					// treated same as all-selected"). Same coercion
					// as the server-side empty-list collapse.
					applyFilterChange({
						companies: next.length ? next : null,
					});
				},
			});
		}

		// Account name (when scope resolves to >1 unique account_name)
		if (meta.account_names && meta.account_names.length > 1) {
			const acctOptions = meta.account_names.map(function (a) {
				return {
					value: a.name,
					label: a.name + (a.company_count > 1
						? ' (' + a.company_count + ' cos)' : ''),
				};
			});
			renderMultiSelect(host, {
				key: 'account_names',
				label: 'Accounts',
				options: acctOptions,
				selected: state.account_names || [],
				onApply: function (next) {
					applyFilterChange({
						account_names: next.length ? next : null,
					});
				},
			});
		}

		// Date range (always)
		renderDateRange(host);

		// Party (when scope is_party_trackable -- we approximate
		// "trackable" by checking if the metadata returned any
		// parties at all; if there are zero parties in scope, the
		// input would be useless anyway).
		if (meta.parties && meta.parties.length > 0) {
			renderPartyInput(host, meta.parties);
		}

		// Voucher type (always available; collapsed by default
		// behind "Advanced filters")
		if (meta.voucher_types && meta.voucher_types.length > 1) {
			renderAdvancedFilters(host, meta);
		}
	}

	function renderDateRange(host) {
		const wrap = document.createElement('label');
		wrap.className = 'dgv-gl-filter-field dgv-gl-filter-daterange';
		wrap.innerHTML =
			'<span class="dgv-gl-filter-field-label">Date range</span>' +
			'<span class="dgv-gl-filter-daterange-inputs">' +
				'<input type="date" class="dgv-gl-filter-from" />' +
				'<span class="dgv-gl-filter-daterange-sep">→</span>' +
				'<input type="date" class="dgv-gl-filter-to" max="' + state.as_of_date + '" />' +
			'</span>';
		host.appendChild(wrap);
		const fromInput = wrap.querySelector('.dgv-gl-filter-from');
		const toInput   = wrap.querySelector('.dgv-gl-filter-to');
		if (state.from_date) fromInput.value = state.from_date;
		if (state.to_date)   toInput.value = state.to_date;
		fromInput.addEventListener('change', function () {
			applyFilterChange({ from_date: fromInput.value || null });
		});
		toInput.addEventListener('change', function () {
			applyFilterChange({ to_date: toInput.value || null });
		});
	}

	function renderPartyInput(host, parties) {
		const wrap = document.createElement('label');
		wrap.className = 'dgv-gl-filter-field dgv-gl-filter-party';
		// Build a datalist of suggestions (top 50 by row count from
		// the metadata endpoint). Free-text input accepted -- user
		// can type a party not in the list.
		let optionsHtml = '';
		parties.forEach(function (p) {
			optionsHtml += '<option value="' + escapeAttr(p.party) + '"></option>';
		});
		wrap.innerHTML =
			'<span class="dgv-gl-filter-field-label">Party</span>' +
			'<input type="text" class="dgv-gl-filter-party-input" ' +
			'       list="dgv-gl-party-suggestions" placeholder="Party name…" />' +
			'<datalist id="dgv-gl-party-suggestions">' + optionsHtml + '</datalist>';
		host.appendChild(wrap);
		const input = wrap.querySelector('.dgv-gl-filter-party-input');
		input.value = state.party || '';
		input.addEventListener('change', function () {
			const v = input.value.trim();
			if (!v) {
				applyFilterChange({ party: null, party_type: null });
				return;
			}
			// Look up party_type from the metadata if the user
			// picked a known party. Free-text without a match falls
			// back to "Customer" guess; if the user gets it wrong
			// they see zero results and can clear.
			const match = parties.find(function (p) { return p.party === v; });
			const ptype = match ? match.party_type : (state.party_type || 'Customer');
			applyFilterChange({ party: v, party_type: ptype });
		});
	}

	function renderAdvancedFilters(host, meta) {
		const det = document.createElement('details');
		det.className = 'dgv-gl-filter-advanced';
		// Auto-open if voucher_types is non-default in URL on load
		if (state.voucher_types && state.voucher_types.length) {
			det.open = true;
		}
		det.innerHTML = '<summary>Advanced filters</summary>';
		host.appendChild(det);
		const vtOptions = meta.voucher_types.map(function (vt) {
			return { value: vt, label: vt };
		});
		renderMultiSelect(det, {
			key: 'voucher_types',
			label: 'Voucher type',
			options: vtOptions,
			selected: state.voucher_types || [],
			onApply: function (next) {
				applyFilterChange({
					voucher_types: next.length ? next : null,
				});
			},
		});
	}

	/**
	 * Vanilla multi-select dropdown component.
	 *
	 * opts: { key, label, options: [{value, label}], selected: [value],
	 *         onApply: function(nextSelection) }
	 *
	 * Renders into `host`. Returns nothing -- the onApply callback is
	 * the contract.
	 */
	function renderMultiSelect(host, opts) {
		const wrap = document.createElement('div');
		wrap.className = 'dgv-gl-filter-field dgv-gl-multiselect';
		// Trigger text shows the selection summary -- "All" when
		// nothing's selected (default = all matching), or "(N)"
		// when the user has narrowed. Label sits ABOVE the trigger
		// in the field-label span so all five filters share the
		// same label-above layout.
		const triggerSummary = opts.selected.length
			? opts.selected.length + ' selected'
			: 'All';
		// Label is rendered as-is; the .dgv-gl-filter-field-label CSS
		// applies text-transform:uppercase + letter-spacing for
		// visual consistency across all five filter labels.
		wrap.innerHTML =
			'<span class="dgv-gl-filter-field-label">' + escapeHtml(opts.label) + '</span>' +
			'<button type="button" class="dgv-gl-multiselect-trigger">' +
				'<span class="dgv-gl-multiselect-label">' + escapeHtml(triggerSummary) + '</span>' +
				'<span class="dgv-gl-multiselect-caret">▾</span>' +
			'</button>' +
			'<div class="dgv-gl-multiselect-popup" hidden>' +
				'<div class="dgv-gl-multiselect-popup-options"></div>' +
				'<div class="dgv-gl-multiselect-popup-actions">' +
					'<button type="button" class="dgv-gl-multiselect-cancel">Cancel</button>' +
					'<button type="button" class="dgv-gl-multiselect-apply">Apply</button>' +
				'</div>' +
			'</div>';
		host.appendChild(wrap);
		const trigger = wrap.querySelector('.dgv-gl-multiselect-trigger');
		const popup   = wrap.querySelector('.dgv-gl-multiselect-popup');
		const optsEl  = wrap.querySelector('.dgv-gl-multiselect-popup-options');
		const applyBtn  = wrap.querySelector('.dgv-gl-multiselect-apply');
		const cancelBtn = wrap.querySelector('.dgv-gl-multiselect-cancel');

		// Render checkboxes; track DRAFT selection.
		let draft = opts.selected.slice();
		opts.options.forEach(function (o) {
			const id = 'ms-' + opts.key + '-' + Math.random().toString(36).slice(2, 8);
			const item = document.createElement('label');
			item.className = 'dgv-gl-multiselect-option';
			item.innerHTML =
				'<input type="checkbox" id="' + id + '" value="' + escapeAttr(o.value) + '" ' +
				(opts.selected.indexOf(o.value) !== -1 ? 'checked' : '') + ' />' +
				'<span>' + escapeHtml(o.label) + '</span>';
			optsEl.appendChild(item);
			item.querySelector('input').addEventListener('change', function (e) {
				if (e.target.checked) {
					if (draft.indexOf(o.value) === -1) draft.push(o.value);
				} else {
					draft = draft.filter(function (v) { return v !== o.value; });
				}
			});
		});

		trigger.addEventListener('click', function () {
			// Reset draft to current selected state when opening.
			draft = opts.selected.slice();
			popup.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
				cb.checked = opts.selected.indexOf(cb.value) !== -1;
			});
			popup.hidden = !popup.hidden;
		});
		cancelBtn.addEventListener('click', function () { popup.hidden = true; });
		applyBtn.addEventListener('click', function () {
			popup.hidden = true;
			opts.onApply(draft);
		});
		// Click outside closes.
		document.addEventListener('click', function (e) {
			if (!wrap.contains(e.target) && !popup.hidden) {
				popup.hidden = true;
			}
		});
	}

	/**
	 * Apply a partial filter change to state, push to URL, refetch.
	 * Always resets pagination to page 1 (per spec §9).
	 */
	function applyFilterChange(patch) {
		Object.assign(state, patch);
		state.page = 1;
		pushUrl();
		fetchAndRender();
		// Re-render filter UI to reflect new state (chips, count badges).
		renderFilters();
		renderFilterChips();
		updateMobileBadge();
	}

	function clearAllFilters() {
		state.account_names = null;
		state.from_date = null;
		state.to_date = null;
		state.party = null;
		state.party_type = null;
		state.voucher_types = null;
		state.page = 1;
		// Note: companies is intentionally NOT cleared -- it's a
		// scope concern, not a HALT 2.5 filter. Same boundary the
		// CSV export uses for the `_filtered` segment.
		pushUrl();
		fetchAndRender();
		renderFilters();
		renderFilterChips();
		updateMobileBadge();
	}

	/**
	 * Render the active-filter chip row below the toolbar. One chip
	 * per non-default filter, plus a "Clear all" button.
	 *
	 * Per spec amendment 2: chip max-width 200px + ellipsize +
	 * `title` attribute holding the full selected value for hover-
	 * reveal. CSS handles the ellipsize; HTML provides the title.
	 */
	function renderFilterChips() {
		const host = document.getElementById('dgv-gl-filter-chips');
		const chips = [];
		if (state.account_names && state.account_names.length) {
			chips.push({
				label: 'Accounts × ' + state.account_names.length,
				title: 'Accounts: ' + state.account_names.join(', '),
				clear: function () { applyFilterChange({ account_names: null }); },
			});
		}
		if (state.from_date || state.to_date) {
			const f = state.from_date || '…';
			const t = state.to_date || state.as_of_date || '…';
			chips.push({
				label: f + ' → ' + t,
				title: 'Date range: ' + f + ' to ' + t,
				clear: function () { applyFilterChange({ from_date: null, to_date: null }); },
			});
		}
		if (state.party) {
			chips.push({
				label: 'Party: ' + state.party,
				title: 'Party: ' + state.party + ' (' + (state.party_type || '?') + ')',
				clear: function () { applyFilterChange({ party: null, party_type: null }); },
			});
		}
		if (state.voucher_types && state.voucher_types.length) {
			chips.push({
				label: 'Voucher × ' + state.voucher_types.length,
				title: 'Voucher types: ' + state.voucher_types.join(', '),
				clear: function () { applyFilterChange({ voucher_types: null }); },
			});
		}

		if (!chips.length) {
			host.hidden = true;
			host.innerHTML = '';
			return;
		}
		host.hidden = false;
		host.innerHTML = '';
		chips.forEach(function (c) {
			const chip = document.createElement('span');
			chip.className = 'dgv-gl-filter-chip';
			chip.setAttribute('title', c.title);
			chip.innerHTML =
				'<span class="dgv-gl-filter-chip-label">' + escapeHtml(c.label) + '</span>' +
				'<button type="button" class="dgv-gl-filter-chip-clear" aria-label="Clear">×</button>';
			host.appendChild(chip);
			chip.querySelector('.dgv-gl-filter-chip-clear').addEventListener('click', c.clear);
		});
		const clearAll = document.createElement('button');
		clearAll.className = 'dgv-gl-filter-clear-all';
		clearAll.type = 'button';
		clearAll.textContent = 'Clear all filters';
		clearAll.addEventListener('click', clearAllFilters);
		host.appendChild(clearAll);
	}

	function updateMobileBadge() {
		const badge = document.getElementById('dgv-gl-filter-mobile-badge');
		const n = filterCount();
		if (n > 0) {
			badge.hidden = false;
			badge.textContent = n;
		} else {
			badge.hidden = true;
		}
	}

	function filterCount() {
		let n = 0;
		if (state.account_names && state.account_names.length) n += 1;
		if (state.from_date || state.to_date) n += 1;
		if (state.party) n += 1;
		if (state.voucher_types && state.voucher_types.length) n += 1;
		return n;
	}

	function escapeAttr(s) {
		if (s === null || s === undefined) return '';
		return String(s)
			.replace(/&/g, '&amp;')
			.replace(/"/g, '&quot;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;');
	}
	function escapeHtml(s) {
		if (s === null || s === undefined) return '';
		return String(s)
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			.replace(/"/g, '&quot;')
			.replace(/'/g, '&#39;');
	}


	function fetchAndRender() {
		const args = {
			page: state.page,
			page_size: state.page_size,
			sort: state.sort,
		};
		if (state.resolvedAccounts !== null) {
			args.accounts = JSON.stringify(state.resolvedAccounts);
		} else if (state.scope.kind === 'account' || state.scope.kind === 'subtree') {
			args.scope = JSON.stringify({
				type: state.scope.kind,
				value: state.scope.id,
			});
		} else {
			// Scope shape is unrecognised -- malformed link.
			const wrap = document.getElementById('dgv-gl-table-wrap');
			if (wrap && window.dgvRenderErrorTile) {
				wrap.innerHTML = '';
				window.dgvRenderErrorTile(
					{ status: 404, responseJSON: { malformed_scope: true } },
					wrap,
					null
				);
			} else {
				showError('Could not resolve scope.');
			}
			return;
		}
		if (state.as_of_date) args.as_of_date = state.as_of_date;
		if (state.companies)  args.companies = JSON.stringify(state.companies);
		if (state.party)      args.party = state.party;
		if (state.party_type) args.party_type = state.party_type;
		// HALT 2.5 filter params -- comma-separated lists / ISO dates.
		// Server normalises empty / missing as no-op.
		if (state.account_names && state.account_names.length) {
			args.account_names = state.account_names.join(',');
		}
		if (state.from_date) args.from_date = state.from_date;
		if (state.to_date)   args.to_date = state.to_date;
		if (state.voucher_types && state.voucher_types.length) {
			args.voucher_types = state.voucher_types.join(',');
		}

		// Skeleton state: keep prior table visible while loading;
		// disable pager buttons.
		$('#dgv-gl-prev').prop('disabled', true);
		$('#dgv-gl-next').prop('disabled', true);
		$('#dgv-gl-page-info').text('Loading…');

		// Race-condition guard (HALT 6.3 category 4): every page-state
		// change (sort / page-size / filter / pager) re-enters
		// fetchAndRender. Without a token, two-quick-clicks could let
		// the first response paint after the second is initiated.
		state.fetchToken = (state.fetchToken || 0) + 1;
		const myToken = state.fetchToken;

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.gl_drill_v1.get_gl_entries',
			args: args,
			callback: function (r) {
				if (myToken !== state.fetchToken) return; // stale
				const data = (r && r.message) || null;
				if (!data) {
					showError('No data returned from server.');
					return;
				}
				state.data = data;
				renderPage();
			},
			error: function (r, xhr) {
				if (myToken !== state.fetchToken) return; // stale
				// Replace the GL table with a classified error tile
				// (commit-6 HALT 6.2). Retry re-fires the same fetch.
				const wrap = document.getElementById('dgv-gl-table-wrap');
				if (wrap && window.dgvRenderErrorTile) {
					wrap.innerHTML = '';
					window.dgvRenderErrorTile(xhr, wrap, () => fetchAndRender());
				} else {
					showError('Could not load GL entries.');
				}
			},
		});
	}

	// -----------------------------------------------------------------
	// Render
	// -----------------------------------------------------------------

	function renderPage() {
		const data = state.data;
		const label = data.scope_label || state.resolvedLabel || state.scope.id || '—';

		document.getElementById('dgv-gl-bc-current').textContent =
			'GL entries (' + (data.total_entries || 0).toLocaleString() + ')';
		document.getElementById('dgv-gl-title').textContent = label;
		document.getElementById('dgv-gl-sub').textContent = scopeSubLine();
		document.title = label + ' — GL entries';

		// Totals (right side of hero): show entry count + party filter
		// chip if active.
		let totalsHtml = '';
		totalsHtml += '<div class="dgv-gl-totals-num">' +
			(data.total_entries || 0).toLocaleString() + '</div>';
		totalsHtml += '<div class="dgv-gl-totals-label">GL entries</div>';
		if (state.party) {
			totalsHtml += '<div class="dgv-gl-party-chip">Party: ' +
				escape(state.party) +
				' <button type="button" class="dgv-gl-party-clear" title="Remove party filter">×</button></div>';
		}
		document.getElementById('dgv-gl-totals').innerHTML = totalsHtml;
		const clearBtn = document.querySelector('.dgv-gl-party-clear');
		if (clearBtn) {
			clearBtn.addEventListener('click', function () {
				state.party = null;
				state.party_type = null;
				state.page = 1;
				pushUrl();
				fetchAndRender();
			});
		}

		// Fanout banner. Threshold tightened in HALT 2 (Aditya call):
		// the spec's >5 accounts OR >1 company tripped on every multi-co
		// drill including 12-leaf 26ms loads -- not the signal users
		// need. New thresholds N_accounts > 20 OR N_companies > 5
		// surface only genuinely large fanouts. Truncation banner is a
		// separate trigger and stays on actual >50K row caps.
		const fanout = data.scope_fanout || {};
		const showFanout = (fanout.n_accounts > 20) || (fanout.n_companies > 5);
		const fanoutEl = document.getElementById('dgv-gl-fanout-banner');
		if (showFanout) {
			// v0.5 copy: running balance is now scope-wide (no
			// PARTITION BY in the window), so the previous "resets
			// per (account, company)" wording would mislead.
			fanoutEl.textContent =
				'GL entries across ' + fanout.n_accounts +
				' accounts × ' + fanout.n_companies + ' companies. ' +
				'Running balance is scope-wide, in date order.';
			fanoutEl.hidden = false;
		} else {
			fanoutEl.hidden = true;
		}

		// Truncation banner.
		const truncEl = document.getElementById('dgv-gl-truncate-banner');
		if (data.is_truncated) {
			truncEl.textContent =
				'Showing first 50,000 of ' + (data.total_entries || 0).toLocaleString() +
				' entries. Running balance is relative to the visible window. ' +
				'Narrow scope or use CSV export for the full set.';
			truncEl.hidden = false;
		} else {
			truncEl.hidden = true;
		}

		// Pager state.
		const cap = data.is_truncated ? 50000 : (data.total_entries || 0);
		const lastPage = Math.max(1, Math.ceil(cap / state.page_size));
		$('#dgv-gl-prev').prop('disabled', state.page <= 1);
		$('#dgv-gl-next').prop('disabled', state.page >= lastPage);
		const fromIdx = (state.page - 1) * state.page_size + 1;
		const toIdx = Math.min(state.page * state.page_size, cap);
		const pageInfo = (data.total_entries || 0) === 0
			? '0 entries'
			: 'Showing ' + fromIdx.toLocaleString() + '–' + toIdx.toLocaleString() +
			  ' of ' + cap.toLocaleString() +
			  (data.is_truncated ? '+' : '');
		$('#dgv-gl-page-info').text(pageInfo);

		// Table.
		const wrap = document.getElementById('dgv-gl-table-wrap');
		if (!data.entries || !data.entries.length) {
			wrap.innerHTML = '<div class="dgv-gl-empty">' +
				'No GL entries for this scope as of ' + escape(state.as_of_date) + '.' +
				'</div>';
			return;
		}
		wrap.innerHTML = renderTable(data.entries, state.sort);
		bindVoucherLinks(wrap);
	}

	/**
	 * Render the GL entries table.
	 *
	 * Group dividers + "<company> • <account>" chip appear at every
	 * (company, account) transition in the displayed sequence -- only
	 * when sort is posting_date_*. For amount_* sorts, rows are not
	 * grouped (running balance still computed, but the partition
	 * boundary doesn't align with display order, so dividers/labels
	 * would be confusing).
	 */
	function renderTable(entries, sort) {
		const showDividers = sort.indexOf('posting_date') === 0;

		let prevKey = null;
		const rowsHtml = entries.map(function (e) {
			const key = e.company + '|' + e.account;
			const isBoundary = showDividers && key !== prevKey;
			prevKey = key;

			const dividerHtml = isBoundary
				? '<tr class="dgv-gl-group-divider">' +
				    '<td colspan="6">' +
				      '<span class="dgv-gl-group-chip">' +
				        escape(e.company) + ' • ' + escape(stripCompanySuffix(e.account)) +
				      '</span>' +
				    '</td>' +
				  '</tr>'
				: '';

			const dateStr = e.posting_date || '';
			const partyHtml = e.party
				? escape(e.party) +
				  '<span class="dgv-gl-party-type">' + escape(e.party_type || '') + '</span>'
				: '<span class="dgv-gl-empty-cell">—</span>';
			const remarksHtml = e.remarks
				? '<span class="dgv-gl-remarks"' +
				  (e.remarks_truncated
				    ? ' title="(remarks truncated; see voucher for full text)"'
				    : '') +
				  '>' + escape(e.remarks) + '</span>'
				: '<span class="dgv-gl-empty-cell">—</span>';
			const voucherHtml = '<a href="#" class="dgv-gl-voucher-link" ' +
				'data-voucher-type="' + escape(e.voucher_type) + '" ' +
				'data-voucher-no="' + escape(e.voucher_no) + '">' +
				escape(e.voucher_no) +
				'</a>' +
				'<span class="dgv-gl-voucher-type">' + escape(e.voucher_type) + '</span>';

			return dividerHtml + '<tr>' +
				'<td class="dgv-gl-cell-date">' + escape(dateStr) + '</td>' +
				'<td class="dgv-gl-cell-voucher">' + voucherHtml + '</td>' +
				'<td class="dgv-gl-cell-party">' + partyHtml + '</td>' +
				'<td class="dgv-gl-cell-amount" data-sign="' +
					(e.signed_amount < 0 ? 'neg' : 'pos') + '">' +
					formatSignedAmount(e.signed_amount) +
				'</td>' +
				'<td class="dgv-gl-cell-running" data-sign="' +
					(e.running_balance < 0 ? 'neg' : 'pos') + '">' +
					formatSignedAmount(e.running_balance) +
				'</td>' +
				'<td class="dgv-gl-cell-remarks">' + remarksHtml + '</td>' +
			'</tr>';
		}).join('');

		const dividerNote = showDividers ? '' :
			'<div class="dgv-gl-sort-note">Sorted by amount; ' +
			'group dividers hidden. Running balance still ' +
			'accumulates scope-wide in date order (independent of ' +
			'this display sort).</div>';

		return dividerNote +
			'<table class="dgv-gl-table">' +
				'<thead>' +
					'<tr>' +
						'<th class="dgv-gl-th-date">Date</th>' +
						'<th class="dgv-gl-th-voucher">Voucher</th>' +
						'<th class="dgv-gl-th-party">Party</th>' +
						'<th class="dgv-gl-th-amount">Amount</th>' +
						'<th class="dgv-gl-th-running">Running balance</th>' +
						'<th class="dgv-gl-th-remarks">Remarks</th>' +
					'</tr>' +
				'</thead>' +
				'<tbody>' + rowsHtml + '</tbody>' +
			'</table>';
	}

	function bindVoucherLinks(wrap) {
		// Voucher cells navigate to /app/<voucher-type>/<voucher-no>.
		// frappe.set_route handles the standard ERPNext document view.
		wrap.querySelectorAll('.dgv-gl-voucher-link').forEach(function (a) {
			a.addEventListener('click', function (e) {
				e.preventDefault();
				const vt = a.getAttribute('data-voucher-type');
				const vn = a.getAttribute('data-voucher-no');
				if (vt && vn) {
					frappe.set_route(vt, vn);
				}
			});
		});
	}

	function scopeSubLine() {
		const cos = state.companies;
		const parts = [];
		if (Array.isArray(cos) && cos.length === 1) {
			parts.push(cos[0]);
		} else if (Array.isArray(cos) && cos.length > 1) {
			parts.push(cos.length + ' companies');
		} else {
			parts.push('All companies');
		}
		if (state.as_of_date) {
			parts.push('as of ' + formatLongDate(state.as_of_date));
		}
		return parts.join(' · ');
	}

	function formatLongDate(iso) {
		if (!iso) return '';
		try {
			const d = new Date(iso);
			return d.toLocaleDateString(undefined, {
				day: 'numeric', month: 'long', year: 'numeric',
			});
		} catch (e) { return iso; }
	}

	function formatSignedAmount(n) {
		// Always show sign; leverage formatRupeesIndian for the
		// rupee-grouped string. formatRupeesIndian already prefixes
		// −/₹ as needed; pass through.
		const v = Number(n) || 0;
		if (Math.abs(v) < 0.005) {
			return '<span class="dgv-zero-balance">—</span>';
		}
		return window.dgvDrill.formatRupeesIndian(v);
	}

	function stripCompanySuffix(accountFullName) {
		// "Sundry Creditors - GHRCE" -> "Sundry Creditors"
		// Same heuristic ERPNext uses (last " - <abbr>" segment).
		const idx = accountFullName.lastIndexOf(' - ');
		return idx > 0 ? accountFullName.slice(0, idx) : accountFullName;
	}


	// -----------------------------------------------------------------
	// Helpers
	// -----------------------------------------------------------------

	function showError(message) {
		document.getElementById('dgv-gl-bc-current').textContent = 'Error';
		document.getElementById('dgv-gl-title').textContent = 'GL drill';
		document.getElementById('dgv-gl-sub').textContent = '';
		document.getElementById('dgv-gl-totals').innerHTML = '';
		document.getElementById('dgv-gl-fanout-banner').hidden = true;
		document.getElementById('dgv-gl-truncate-banner').hidden = true;
		document.getElementById('dgv-gl-toolbar').hidden = true;
		document.getElementById('dgv-gl-table-wrap').innerHTML =
			'<div class="dgv-gl-empty dgv-gl-error">' + escape(message) + '</div>';
	}

	function escape(s) {
		if (s === null || s === undefined) return '';
		return frappe.utils.escape_html(String(s));
	}
};
