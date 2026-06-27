/* ICD Mapping settings page.
 *
 * Lets the GroupView Owner curate the list of "Inter-College Deposit"
 * accounts that the ICD spotlight card aggregates (and the Unsecured
 * Loans card excludes). One row per stripped account_name, with a
 * checkbox to toggle ICD membership. Initial state = current ICD set
 * from `tabDGV ICD Account`; uncommitted changes tracked in JS only
 * until Save.
 *
 * Server APIs:
 *   - get_icd_candidates    -- initial load + refresh after save
 *   - suggest_icd_candidates -- pre-checks high-confidence rows
 *   - save_icd_list         -- diff-based persistence + cache refresh
 */

frappe.pages['dgv-icd-mapping'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'ICD Mapping',
		single_column: true,
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	// Page-scoped CSS: loaded lazily so it doesn't bloat every Frappe
	// desk page. Idempotent -- inject once per session.
	if (!document.querySelector('link[data-dgv-icd-css]')) {
		const link = document.createElement('link');
		link.rel = 'stylesheet';
		link.href = '/assets/dux_groupview/css/icd_mapping.css';
		link.setAttribute('data-dgv-icd-css', '1');
		document.head.appendChild(link);
	}

	const $body = $(wrapper).find('.layout-main-section').empty();

	$body.html(`
		<div class="dgv-icd-shell">
			<header class="dgv-icd-header">
				<div class="dgv-icd-title-block">
					<button type="button" class="dgv-icd-back" id="dgv-icd-back">
						← Back to cockpit
					</button>
					<h1 class="dgv-icd-title">ICD account mapping</h1>
					<div class="dgv-icd-subtitle" id="dgv-icd-subtitle">
						Loading…
					</div>
				</div>
				<div class="dgv-icd-toolbar">
					<button type="button" class="dgv-icd-btn"
					        id="dgv-icd-suggest"
					        title="Pre-tick accounts whose name matches an internal company">
						Suggest
					</button>
					<button type="button" class="dgv-icd-btn dgv-icd-btn-primary"
					        id="dgv-icd-save">
						Save changes
					</button>
				</div>
			</header>

			<div class="dgv-icd-controls">
				<div class="dgv-icd-filters" role="tablist">
					<button class="dgv-icd-filter dgv-icd-filter-active"
					        data-filter="all">All</button>
					<button class="dgv-icd-filter" data-filter="icd">
						ICD only
					</button>
					<button class="dgv-icd-filter" data-filter="external">
						External only
					</button>
				</div>
				<input type="text" class="dgv-icd-search"
				       id="dgv-icd-search"
				       placeholder="Search account name…" />
				<div class="dgv-icd-counts" id="dgv-icd-counts"></div>
			</div>

			<div class="dgv-icd-table-wrap">
				<table class="dgv-icd-table">
					<thead>
						<tr>
							<th class="dgv-icd-th-toggle">
								<input type="checkbox" id="dgv-icd-toggle-visible"
								       title="Toggle all currently visible rows" />
							</th>
							<th class="dgv-icd-th-name">Account name</th>
							<th class="dgv-icd-th-balance">Balance</th>
							<th class="dgv-icd-th-companies"># Companies</th>
							<th class="dgv-icd-th-where">Where</th>
						</tr>
					</thead>
					<tbody id="dgv-icd-rows">
						<tr><td colspan="5" class="dgv-icd-empty">Loading…</td></tr>
					</tbody>
				</table>
			</div>

			<section class="dgv-icd-orphans" id="dgv-icd-orphans" hidden>
				<h2 class="dgv-icd-orphans-title">
					Orphaned ICD entries
				</h2>
				<div class="dgv-icd-orphans-help">
					Flagged as ICD but no longer match a leaf under
					Unsecured Loans (renamed, moved, or disabled).
					Uncheck to remove on the next Save.
				</div>
				<ul class="dgv-icd-orphans-list" id="dgv-icd-orphans-list"></ul>
			</section>
		</div>
	`);

	// ----- State ----------------------------------------------------
	// `rows`         -- last server payload, sorted by |balance| desc.
	// `icdSelected`  -- live working set of ICD account_names. Starts
	//                   as the server's current set, mutated by every
	//                   toggle; persisted to the doctype on Save.
	// `orphanKept`   -- orphan names the user has NOT unticked. Saved
	//                   alongside `icdSelected` (the union goes to
	//                   save_icd_list so unchanged orphans survive).
	let rows = [];
	let icdSelected = new Set();
	let orphanKept = new Set();
	let filter = 'all';
	let search = '';

	// ----- Wiring ---------------------------------------------------
	$('#dgv-icd-back').on('click', () => {
		frappe.set_route('groupview');
	});
	$('#dgv-icd-save').on('click', onSave);
	$('#dgv-icd-suggest').on('click', onSuggest);
	$('#dgv-icd-search').on('input', function() {
		search = ($(this).val() || '').toLowerCase().trim();
		render();
	});
	$('.dgv-icd-filter').on('click', function() {
		filter = $(this).data('filter');
		$('.dgv-icd-filter').removeClass('dgv-icd-filter-active');
		$(this).addClass('dgv-icd-filter-active');
		render();
	});
	$('#dgv-icd-toggle-visible').on('change', function() {
		const next = !!$(this).prop('checked');
		visibleRows().forEach(r => {
			if (next) icdSelected.add(r.account_name);
			else icdSelected.delete(r.account_name);
		});
		render();
	});

	// Row-level checkbox toggle (delegated).
	$('#dgv-icd-rows').on('change', '.dgv-icd-row-toggle', function() {
		const name = $(this).data('name');
		if ($(this).prop('checked')) icdSelected.add(name);
		else icdSelected.delete(name);
		updateCounts();
	});
	$('#dgv-icd-orphans-list').on('change', '.dgv-icd-orphan-toggle',
		function() {
			const name = $(this).data('name');
			if ($(this).prop('checked')) orphanKept.add(name);
			else orphanKept.delete(name);
		});

	load();

	// ----- Functions ------------------------------------------------
	function load() {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.icd_settings.get_icd_candidates',
			callback: (r) => {
				if (!r.message) return;
				const m = r.message;
				rows = m.rows || [];
				icdSelected = new Set(
					rows.filter(x => x.is_icd).map(x => x.account_name));
				orphanKept = new Set((m.orphans || []).map(o => o.account_name));
				$('#dgv-icd-subtitle').text(m.snapshot_date
					? `Showing ${rows.length} accounts under Unsecured Loans · `
					  + `balances as of ${m.snapshot_date}.`
					: `Showing ${rows.length} accounts under Unsecured Loans · `
					  + `no completed snapshot yet.`);
				renderOrphans(m.orphans || []);
				render();
			},
		});
	}

	function render() {
		const v = visibleRows();
		const $tbody = $('#dgv-icd-rows').empty();
		if (!v.length) {
			$tbody.append(
				'<tr><td colspan="5" class="dgv-icd-empty">'
				+ 'No matching accounts.</td></tr>');
			updateCounts();
			return;
		}
		const html = v.map(r => {
			const checked = icdSelected.has(r.account_name) ? 'checked' : '';
			return `
				<tr class="dgv-icd-row">
					<td class="dgv-icd-td-toggle">
						<input type="checkbox" class="dgv-icd-row-toggle"
						       data-name="${escapeHtml(r.account_name)}"
						       ${checked} />
					</td>
					<td class="dgv-icd-td-name">
						${escapeHtml(r.account_name)}
					</td>
					<td class="dgv-icd-td-balance ${r.balance < 0 ? 'dgv-icd-neg' : ''}">
						${formatRupees(r.balance)}
					</td>
					<td class="dgv-icd-td-companies">${r.company_count}</td>
					<td class="dgv-icd-td-where"
					    title="${escapeAttr(r.companies)}">
						${escapeHtml(truncateCompanies(r.companies))}
					</td>
				</tr>
			`;
		}).join('');
		$tbody.html(html);

		// Sync the "toggle all visible" checkbox state: checked iff
		// every visible row is already in icdSelected.
		const allChecked = v.every(r => icdSelected.has(r.account_name));
		const anyChecked = v.some(r => icdSelected.has(r.account_name));
		const $toggle = $('#dgv-icd-toggle-visible');
		$toggle.prop('checked', allChecked);
		$toggle.prop('indeterminate', !allChecked && anyChecked);

		updateCounts();
	}

	function renderOrphans(orphans) {
		const $section = $('#dgv-icd-orphans');
		if (!orphans.length) {
			$section.attr('hidden', true);
			return;
		}
		const html = orphans.map(o => `
			<li class="dgv-icd-orphan">
				<label>
					<input type="checkbox" class="dgv-icd-orphan-toggle"
					       data-name="${escapeAttr(o.account_name)}"
					       checked />
					<span>${escapeHtml(o.account_name)}</span>
				</label>
			</li>
		`).join('');
		$('#dgv-icd-orphans-list').html(html);
		$section.removeAttr('hidden');
	}

	function visibleRows() {
		return rows.filter(r => {
			if (search && !r.account_name.toLowerCase().includes(search))
				return false;
			if (filter === 'icd' && !icdSelected.has(r.account_name))
				return false;
			if (filter === 'external' && icdSelected.has(r.account_name))
				return false;
			return true;
		});
	}

	function updateCounts() {
		const total = rows.length;
		const icd = rows.filter(r => icdSelected.has(r.account_name)).length;
		$('#dgv-icd-counts').text(
			`${icd} of ${total} marked ICD · ${total - icd} treated as external`);
	}

	function onSuggest() {
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.icd_settings.suggest_icd_candidates',
			freeze: true,
			freeze_message: 'Scanning account names…',
			callback: (r) => {
				if (!r.message) return;
				const sug = r.message.suggested || [];
				if (!sug.length) {
					frappe.show_alert({
						message: 'No high-confidence ICD matches found.',
						indicator: 'orange',
					});
					return;
				}
				let added = 0;
				sug.forEach(s => {
					if (!icdSelected.has(s.account_name)) {
						icdSelected.add(s.account_name);
						added++;
					}
				});
				render();
				frappe.show_alert({
					message: `Pre-checked ${added} new account(s) — `
					       + `review and save when ready.`,
					indicator: 'blue',
				});
			},
		});
	}

	function onSave() {
		// Union of live ICDs + any orphans the user kept ticked.
		const payload = Array.from(new Set([
			...icdSelected,
			...orphanKept,
		]));
		frappe.call({
			method: 'dux_groupview.dux_groupview.api.icd_settings.save_icd_list',
			args: { account_names: JSON.stringify(payload) },
			freeze: true,
			freeze_message: 'Saving and refreshing spotlight cache…',
			callback: (r) => {
				if (!r.message) return;
				const m = r.message;
				const summary = (m.added.length || m.removed.length)
					? `+${m.added.length} added, -${m.removed.length} removed`
					: 'no changes';
				frappe.show_alert({
					message: `Saved (${summary}). Cards will reflect the new `
					       + `split on next cockpit load.`,
					indicator: 'green',
				});
				// Re-load to refresh server's current ICD set + orphan list.
				load();
			},
		});
	}

	// ----- Tiny helpers --------------------------------------------
	function escapeHtml(s) {
		return String(s == null ? '' : s)
			.replace(/&/g, '&amp;').replace(/</g, '&lt;')
			.replace(/>/g, '&gt;').replace(/"/g, '&quot;');
	}
	function escapeAttr(s) { return escapeHtml(s); }

	function formatRupees(v) {
		const n = Number(v) || 0;
		if (n === 0) return '—';
		const sign = n < 0 ? '−' : '';
		const abs = Math.abs(n);
		// Indian crore for values >= 1 crore, lakh otherwise. Same
		// convention the cockpit uses for medium-density tables.
		if (abs >= 10000000) return `${sign}${(abs / 10000000).toFixed(2)} Cr`;
		if (abs >= 100000)  return `${sign}${(abs / 100000).toFixed(2)} L`;
		return `${sign}${abs.toLocaleString('en-IN', {
			minimumFractionDigits: 2, maximumFractionDigits: 2,
		})}`;
	}

	function truncateCompanies(s) {
		if (!s) return '';
		if (s.length <= 80) return s;
		return s.slice(0, 77) + '…';
	}
};
