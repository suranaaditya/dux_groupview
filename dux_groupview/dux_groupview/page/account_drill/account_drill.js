/*
 * dux_groupview — Account drill full page (Phase 4 commit 3 Phase 2)
 *
 * URL: /app/account-drill?scope=<scope_id>&as_of=<iso_date>&companies=<csv>
 *
 *   scope_id forms (encoded by buildDrillUrl in public/js/account_drill.js):
 *     - <card_id>           — spotlight card scope (e.g. "sundry_creditors")
 *     - account:<acct_name> — pivot leaf row click
 *     - subtree:<acct_name> — subtree drill (commit 4 territory)
 *
 *   as_of:    optional ISO date; defaults to server's today
 *   companies: optional CSV of company names; defaults to user's allowed set
 *
 * The page renders entirely from URL parameters — there is no
 * server-side data templating. The Python controller is a stub. All
 * data comes from the same APIs the panel uses.
 *
 * Components (renderHero, renderTrendChart, renderCompanyBreakdownTable,
 * renderPartyBreakdownTable) are imported from window.dgvDrill (set up
 * by public/js/account_drill.js, loaded earlier via app_include_js).
 * The page passes different `opts` so a single render function emits
 * panel-sized OR page-sized output without code duplication.
 */

frappe.pages['account-drill'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Account drill',
		single_column: true,
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$body.html(`
		<div class="dgv-drill-page" id="dgv-drill-page">

			<header class="dgv-drill-page-breadcrumb">
				<a href="/app/groupview">Cockpit</a>
				<span class="dgv-bc-sep">/</span>
				<span class="dgv-bc-mid">Account drill</span>
				<span class="dgv-bc-sep">/</span>
				<span class="dgv-bc-current" id="dgv-page-bc-current">…</span>

				<div class="dgv-drill-page-actions">
					<button id="dgv-page-share">Share link</button>
					<button id="dgv-page-print">Print</button>
				</div>
			</header>

			<main class="dgv-drill-page-body">

				<section class="dgv-drill-page-hero" id="dgv-page-hero">
					<div class="dgv-drill-page-hero-meta">
						<div class="dgv-drill-eyebrow">Account drill</div>
						<h2 class="dgv-drill-title" id="dgv-page-title">…</h2>
						<div class="dgv-drill-scope-sub" id="dgv-page-sub"></div>
					</div>
					<div class="dgv-drill-page-hero-figure" id="dgv-page-hero-fig"></div>
				</section>

				<section class="dgv-drill-page-trend">
					<div class="dgv-section-eyebrow">12-month trend</div>
					<div id="dgv-page-trend"></div>
				</section>

				<section class="dgv-drill-page-tables" id="dgv-page-tables">
					<div class="dgv-drill-page-tables-card" id="dgv-page-cocard">
						<div class="dgv-section-eyebrow">By company</div>
						<div id="dgv-page-co"></div>
					</div>
					<div class="dgv-drill-page-tables-card" id="dgv-page-pcard" hidden>
						<div id="dgv-page-party"></div>
					</div>
				</section>

				<footer class="dgv-drill-page-actions-bar" id="dgv-page-actions">
				</footer>

			</main>
		</div>
	`);

	// -----------------------------------------------------------------
	// State + URL parse
	// -----------------------------------------------------------------

	const parsed = window.dgvParseAccountDrillHash(window.location.search);
	// parsed: { scope: {kind, id} | null, as_of_date, companies }
	const state = {
		scope: parsed.scope,
		as_of_date: parsed.as_of_date || frappe.datetime.get_today(),
		companies: parsed.companies,    // null OR string[]
		// Filled in after fetch:
		resolvedAccounts: null,
		resolvedLabel: null,
		breakdown: null,
		partyData: null,
	};

	if (!state.scope) {
		showError('Missing scope parameter. Open this page from a drill card or pivot row.');
		return;
	}

	// -----------------------------------------------------------------
	// Fetch + render
	// -----------------------------------------------------------------

	if (state.scope.kind === 'card') {
		fetchCardScope(state.scope.id).then(() => fetchBreakdown());
	} else {
		fetchBreakdown();
	}

	// -----------------------------------------------------------------
	// Wire breadcrumb actions
	// -----------------------------------------------------------------

	$('#dgv-page-print').on('click', () => window.print());
	$('#dgv-page-share').on('click', shareLink);


	// =================================================================
	// Implementation
	// =================================================================

	function fetchCardScope(card_id) {
		// Look up the card's `match` predicate by fetching the spotlight
		// cards once. Pick by card_id, hand the predicate to
		// resolve_match_to_accounts. Two-step is unavoidable without
		// modifying commit-2 APIs (cards_v1.resolve_match_to_accounts
		// takes a predicate, not a card_id).
		const isFullScope = state.companies === null;
		const method = isFullScope
			? 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards'
			: 'dux_groupview.dux_groupview.api.cockpit.get_spotlight_cards_filtered';
		const args = { snapshot_date: state.as_of_date };
		if (!isFullScope) args.companies = JSON.stringify(state.companies);

		return new Promise((resolve) => {
			frappe.call({
				method: method,
				args: args,
				callback: (r) => {
					const cards = (r && r.message) || [];
					const card = cards.find(c => c.card_id === card_id);
					if (!card) {
						showError(`Unknown spotlight card: ${escape(card_id)}`);
						resolve();
						return;
					}
					state.resolvedLabel = card.label;
					// Resolve match predicate to leaf accounts.
					frappe.call({
						method: 'dux_groupview.dux_groupview.api.cards_v1.resolve_match_to_accounts',
						args: {
							match: JSON.stringify(card.match),
							companies: state.companies
								? JSON.stringify(state.companies) : null,
							label: card.label,
						},
						callback: (rr) => {
							const m = (rr && rr.message) || {};
							state.resolvedAccounts = m.accounts || [];
							resolve();
						},
					});
				},
			});
		});
	}

	function fetchBreakdown() {
		const args = {};
		if (state.resolvedAccounts !== null) {
			args.accounts = JSON.stringify(state.resolvedAccounts);
			args.scope_label = state.resolvedLabel || '';
		} else if (state.scope.kind === 'account' || state.scope.kind === 'subtree') {
			args.scope = JSON.stringify({
				type: state.scope.kind,
				value: state.scope.id,
			});
			args.scope_label = state.scope.id;
		} else {
			showError('Could not resolve scope.');
			return;
		}
		if (state.as_of_date) args.as_of_date = state.as_of_date;
		if (state.companies)  args.companies = JSON.stringify(state.companies);

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.account_drill_v1.get_account_breakdown',
			args: args,
			callback: (r) => {
				const data = (r && r.message) || null;
				if (!data) {
					showError('No breakdown data returned.');
					return;
				}
				state.breakdown = data;
				renderPage();
				if (data.is_party_trackable) fetchPartyBreakdown();
			},
		});
	}

	function fetchPartyBreakdown() {
		const args = {
			page: 1,
			page_size: 8,  // page shows top 8 (vs 5 in panel)
			sort: 'balance_desc',
		};
		if (state.resolvedAccounts !== null) {
			args.accounts = JSON.stringify(state.resolvedAccounts);
		} else {
			args.scope = JSON.stringify({
				type: state.scope.kind,
				value: state.scope.id,
			});
		}
		if (state.as_of_date) args.as_of_date = state.as_of_date;
		if (state.companies)  args.companies = JSON.stringify(state.companies);

		frappe.call({
			method: 'dux_groupview.dux_groupview.api.party_drill_v1.get_party_breakdown',
			args: args,
			callback: (r) => {
				state.partyData = (r && r.message) || null;
				renderPartySection();
			},
		});
	}

	// -----------------------------------------------------------------
	// Render
	// -----------------------------------------------------------------

	function renderPage() {
		const data = state.breakdown;
		const label = data.scope_label || state.resolvedLabel || state.scope.id || '—';

		document.getElementById('dgv-page-bc-current').textContent = label;
		document.getElementById('dgv-page-title').textContent = label;
		document.getElementById('dgv-page-sub').textContent = scopeSubLine();
		// Update the document title so browser tabs / bookmarks read clean.
		document.title = `${label} — Account drill`;

		// Hero figure (right side of the hero row).
		document.getElementById('dgv-page-hero-fig').innerHTML =
			window.dgvDrill.renderHero(data, { tier: 'page' });

		// Trend chart — wider, taller, dense month labels.
		document.getElementById('dgv-page-trend').innerHTML =
			window.dgvDrill.renderTrendChart(data.trend_12mo || [], {
				width: 832,
				height: 160,
				monthLabels: 'dense',
				tooltipsEnabled: true,
			});
		window.dgvDrill.bindTrendTooltip(document.getElementById('dgv-page-trend'));

		// By-company table (left card). Same single-company hide rule
		// as the panel.
		const isSingleCoScope = Array.isArray(state.companies)
		                       && state.companies.length === 1;
		const coCard = document.getElementById('dgv-page-cocard');
		if (isSingleCoScope) {
			coCard.hidden = true;
		} else {
			coCard.hidden = false;
			document.getElementById('dgv-page-co').innerHTML =
				window.dgvDrill.renderCompanyBreakdownTable(data.by_company || [], {
					showSparklines: true,
					sparklineSize: [80, 14],
					maxNameLength: null,
				});
		}

		// Action bar at the bottom — same buttons, same stubs.
		const actionsEl = document.getElementById('dgv-page-actions');
		actionsEl.innerHTML = window.dgvDrill.renderActionBar({ disabled: false });
		actionsEl.querySelector('.dgv-drill-action-primary').addEventListener('click',
			() => window.dgvDrill.stubGlDrill({ label: label }, state));
		actionsEl.querySelector('.dgv-drill-action-secondary').addEventListener('click',
			() => window.dgvDrill.stubExportCsv({ label: label }, state));
	}

	function renderPartySection() {
		const card = document.getElementById('dgv-page-pcard');
		const host = document.getElementById('dgv-page-party');
		if (!state.partyData || !state.partyData.parties || !state.partyData.parties.length) {
			card.hidden = true;
			host.innerHTML = '';
			return;
		}
		card.hidden = false;
		host.innerHTML = window.dgvDrill.renderPartyBreakdownTable(
			state.partyData.parties, {
				total: state.partyData.total_parties || state.partyData.parties.length,
				displayedCount: state.partyData.parties.length,
				showCompanyCount: true,
				showViewAll: (state.partyData.total_parties || 0)
					> state.partyData.parties.length,
			});
		const viewAll = host.querySelector('.dgv-drill-view-all');
		if (viewAll) {
			viewAll.addEventListener('click',
				() => window.dgvDrill.stubViewAllParties(state));
		}
	}

	function scopeSubLine() {
		// Mirror of the panel's scopeSubLine (same precedence rules).
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


	// -----------------------------------------------------------------
	// Share-link button
	// -----------------------------------------------------------------

	function shareLink() {
		// Reconstruct the canonical URL for this drill state. Always
		// includes scope; includes as_of and companies only when they
		// were explicitly set, so a default-state URL stays compact.
		const params = [];
		const scopeParam = state.scope.kind === 'card'
			? state.scope.id
			: state.scope.kind + ':' + state.scope.id;
		params.push('scope=' + encodeURIComponent(scopeParam));
		if (state.as_of_date) params.push('as_of=' + encodeURIComponent(state.as_of_date));
		if (state.companies && state.companies.length) {
			params.push('companies=' + encodeURIComponent(state.companies.join(',')));
		}
		const url = window.location.origin + '/app/account-drill?' + params.join('&');

		copyToClipboard(url).then((ok) => {
			frappe.show_alert({
				message: ok ? 'Link copied to clipboard.' : 'Could not copy link.',
				indicator: ok ? 'green' : 'red',
			}, 3);
		});
	}

	function copyToClipboard(text) {
		if (navigator.clipboard && navigator.clipboard.writeText) {
			return navigator.clipboard.writeText(text).then(() => true, () => false);
		}
		// Fallback for older browsers / non-secure contexts.
		try {
			const ta = document.createElement('textarea');
			ta.value = text;
			ta.style.position = 'fixed';
			ta.style.opacity = '0';
			document.body.appendChild(ta);
			ta.focus();
			ta.select();
			const ok = document.execCommand('copy');
			document.body.removeChild(ta);
			return Promise.resolve(ok);
		} catch (e) {
			return Promise.resolve(false);
		}
	}


	// -----------------------------------------------------------------
	// Helpers
	// -----------------------------------------------------------------

	function showError(message) {
		document.getElementById('dgv-page-bc-current').textContent = 'Error';
		document.getElementById('dgv-page-title').textContent = 'Account drill';
		document.getElementById('dgv-page-sub').textContent = '';
		document.getElementById('dgv-page-hero-fig').innerHTML =
			'<div class="dgv-drill-empty">' + escape(message) + '</div>';
		document.getElementById('dgv-page-trend').innerHTML = '';
		document.getElementById('dgv-page-cocard').hidden = true;
		document.getElementById('dgv-page-pcard').hidden = true;
		document.getElementById('dgv-page-actions').innerHTML = '';
	}

	function escape(s) {
		if (s === null || s === undefined) return '';
		return frappe.utils.escape_html(String(s));
	}
};
