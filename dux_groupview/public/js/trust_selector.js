/* DuxTrustSelector — header-pill popover for cockpit scope.
 *
 * Lets the user pick a subset of trusts and companies. The current
 * scope drives both the spotlight cards and the pivot grid in the
 * cockpit page. State is held in two parallel sets:
 *
 *     committed  -- the scope currently in effect (mirrors the pill
 *                   summary). Updated only when Apply is clicked.
 *     draft      -- the in-popover working copy. Synced from committed
 *                   on open(); dropped on Cancel; promoted to
 *                   committed on Apply.
 *
 * Tri-state checkboxes use inline SVG (no library).
 *
 * Public API:
 *
 *     new DuxTrustSelector(triggerEl, {
 *         trusts,             // array from api.pivot.get_scope_options
 *         initialSelection,   // array of company names; defaults to
 *                             // every company in `trusts`
 *         onApply,            // (selectedCompanies, isAll) => void
 *         onCancel,           // () => void
 *     })
 *
 *     selector.open()
 *     selector.close()
 *     selector.getSelection() -> array of company names
 *     selector.setSelection(arr) -> void  (also re-renders the pill)
 *     selector.summarize()    -> {label, isAll}
 */

(function () {
	'use strict';

	const ALL_TRUSTS_THRESHOLD = 4; // >= this -> "N trusts, M companies"

	class DuxTrustSelector {
		constructor(triggerEl, options) {
			this.trigger = triggerEl;
			this.options = Object.assign({
				trusts: [],
				initialSelection: null,
				onApply: () => {},
				onCancel: () => {},
			}, options || {});

			this.allCompanies = this._collectAllCompanies(this.options.trusts);
			this.companyToTrust = this._buildCompanyTrustMap(this.options.trusts);

			// committed: the canonical scope the cockpit is currently rendering.
			this.committed = new Set(
				this.options.initialSelection || this.allCompanies
			);
			// Drop any stale company that's no longer in the user's allowed set.
			for (const c of [...this.committed]) {
				if (!this.allCompanies.includes(c)) this.committed.delete(c);
			}
			// Empty committed isn't useful -- coerce to "all".
			if (this.committed.size === 0) {
				this.committed = new Set(this.allCompanies);
			}

			this.draft = new Set(this.committed);
			this.expandedTrusts = new Set();
			this.searchQuery = '';
			this.popoverEl = null;
			this.isOpen = false;

			// Update the pill once on construction.
			this._renderPill();

			// Wire trigger.
			this._onTriggerClick = this._onTriggerClick.bind(this);
			this._onDocumentClick = this._onDocumentClick.bind(this);
			this._onDocumentKeydown = this._onDocumentKeydown.bind(this);
			this.trigger.addEventListener('click', this._onTriggerClick);
		}

		// -----------------------------------------------------------------
		// Public API
		// -----------------------------------------------------------------

		open() {
			if (this.isOpen) return;
			this.isOpen = true;
			this.draft = new Set(this.committed);
			this._autoExpandWithDraft();
			this._mountPopover();
			document.addEventListener('mousedown', this._onDocumentClick, true);
			document.addEventListener('keydown', this._onDocumentKeydown, true);
		}

		close() {
			if (!this.isOpen) return;
			this.isOpen = false;
			document.removeEventListener('mousedown', this._onDocumentClick, true);
			document.removeEventListener('keydown', this._onDocumentKeydown, true);
			if (this.popoverEl && this.popoverEl.parentNode) {
				this.popoverEl.parentNode.removeChild(this.popoverEl);
			}
			this.popoverEl = null;
			this.searchQuery = '';
		}

		getSelection() {
			return [...this.committed].sort();
		}

		setSelection(companyArray) {
			let next = new Set(
				(companyArray || []).filter(c => this.allCompanies.includes(c))
			);
			if (next.size === 0) {
				next = new Set(this.allCompanies);
			}
			this.committed = next;
			this._renderPill();
		}

		summarize() {
			return _summarize(this.committed, this.options.trusts, this.allCompanies);
		}

		destroy() {
			this.close();
			this.trigger.removeEventListener('click', this._onTriggerClick);
		}

		// -----------------------------------------------------------------
		// Trigger + document handlers
		// -----------------------------------------------------------------

		_onTriggerClick(e) {
			e.preventDefault();
			e.stopPropagation();
			if (this.isOpen) {
				this.close();
				this.options.onCancel();
			} else {
				this.open();
			}
		}

		_onDocumentClick(e) {
			if (!this.popoverEl) return;
			if (this.popoverEl.contains(e.target)) return;
			if (this.trigger.contains(e.target)) return;
			// Outside click -> cancel.
			this.close();
			this.options.onCancel();
		}

		_onDocumentKeydown(e) {
			if (e.key === 'Escape') {
				e.preventDefault();
				this.close();
				this.options.onCancel();
			} else if (e.key === 'Enter' &&
				this.popoverEl && this.popoverEl.contains(document.activeElement)) {
				const tag = (document.activeElement.tagName || '').toLowerCase();
				// Don't hijack Enter while typing in the search box -- they
				// might just be flushing the input. Only Apply via Enter
				// when focus is on a row or a button.
				if (tag !== 'input') {
					e.preventDefault();
					this._handleApply();
				}
			}
		}

		// -----------------------------------------------------------------
		// Pill (the trigger label)
		// -----------------------------------------------------------------

		_renderPill() {
			const { label, isAll } = this.summarize();
			// The trigger is owned by the host page; keep the structure
			// minimal so the host's CSS controls visual style.
			this.trigger.classList.toggle('dgv-scope-pill-all', isAll);
			this.trigger.classList.toggle('dgv-scope-pill-scoped', !isAll);
			this.trigger.innerHTML = `
				<span class="dgv-scope-pill-prefix">Showing:</span>
				<span class="dgv-scope-pill-summary">${escapeHtml(label)}</span>
				<span class="dgv-scope-pill-caret" aria-hidden="true">▾</span>
			`;
			this.trigger.setAttribute('aria-haspopup', 'true');
			this.trigger.setAttribute('aria-expanded', this.isOpen ? 'true' : 'false');
		}

		// -----------------------------------------------------------------
		// Popover lifecycle
		// -----------------------------------------------------------------

		_mountPopover() {
			const container = this.options.container || document.body;
			this.popoverEl = document.createElement('div');
			this.popoverEl.className = 'dgv-trust-popover';
			this.popoverEl.setAttribute('role', 'dialog');
			this.popoverEl.setAttribute('aria-label', 'Pick trusts and companies');
			container.appendChild(this.popoverEl);

			this._positionPopover();
			this._renderPopover();

			// Reposition on resize or scroll while open.
			this._reposition = () => this._positionPopover();
			window.addEventListener('resize', this._reposition);
			window.addEventListener('scroll', this._reposition, true);

			// Focus the search input for fast filtering.
			const search = this.popoverEl.querySelector('.dgv-popover-search');
			if (search) search.focus();
		}

		_positionPopover() {
			if (!this.popoverEl) return;
			const rect = this.trigger.getBoundingClientRect();
			const popHeight = this.popoverEl.offsetHeight || 480;
			const margin = 6;
			let top = rect.bottom + margin + window.scrollY;
			// If overflows viewport bottom, drop it above the trigger.
			if (rect.bottom + popHeight + margin > window.innerHeight) {
				const above = rect.top - popHeight - margin + window.scrollY;
				if (above >= window.scrollY + margin) top = above;
			}
			let left = rect.left + window.scrollX;
			const popWidth = 620;
			if (left + popWidth > window.innerWidth - margin) {
				left = Math.max(margin, window.innerWidth - popWidth - margin);
			}
			this.popoverEl.style.top = `${top}px`;
			this.popoverEl.style.left = `${left}px`;
		}

		_renderPopover() {
			const summary = _summarize(this.draft, this.options.trusts, this.allCompanies);
			this.popoverEl.innerHTML = `
				<div class="dgv-popover-header">
					<input type="text" class="dgv-popover-search"
					       placeholder="Search trusts and companies…"
					       autocomplete="off" spellcheck="false"
					       value="${escapeAttr(this.searchQuery)}" />
					<div class="dgv-popover-bulk">
						<button type="button" class="dgv-popover-link"
						        data-action="select-all">Select all</button>
						<span class="dgv-popover-link-sep">·</span>
						<button type="button" class="dgv-popover-link"
						        data-action="deselect-all">Clear</button>
					</div>
				</div>
				<div class="dgv-popover-list" id="dgv-popover-list"></div>
				<div class="dgv-popover-footer">
					<span class="dgv-popover-summary">${escapeHtml(summary.label)}</span>
					<div class="dgv-popover-actions">
						<button type="button" class="dgv-popover-btn dgv-popover-btn-secondary"
						        data-action="cancel">Cancel</button>
						<button type="button" class="dgv-popover-btn dgv-popover-btn-primary"
						        data-action="apply">Apply</button>
					</div>
				</div>
			`;

			this._renderList();

			// Wire popover handlers (delegated where reasonable).
			this.popoverEl.addEventListener('click', (e) => this._onPopoverClick(e));
			this.popoverEl.addEventListener('change', (e) => this._onPopoverChange(e));
			const search = this.popoverEl.querySelector('.dgv-popover-search');
			search.addEventListener('input', (e) => {
				this.searchQuery = e.target.value || '';
				this._autoExpandWithSearch();
				this._renderList();
			});
		}

		// -----------------------------------------------------------------
		// List rendering
		// -----------------------------------------------------------------

		_renderList() {
			const listEl = this.popoverEl.querySelector('#dgv-popover-list');
			const q = this.searchQuery.trim().toLowerCase();
			const trusts = this.options.trusts;

			let html = '';
			let anyRows = false;

			trusts.forEach(trust => {
				const filteredCompanies = q
					? trust.companies.filter(c =>
						c.toLowerCase().includes(q) || trust.name.toLowerCase().includes(q)
						|| (trust.abbr || '').toLowerCase().includes(q))
					: trust.companies;

				const trustNameMatches = q && (
					trust.name.toLowerCase().includes(q) ||
					(trust.abbr || '').toLowerCase().includes(q)
				);

				// If the search query doesn't hit the trust name and no
				// companies match, skip the trust entirely.
				if (q && !trustNameMatches && filteredCompanies.length === 0) {
					return;
				}

				anyRows = true;
				const triState = this._trustTriState(trust);
				const expanded = this.expandedTrusts.has(trust.id);
				const showCompanies = expanded || (q && filteredCompanies.length > 0);

				html += this._trustRowHTML(trust, triState, expanded);
				if (showCompanies) {
					filteredCompanies.forEach(c => {
						html += this._companyRowHTML(trust, c);
					});
				}
			});

			if (!anyRows) {
				html = `<div class="dgv-popover-empty">No matches.</div>`;
			}

			listEl.innerHTML = html;
			// Update footer summary.
			const summary = _summarize(this.draft, trusts, this.allCompanies);
			const summaryEl = this.popoverEl.querySelector('.dgv-popover-summary');
			if (summaryEl) summaryEl.textContent = summary.label;
		}

		_trustRowHTML(trust, triState, expanded) {
			const caret = expanded ? '▾' : '▸';
			const checkboxSvg = _checkboxSvg(triState, trust.color);
			return `
				<div class="dgv-popover-row dgv-popover-trust-row${expanded ? ' is-expanded' : ''}"
				     data-trust-id="${escapeAttr(trust.id)}">
					<button type="button" class="dgv-popover-caret"
					        data-action="toggle-expand"
					        data-trust-id="${escapeAttr(trust.id)}"
					        aria-label="${escapeAttr(expanded ? 'Collapse' : 'Expand')}">${caret}</button>
					<button type="button" class="dgv-popover-checkbox dgv-popover-checkbox-${triState}"
					        data-action="toggle-trust"
					        data-trust-id="${escapeAttr(trust.id)}"
					        aria-label="Toggle ${escapeAttr(trust.name)}"
					        aria-checked="${triState === 'check' ? 'true' : (triState === 'dash' ? 'mixed' : 'false')}">
						${checkboxSvg}
					</button>
					<span class="dgv-popover-dot"
					      style="background:${escapeAttr(trust.color || '#888780')};"></span>
					<span class="dgv-popover-trust-abbr"
					      style="color:${escapeAttr(trust.color || '#888780')};">${escapeHtml(trust.abbr || '')}</span>
					<span class="dgv-popover-trust-name">${escapeHtml(trust.name || '')}</span>
					<span class="dgv-popover-count">${this._trustCountLabel(trust)}</span>
				</div>
			`;
		}

		_companyRowHTML(trust, companyName) {
			const checked = this.draft.has(companyName);
			const triState = checked ? 'check' : 'empty';
			const checkboxSvg = _checkboxSvg(triState, trust.color);
			return `
				<div class="dgv-popover-row dgv-popover-company-row"
				     data-company="${escapeAttr(companyName)}"
				     data-trust-id="${escapeAttr(trust.id)}">
					<span class="dgv-popover-caret-spacer"></span>
					<button type="button" class="dgv-popover-checkbox dgv-popover-checkbox-${triState}"
					        data-action="toggle-company"
					        data-company="${escapeAttr(companyName)}"
					        aria-checked="${checked ? 'true' : 'false'}"
					        aria-label="Toggle ${escapeAttr(companyName)}">
						${checkboxSvg}
					</button>
					<span class="dgv-popover-company-name">${escapeHtml(companyName)}</span>
				</div>
			`;
		}

		// -----------------------------------------------------------------
		// Event delegation
		// -----------------------------------------------------------------

		_onPopoverClick(e) {
			const action = e.target.closest('[data-action]');
			if (!action) return;
			const what = action.getAttribute('data-action');
			if (what === 'toggle-expand') {
				const id = action.getAttribute('data-trust-id');
				if (this.expandedTrusts.has(id)) this.expandedTrusts.delete(id);
				else this.expandedTrusts.add(id);
				this._renderList();
			} else if (what === 'toggle-trust') {
				const id = action.getAttribute('data-trust-id');
				this._toggleTrust(id);
				this._renderList();
			} else if (what === 'toggle-company') {
				const c = action.getAttribute('data-company');
				this._toggleCompany(c);
				this._renderList();
			} else if (what === 'select-all') {
				this.draft = new Set(this.allCompanies);
				this._renderList();
			} else if (what === 'deselect-all') {
				this.draft = new Set();
				this._renderList();
			} else if (what === 'apply') {
				this._handleApply();
			} else if (what === 'cancel') {
				this.close();
				this.options.onCancel();
			}
		}

		_onPopoverChange() {
			// Reserved for native input events; unused for now.
		}

		_handleApply() {
			// Empty selection coerces to "all" -- the cockpit needs at
			// least one company to render. Better than a silent empty
			// state.
			let next = new Set(this.draft);
			if (next.size === 0) {
				next = new Set(this.allCompanies);
			}
			this.committed = next;
			this._renderPill();
			this.close();
			const summary = this.summarize();
			this.options.onApply(this.getSelection(), summary.isAll);
		}

		// -----------------------------------------------------------------
		// Selection mutations
		// -----------------------------------------------------------------

		_toggleTrust(trustId) {
			const trust = this.options.trusts.find(t => t.id === trustId);
			if (!trust) return;
			const triState = this._trustTriState(trust);
			if (triState === 'check' || triState === 'dash') {
				trust.companies.forEach(c => this.draft.delete(c));
			} else {
				trust.companies.forEach(c => this.draft.add(c));
			}
		}

		_toggleCompany(companyName) {
			if (this.draft.has(companyName)) this.draft.delete(companyName);
			else this.draft.add(companyName);
		}

		_trustTriState(trust) {
			let n = 0;
			for (const c of trust.companies) {
				if (this.draft.has(c)) n++;
			}
			if (n === 0) return 'empty';
			if (n === trust.companies.length) return 'check';
			return 'dash';
		}

		_trustCountLabel(trust) {
			const total = trust.companies.length;
			let n = 0;
			for (const c of trust.companies) if (this.draft.has(c)) n++;
			if (n === 0) return `0 of ${total}`;
			if (n === total) return `${total} of ${total}`;
			return `${n} of ${total}`;
		}

		_autoExpandWithDraft() {
			// Auto-expand any trust whose membership is in a partial state.
			this.options.trusts.forEach(t => {
				const tri = this._trustTriState(t);
				if (tri === 'dash') this.expandedTrusts.add(t.id);
			});
		}

		_autoExpandWithSearch() {
			const q = this.searchQuery.trim().toLowerCase();
			if (!q) return;
			this.options.trusts.forEach(t => {
				if (t.name.toLowerCase().includes(q) ||
				    (t.abbr || '').toLowerCase().includes(q)) {
					this.expandedTrusts.add(t.id);
					return;
				}
				const hit = t.companies.some(c => c.toLowerCase().includes(q));
				if (hit) this.expandedTrusts.add(t.id);
			});
		}

		// -----------------------------------------------------------------
		// Helpers
		// -----------------------------------------------------------------

		_collectAllCompanies(trusts) {
			const out = [];
			(trusts || []).forEach(t => (t.companies || []).forEach(c => out.push(c)));
			return out;
		}

		_buildCompanyTrustMap(trusts) {
			const m = {};
			(trusts || []).forEach(t => (t.companies || []).forEach(c => {
				m[c] = t;
			}));
			return m;
		}
	}

	// ---------------------------------------------------------------------
	// Summary builder (pure)
	// ---------------------------------------------------------------------

	function _summarize(selectedSet, trusts, allCompanies) {
		const total = allCompanies.length;
		const selected = selectedSet.size;
		if (selected === 0) {
			return { label: 'No companies', isAll: false };
		}
		if (selected === total) {
			return { label: 'All companies', isAll: true };
		}

		// Per-trust state.
		const fullyIn = []; // trusts where every company is selected
		const partiallyIn = []; // trusts with at least one but not all
		trusts.forEach(t => {
			let n = 0;
			t.companies.forEach(c => { if (selectedSet.has(c)) n++; });
			if (n === t.companies.length && t.companies.length > 0) fullyIn.push({ trust: t, count: n });
			else if (n > 0) partiallyIn.push({ trust: t, count: n });
		});

		const involved = fullyIn.length + partiallyIn.length;

		// Single-trust cases.
		if (involved === 1) {
			const entry = fullyIn[0] || partiallyIn[0];
			const t = entry.trust;
			const trustTotal = t.companies.length;
			const word = trustTotal === 1 ? 'company' : 'companies';
			if (entry.count === trustTotal) {
				return { label: `${t.abbr} (${trustTotal} ${word})`, isAll: false };
			}
			return { label: `${t.abbr} (${entry.count} of ${trustTotal})`, isAll: false };
		}

		// Multi-trust cases.
		if (involved < ALL_TRUSTS_THRESHOLD) {
			const abbrs = [...fullyIn, ...partiallyIn]
				.map(e => e.trust.abbr).join(', ');
			const word = selected === 1 ? 'company' : 'companies';
			return {
				label: `${abbrs} (${selected} ${word})`,
				isAll: false,
			};
		}
		const word = selected === 1 ? 'company' : 'companies';
		return {
			label: `${involved} trusts, ${selected} ${word}`,
			isAll: false,
		};
	}

	// ---------------------------------------------------------------------
	// Inline checkbox SVG (3 states)
	// ---------------------------------------------------------------------

	function _checkboxSvg(state, accentColor) {
		const fill = (state === 'check' || state === 'dash')
			? (accentColor || '#0f172a') : 'transparent';
		const stroke = (state === 'empty')
			? '#cbd5e1' : (accentColor || '#0f172a');
		const inner = state === 'check'
			? `<path d="M5.5 10.5 L8.5 13.5 L13.5 7.5"
			        stroke="#fff" stroke-width="2" fill="none"
			        stroke-linecap="round" stroke-linejoin="round" />`
			: state === 'dash'
				? `<path d="M5 10 L13 10"
				          stroke="#fff" stroke-width="2.2" fill="none"
				          stroke-linecap="round" />`
				: '';
		return `<svg width="18" height="18" viewBox="0 0 18 18"
		             xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
			<rect x="1" y="1" width="16" height="16" rx="4"
			      fill="${escapeAttr(fill)}"
			      stroke="${escapeAttr(stroke)}"
			      stroke-width="1.5" />
			${inner}
		</svg>`;
	}

	// ---------------------------------------------------------------------
	// Escape helpers
	// ---------------------------------------------------------------------

	function escapeHtml(s) {
		if (s === null || s === undefined) return '';
		return String(s).replace(/[&<>"']/g, ch => ({
			'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
		}[ch]));
	}
	function escapeAttr(s) { return escapeHtml(s); }

	window.DuxTrustSelector = DuxTrustSelector;
})();
