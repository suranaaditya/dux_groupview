/*
 * dux_groupview — account drill UI (Phase 4 commit 3)
 *
 * Two surfaces share the same render code:
 *   - Slide-out panel inside the cockpit page (dgvOpenAccountDrillPanel)
 *   - Full Frappe page at /app/account-drill (consumes the same component
 *     functions with different `opts` for size / density)
 *
 * "Components" here are render functions that return HTML strings
 * parameterised by (data, opts). A bug fix in one function applies to
 * both surfaces.
 *
 * Stubs for commit 4: View GL entries, Export CSV, View all parties.
 * The buttons exist now so panel chrome reads as complete.
 */

(function (root) {
	'use strict';

	// =========================================================================
	// Public API
	// =========================================================================

	root.dgvOpenAccountDrillPanel = openPanel;
	root.dgvCloseAccountDrillPanel = closePanel;
	root.dgvParseAccountDrillHash = parseDrillUrlParams;
	root.dgvRenderErrorTile = renderErrorTile;
	root.dgvClassifyError = classifyError;

	// Component render functions exported so the full page (account_drill.js
	// inside the new Frappe page) can call them with its own opts.
	root.dgvDrill = {
		renderHeader: renderHeader,
		renderHero: renderHero,
		renderTrendChart: renderTrendChart,
		renderCompanyBreakdownTable: renderCompanyBreakdownTable,
		renderPartyBreakdownTable: renderPartyBreakdownTable,
		renderActionBar: renderActionBar,
		formatCrore: formatCrore,
		formatRupeesIndian: formatRupeesIndian,
		formatMonth: formatMonth,
		bindTrendTooltip: bindTrendTooltip,
		stubGlDrill: stubGlDrill,
		stubExportCsv: stubExportCsv,
		stubViewAllParties: stubViewAllParties,
	};


	// =========================================================================
	// Panel state
	// =========================================================================

	var panelEl = null;            // <div class="dgv-drill-overlay">
	var panelKeyHandler = null;    // Esc key listener (added on open, removed on close)
	var panelLastFocus = null;     // element to restore focus to on close
	var currentRequest = null;     // most-recent open args (for the expand-to-full-page)

	// Race-condition guards (commit-6 HALT 6.3 category 4):
	//   panelFetchToken -- monotonic counter; every open / close /
	//     popstate increments it. In-flight callbacks check their
	//     captured token against the current one and drop stale
	//     responses (so Card A's fetch can't paint after Card B is
	//     clicked, and a fetch in progress when the panel closes
	//     can't run setState-on-unmounted-style code).
	//   panelInFlightKey -- the request key (card_id || scope JSON)
	//     of the currently-loading panel. Same-target double-clicks
	//     return early instead of firing a duplicate fetch.
	var panelFetchToken = 0;
	var panelInFlightKey = null;


	// =========================================================================
	// Open / close
	// =========================================================================

	/**
	 * Open the drill panel.
	 *
	 * args:
	 *   source       'card' | 'pivot'
	 *   match        (card source) the predicate dict from cards.py
	 *   card_id      (card source) used to build a deep-link URL on expand
	 *   scope        (pivot source) {type: 'account', value: <account_name>}
	 *   scope_label  human-readable title shown in the header
	 *   as_of_date   ISO date string. Defaults to today server-side.
	 *   companies    array of company names, OR null for "all"
	 */
	function openPanel(args) {
		args = args || {};
		// Commit-6 HALT 6.3 category 4.c: same-card double-click
		// returns early. The lock is per-request-key, not global, so
		// a different card click during a pending fetch DOES fire
		// (the prior callback is invalidated by the token bump
		// below, not by the lock).
		var newKey = computeRequestKey(args);
		if (panelInFlightKey !== null && panelInFlightKey === newKey) {
			return;
		}
		// Different target (or no in-flight fetch). Bump token so any
		// prior in-flight callback drops its result; capture the new
		// key as the in-flight one.
		panelFetchToken += 1;
		panelInFlightKey = newKey;

		currentRequest = args;
		ensurePanelDom();
		panelLastFocus = document.activeElement;
		showSkeleton(args);
		showPanel();
		attachKeyHandler();
		fetchAndRender(args);
	}

	function computeRequestKey(args) {
		// Two card-clicks of the SAME card → same key (lock).
		// Two pivot-leaf clicks for the same scope → same key.
		// Different scope or different card → different key.
		if (args.source === 'card' && args.card_id) {
			return 'card:' + args.card_id;
		}
		if (args.source === 'pivot' && args.scope) {
			try {
				return 'pivot:' + JSON.stringify(args.scope);
			} catch (e) {
				return 'pivot:' + (args.scope && args.scope.value);
			}
		}
		return 'unknown:' + Math.random();
	}

	function closePanel() {
		if (!panelEl) return;
		panelEl.hidden = true;
		panelEl.classList.remove('dgv-drill-open');
		detachKeyHandler();
		// Bump the token so any callback in flight drops its result
		// when it arrives (HALT 6.3 category 4.b + 4.d).
		panelFetchToken += 1;
		panelInFlightKey = null;
		// Restore focus to the trigger if it's still in the DOM.
		if (panelLastFocus && document.body.contains(panelLastFocus)) {
			try { panelLastFocus.focus(); } catch (e) { /* ignore */ }
		}
		panelLastFocus = null;
	}

	function showPanel() {
		panelEl.hidden = false;
		// Force layout, then add the open class so the slide-in transition
		// runs from translateX(100%) to translateX(0).
		// eslint-disable-next-line no-unused-expressions
		panelEl.offsetHeight;
		panelEl.classList.add('dgv-drill-open');
		// Focus the close button so Tab cycles inside the panel.
		var closeBtn = panelEl.querySelector('.dgv-drill-close');
		if (closeBtn) {
			try { closeBtn.focus(); } catch (e) { /* ignore */ }
		}
	}


	// =========================================================================
	// DOM scaffolding
	// =========================================================================

	function ensurePanelDom() {
		if (panelEl) return;
		panelEl = document.createElement('div');
		panelEl.className = 'dgv-drill-overlay';
		panelEl.id = 'dgv-drill-overlay';
		panelEl.hidden = true;
		panelEl.innerHTML = panelHtml();
		document.body.appendChild(panelEl);

		// Wire close-button + backdrop click + expand button.
		var backdrop = panelEl.querySelector('.dgv-drill-backdrop');
		if (backdrop) {
			backdrop.addEventListener('click', closePanel);
		}
		var closeBtn = panelEl.querySelector('.dgv-drill-close');
		if (closeBtn) {
			closeBtn.addEventListener('click', closePanel);
		}
		var expandBtn = panelEl.querySelector('.dgv-drill-expand');
		if (expandBtn) {
			expandBtn.addEventListener('click', expandToFullPage);
		}
	}

	function panelHtml() {
		return '' +
			'<div class="dgv-drill-backdrop" aria-hidden="true"></div>' +
			'<aside class="dgv-drill-panel" role="dialog"' +
			'        aria-modal="true" aria-labelledby="dgv-drill-title">' +
				'<header class="dgv-drill-panel-head">' +
					'<div class="dgv-drill-panel-meta">' +
						'<div class="dgv-drill-eyebrow">Account drill</div>' +
						'<h2 class="dgv-drill-title" id="dgv-drill-title">…</h2>' +
						'<div class="dgv-drill-scope-sub" id="dgv-drill-scope-sub"></div>' +
					'</div>' +
					'<div class="dgv-drill-panel-actions">' +
						expandIconButton() +
						closeIconButton() +
					'</div>' +
				'</header>' +
				'<div class="dgv-drill-body" id="dgv-drill-body">' +
					sectionHtml('hero',       '<div class="dgv-drill-hero-skeleton">…</div>') +
					sectionHtml('trend',      '<div class="dgv-section-eyebrow">12-month trend</div>' +
					                          '<div class="dgv-drill-trend-host"></div>') +
					sectionHtml('by-company', '<div class="dgv-section-eyebrow">By company</div>' +
					                          '<div class="dgv-drill-company-host"></div>') +
					sectionHtml('by-party',   '<div class="dgv-drill-party-host"></div>',
					            { hidden: true }) +
				'</div>' +
				'<footer class="dgv-drill-actions" id="dgv-drill-actions"></footer>' +
			'</aside>';
	}

	function sectionHtml(name, inner, opts) {
		var hiddenAttr = opts && opts.hidden ? ' hidden' : '';
		return '<section class="dgv-drill-section dgv-drill-' + name + '"' + hiddenAttr + '>' +
		       inner + '</section>';
	}

	function expandIconButton() {
		return '<button class="dgv-drill-expand" type="button"' +
		       ' title="Open as full page" aria-label="Open as full page">' +
				'<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">' +
				'<path d="M9 1 H13 V5 M13 1 L8 6 M5 13 H1 V9 M1 13 L6 8"' +
				' stroke="currentColor" stroke-width="1.5" fill="none"' +
				' stroke-linecap="round" stroke-linejoin="round"/>' +
				'</svg>' +
				'</button>';
	}

	function closeIconButton() {
		return '<button class="dgv-drill-close" type="button"' +
		       ' title="Close" aria-label="Close panel">' +
				'<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">' +
				'<path d="M2 2 L12 12 M12 2 L2 12"' +
				' stroke="currentColor" stroke-width="1.5" fill="none"' +
				' stroke-linecap="round"/>' +
				'</svg>' +
				'</button>';
	}


	// =========================================================================
	// Render — orchestration
	// =========================================================================

	function showSkeleton(args) {
		var titleEl = panelEl.querySelector('#dgv-drill-title');
		var subEl   = panelEl.querySelector('#dgv-drill-scope-sub');
		var bodyEl  = panelEl.querySelector('#dgv-drill-body');
		var actionsEl = panelEl.querySelector('#dgv-drill-actions');

		var title = args.scope_label || (args.scope && args.scope.value) || '…';
		titleEl.textContent = title;
		subEl.textContent = scopeSubLine(args);

		// Skeleton state per commit-6 HALT 6.1: real placeholder bars
		// at the right widths so the panel reserves layout space and
		// the user sees structured loading instead of a "…" tease.
		bodyEl.querySelector('.dgv-drill-hero').innerHTML =
			'<div class="dgv-drill-hero-skeleton-bars">' +
				'<div class="dgv-skeleton-line eyebrow"></div>' +
				'<div class="dgv-skeleton-line tall medium"></div>' +
				'<div class="dgv-skeleton-line short"></div>' +
			'</div>';
		bodyEl.querySelector('.dgv-drill-trend-host').innerHTML =
			'<div class="dgv-skeleton-line tall"></div>';
		bodyEl.querySelector('.dgv-drill-company-host').innerHTML =
			skeletonRowsHtml(5);
		// by-party stays hidden during the initial skeleton because we
		// don't know yet whether the scope is party-trackable. Once
		// the breakdown call returns, renderDrillData reveals the
		// section + paints its skeleton if `is_party_trackable=true`,
		// then renderPartySection swaps in real data (or the empty
		// banner) when the party_breakdown call resolves.
		bodyEl.querySelector('.dgv-drill-by-company').hidden = false;
		bodyEl.querySelector('.dgv-drill-by-party').hidden = true;
		bodyEl.querySelector('.dgv-drill-party-host').innerHTML = '';

		actionsEl.innerHTML = renderActionBar({ disabled: true });
	}

	function skeletonRowsHtml(n) {
		// Table-like rows used by drill panel by-company / by-party
		// during fetch. Width variance gives the eye a sense of "real
		// data" rather than a uniform grid.
		var widths = ['wide', '', 'narrow', 'right-align'];
		var rows = '';
		for (var i = 0; i < n; i++) {
			rows += '<div class="dgv-skeleton-row">';
			for (var j = 0; j < widths.length; j++) {
				rows += '<div class="dgv-skeleton-cell ' + widths[j] + '"></div>';
			}
			rows += '</div>';
		}
		return rows;
	}

	function scopeSubLine(args) {
		// Single-company scope shows the company NAME — losing it to a
		// "1 companies" count was the original information loss. Pivot
		// numeric-cell clicks always hit this branch; spotlight cards
		// and pivot leaf-row clicks hit the multi-company branch.
		var parts = [];
		var cos = args.companies;
		if (Array.isArray(cos) && cos.length === 1) {
			parts.push(cos[0]);
		} else if (Array.isArray(cos) && cos.length > 1) {
			parts.push(cos.length + ' companies');
		} else {
			parts.push('All companies');
		}
		if (args.as_of_date) {
			parts.push('as of ' + formatLongDate(args.as_of_date));
		}
		return parts.join(' · ');
	}

	function fetchAndRender(args) {
		// Path 1: spotlight card click. Resolve the match predicate to a
		// leaf list, then fetch the account breakdown by `accounts`.
		// Path 2: pivot leaf row click. We already have a scope object;
		// pass it to the breakdown API directly.
		if (args.source === 'card') {
			var myToken = panelFetchToken;
			frappe.call({
				method: 'dux_groupview.dux_groupview.api.cards_v1.resolve_match_to_accounts',
				args: {
					match: JSON.stringify(args.match || {}),
					companies: args.companies ? JSON.stringify(args.companies) : null,
					label: args.scope_label || '',
				},
				callback: function (r) {
					if (myToken !== panelFetchToken) return; // stale
					var accounts = (r && r.message && r.message.accounts) || [];
					var label = (r && r.message && r.message.label) || args.scope_label || '';
					if (!accounts.length) {
						renderEmptyDrill(label, args);
						panelInFlightKey = null;
						return;
					}
					// Don't release the lock yet -- still fetching the
					// breakdown. fetchBreakdownByAccounts will release
					// on its own success / error.
					fetchBreakdownByAccounts(accounts, label, args);
				},
				error: function (r, xhr) {
					if (myToken !== panelFetchToken) return; // stale
					panelInFlightKey = null;
					renderPanelError(xhr, function () { fetchAndRender(args); });
				},
			});
		} else if (args.source === 'pivot') {
			fetchBreakdownByScope(args);
		} else {
			renderEmptyDrill(args.scope_label || '', args);
			panelInFlightKey = null;
		}
	}

	function renderPanelError(xhr, retryFn) {
		// Replace the whole panel body with a single error tile -- a
		// failed breakdown invalidates trend, by-company, by-party
		// alike. Section-scoped errors (e.g., parties fetch only fails)
		// route through a more targeted handler below.
		var bodyEl  = panelEl.querySelector('#dgv-drill-body');
		var actions = panelEl.querySelector('#dgv-drill-actions');
		bodyEl.innerHTML = '<div id="dgv-drill-error-host"></div>';
		root.dgvRenderErrorTile(xhr, bodyEl.firstElementChild, retryFn);
		actions.innerHTML = renderActionBar({ disabled: true });
	}

	function fetchBreakdownByAccounts(accounts, label, args) {
		var apiArgs = {
			accounts: JSON.stringify(accounts),
			scope_label: label,
		};
		if (args.as_of_date) apiArgs.as_of_date = args.as_of_date;
		if (args.companies)  apiArgs.companies = JSON.stringify(args.companies);

		var myToken = panelFetchToken;
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.account_drill_v1.get_account_breakdown',
			args: apiArgs,
			callback: function (r) {
				if (myToken !== panelFetchToken) return; // stale
				var data = (r && r.message) || null;
				// Release the same-card lock here -- the by-party
				// follow-up call uses its own token check, but the
				// "card is loading" affordance ends with the breakdown
				// arriving.
				panelInFlightKey = null;
				renderDrillData(data, args, { accounts: accounts, label: label });
				if (data && data.is_party_trackable) {
					fetchPartyBreakdown({ accounts: accounts }, args);
				}
			},
			error: function (r, xhr) {
				if (myToken !== panelFetchToken) return; // stale
				panelInFlightKey = null;
				renderPanelError(xhr, function () {
					showSkeleton(args);
					fetchBreakdownByAccounts(accounts, label, args);
				});
			},
		});
	}

	function fetchBreakdownByScope(args) {
		var apiArgs = {
			scope: JSON.stringify(args.scope || {}),
		};
		if (args.scope_label) apiArgs.scope_label = args.scope_label;
		if (args.as_of_date)  apiArgs.as_of_date = args.as_of_date;
		if (args.companies)   apiArgs.companies = JSON.stringify(args.companies);

		var myToken = panelFetchToken;
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.account_drill_v1.get_account_breakdown',
			args: apiArgs,
			callback: function (r) {
				if (myToken !== panelFetchToken) return; // stale
				var data = (r && r.message) || null;
				panelInFlightKey = null;
				renderDrillData(data, args, { scope: args.scope, label: args.scope_label });
				if (data && data.is_party_trackable) {
					fetchPartyBreakdown({ scope: args.scope }, args);
				}
			},
			error: function (r, xhr) {
				if (myToken !== panelFetchToken) return; // stale
				panelInFlightKey = null;
				renderPanelError(xhr, function () {
					showSkeleton(args);
					fetchBreakdownByScope(args);
				});
			},
		});
	}

	function fetchPartyBreakdown(scopeOrAccounts, args) {
		var apiArgs = {
			page: 1,
			page_size: 5,  // panel shows top 5
			sort: 'balance_desc',
		};
		if (scopeOrAccounts.accounts) {
			apiArgs.accounts = JSON.stringify(scopeOrAccounts.accounts);
		}
		if (scopeOrAccounts.scope) {
			apiArgs.scope = JSON.stringify(scopeOrAccounts.scope);
		}
		if (args.as_of_date) apiArgs.as_of_date = args.as_of_date;
		if (args.companies)  apiArgs.companies = JSON.stringify(args.companies);

		var myToken = panelFetchToken;
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.party_drill_v1.get_party_breakdown',
			args: apiArgs,
			callback: function (r) {
				if (myToken !== panelFetchToken) return; // stale
				var data = (r && r.message) || null;
				renderPartySection(data, args);
			},
			error: function (r, xhr) {
				if (myToken !== panelFetchToken) return; // stale
				// Section-scoped error: only the by-party host carries
				// the failure; hero / trend / by-company are already
				// rendered. Compact tile so it sits inline in the
				// section without dwarfing the rest of the panel.
				var host = panelEl.querySelector('.dgv-drill-party-host');
				var section = panelEl.querySelector('.dgv-drill-by-party');
				if (!host) return;
				section.hidden = false;
				host.innerHTML =
					'<div class="dgv-section-eyebrow">By party</div>' +
					'<div id="dgv-drill-party-error-host"></div>';
				root.dgvRenderErrorTile(
					xhr,
					host.querySelector('#dgv-drill-party-error-host'),
					function () {
						host.innerHTML =
							'<div class="dgv-section-eyebrow">By party</div>' +
							skeletonRowsHtml(5);
						fetchPartyBreakdown(scopeOrAccounts, args);
					},
					{ compact: true }
				);
			},
		});
	}

	function renderEmptyDrill(label, args) {
		var bodyEl = panelEl.querySelector('#dgv-drill-body');
		var actionsEl = panelEl.querySelector('#dgv-drill-actions');
		bodyEl.querySelector('.dgv-drill-hero').innerHTML =
			'<div class="dgv-drill-empty">No matching accounts in scope.</div>';
		bodyEl.querySelector('.dgv-drill-trend-host').innerHTML = '';
		bodyEl.querySelector('.dgv-drill-company-host').innerHTML = '';
		bodyEl.querySelector('.dgv-drill-by-party').hidden = true;
		actionsEl.innerHTML = renderActionBar({ disabled: true });
		updateHeader(label, args);
	}

	function renderDrillData(data, args, ctx) {
		if (!data) {
			renderEmptyDrill(ctx.label, args);
			return;
		}
		updateHeader(data.scope_label || ctx.label || '', args);

		var bodyEl    = panelEl.querySelector('#dgv-drill-body');
		var heroEl    = bodyEl.querySelector('.dgv-drill-hero');
		var trendHost = bodyEl.querySelector('.dgv-drill-trend-host');
		var coHost    = bodyEl.querySelector('.dgv-drill-company-host');
		var actions   = panelEl.querySelector('#dgv-drill-actions');

		heroEl.innerHTML = renderHero(data, { tier: 'panel' });

		trendHost.innerHTML = renderTrendChart(data.trend_12mo || [], {
			width: 432,
			height: 120,
			monthLabels: 'sparse',
			tooltipsEnabled: true,
		});
		bindTrendTooltip(trendHost);

		// Hide the by-company section when scope is a single company.
		// Same conditional-render rationale as by-party for non-trackable
		// accounts: a one-row "By company" table adds no information when
		// the user's scope is already a single entity. Multi-company scope
		// with one active company still renders ("only X moved" is useful
		// information; we trigger off the scope, not the result size).
		var coSection = bodyEl.querySelector('.dgv-drill-by-company');
		var isSingleCoScope = Array.isArray(args.companies)
		                     && args.companies.length === 1;
		if (isSingleCoScope) {
			coSection.hidden = true;
			coHost.innerHTML = '';
		} else {
			coSection.hidden = false;
			coHost.innerHTML = renderCompanyBreakdownTable(data.by_company || [], {
				showSparklines: false,
				maxNameLength: null,
			});
		}

		actions.innerHTML = renderActionBar({
			disabled: false,
			ctx: ctx, args: args,
		});
		bindActionBar(actions, ctx, args);

		// Skeleton → content cross-fade (HALT 6.3 category 5).
		// Triggered after the host innerHTML is swapped from skeleton
		// bars to real-data render. force-reflow trick: remove the
		// class, read offsetHeight, re-add — restarts the CSS
		// animation so re-renders within the same panel session
		// re-trigger it.
		fadeInHost(heroEl);
		fadeInHost(trendHost);
		fadeInHost(coHost);

		// Party section: reveal the section + paint a 5-row skeleton
		// when the breakdown indicates the scope is party-trackable, so
		// the user sees structured loading during the in-flight
		// party_breakdown fetch (commit-6 HALT 6.1). When it isn't
		// trackable, keep the section hidden -- "By party" has no
		// meaning for, e.g., Cash & Bank.
		var partySection = bodyEl.querySelector('.dgv-drill-by-party');
		var partyHost    = bodyEl.querySelector('.dgv-drill-party-host');
		if (data.is_party_trackable) {
			partySection.hidden = false;
			partyHost.innerHTML =
				'<div class="dgv-section-eyebrow">By party</div>' +
				skeletonRowsHtml(5);
		} else {
			partySection.hidden = true;
			partyHost.innerHTML = '';
		}
	}

	function renderPartySection(data, args) {
		var section = panelEl.querySelector('.dgv-drill-by-party');
		var host    = panelEl.querySelector('.dgv-drill-party-host');
		// Reaching this function implies the scope was deemed party-
		// trackable by the breakdown call (renderDrillData only fires
		// fetchPartyBreakdown then). So an empty parties list means
		// "trackable but zero parties" -- a real empty state worth
		// surfacing rather than silently hiding the section.
		if (!data || !data.parties || !data.parties.length) {
			section.hidden = false;
			host.innerHTML =
				'<div class="dgv-section-eyebrow">By party</div>' +
				'<div class="dgv-empty-inline">' +
					'No parties with non-zero balance for this scope.' +
				'</div>';
			return;
		}
		section.hidden = false;
		host.innerHTML = renderPartyBreakdownTable(data.parties, {
			total: data.total_parties || data.parties.length,
			displayedCount: data.parties.length,
			showCompanyCount: false,
			showViewAll: (data.total_parties || 0) > data.parties.length,
		});
		bindPartyViewAll(host, args);
		fadeInHost(host);
	}

	function fadeInHost(el) {
		if (!el) return;
		el.classList.remove('dgv-fade-in');
		// Force reflow so the animation restarts even if the class was
		// previously applied. `void el.offsetHeight` is the standard
		// trick: reading layout-affecting properties forces the browser
		// to flush style + layout before the next paint.
		// eslint-disable-next-line no-unused-expressions
		el.offsetHeight;
		el.classList.add('dgv-fade-in');
	}

	function updateHeader(label, args) {
		var titleEl = panelEl.querySelector('#dgv-drill-title');
		var subEl   = panelEl.querySelector('#dgv-drill-scope-sub');
		titleEl.textContent = label || '—';
		subEl.textContent = scopeSubLine(args);
	}


	// =========================================================================
	// Components — header, hero, trend, by-company, by-party, action bar
	// =========================================================================

	function renderHeader(data, opts) {
		// Used by the full page; the panel renders header inline.
		opts = opts || {};
		var eyebrow = escapeHtml(opts.eyebrow || 'Account drill');
		var title   = escapeHtml(data.scope_label || '—');
		var sub     = escapeHtml(opts.scopeSub || '');
		return '' +
			'<div class="dgv-drill-eyebrow">' + eyebrow + '</div>' +
			'<h2 class="dgv-drill-title">' + title + '</h2>' +
			'<div class="dgv-drill-scope-sub">' + sub + '</div>';
	}

	function renderHero(data, opts) {
		opts = opts || {};
		var v = Number(data.group_total) || 0;
		var fig = formatCrore(v);
		var deltaHtml = renderHeroDelta(data);
		return '' +
			'<div class="dgv-drill-hero-eyebrow">Group total</div>' +
			'<div class="dgv-drill-hero-figure ' +
			(opts.tier === 'page' ? 'dgv-drill-hero-page' : '') + '">' +
				'<span class="dgv-drill-hero-amount">₹' + escapeHtml(fig.amount) + '</span>' +
				'<span class="dgv-drill-hero-unit">' + escapeHtml(fig.unit) + '</span>' +
			'</div>' +
			deltaHtml;
	}

	function renderHeroDelta(data) {
		// Compute MoM delta from the 12-month trend: latest minus the
		// month before it. Avoids shipping a separate field; trend
		// already has the values we need.
		var trend = data.trend_12mo || [];
		if (trend.length < 2) return '';
		var current = trend[trend.length - 1];
		var prior   = trend[trend.length - 2];
		if (!current || current.value === null || current.value === undefined) return '';
		if (!prior   || prior.value   === null || prior.value   === undefined) return '';
		var delta = (Number(current.value) || 0) - (Number(prior.value) || 0);
		if (Math.abs(delta) < 0.01) {
			return '<div class="dgv-drill-hero-delta flat">' +
			       'Unchanged from ' + escapeHtml(formatMonth(prior.month)) +
			       '</div>';
		}
		var dir   = delta > 0 ? 'Up' : 'Down';
		var klass = delta > 0 ? 'up' : 'down';
		var fig   = formatCrore(Math.abs(delta));
		return '<div class="dgv-drill-hero-delta ' + klass + '">' +
		       dir + ' ₹' + escapeHtml(fig.amount) + ' ' + escapeHtml(fig.unit) +
		       ' from ' + escapeHtml(formatMonth(prior.month)) +
		       '</div>';
	}

	/**
	 * Render a 12-month trend chart as inline SVG.
	 *
	 * data: array of { month: 'YYYY-MM', value: number|null }. Order is
	 *       oldest-first (left-to-right).
	 *
	 * opts: { width, height, monthLabels: 'sparse' | 'dense',
	 *         tooltipsEnabled: bool }
	 *
	 * Hover tooltips are enabled by default in the cockpit panel; the
	 * caller binds them post-render via bindTrendTooltip(hostEl).
	 */
	function renderTrendChart(data, opts) {
		opts = opts || {};
		var W = opts.width || 432;
		var H = opts.height || 120;
		var labelStyle = opts.monthLabels || 'sparse';
		var tooltipsEnabled = opts.tooltipsEnabled !== false;

		var padL = 6, padR = 6;
		var padT = 8;
		var padB = labelStyle === 'none' ? 8 : 22;  // room for axis labels
		var plotW = W - padL - padR;
		var plotH = H - padT - padB;

		if (!data.length) {
			return '<div class="dgv-drill-trend-empty">No trend data.</div>';
		}

		// Compute min/max across non-null values.
		var values = data.map(function (d) { return d.value; })
		                 .filter(function (v) { return v !== null && v !== undefined; });
		var hasData = values.length > 0;
		var min = hasData ? Math.min.apply(null, values) : 0;
		var max = hasData ? Math.max.apply(null, values) : 0;
		// Pad the range so flat lines don't sit on top/bottom edge.
		if (min === max) {
			var pad = Math.abs(max) * 0.1 || 1;
			min -= pad; max += pad;
		} else {
			var pad2 = (max - min) * 0.1;
			min -= pad2; max += pad2;
		}
		var range = max - min || 1;

		function xFor(i) { return padL + (i / (data.length - 1 || 1)) * plotW; }
		function yFor(v) { return padT + (1 - (v - min) / range) * plotH; }

		// Build a polyline path that splits at null gaps.
		var pathSegments = [];
		var seg = [];
		data.forEach(function (d, i) {
			if (d.value === null || d.value === undefined) {
				if (seg.length > 1) pathSegments.push(seg);
				seg = [];
				return;
			}
			seg.push([xFor(i), yFor(d.value)]);
		});
		if (seg.length > 1) pathSegments.push(seg);

		var pathsHtml = pathSegments.map(function (s) {
			var d = 'M' + s.map(function (p) { return p[0].toFixed(2) + ',' + p[1].toFixed(2); })
			              .join(' L');
			return '<path d="' + d + '" class="dgv-trend-line" />';
		}).join('');

		// Endpoint dot at the latest non-null point.
		var lastIdx = -1;
		for (var i = data.length - 1; i >= 0; i--) {
			if (data[i].value !== null && data[i].value !== undefined) { lastIdx = i; break; }
		}
		var endpointHtml = '';
		if (lastIdx >= 0) {
			endpointHtml = '<circle cx="' + xFor(lastIdx).toFixed(2) +
			               '" cy="' + yFor(data[lastIdx].value).toFixed(2) +
			               '" r="3" class="dgv-trend-endpoint" />';
		}

		// Hit-zones (transparent circles, larger radius for easier hover).
		var hitZonesHtml = '';
		if (tooltipsEnabled) {
			data.forEach(function (d, i) {
				if (d.value === null || d.value === undefined) return;
				var fig = formatCrore(d.value);
				var sign = d.value < 0 ? '−' : '';
				var label = sign + '₹' + fig.amount + ' ' + fig.unit + ' · ' + formatMonth(d.month);
				hitZonesHtml += '<circle class="dgv-trend-hit"' +
					' cx="' + xFor(i).toFixed(2) + '"' +
					' cy="' + yFor(d.value).toFixed(2) + '"' +
					' r="10"' +
					' data-tooltip="' + escapeAttr(label) + '"' +
					'></circle>';
			});
		}

		// Axis labels (months) -- sparse: every 3rd; dense: every label.
		var labelsHtml = '';
		if (labelStyle !== 'none') {
			var stride = labelStyle === 'dense' ? 1 : 3;
			data.forEach(function (d, i) {
				if (i % stride !== 0 && i !== data.length - 1) return;
				labelsHtml += '<text class="dgv-trend-axis-label"' +
					' x="' + xFor(i).toFixed(2) + '"' +
					' y="' + (H - 4) + '"' +
					' text-anchor="middle">' +
					escapeHtml(formatMonthShort(d.month)) +
					'</text>';
			});
		}

		// Subtle horizontal grid: top, mid, bottom.
		var gridHtml = '';
		[padT, padT + plotH / 2, padT + plotH].forEach(function (y) {
			gridHtml += '<line class="dgv-trend-grid"' +
				' x1="' + padL + '" x2="' + (W - padR) + '"' +
				' y1="' + y + '" y2="' + y + '"></line>';
		});

		return '<svg class="dgv-trend-svg" width="' + W + '" height="' + H + '"' +
			' viewBox="0 0 ' + W + ' ' + H + '"' +
			' role="img" aria-label="12-month trend chart">' +
			gridHtml +
			pathsHtml +
			endpointHtml +
			labelsHtml +
			hitZonesHtml +
			'</svg>';
	}

	/**
	 * Wire mousemove tooltips on a trend-chart host element. The chart
	 * SVG must have been rendered by renderTrendChart with
	 * tooltipsEnabled: true (default).
	 */
	function bindTrendTooltip(hostEl) {
		if (!hostEl) return;
		var hits = hostEl.querySelectorAll('.dgv-trend-hit');
		if (!hits.length) return;

		var tooltip = ensureTooltipEl();
		hits.forEach(function (hit) {
			hit.addEventListener('mouseenter', function (e) {
				tooltip.textContent = hit.getAttribute('data-tooltip') || '';
				tooltip.hidden = false;
			});
			hit.addEventListener('mousemove', function (e) {
				positionTooltip(tooltip, e);
			});
			hit.addEventListener('mouseleave', function () {
				tooltip.hidden = true;
			});
		});
	}

	function ensureTooltipEl() {
		var t = document.getElementById('dgv-drill-tooltip');
		if (t) return t;
		t = document.createElement('div');
		t.id = 'dgv-drill-tooltip';
		t.className = 'dgv-drill-tooltip';
		t.hidden = true;
		document.body.appendChild(t);
		return t;
	}

	function positionTooltip(el, evt) {
		var x = evt.clientX + 12;
		var y = evt.clientY - 28;
		// Clamp to viewport so the tooltip never disappears off-screen.
		var ww = window.innerWidth;
		var hh = window.innerHeight;
		var rect = el.getBoundingClientRect();
		if (x + rect.width > ww - 8) x = ww - rect.width - 8;
		if (y < 8) y = evt.clientY + 16;
		if (y + rect.height > hh - 8) y = hh - rect.height - 8;
		el.style.left = x + 'px';
		el.style.top  = y + 'px';
	}

	/**
	 * rows: [{ company, value, sparkline }]
	 *
	 * opts: { showSparklines, sparklineSize: [w, h], maxNameLength }
	 */
	function renderCompanyBreakdownTable(rows, opts) {
		opts = opts || {};
		if (!rows.length) {
			return '<div class="dgv-drill-empty">' +
				'No companies with activity in this scope.</div>';
		}
		var sparklineW = opts.sparklineSize ? opts.sparklineSize[0] : 80;
		var sparklineH = opts.sparklineSize ? opts.sparklineSize[1] : 14;

		var rowsHtml = rows.map(function (row) {
			var fig = formatCrore(row.value);
			var sign = (Number(row.value) || 0) < 0 ? '−' : '';
			var name = escapeHtml(row.company);
			if (opts.maxNameLength && row.company.length > opts.maxNameLength) {
				name = escapeHtml(row.company.slice(0, opts.maxNameLength - 1) + '…');
			}
			var sparkCell = '';
			if (opts.showSparklines) {
				sparkCell = '<td class="dgv-drill-co-spark">' +
					renderSparkline(row.sparkline || [], sparklineW, sparklineH) +
					'</td>';
			}
			return '<tr>' +
				'<td class="dgv-drill-co-name">' + name + '</td>' +
				sparkCell +
				'<td class="dgv-drill-co-value">' +
					sign + '₹' + escapeHtml(fig.amount) + ' ' +
					'<span class="dgv-drill-co-unit">' + escapeHtml(fig.unit) + '</span>' +
				'</td>' +
				'</tr>';
		}).join('');

		return '<table class="dgv-drill-co-table' +
			(opts.showSparklines ? ' dgv-drill-co-table-with-spark' : '') +
			'"><tbody>' + rowsHtml + '</tbody></table>';
	}

	/**
	 * Tiny inline-SVG sparkline. data: [number|null, ...] (12 entries).
	 */
	function renderSparkline(data, W, H) {
		if (!data.length) return '';
		var values = data.filter(function (v) { return v !== null && v !== undefined; });
		if (!values.length) return '';
		var min = Math.min.apply(null, values);
		var max = Math.max.apply(null, values);
		if (min === max) { min -= 1; max += 1; }
		var range = max - min;
		var padX = 1;
		function xFor(i) { return padX + (i / (data.length - 1 || 1)) * (W - 2 * padX); }
		function yFor(v) { return 2 + (1 - (v - min) / range) * (H - 4); }

		var d = '';
		var started = false;
		data.forEach(function (v, i) {
			if (v === null || v === undefined) return;
			d += (started ? ' L' : 'M') + xFor(i).toFixed(2) + ',' + yFor(v).toFixed(2);
			started = true;
		});
		if (!d) return '';
		return '<svg class="dgv-drill-spark" width="' + W + '" height="' + H +
			'" viewBox="0 0 ' + W + ' ' + H + '" aria-hidden="true">' +
			'<path d="' + d + '" class="dgv-drill-spark-line" />' +
			'</svg>';
	}

	/**
	 * rows: [{ party_type, party, balance, company_count, is_group_company }]
	 *
	 * opts: { total, displayedCount, showCompanyCount, showViewAll }
	 */
	function renderPartyBreakdownTable(rows, opts) {
		opts = opts || {};
		// Defensive client-side filter: drop sub-rupee residuals if any
		// slip past the server's HAVING ABS(balance) >= 1 (commit 3.1).
		// Negligible cost; protects the panel from showing "Rs 0" rows
		// if the server filter is ever bypassed or relaxed.
		rows = (rows || []).filter(function (r) {
			return Math.abs(Number(r && r.balance) || 0) >= 1;
		});

		var total = opts.total || rows.length;
		var displayed = opts.displayedCount || rows.length;

		// Subtitle qualifier explains the count: N is the count of
		// parties WITH NON-ZERO BALANCE (i.e. surviving the >=Rs 1
		// filter), not the total number of party rows in the GL.
		var subline = displayed >= total
			? total + (total === 1 ? ' party' : ' parties')
			  + ' with non-zero balance — sorted by balance'
			: 'Top ' + displayed + ' of ' + total
			  + ' with non-zero balance — sorted by balance';

		var viewAllBtn = opts.showViewAll
			? '<button class="dgv-drill-view-all" type="button">View all →</button>'
			: '';

		var rowsHtml = rows.map(function (row) {
			// formatRupeesIndian renders the balance directly in full
			// rupees with Indian comma grouping. Lakh-range balances
			// (the common case for AP/AR) read clearly here in a way
			// crore formatting (formatCrore) cropped away.
			var balanceHtml = formatRupeesIndian(row.balance);
			var groupBadge = row.is_group_company
				? '<span class="dgv-drill-party-group-badge"' +
				  ' title="Group company">Group co</span>'
				: '';
			var coCount = (opts.showCompanyCount && row.company_count > 1)
				? '<span class="dgv-drill-party-co-count">' +
				  row.company_count + ' cos</span>'
				: '';
			return '<tr>' +
				'<td class="dgv-drill-party-name">' +
				escapeHtml(row.party) + groupBadge + coCount +
				'</td>' +
				'<td class="dgv-drill-party-value">' + balanceHtml + '</td>' +
				'</tr>';
		}).join('');

		return '<header class="dgv-drill-party-head">' +
				'<div>' +
					'<div class="dgv-section-eyebrow">By party</div>' +
					'<div class="dgv-drill-party-sub">' + escapeHtml(subline) + '</div>' +
				'</div>' +
				viewAllBtn +
			'</header>' +
			'<table class="dgv-drill-party-table"><tbody>' + rowsHtml + '</tbody></table>';
	}

	function renderActionBar(opts) {
		opts = opts || {};
		var disabledAttr = opts.disabled ? ' disabled' : '';
		return '<button class="dgv-drill-action-primary" type="button"' + disabledAttr + '>' +
				'View GL entries →' +
			'</button>' +
			'<button class="dgv-drill-action-secondary" type="button"' + disabledAttr + '>' +
				'Export CSV' +
			'</button>';
	}

	function bindActionBar(actionsEl, ctx, args) {
		var primary = actionsEl.querySelector('.dgv-drill-action-primary');
		var secondary = actionsEl.querySelector('.dgv-drill-action-secondary');
		if (primary)   primary.addEventListener('click', function () { stubGlDrill(ctx, args); });
		if (secondary) secondary.addEventListener('click', function () { stubExportCsv(ctx, args); });
	}

	function bindPartyViewAll(host, args) {
		var btn = host.querySelector('.dgv-drill-view-all');
		if (btn) btn.addEventListener('click', function () { stubViewAllParties(args); });
	}


	// =========================================================================
	// Action handlers (commit 4 wired these from stubs)
	// =========================================================================
	// Function names retained so the window.dgvDrill exports + bindActionBar/
	// bindPartyViewAll wirings keep working without renames. stubExportCsv
	// and stubViewAllParties stay stubbed -- HALT 2 wires CSV; HALT 3 wires
	// the party-list page.

	function stubGlDrill(ctx, args) {
		// Both call sites land here:
		//   - Panel:    args = currentRequest shape (source/card_id/
		//               scope-as-{type,value}/companies/as_of_date)
		//   - Page:     args = page state shape (scope:{kind,id}/
		//               companies/as_of_date)
		// buildGlDrillUrl handles both shapes.
		var url = buildGlDrillUrl(args);
		window.location.href = url;
	}

	function stubExportCsv(ctx, args) {
		// HALT 2 wired: navigate to the export endpoint, browser
		// handles the file download via Content-Disposition: attachment.
		// `ctx.accounts` (when present, from card-resolved scopes) and
		// `currentRequest`/page-state args together carry the scope
		// info; buildAccountBreakdownCsvUrl normalizes both shapes.
		var url = buildAccountBreakdownCsvUrl(ctx, args);
		if (!url) {
			frappe.show_alert({
				message: 'Could not build export URL — scope is unresolvable.',
				indicator: 'red',
			}, 4);
			return;
		}
		window.location.href = url;
	}

	function stubViewAllParties(args) {
		// HALT 4 wired: navigate to /app/party-list with the same
		// scope shape as the panel/page is currently viewing. Click
		// happens from either the panel's "View all parties" link
		// (via bindPartyViewAll) or the account-drill full page's
		// matching link -- both pass the page-state-shaped args
		// object that buildPartyListUrl handles below.
		var url = buildPartyListUrl(args);
		if (!url) {
			frappe.show_alert({
				message: 'Could not build party-list URL — scope is unresolvable.',
				indicator: 'red',
			}, 4);
			return;
		}
		window.location.href = url;
	}

	/**
	 * Build /app/party-list?scope=...&as_of=...&companies=...
	 *
	 * Per HALT 4 spec: cross-scope navigation, so NO filter params
	 * carry over (party-list page doesn't have HALT 2.5-style
	 * filters yet anyway; future Phase 5 may add).
	 *
	 * Two arg shapes (mirror of buildGlDrillUrl):
	 *   - Panel:    args = currentRequest with {source, card_id,
	 *               scope:{type,value}, scope_label, as_of_date,
	 *               companies}
	 *   - Page:     args = page state with {scope:{kind,id},
	 *               as_of_date, companies, resolvedAccounts}
	 */
	function buildPartyListUrl(args) {
		var params = [];
		if (args.source === 'card' && args.card_id) {
			params.push('scope=' + encodeURIComponent(args.card_id));
		} else if (args.source === 'pivot' && args.scope &&
		           args.scope.value) {
			var prefix = (args.scope.type === 'subtree')
				? 'subtree:' : 'account:';
			params.push('scope=' + encodeURIComponent(prefix + args.scope.value));
		} else if (args.scope && args.scope.kind && args.scope.id) {
			if (args.scope.kind === 'card') {
				params.push('scope=' + encodeURIComponent(args.scope.id));
			} else {
				params.push('scope=' + encodeURIComponent(
					args.scope.kind + ':' + args.scope.id));
			}
		} else {
			return null;
		}
		if (args.as_of_date) {
			params.push('as_of=' + encodeURIComponent(args.as_of_date));
		}
		if (args.companies && args.companies.length) {
			params.push('companies=' + encodeURIComponent(args.companies.join(',')));
		}
		return '/app/party-list' + (params.length ? '?' + params.join('&') : '');
	}

	/**
	 * Build /app/gl-drill?scope=...&as_of=...&companies=... from either
	 * a panel-args object (source/card_id/scope-as-{type,value}) or a
	 * page-state object (scope:{kind,id}). Mirrors the URL contract
	 * the page reads via window.dgvParseAccountDrillHash.
	 *
	 * No page/page_size/sort emitted -- the GL page chooses defaults
	 * from its own toolbar on first load and pushes its own URL state
	 * thereafter. Linking with a forced sort/page would surprise the
	 * user (their toolbar would show that state on every visit from
	 * this entry point).
	 *
	 * **HALT 2.5 — DO NOT emit filter params here.** This helper is
	 * called from cross-scope navigation paths (panel → GL page,
	 * account-drill page → GL page). Per filter spec §5 amendment 3,
	 * filters are per-scope and reset on cross-scope navigation.
	 * The receiving GL page parses the URL, finds no filter params,
	 * and renders with default (empty) filter state. If a future
	 * call site needs to carry filters across scopes, it should NOT
	 * be added to this helper -- it should use a different entry
	 * point with explicit naming so the cross-scope-reset semantic
	 * stays visible in the URL contract.
	 */
	function buildGlDrillUrl(args) {
		var params = [];
		// Panel shape: source + card_id (card path) OR
		// source + scope-as-{type,value} (pivot path).
		if (args.source === 'card' && args.card_id) {
			params.push('scope=' + encodeURIComponent(args.card_id));
		} else if (args.source === 'pivot' && args.scope &&
		           args.scope.value) {
			var prefix = (args.scope.type === 'subtree')
				? 'subtree:' : 'account:';
			params.push('scope=' + encodeURIComponent(prefix + args.scope.value));
		}
		// Page-state shape: scope:{kind,id}.
		else if (args.scope && args.scope.kind && args.scope.id) {
			if (args.scope.kind === 'card') {
				params.push('scope=' + encodeURIComponent(args.scope.id));
			} else {
				params.push('scope=' + encodeURIComponent(
					args.scope.kind + ':' + args.scope.id));
			}
		}
		if (args.as_of_date) {
			params.push('as_of=' + encodeURIComponent(args.as_of_date));
		}
		if (args.companies && args.companies.length) {
			params.push('companies=' + encodeURIComponent(args.companies.join(',')));
		}
		return '/app/gl-drill' + (params.length ? '?' + params.join('&') : '');
	}

	/**
	 * Build the URL that downloads the account-breakdown CSV. Targets
	 * the whitelisted endpoint directly; browser handles the download
	 * via Content-Disposition: attachment from the server response.
	 *
	 * Two arg shapes (matching how stubExportCsv is called):
	 *   - Panel: ctx = { scope?, label?, accounts? } from
	 *            renderDrillData; args = currentRequest with
	 *            { source, card_id, scope, scope_label, as_of_date,
	 *              companies }.
	 *   - Page:  ctx = { label }; args = page state with
	 *            { scope: {kind, id}, as_of_date, companies,
	 *              resolvedAccounts } (the page resolves card scopes
	 *            to leaves before binding the export button).
	 *
	 * Prefer pre-resolved `accounts` over `scope` when available --
	 * cheaper for the server (no card-resolution round-trip) and lets
	 * card-driven exports work even if the card definition changes
	 * mid-session (URL captures a frozen leaf list).
	 */
	function buildAccountBreakdownCsvUrl(ctx, args) {
		var qs = [];
		// Prefer resolved accounts list -- ctx.accounts (panel)
		// or args.resolvedAccounts (page).
		var resolved = (ctx && ctx.accounts)
			|| (args && args.resolvedAccounts)
			|| null;
		if (Array.isArray(resolved) && resolved.length) {
			qs.push('accounts=' + encodeURIComponent(JSON.stringify(resolved)));
			var label = (ctx && ctx.label) || (args && args.scope_label)
				|| (args && args.scope && args.scope.value)
				|| (args && args.scope && args.scope.id) || '';
			if (label) qs.push('scope_label=' + encodeURIComponent(label));
		}
		// Otherwise fall back to ScopeSpec dict for {account, subtree} kinds.
		else if (args && args.scope && args.scope.value) {
			qs.push('scope=' + encodeURIComponent(JSON.stringify({
				type: args.scope.type, value: args.scope.value,
			})));
			if (args.scope_label) {
				qs.push('scope_label=' + encodeURIComponent(args.scope_label));
			}
		} else if (args && args.scope && args.scope.kind && args.scope.id
		           && args.scope.kind !== 'card') {
			qs.push('scope=' + encodeURIComponent(JSON.stringify({
				type: args.scope.kind, value: args.scope.id,
			})));
		} else {
			// No usable scope info -- caller should have shown an alert.
			return null;
		}
		if (args && args.as_of_date) {
			qs.push('as_of_date=' + encodeURIComponent(args.as_of_date));
		}
		if (args && args.companies && args.companies.length) {
			qs.push('companies=' + encodeURIComponent(JSON.stringify(args.companies)));
		}
		return '/api/method/dux_groupview.dux_groupview.api.account_drill_v1.export_account_breakdown_csv?'
			+ qs.join('&');
	}


	// =========================================================================
	// Expand → full page
	// =========================================================================

	function expandToFullPage() {
		if (!currentRequest) {
			closePanel();
			return;
		}
		var url = buildDrillUrl(currentRequest);
		closePanel();
		// frappe.set_route handles SPA-friendly navigation when the
		// caller is already inside the desk. Falling back to plain
		// location.href keeps the share-link / paste-URL path working
		// the same way (Frappe's router intercepts /app/* loads).
		try {
			window.location.href = url;
		} catch (e) {
			window.location.assign(url);
		}
	}

	function buildDrillUrl(args) {
		var params = [];
		if (args.source === 'card' && args.card_id) {
			params.push('scope=' + encodeURIComponent(args.card_id));
		} else if (args.source === 'pivot' && args.scope) {
			var sv = (args.scope.value || '');
			var prefix = (args.scope.type === 'subtree') ? 'subtree:' : 'account:';
			params.push('scope=' + encodeURIComponent(prefix + sv));
		}
		if (args.as_of_date) {
			params.push('as_of=' + encodeURIComponent(args.as_of_date));
		}
		if (args.companies && args.companies.length) {
			params.push('companies=' + encodeURIComponent(args.companies.join(',')));
		}
		return '/app/account-drill' + (params.length ? '?' + params.join('&') : '');
	}

	/**
	 * Parse the deep-link URL params on the full page. Returns:
	 *   { scope: {kind, id}, as_of_date, companies }
	 *
	 * scope.kind is one of 'card' | 'account' | 'subtree' (matching
	 * the encoding in buildDrillUrl).
	 */
	function parseDrillUrlParams(searchString) {
		var s = (searchString || window.location.search || '').replace(/^\?/, '');
		var out = { scope: null, as_of_date: null, companies: null };
		if (!s) return out;
		s.split('&').forEach(function (kv) {
			if (!kv) return;
			var eq = kv.indexOf('=');
			if (eq < 0) return;
			var k = decodeURIComponent(kv.slice(0, eq));
			var v = decodeURIComponent(kv.slice(eq + 1));
			if (k === 'scope') {
				if (v.indexOf('account:') === 0) {
					out.scope = { kind: 'account', id: v.slice(8) };
				} else if (v.indexOf('subtree:') === 0) {
					out.scope = { kind: 'subtree', id: v.slice(8) };
				} else {
					out.scope = { kind: 'card', id: v };
				}
			} else if (k === 'as_of') {
				out.as_of_date = v;
			} else if (k === 'companies') {
				out.companies = v.split(',').filter(Boolean);
			}
		});
		return out;
	}


	// =========================================================================
	// Key handler — Esc closes
	// =========================================================================

	function attachKeyHandler() {
		if (panelKeyHandler) return;
		panelKeyHandler = function (e) {
			if (e.key === 'Escape' || e.key === 'Esc') {
				closePanel();
			}
		};
		document.addEventListener('keydown', panelKeyHandler);
	}

	function detachKeyHandler() {
		if (!panelKeyHandler) return;
		document.removeEventListener('keydown', panelKeyHandler);
		panelKeyHandler = null;
	}


	// =========================================================================
	// Format helpers
	// =========================================================================

	function formatCrore(value) {
		var n = Number(value) || 0;
		var abs = Math.abs(n);
		// Default to crore. Nothing in the panel uses lakh.
		var cr = abs / 10000000;
		// 1 decimal at >= 1 Cr, 2 decimals below (so values like
		// "₹0.42 Cr" don't degenerate to "₹0.4 Cr").
		var amount = cr >= 1 ? cr.toFixed(1) : cr.toFixed(2);
		return { amount: amount, unit: 'Cr' };
	}

	// formatRupeesIndian — render a rupee amount in full with Indian
	// comma grouping (commit 3.1). Used by the by-party table only;
	// hero, by-company, and trend tooltip stay on formatCrore.
	//
	// Returns a ready-to-insert HTML string. Sub-rupee inputs (which
	// shouldn't reach here after the server's HAVING ABS(balance) >= 1
	// + the renderPartyBreakdownTable client-side filter) render as a
	// styled em-dash so the cell still renders sensibly if ever hit.
	//
	// Browser console smoke tests (no JS test infra in repo):
	//   formatRupeesIndian(47250)     === '₹47,250'
	//   formatRupeesIndian(248500)    === '₹2,48,500'
	//   formatRupeesIndian(24842500)  === '₹2,48,42,500'
	//   formatRupeesIndian(-47250)    === '−₹47,250'
	//   formatRupeesIndian(150000000) === '₹15,00,00,000'
	function formatRupeesIndian(rupees) {
		var n = Number(rupees) || 0;
		if (n === 0 || Math.abs(n) < 0.5) {
			return '<span class="dgv-zero-balance">—</span>';
		}
		var abs = Math.abs(Math.round(n));
		var sign = n < 0 ? '−' : '';
		return sign + '₹' + formatIndianGrouping(abs);
	}

	function formatIndianGrouping(n) {
		// Indian convention: rightmost 3 digits group, then groups of 2.
		//   47250    -> "47,250"
		//   248500   -> "2,48,500"
		//   24842500 -> "2,48,42,500"
		var s = String(n);
		if (s.length <= 3) return s;
		var last3 = s.slice(-3);
		var rest = s.slice(0, -3);
		var restGrouped = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',');
		return restGrouped + ',' + last3;
	}

	function formatMonth(yyyymm) {
		// 'YYYY-MM' → 'MMM YYYY'
		if (!yyyymm) return '';
		var parts = String(yyyymm).split('-');
		if (parts.length < 2) return yyyymm;
		var y = parseInt(parts[0], 10);
		var m = parseInt(parts[1], 10);
		var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
		              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
		if (!m || m < 1 || m > 12 || isNaN(y)) return yyyymm;
		return months[m - 1] + ' ' + y;
	}

	function formatMonthShort(yyyymm) {
		// 'YYYY-MM' → 'MMM' (or 'MMM YY' on January as a year boundary cue)
		if (!yyyymm) return '';
		var parts = String(yyyymm).split('-');
		if (parts.length < 2) return yyyymm;
		var y = parseInt(parts[0], 10);
		var m = parseInt(parts[1], 10);
		var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
		              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
		if (!m || m < 1 || m > 12 || isNaN(y)) return yyyymm;
		var label = months[m - 1];
		if (m === 1) label += " '" + String(y).slice(-2);
		return label;
	}

	function formatLongDate(iso) {
		if (!iso) return '';
		try {
			var d = new Date(iso);
			return d.toLocaleDateString(undefined, {
				day: 'numeric', month: 'long', year: 'numeric',
			});
		} catch (e) { return iso; }
	}


	// =========================================================================
	// Tiny escape helpers
	// =========================================================================

	function escapeHtml(s) {
		if (s === null || s === undefined) return '';
		return String(s)
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			.replace(/"/g, '&quot;')
			.replace(/'/g, '&#39;');
	}

	function escapeAttr(s) { return escapeHtml(s); }


	// =========================================================================
	// Error tiles (Phase 4 commit 6 HALT 6.2)
	//
	// Single source of truth for the four error categories the cockpit
	// surfaces consistently across cockpit cards, focus mode, drill
	// panel, GL drill page, and party-list page. Exposed as
	// `window.dgvRenderErrorTile` so each surface can route its
	// frappe.call error callback through here.
	//
	// Categories:
	//   network    -- httpStatus 0 / undefined / "Network Error"
	//   permission -- httpStatus 403
	//   invalid    -- httpStatus 404 with malformed_scope:true in body
	//   server     -- httpStatus 5xx (or any other non-2xx fallback)
	// =========================================================================

	function classifyError(xhrOrErr) {
		if (!xhrOrErr) {
			return { category: 'network' };
		}
		var status = (typeof xhrOrErr.status === 'number')
			? xhrOrErr.status
			: 0;
		var body = xhrOrErr.responseJSON || null;

		if (status === 0 || status === undefined) {
			return { category: 'network', status: status };
		}
		if (status === 403) {
			return { category: 'permission', status: status };
		}
		if (status === 404 && body && body.malformed_scope) {
			return { category: 'invalid', status: status };
		}
		if (status >= 500) {
			return { category: 'server', status: status };
		}
		// Catch-all: 4xx that isn't 403 / 404+malformed treated as
		// server-side something-went-wrong (the user can try again).
		return { category: 'server', status: status };
	}

	function renderErrorTile(xhrOrErr, hostEl, retryFn, opts) {
		opts = opts || {};
		if (!hostEl) return;

		var info = classifyError(xhrOrErr);
		var category = info.category;
		var message, actionText, actionFn;

		switch (category) {
		case 'network':
			message = 'Could not load this view. Check your connection and retry.';
			actionText = 'Retry';
			actionFn = retryFn || null;
			break;
		case 'permission':
			message = "You don't have permission to view this scope. " +
			          'Contact your administrator.';
			actionText = null;
			actionFn = null;
			break;
		case 'invalid':
			message = 'This link is no longer valid. Return to cockpit?';
			actionText = 'Cockpit';
			actionFn = function () { window.location.href = '/app/groupview'; };
			break;
		case 'server':
		default:
			message = 'Something went wrong. Try again or contact support.';
			actionText = 'Retry';
			actionFn = retryFn || null;
			break;
		}

		var compactClass = opts.compact ? ' dgv-error-compact' : '';
		var actionHtml = actionText
			? '<button class="dgv-error-action" type="button">' +
			  escapeHtml(actionText) + '</button>'
			: '';

		hostEl.innerHTML =
			'<div class="dgv-error-tile dgv-error-' + category + compactClass + '"' +
			' role="alert">' +
				'<div class="dgv-error-icon" aria-hidden="true">⚠</div>' +
				'<div class="dgv-error-message">' + escapeHtml(message) + '</div>' +
				actionHtml +
			'</div>';

		if (actionFn) {
			var btn = hostEl.querySelector('.dgv-error-action');
			if (btn) {
				btn.addEventListener('click', function () {
					try { actionFn(); } catch (e) { /* swallow */ }
				});
			}
		}
	}

})(window);
