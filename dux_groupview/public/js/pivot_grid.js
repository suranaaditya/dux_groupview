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
				{ format: 'crore', height: 600, depth: 'all' },
				options || {}
			);
			this.data = null;
			this.heatmap = false;
			this.searchQuery = '';
			this.collapsedTrusts = new Set();
			// `collapsedAccounts` and `userExpanded` together model the
			// user's manual expand/collapse intent, layered on top of the
			// depth-driven default. See _isGroupExpanded for the merge
			// rule. We keep them as separate sets (rather than a single
			// "currentlyExpanded" set) so toggling depth doesn't silently
			// erase the user's manual choices.
			this.collapsedAccounts = new Set();
			this.userExpanded = new Set();
			this.depth = this.options.depth;
			this.format = this.options.format || 'crore';
			this._byId = null;
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

		setFormat(fmt) {
			// fmt: "crore" | "lakh" | "full". Anything else collapses
			// to "crore" (the spec default; safer than blank cells).
			const next = (fmt === 'lakh' || fmt === 'full') ? fmt : 'crore';
			this.format = next;
			if (this.data) this.data.format = next;
			// Mark the table so CSS can widen numeric columns under
			// "full" (17-char Indian-format numbers don't fit the
			// default 110px min-width).
			const table = this.container.querySelector('.pivot-table');
			if (table) table.setAttribute('data-format', next);
			this._rebuildRows();
		}

		setDepth(n) {
			// n: a number (1, 2, 3, ...) or the string "all". Anything
			// else collapses to "all" (= every account visible by depth).
			if (n === 'all' || n === null || n === undefined) {
				this.depth = 'all';
			} else {
				const parsed = parseInt(n, 10);
				this.depth = (!isNaN(parsed) && parsed >= 0) ? parsed : 'all';
			}
			// Manual expand/collapse state intentionally preserved across
			// depth changes -- a user who expanded a deep group keeps that
			// expansion when they switch depths.
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
			const group = this._byId && this._byId.get(accountId);
			if (!group) {
				this.collapsedAccounts.add(accountId);
				this._rebuildRows();
				return;
			}
			this.userExpanded.delete(accountId);
			if (this._visibleByDepth(group)) {
				this.collapsedAccounts.add(accountId);
			} else {
				this.collapsedAccounts.delete(accountId);
			}
			this._rebuildRows();
		}

		expandAccount(accountId) {
			const group = this._byId && this._byId.get(accountId);
			if (!group) {
				this.collapsedAccounts.delete(accountId);
				this._rebuildRows();
				return;
			}
			this.collapsedAccounts.delete(accountId);
			if (!this._visibleByDepth(group)) {
				this.userExpanded.add(accountId);
			} else {
				this.userExpanded.delete(accountId);
			}
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

			// Sync the table's data-format attribute now (rather than only
			// from setFormat()) so a fresh render() picks up the option.
			const table = this.container.querySelector('.pivot-table');
			if (table) table.setAttribute('data-format', this.format);

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

			// Index accounts by id so depth/visibility helpers can walk
			// the parent chain in O(depth).
			this._byId = new Map(accounts.map(a => [a.id, a]));

			return {
				snapshot_date: data.snapshot_date,
				snapshot_age_seconds: data.snapshot_age_seconds,
				// `this.format` is the live runtime value (mutated by
				// setFormat). Server payload's format is treated as a
				// hint only; user toolbar wins.
				format: this.format,
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
			const byId = this._byId;
			const search = this.searchQuery;

			let candidates;
			if (search) {
				// Search overrides depth + manual collapse: show every
				// account whose name matches, plus its ancestors so the
				// hierarchy context is preserved.
				const keep = new Set();
				accounts.forEach(a => {
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
				candidates = accounts.filter(a => keep.has(a.id));
			} else {
				// Default mode: visible iff every ancestor in the chain is
				// "expanded" in the merged sense (depth-default + manual
				// overrides). See _isGroupExpanded.
				candidates = accounts.filter(a => this._isAccountVisible(a));
			}

			// Hide rows whose displayed value is 0. We check the SUM across
			// visible columns rather than each cell individually -- the
			// per-cell check would keep "offsetting" rows where +X in one
			// company and -X in another net to 0 in the Group Total but
			// each individual cell is non-zero. Those rows show as all
			// dashes in the displayed total, so users (correctly) expect
			// them hidden.
			//
			// Ancestor promotion: any non-zero-sum account keeps its full
			// parent chain so groups never lose their children. The
			// bubble-up invariant in pivot.py guarantees that if any
			// descendant has a non-zero contribution to the group's sum,
			// the group's sum is also non-zero (otherwise the group's
			// descendants must also exactly offset). So the promotion is
			// mostly a no-op safety net.
			const visibleCompanies = (this._visibleCols || [])
				.map(c => c.company);
			if (!visibleCompanies.length) return candidates;
			const balances = this.data.balances || {};
			const keep = new Set();
			// Coerce to Number explicitly: Frappe occasionally serializes
			// decimals as strings, and string arithmetic would concat
			// instead of sum. Number("0.00") === 0 is correct.
			candidates.forEach(a => {
				const row = balances[a.id];
				if (!row) return;
				let sum = 0;
				for (let i = 0; i < visibleCompanies.length; i++) {
					const n = Number(row[visibleCompanies[i]]);
					if (isFinite(n)) sum += n;
				}
				// 1-paisa floor absorbs floating-point dust without hiding
				// any business-meaningful value.
				if (Math.abs(sum) < 0.01) return;
				keep.add(a.id);
				let cur = a;
				while (cur && cur.parent) {
					const p = byId.get(cur.parent);
					if (!p) break;
					keep.add(p.id);
					cur = p;
				}
			});
			return candidates.filter(a => keep.has(a.id));
		}

		_visibleByDepth(node) {
			// Semantics: `depth = N` shows N levels (1 = roots only,
			// 2 = roots + first level of children, etc.). A node's
			// children sit at node.depth + 1; they are visible by
			// default only if node.depth + 1 < N (strict inequality --
			// at depth=1 the root is at depth 0 and its children at
			// depth 1 should NOT be visible, since "1 level" = roots
			// alone).
			if (this.depth === 'all') return true;
			return (node.depth + 1) < this.depth;
		}

		_isGroupExpanded(group) {
			// Merge rule:
			//   user collapsed wins  → false
			//   else user expanded   → true
			//   else depth default
			if (this.collapsedAccounts.has(group.id)) return false;
			if (this.userExpanded.has(group.id)) return true;
			return this._visibleByDepth(group);
		}

		_isAccountVisible(account) {
			// Walk ancestors. If any ancestor is "effectively collapsed"
			// per _isGroupExpanded, the account is hidden.
			let cur = account;
			while (cur && cur.parent) {
				const parent = this._byId.get(cur.parent);
				if (!parent) break;
				if (!this._isGroupExpanded(parent)) return false;
				cur = parent;
			}
			return true;
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
				    this._isGroupExpanded(acct) ? '▾' : '▸'
				}</span>`
				: '<span class="pivot-account-caret pivot-leaf">·</span>';
			// Group-row icon: two horizontal stripes evoking a
			// "summary line". Inline SVG so it inherits currentColor
			// and stays crisp at any zoom level.
			const groupIcon = acct.is_group
				? '<svg class="pivot-account-icon" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">' +
				  '<rect x="1" y="3" width="10" height="1.5" rx="0.5" fill="currentColor" />' +
				  '<rect x="1" y="7" width="10" height="1.5" rx="0.5" fill="currentColor" />' +
				  '</svg>'
				: '';
			const groupClass = acct.is_group ? ' pivot-row-group' : ' pivot-row-leaf';
			const searchHit = (this.searchQuery &&
				acct.name.toLowerCase().includes(this.searchQuery))
				? ' pivot-row-search-hit' : '';

			let html = `<tr class="pivot-row${groupClass}${searchHit}"
			                data-account-id="${escapeAttr(acct.id)}"
			                data-depth="${acct.depth}">`;
			html += `<td class="pivot-cell-label pivot-sticky-left">
			           ${indent}${caret}${groupIcon}
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
				const group = this._byId && this._byId.get(id);
				if (group && this._isGroupExpanded(group)) {
					this.collapseAccount(id);
				} else {
					this.expandAccount(id);
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

		if (format === 'full') {
			// Raw rupee value, Indian comma grouping, 2 decimal places.
			// Negatives wrapped in parens (accounting convention; the
			// minus sign from Indian formatting is dropped in favour of
			// surrounding parentheses, mirroring the crore/lakh paths).
			const formatted = formatIndian(num);
			if (num < 0) {
				// formatIndian already wraps negatives in parens; mark
				// with .pivot-neg so the colour rule applies.
				return `<span class="pivot-neg">${formatted}</span>`;
			}
			return formatted;
		}

		// crore / lakh: scale, then locale-format (en-IN handles
		// Indian grouping for the integer part).
		const scaled = (format === 'lakh') ? num / 100000 : num / 10000000;
		const abs = Math.abs(scaled);
		const formatted = abs.toLocaleString('en-IN', {
			minimumFractionDigits: 2, maximumFractionDigits: 2,
		});
		if (num < 0) return `<span class="pivot-neg">(${formatted})</span>`;
		return formatted;
	}

	// Indian-format the raw value. Last 3 digits before the decimal,
	// then groups of 2 to the left. Mirrors the spec'd JS reference and
	// the Python equivalent in dux_groupview/pivot/format.py used by
	// the unit test.
	function formatIndian(value) {
		if (value === 0) return '0.00';
		const abs = Math.abs(value).toFixed(2);  // "12345678901.23"
		const [whole, decimal] = abs.split('.');
		const lastThree = whole.slice(-3);
		const rest = whole.slice(0, -3);
		// `\B(?=(\d{2})+(?!\d))` inserts commas before every position
		// where 2+ pairs of digits follow with no trailing digit -- the
		// classic Indian-grouping regex.
		const restWithCommas = rest
			? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')
			: '';
		const formatted = restWithCommas
			? `${restWithCommas},${lastThree}.${decimal}`
			: `${lastThree}.${decimal}`;
		return value < 0 ? `(${formatted})` : formatted;
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
