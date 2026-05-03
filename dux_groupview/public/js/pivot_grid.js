/* DuxPivotGrid — virtualized pivot grid for the GroupView cockpit.
 *
 * Reads server payload from dux_groupview.api.pivot.get_pivot_data and
 * renders it as a virtualized HTML table with:
 *
 *   - Account hierarchy down the rows (sticky first column, indent + carets)
 *   - Companies across the columns, grouped under trust headers
 *   - Per-row Group Total column (sticky right)
 *   - Heatmap toggle (per-row magnitude tinted in trust colour)
 *   - Live search (substring match on account name)
 *   - Trust column collapse / expand
 *   - Account row collapse / expand
 *
 * Cell click dispatches a 'dux-pivot-cell-click' CustomEvent on the
 * container; cockpit.js listens and shows the Phase 4 placeholder toast.
 *
 * Depends on Clusterize (vendored at vendor/clusterize/clusterize.min.js,
 * loaded by hooks.py before this file).
 */

(function () {
	'use strict';

	if (typeof window.Clusterize === 'undefined') {
		console.error('[DuxPivotGrid] Clusterize.js not loaded');
		return;
	}

	const ROW_HEIGHT = 32;
	const HEATMAP_MIN_OPACITY = 0.05;
	const HEATMAP_MAX_OPACITY = 0.45;

	class DuxPivotGrid {
		constructor(containerEl, options) {
			this.container = containerEl;
			this.options = Object.assign(
				{ format: 'crore', height: 600 },
				options || {}
			);
			this.data = null;
			this.heatmap = false;
			this.searchQuery = '';
			this.collapsedTrusts = new Set();
			this.collapsedAccounts = new Set();
			this.clusterize = null;

			this._mountSkeleton();
		}

		// -----------------------------------------------------------------
		// Public API
		// -----------------------------------------------------------------

		render(data) {
			this.data = this._normalize(data);
			this._renderHeader();
			this._rebuildRows();
		}

		updateData(newData) {
			this.render(newData);
		}

		setHeatmap(enabled) {
			this.heatmap = !!enabled;
			this._rebuildRows();
		}

		setSearch(query) {
			this.searchQuery = (query || '').toLowerCase().trim();
			this._rebuildRows();
		}

		collapseTrust(trustId) {
			this.collapsedTrusts.add(trustId);
			this._renderHeader();
			this._rebuildRows();
		}

		expandTrust(trustId) {
			this.collapsedTrusts.delete(trustId);
			this._renderHeader();
			this._rebuildRows();
		}

		collapseAccount(accountId) {
			this.collapsedAccounts.add(accountId);
			this._rebuildRows();
		}

		expandAccount(accountId) {
			this.collapsedAccounts.delete(accountId);
			this._rebuildRows();
		}

		destroy() {
			this.container.innerHTML = '';
		}

		// -----------------------------------------------------------------
		// Skeleton (called once)
		// -----------------------------------------------------------------

		_mountSkeleton() {
			this.container.innerHTML = `
				<div class="pivot-container" style="height: ${this.options.height}px;">
					<div class="pivot-scroll" id="pivot-scroll">
						<table class="pivot-table">
							<thead id="pivot-head"></thead>
							<tbody class="clusterize-content" id="pivot-body" tabindex="0">
								<tr class="clusterize-no-data"><td>Loading…</td></tr>
							</tbody>
							<tfoot id="pivot-foot"></tfoot>
						</table>
					</div>
				</div>
			`;
			this.scrollEl = this.container.querySelector('#pivot-scroll');
			this.headEl = this.container.querySelector('#pivot-head');
			this.bodyEl = this.container.querySelector('#pivot-body');
			this.footEl = this.container.querySelector('#pivot-foot');

			// Single delegated click handler on the body for cell + caret clicks.
			this.bodyEl.addEventListener('click', (e) => this._onBodyClick(e));
			// Trust header clicks (collapse/expand) are wired during _renderHeader.
		}

		// -----------------------------------------------------------------
		// Data normalisation
		// -----------------------------------------------------------------

		_normalize(data) {
			// Pre-compute the visible column order so render passes can skip
			// rebuilding it. Columns are: trustId -> [company, company...].
			const trusts = data.trusts || [];
			const accounts = data.accounts || [];
			const balances = data.balances || {};

			const flatColumns = [];  // [{trustId, trustColor, company}]
			trusts.forEach(t => {
				t.companies.forEach(c => {
					flatColumns.push({
						trustId: t.id,
						trustColor: t.color,
						company: c,
					});
				});
			});

			return {
				snapshot_date: data.snapshot_date,
				snapshot_age_seconds: data.snapshot_age_seconds,
				format: data.format || this.options.format,
				trusts,
				accounts,
				balances,
				flatColumns,
			};
		}

		// -----------------------------------------------------------------
		// Header
		// -----------------------------------------------------------------

		_renderHeader() {
			const { trusts, flatColumns } = this.data;

			// Visible columns after collapsing trusts.
			const visibleCols = flatColumns.filter(
				c => !this.collapsedTrusts.has(c.trustId)
			);

			// Trust row.
			const trustRow = trusts.map(t => {
				const isCollapsed = this.collapsedTrusts.has(t.id);
				const span = isCollapsed ? 1 : t.companies.length;
				const caret = isCollapsed ? '▸' : '▾';
				return `
					<th class="pivot-trust-head"
					    colspan="${span}"
					    data-trust-id="${escapeAttr(t.id)}"
					    style="border-left-color: ${escapeAttr(t.color)};">
						<span class="pivot-trust-caret">${caret}</span>
						<span class="pivot-trust-abbr"
						      style="background: ${escapeAttr(t.color)};">${escapeHtml(t.abbr)}</span>
						<span class="pivot-trust-name">${escapeHtml(t.name)}</span>
					</th>
				`;
			}).join('');

			// Company row.
			let companyRow = '';
			trusts.forEach(t => {
				if (this.collapsedTrusts.has(t.id)) {
					companyRow += `<th class="pivot-company-head pivot-collapsed-cell"
					                   style="border-left-color: ${escapeAttr(t.color)};">
					                  <span class="pivot-collapsed-count">${t.companies.length}</span>
					                </th>`;
				} else {
					t.companies.forEach((c, idx) => {
						const borderStyle = idx === 0
							? `border-left: 3px solid ${escapeAttr(t.color)};` : '';
						companyRow += `<th class="pivot-company-head"
						                   style="${borderStyle}"
						                   title="${escapeAttr(c)}">${escapeHtml(c)}</th>`;
					});
				}
			});

			this.headEl.innerHTML = `
				<tr class="pivot-trust-row">
					<th class="pivot-corner pivot-sticky-left" rowspan="2">Account</th>
					${trustRow}
					<th class="pivot-total-head pivot-sticky-right" rowspan="2">Group Total</th>
				</tr>
				<tr class="pivot-company-row">${companyRow}</tr>
			`;

			// Wire trust header clicks.
			this.headEl.querySelectorAll('.pivot-trust-head').forEach(el => {
				el.addEventListener('click', () => {
					const trustId = el.getAttribute('data-trust-id');
					if (this.collapsedTrusts.has(trustId)) {
						this.expandTrust(trustId);
					} else {
						this.collapseTrust(trustId);
					}
				});
			});

			this._visibleCols = visibleCols;
		}

		// -----------------------------------------------------------------
		// Row HTML
		// -----------------------------------------------------------------

		_rebuildRows() {
			if (!this.data) return;

			const visible = this._visibleAccounts();
			const rows = visible.map(acct => this._buildRowHTML(acct));

			// Direct DOM render. Clusterize is vendored and loaded but
			// disabled here -- its scroll-spacer math conflicts with our
			// sticky first/last column setup, producing a bottom-of-scroll
			// snap-back when row content height differs from the CSS
			// height by even a pixel. With ~500 rows on dev (and a Phase 3
			// production target of ~700 unique accounts × 60 columns),
			// direct rendering is well within DOM performance budgets.
			// Production-scale revisit (Phase 3.5 if needed) can swap in
			// a frozen-column virtualization library properly.
			this.bodyEl.innerHTML = rows.join('') ||
				'<tr class="clusterize-no-data"><td>No accounts to show.</td></tr>';

			// Total row in <tfoot>, sticky to bottom of scroll container.
			this.footEl.innerHTML = this._buildTotalRowHTML(visible);
		}

		_visibleAccounts() {
			const { accounts } = this.data;
			const search = this.searchQuery;

			// Build descendant set for collapsed accounts so we can hide them.
			const hidden = new Set();
			if (this.collapsedAccounts.size) {
				const childrenOf = new Map();
				accounts.forEach(a => {
					if (!childrenOf.has(a.parent)) childrenOf.set(a.parent, []);
					childrenOf.get(a.parent).push(a.id);
				});
				const queue = [...this.collapsedAccounts];
				while (queue.length) {
					const id = queue.shift();
					(childrenOf.get(id) || []).forEach(childId => {
						hidden.add(childId);
						queue.push(childId);
					});
				}
			}

			let visible = accounts.filter(a => !hidden.has(a.id));

			if (search) {
				// When searching, show matching rows AND their ancestors (for context).
				const byId = new Map(accounts.map(a => [a.id, a]));
				const keep = new Set();
				visible.forEach(a => {
					if (a.name.toLowerCase().includes(search)) {
						keep.add(a.id);
						let cur = a;
						while (cur && cur.parent) {
							const p = byId.get(cur.parent);
							if (!p) break;
							keep.add(p.id);
							cur = p;
						}
					}
				});
				visible = visible.filter(a => keep.has(a.id));
				visible.searchActive = true;
			}

			return visible;
		}

		_buildRowHTML(acct) {
			const { balances, flatColumns } = this.data;
			const acctBalances = balances[acct.id] || {};

			// Compute row max for heatmap.
			let rowMax = 0;
			if (this.heatmap) {
				this._visibleCols.forEach(col => {
					const v = Math.abs(acctBalances[col.company] || 0);
					if (v > rowMax) rowMax = v;
				});
			}

			// Indent account label by depth.
			const indent = '<span class="pivot-indent" style="width: ' +
				(acct.depth * 16) + 'px;"></span>';
			const caret = acct.is_group
				? `<span class="pivot-account-caret"
				          data-account-id="${escapeAttr(acct.id)}">${
				    this.collapsedAccounts.has(acct.id) ? '▸' : '▾'
				}</span>`
				: '<span class="pivot-account-caret pivot-leaf">·</span>';
			const groupClass = acct.is_group ? ' pivot-row-group' : ' pivot-row-leaf';
			const searchHit = (this.searchQuery &&
				acct.name.toLowerCase().includes(this.searchQuery))
				? ' pivot-row-search-hit' : '';

			let html = `<tr class="pivot-row${groupClass}${searchHit}"
			                data-account-id="${escapeAttr(acct.id)}">`;
			html += `<td class="pivot-cell-label pivot-sticky-left">
			           ${indent}${caret}
			           <span class="pivot-account-name">${escapeHtml(acct.name)}</span>
			         </td>`;

			let visibleSum = 0;
			this.data.trusts.forEach(t => {
				if (this.collapsedTrusts.has(t.id)) {
					html += `<td class="pivot-cell-collapsed"
					            style="border-left-color: ${escapeAttr(t.color)};"
					            title="${escapeAttr(t.companies.length + ' companies hidden')}">
					           …
					         </td>`;
				} else {
					t.companies.forEach((c, idx) => {
						const v = acctBalances[c] || 0;
						visibleSum += v;
						const borderStyle = idx === 0
							? `border-left: 3px solid ${escapeAttr(t.color)};` : '';
						const heatStyle = (this.heatmap && rowMax > 0 && v !== 0)
							? `background-color: ${rgbaFromHex(t.color, _heatOpacity(Math.abs(v) / rowMax))};`
							: '';
						const styleAttr = (borderStyle || heatStyle)
							? ` style="${borderStyle}${heatStyle}"` : '';
						html += `<td class="pivot-cell-num"
						            data-account-id="${escapeAttr(acct.id)}"
						            data-company="${escapeAttr(c)}"
						            data-value="${v}"${styleAttr}>${
						    formatNumber(v, this.data.format)
						}</td>`;
					});
				}
			});

			html += `<td class="pivot-cell-total pivot-sticky-right">${
			    formatNumber(visibleSum, this.data.format)
			}</td>`;
			html += `</tr>`;
			return html;
		}

		_buildTotalRowHTML(accounts) {
			// Bottom total row sums leaf-account balances by company so we
			// don't double-count groups.
			const totals = {};
			let grand = 0;
			this.data.flatColumns.forEach(c => totals[c.company] = 0);
			accounts.forEach(a => {
				if (a.is_group) return;
				const balances = this.data.balances[a.id] || {};
				this.data.flatColumns.forEach(c => {
					const v = balances[c.company] || 0;
					totals[c.company] += v;
				});
			});
			let html = `<tr class="pivot-row pivot-row-total">`;
			html += `<td class="pivot-cell-label pivot-sticky-left">
			           <span class="pivot-account-name">Total (leaf accounts)</span>
			         </td>`;
			this.data.trusts.forEach(t => {
				if (this.collapsedTrusts.has(t.id)) {
					html += `<td class="pivot-cell-collapsed"
					            style="border-left-color: ${escapeAttr(t.color)};">…</td>`;
				} else {
					t.companies.forEach((c, idx) => {
						const v = totals[c] || 0;
						grand += v;
						const borderStyle = idx === 0
							? `border-left: 3px solid ${escapeAttr(t.color)};` : '';
						const styleAttr = borderStyle ? ` style="${borderStyle}"` : '';
						html += `<td class="pivot-cell-num pivot-cell-total-num"${styleAttr}>${
						    formatNumber(v, this.data.format)
						}</td>`;
					});
				}
			});
			html += `<td class="pivot-cell-total pivot-sticky-right">${
			    formatNumber(grand, this.data.format)
			}</td>`;
			html += `</tr>`;
			return html;
		}

		// -----------------------------------------------------------------
		// Body click delegation
		// -----------------------------------------------------------------

		_onBodyClick(e) {
			const caret = e.target.closest('.pivot-account-caret');
			if (caret && !caret.classList.contains('pivot-leaf')) {
				const id = caret.getAttribute('data-account-id');
				if (this.collapsedAccounts.has(id)) {
					this.expandAccount(id);
				} else {
					this.collapseAccount(id);
				}
				return;
			}
			const cell = e.target.closest('.pivot-cell-num');
			if (cell && !cell.classList.contains('pivot-cell-total-num')) {
				const detail = {
					account: cell.getAttribute('data-account-id'),
					company: cell.getAttribute('data-company'),
					value: parseFloat(cell.getAttribute('data-value')) || 0,
					snapshot_date: this.data && this.data.snapshot_date,
				};
				this.container.dispatchEvent(new CustomEvent('dux-pivot-cell-click', {
					detail, bubbles: true,
				}));
			}
		}
	}

	// ---------------------------------------------------------------------
	// Number formatting + colour helpers
	// ---------------------------------------------------------------------

	function formatNumber(value, format) {
		const num = Number(value) || 0;
		if (num === 0) return '<span class="pivot-zero">—</span>';
		let scaled, suffix;
		if (format === 'lakh') {
			scaled = num / 100000;
			suffix = '';  // suffix shown in column header instead
		} else {
			scaled = num / 10000000;
			suffix = '';
		}
		const abs = Math.abs(scaled);
		const formatted = abs.toLocaleString('en-IN', {
			minimumFractionDigits: 2, maximumFractionDigits: 2,
		});
		if (num < 0) return `<span class="pivot-neg">(${formatted})</span>${suffix}`;
		return `${formatted}${suffix}`;
	}

	function rgbaFromHex(hex, alpha) {
		const h = (hex || '#888780').replace('#', '');
		const bigint = parseInt(h.length === 3 ?
			h.split('').map(x => x + x).join('') : h, 16);
		const r = (bigint >> 16) & 255;
		const g = (bigint >> 8) & 255;
		const b = bigint & 255;
		return `rgba(${r}, ${g}, ${b}, ${alpha})`;
	}

	function _heatOpacity(ratio) {
		// Map [0..1] -> [HEATMAP_MIN_OPACITY..HEATMAP_MAX_OPACITY].
		return HEATMAP_MIN_OPACITY +
			(HEATMAP_MAX_OPACITY - HEATMAP_MIN_OPACITY) * Math.min(1, Math.max(0, ratio));
	}

	function escapeHtml(s) {
		if (s === null || s === undefined) return '';
		return String(s).replace(/[&<>"']/g, ch => ({
			'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
		}[ch]));
	}
	function escapeAttr(s) { return escapeHtml(s); }

	window.DuxPivotGrid = DuxPivotGrid;
})();
