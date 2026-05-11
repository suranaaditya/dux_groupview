/*
 * dux_groupview — Party list full page (Phase 4 commit 4 HALT 4)
 *
 * URL: /app/party-list?scope=<scope_id>&as_of=<iso>&companies=<csv>
 *      &page=<n>&page_size=<n>&sort=<key>
 *
 *   scope_id forms (re-uses the gl-drill / account-drill convention
 *   parsed by window.dgvParseAccountDrillHash):
 *     - <card_id>           — spotlight card scope
 *     - account:<acct_name> — pivot leaf row click
 *     - subtree:<acct_name> — subtree drill
 *
 *   sort: balance_desc (default) | balance_asc | name_asc | name_desc
 *   page_size offerings: 50 (default), 100, 200, 500
 *
 * The page renders entirely from URL parameters; the Python
 * controller is a stub. Data comes from get_party_breakdown
 * mode='page'.
 *
 * Click-row-to-drill: each row links to /app/gl-drill with the same
 * scope plus party + party_type URL params. HALT 1's gl-drill page
 * already accepts these params; no API changes needed.
 */

frappe.pages['party-list'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Party list',
		single_column: true,
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$body.html(`
		<div class="dgv-pl-page" id="dgv-pl-page">

			<header class="dgv-drill-page-breadcrumb">
				<a href="/app/groupview">Cockpit</a>
				<span class="dgv-bc-sep">/</span>
				<a id="dgv-pl-bc-account" href="#">Account drill</a>
				<span class="dgv-bc-sep">/</span>
				<span class="dgv-bc-current" id="dgv-pl-bc-current">Parties</span>
			</header>

			<section class="dgv-drill-page-hero" id="dgv-pl-hero">
				<div class="dgv-drill-page-hero-meta">
					<div class="dgv-drill-eyebrow">Party list</div>
					<h2 class="dgv-drill-title" id="dgv-pl-title">…</h2>
					<div class="dgv-drill-scope-sub" id="dgv-pl-sub"></div>
				</div>
				<div class="dgv-pl-totals" id="dgv-pl-totals"></div>
			</section>

			<section class="dgv-pl-toolbar" id="dgv-pl-toolbar">
				<div class="dgv-pl-toolbar-left">
					<label class="dgv-pl-toolbar-field">
						<span>Sort</span>
						<select id="dgv-pl-sort">
							<option value="balance_desc">Balance (largest first)</option>
							<option value="balance_asc">Balance (smallest first)</option>
							<option value="name_asc">Name (A → Z)</option>
							<option value="name_desc">Name (Z → A)</option>
						</select>
					</label>
					<label class="dgv-pl-toolbar-field">
						<span>Per page</span>
						<select id="dgv-pl-page-size">
							<option value="50">50</option>
							<option value="100">100</option>
							<option value="200">200</option>
							<option value="500">500</option>
						</select>
					</label>
				</div>
				<div class="dgv-pl-toolbar-right">
					<button class="dgv-pl-export-btn" id="dgv-pl-export" type="button"
					        title="Download all parties as CSV (50K cap)">
						Export CSV
					</button>
					<span class="dgv-pl-page-info" id="dgv-pl-page-info"></span>
					<button class="dgv-pl-pager-btn" id="dgv-pl-prev" type="button" disabled>← Prev</button>
					<button class="dgv-pl-pager-btn" id="dgv-pl-next" type="button" disabled>Next →</button>
				</div>
			</section>

			<section class="dgv-pl-table-wrap" id="dgv-pl-table-wrap">
				<div class="dgv-pl-skeleton" id="dgv-pl-skeleton">
					${(() => {
						// 8-row skeleton (commit-6 HALT 6.1 category 2.d).
						let rows = '';
						for (let i = 0; i < 8; i++) {
							rows += `
								<div class="dgv-skeleton-row">
									<div class="dgv-skeleton-cell wide"></div>
									<div class="dgv-skeleton-cell narrow"></div>
									<div class="dgv-skeleton-cell"></div>
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
		// commit-6 HALT 6.2 category (c) -- malformed scope; route
		// through the shared error tile so the user gets a [Cockpit]
		// button rather than a wall of text.
		const wrap = document.getElementById('dgv-pl-table-wrap');
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
	$('#dgv-pl-sort').val(state.sort);
	$('#dgv-pl-page-size').val(String(state.page_size));

	// -----------------------------------------------------------------
	// Wire toolbar
	// -----------------------------------------------------------------

	$('#dgv-pl-sort').on('change', function () {
		state.sort = $(this).val();
		state.page = 1;
		pushUrl();
		fetchAndRender();
	});
	$('#dgv-pl-page-size').on('change', function () {
		state.page_size = parseInt($(this).val(), 10) || 50;
		state.page = 1;
		pushUrl();
		fetchAndRender();
	});
	$('#dgv-pl-prev').on('click', function () {
		if (state.page > 1) {
			state.page -= 1;
			pushUrl();
			fetchAndRender();
		}
	});
	$('#dgv-pl-next').on('click', function () {
		state.page += 1;
		pushUrl();
		fetchAndRender();
	});
	$('#dgv-pl-export').on('click', function () {
		const url = buildExportCsvUrl();
		window.location.href = url;
	});

	window.addEventListener('popstate', function () {
		const fresh = parseAll(window.location.search);
		state.scope = fresh.scope;
		state.as_of_date = fresh.as_of_date;
		state.companies = fresh.companies;
		state.page = fresh.page;
		state.page_size = fresh.page_size;
		state.sort = fresh.sort;
		$('#dgv-pl-sort').val(state.sort);
		$('#dgv-pl-page-size').val(String(state.page_size));
		fetchAndRender();
	});

	// -----------------------------------------------------------------
	// Initial fetch
	// -----------------------------------------------------------------

	if (state.scope.kind === 'card') {
		resolveCardScope(state.scope.id).then(function () { fetchAndRender(); });
	} else {
		fetchAndRender();
	}


	// =================================================================
	// Implementation
	// =================================================================

	function parseAll(searchString) {
		const parsed = window.dgvParseAccountDrillHash(searchString);
		const params = new URLSearchParams(searchString || window.location.search);
		const page = parseInt(params.get('page') || '1', 10) || 1;
		let page_size = parseInt(params.get('page_size') || '50', 10) || 50;
		if (![50, 100, 200, 500].includes(page_size)) page_size = 50;
		const sort = params.get('sort') || 'balance_desc';
		return {
			scope: parsed.scope,
			as_of_date: parsed.as_of_date || frappe.datetime.get_today(),
			companies: parsed.companies,
			page: Math.max(1, page),
			page_size: page_size,
			sort: ['balance_desc', 'balance_asc', 'name_asc', 'name_desc']
				.includes(sort) ? sort : 'balance_desc',
		};
	}

	function pushUrl() {
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
		params.push('page=' + state.page);
		params.push('page_size=' + state.page_size);
		params.push('sort=' + state.sort);
		const url = '/app/party-list?' + params.join('&');
		window.history.pushState({}, '', url);
	}

	function wireBreadcrumbToAccountDrill() {
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
		document.getElementById('dgv-pl-bc-account').setAttribute('href', url);
	}

	function resolveCardScope(card_id) {
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
						// Stale deep-link to a non-existent card --
						// route through the shared error tile with
						// malformed_scope so the user gets a [Cockpit]
						// button (commit-6 HALT 6.3 carryover).
						const wrap = document.getElementById('dgv-pl-table-wrap');
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

	function fetchAndRender() {
		const args = {
			mode: 'page',
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
			// Malformed scope shape -- route through the shared error
			// tile so the [Cockpit] button is consistent across pages.
			const wrap = document.getElementById('dgv-pl-table-wrap');
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

		$('#dgv-pl-prev').prop('disabled', true);
		$('#dgv-pl-next').prop('disabled', true);
		$('#dgv-pl-page-info').text('Loading…');

		// Race-condition guard (HALT 6.3 category 4): same pattern as
		// gl_drill. Sort / page-size / pager re-enters fire fresh
		// fetches; stale responses get dropped on token mismatch.
		state.fetchToken = (state.fetchToken || 0) + 1;
		const myToken = state.fetchToken;

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.party_drill_v1.get_party_breakdown',
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
				// Replace the party-list table with a classified error
				// tile (commit-6 HALT 6.2). Retry re-fires the same
				// fetch.
				const wrap = document.getElementById('dgv-pl-table-wrap');
				if (wrap && window.dgvRenderErrorTile) {
					wrap.innerHTML = '';
					window.dgvRenderErrorTile(xhr, wrap, () => fetchAndRender());
				} else {
					showError('Could not load parties.');
				}
			},
		});
	}

	function buildExportCsvUrl() {
		const qs = [];
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
		if (state.as_of_date) qs.push('as_of_date=' + encodeURIComponent(state.as_of_date));
		if (state.companies)  qs.push('companies=' + encodeURIComponent(JSON.stringify(state.companies)));
		qs.push('sort=' + encodeURIComponent(state.sort));
		return '/api/method/dux_groupview.dux_groupview.api.party_drill_v1.export_party_list_csv?'
			+ qs.join('&');
	}

	// -----------------------------------------------------------------
	// Render
	// -----------------------------------------------------------------

	function renderPage() {
		const data = state.data;
		const label = state.resolvedLabel || state.scope.id || 'Parties';

		document.getElementById('dgv-pl-bc-current').textContent =
			'Parties (' + (data.total_parties || 0).toLocaleString() + ')';
		document.getElementById('dgv-pl-title').textContent = label;
		document.getElementById('dgv-pl-sub').textContent = scopeSubLine();
		document.title = label + ' — Parties';

		document.getElementById('dgv-pl-totals').innerHTML =
			'<div class="dgv-pl-totals-num">' + (data.total_parties || 0).toLocaleString() + '</div>' +
			'<div class="dgv-pl-totals-label">Parties</div>';

		// Pager state.
		const totalPages = data.total_pages || 1;
		$('#dgv-pl-prev').prop('disabled', state.page <= 1);
		$('#dgv-pl-next').prop('disabled', state.page >= totalPages);
		const fromIdx = (state.page - 1) * state.page_size + 1;
		const toIdx = Math.min(state.page * state.page_size, data.total_parties || 0);
		const pageInfo = (data.total_parties || 0) === 0
			? '0 parties'
			: 'Showing ' + fromIdx.toLocaleString() + '–' + toIdx.toLocaleString() +
			  ' of ' + (data.total_parties || 0).toLocaleString() +
			  ' · page ' + state.page + ' of ' + totalPages;
		$('#dgv-pl-page-info').text(pageInfo);

		const wrap = document.getElementById('dgv-pl-table-wrap');
		if (!data.parties || !data.parties.length) {
			wrap.innerHTML = '<div class="dgv-pl-empty">' +
				'No parties with non-zero balance for this scope.' +
				'</div>';
			return;
		}
		wrap.innerHTML = renderTable(data.parties);
		bindRowClicks(wrap);
	}

	/**
	 * Render the party list table. Click any row to drill into
	 * /app/gl-drill?scope=...&party=...&party_type=... — the gl-drill
	 * page (HALT 1) already supports the party filter via URL.
	 */
	function renderTable(parties) {
		const rowsHtml = parties.map(function (p) {
			const balanceHtml = window.dgvDrill && window.dgvDrill.formatRupeesIndian
				? window.dgvDrill.formatRupeesIndian(p.balance)
				: ('₹' + (Number(p.balance) || 0).toLocaleString());
			const groupBadge = p.is_group_company
				? '<span class="dgv-pl-group-badge" title="Group company">Group co</span>'
				: '';
			return '<tr class="dgv-pl-row" ' +
				'data-party="' + escapeAttr(p.party) + '" ' +
				'data-party-type="' + escapeAttr(p.party_type) + '" ' +
				'data-company-count="' + (p.company_count || 1) + '">' +
				'<td class="dgv-pl-cell-party">' +
					escape(p.party) + groupBadge +
				'</td>' +
				'<td class="dgv-pl-cell-party-type">' + escape(p.party_type) + '</td>' +
				'<td class="dgv-pl-cell-balance" data-sign="' +
					(p.balance < 0 ? 'neg' : 'pos') + '">' +
					balanceHtml +
				'</td>' +
				'<td class="dgv-pl-cell-cos">' +
					(p.company_count > 1 ? p.company_count + ' cos' : '1 co') +
				'</td>' +
				'</tr>';
		}).join('');

		return '<table class="dgv-pl-table">' +
			'<thead>' +
				'<tr>' +
					'<th class="dgv-pl-th-party">Party</th>' +
					'<th class="dgv-pl-th-party-type">Type</th>' +
					'<th class="dgv-pl-th-balance">Balance</th>' +
					'<th class="dgv-pl-th-cos">Companies</th>' +
				'</tr>' +
			'</thead>' +
			'<tbody>' + rowsHtml + '</tbody>' +
		'</table>';
	}

	function bindRowClicks(wrap) {
		wrap.querySelectorAll('.dgv-pl-row').forEach(function (row) {
			row.addEventListener('click', function () {
				const party = row.getAttribute('data-party');
				const ptype = row.getAttribute('data-party-type');
				const coCount = parseInt(
					row.getAttribute('data-company-count') || '1', 10);
				if (!party || !ptype) return;

				// Spec v0.9: GL drill is per-company. If the row's
				// party spans multiple companies under the current
				// scope, ask the user which company to view before
				// navigating -- mirrors the picker pattern from the
				// cockpit drill-panel "View GL entries" path.
				if (coCount > 1 && window.dgvOpenCompanyPickerForGlDrill) {
					openPickerForParty(party, ptype);
					return;
				}
				window.location.href = buildGlDrillForPartyUrl(party, ptype);
			});
		});
	}

	/**
	 * Fetch the party's per-company breakdown to get the exact list of
	 * companies where this party has activity, then open the company
	 * picker scoped to just those companies. Cleaner than showing all
	 * scope companies (which would include zeros for this party).
	 *
	 * Falls back to state.companies if the breakdown call fails or
	 * returns nothing -- the picker is still usable, just less precise.
	 */
	function openPickerForParty(party, party_type) {
		// Mirror the args pattern used by the page's get_party_breakdown
		// fetch (line ~320): card scopes pass pre-resolved
		// `accounts=state.resolvedAccounts`, account/subtree scopes
		// pass `scope={type,value}`. Anything else falls back to the
		// scope's full company list.
		const args = {
			party: party,
			party_type: party_type,
		};
		if (state.resolvedAccounts !== null) {
			args.accounts = JSON.stringify(state.resolvedAccounts);
		} else if (state.scope.kind === 'account' || state.scope.kind === 'subtree') {
			args.scope = JSON.stringify({
				type: state.scope.kind, value: state.scope.id,
			});
		} else {
			// Unresolvable scope shape -- fall back gracefully without
			// the breakdown narrowing.
			openPickerWithCompanies(party, party_type, state.companies || []);
			return;
		}
		if (state.as_of_date) args.as_of_date = state.as_of_date;
		if (state.companies)  args.companies = JSON.stringify(state.companies);

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.party_drill_v1.get_party_company_breakdown',
			args: args,
			callback: function (r) {
				const data = (r && r.message) || {};
				const rows = (data.by_company || []);
				const cos = rows.map(function (x) { return x.company; })
					.filter(function (c) { return c; });
				const cosFinal = cos.length
					? cos
					: (state.companies || []);
				openPickerWithCompanies(party, party_type, cosFinal);
			},
			error: function () {
				// Fallback: scope's full company list. The picker is
				// still usable, just shows companies where this party
				// may have no activity.
				openPickerWithCompanies(party, party_type, state.companies || []);
			},
		});
	}

	function openPickerWithCompanies(party, party_type, companies) {
		// Single-co edge case (defensive -- if the breakdown returned
		// only one company despite company_count > 1 on the row, just
		// navigate directly without the picker).
		if (companies.length <= 1) {
			window.location.href = buildGlDrillForPartyUrl(
				party, party_type, companies[0]);
			return;
		}
		window.dgvOpenCompanyPickerForGlDrill(companies, function (picked) {
			window.location.href = buildGlDrillForPartyUrl(
				party, party_type, picked);
		});
	}

	/**
	 * Build /app/gl-drill?scope=...&as_of=...&companies=...&party=&party_type=
	 * preserving the current scope + as_of + companies, adding the
	 * party filter. HALT 1's gl-drill already supports `party=` /
	 * `party_type=` URL params from its initial implementation.
	 *
	 * Optional `companyOverride`: when present, replaces the scope's
	 * companies list with `[companyOverride]`. Used by the multi-co
	 * picker path (spec v0.9).
	 */
	function buildGlDrillForPartyUrl(party, party_type, companyOverride) {
		const params = [];
		const scopeParam = state.scope.kind === 'card'
			? state.scope.id
			: state.scope.kind + ':' + state.scope.id;
		params.push('scope=' + encodeURIComponent(scopeParam));
		if (state.as_of_date) {
			params.push('as_of=' + encodeURIComponent(state.as_of_date));
		}
		const effective_cos = companyOverride
			? [companyOverride]
			: (state.companies || []);
		if (effective_cos.length) {
			params.push('companies=' + encodeURIComponent(effective_cos.join(',')));
		}
		params.push('party=' + encodeURIComponent(party));
		params.push('party_type=' + encodeURIComponent(party_type));
		return '/app/gl-drill?' + params.join('&');
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
			return d.toLocaleDateString('en-IN', {
				day: 'numeric', month: 'long', year: 'numeric',
			});
		} catch (e) { return iso; }
	}


	// -----------------------------------------------------------------
	// Helpers
	// -----------------------------------------------------------------

	function showError(message) {
		document.getElementById('dgv-pl-bc-current').textContent = 'Error';
		document.getElementById('dgv-pl-title').textContent = 'Party list';
		document.getElementById('dgv-pl-sub').textContent = '';
		document.getElementById('dgv-pl-totals').innerHTML = '';
		document.getElementById('dgv-pl-toolbar').hidden = true;
		document.getElementById('dgv-pl-table-wrap').innerHTML =
			'<div class="dgv-pl-empty dgv-pl-error">' + escape(message) + '</div>';
	}

	function escape(s) {
		if (s === null || s === undefined) return '';
		return frappe.utils.escape_html(String(s));
	}
	function escapeAttr(s) {
		if (s === null || s === undefined) return '';
		return String(s)
			.replace(/&/g, '&amp;')
			.replace(/"/g, '&quot;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;');
	}
};
