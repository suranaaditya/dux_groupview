frappe.pages['groupview'].on_page_load = function(wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'GroupView',
		single_column: true
	});

	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-title').hide();

	const $body = $(wrapper).find('.layout-main-section').empty();

	$('head').append(
		'<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">'
	);

	const today = frappe.datetime.obj_to_user(new Date());

	$body.html(`
		<div class="dgv-phase0-shell">
			<div class="dgv-phase0-card">
				<div class="dgv-phase0-eyebrow">dux_groupview</div>
				<h1 class="dgv-phase0-title">cockpit — Phase 0 scaffolding live.</h1>
				<div class="dgv-phase0-meta">
					<div class="dgv-phase0-meta-row">
						<span class="dgv-phase0-meta-label">Snapshot date</span>
						<span class="dgv-phase0-meta-value" id="dgv-snapshot-date">${frappe.utils.escape_html(today)}</span>
					</div>
					<div class="dgv-phase0-meta-row">
						<span class="dgv-phase0-meta-label">Signed in as</span>
						<span class="dgv-phase0-meta-value" id="dgv-user">…</span>
					</div>
				</div>
				<div class="dgv-phase0-footer">Phase 0</div>
			</div>
		</div>
		<style>
			.dgv-phase0-shell {
				display: flex; align-items: center; justify-content: center;
				min-height: calc(100vh - 60px); padding: 24px;
				background: #f8fafc;
				font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
			}
			.dgv-phase0-card {
				background: #ffffff;
				border: 1px solid #e2e8f0;
				border-radius: 16px;
				box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.06);
				padding: 40px 44px;
				max-width: 520px;
				width: 100%;
				text-align: left;
			}
			.dgv-phase0-eyebrow {
				font-size: 12px; font-weight: 600; letter-spacing: 0.08em;
				text-transform: uppercase; color: #64748b; margin-bottom: 12px;
			}
			.dgv-phase0-title {
				font-size: 22px; font-weight: 600; color: #0f172a;
				margin: 0 0 24px 0; line-height: 1.35;
			}
			.dgv-phase0-meta { display: flex; flex-direction: column; gap: 10px; margin-bottom: 28px; }
			.dgv-phase0-meta-row {
				display: flex; justify-content: space-between; align-items: center;
				padding: 10px 14px; background: #f8fafc; border-radius: 8px;
				font-size: 14px;
			}
			.dgv-phase0-meta-label { color: #64748b; }
			.dgv-phase0-meta-value { color: #0f172a; font-weight: 500; font-variant-numeric: tabular-nums; }
			.dgv-phase0-footer {
				font-size: 11px; color: #94a3b8; letter-spacing: 0.08em;
				text-transform: uppercase; text-align: right;
			}
		</style>
	`);

	frappe.call({
		method: 'frappe.auth.get_logged_user',
		callback: function(r) {
			if (r && r.message) {
				$('#dgv-user').text(r.message);
			}
		}
	});
};
