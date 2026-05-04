# Phase 3.5 — Trust Selector

Archived spec for Phase 3.5, as agreed with Aditya on 2026-05-03.

---

## Goal

Add a header-pill popover that lets the cockpit user scope spotlight
cards and the pivot grid to a subset of trusts and companies. Default
state is the last-used scope (or all-companies for first-time users),
persisted via `localStorage`. Built on top of the existing Phase 3
pivot grid; does not change Phase 1 (snapshot) or the Phase 2 cache
schema.

## Hard rules carried over

- The selector reads only `tabDGV TB Snapshot Row`, `tabAccount`,
  `tabCompany` (transitively, via the existing pivot API). Never
  `tabGL Entry`.
- User Permissions on Company remain the source of truth for what each
  user can see. Selector scope is intersected with the user's
  allowed-companies set on the server -- a user can never widen their
  own visibility through the selector.
- The selector does not change snapshot semantics or the cache
  contents; it only filters the rendered view.

## UX

- Header pill in the cockpit topbar, reading `Showing: {summary} ▾`.
- Click opens a 620 px wide popover with the 10 RGI trusts (plus the
  synthetic `default` "Other" trust if applicable) as expandable rows.
- Each trust row has a tri-state checkbox (empty / dash / check)
  reflecting the partial-selection state of its companies.
- Companies are indented child rows with their own checkboxes.
- Search input at the top filters case-insensitively across both trust
  and company names. Matching trusts auto-expand.
- Apply commits the selection and re-renders cockpit; Cancel
  discards. Click-outside and Esc behave like Cancel.
- Trust dot uses the `color` from `pivot/trust_groups.py`.
- Summary text in the pill is context-aware:
  - All companies → `Showing: All companies`
  - One trust fully selected → `Showing: ASS (16 companies)`
  - One trust partially selected → `Showing: ASS (14 of 16)`
  - Multi-trust → `Showing: 3 trusts, 47 companies`

## Persistence

- `localStorage` key: `dgv_cockpit_scope_v1`. Value is JSON of shape
  `{version: 1, selected_companies: [...], saved_at: ISO}`.
- Loaded on cockpit page boot. If absent or version-mismatched, default
  to all companies.
- Per-browser, not per-Frappe-user. Migrating to a `DGV User
  Preferences` doctype is Phase 5 work (logged as Q11).

## Backend

- `get_pivot_data(snapshot_date, format, companies=None)` -- new
  optional `companies` parameter. When provided, intersected with the
  user's User-Permission-allowed set, then applied at the SQL level
  (`WHERE company IN (...)`). When `None`, behaviour is identical to
  Phase 3.
- `get_pivot_summary(snapshot_date, companies=None)` -- same parameter,
  same intersection.
- `get_scope_options()` -- new lightweight endpoint that returns
  `{trusts: [...]}` covering every company the user can see, regardless
  of current scope. Drives the popover's tree.
- `get_spotlight_cards_filtered(snapshot_date, companies)` -- new
  endpoint. Re-aggregates each card from `tabDGV TB Snapshot Row` for
  the supplied subset of companies. Falls through to the cache via
  `get_spotlight_cards` when `companies is None` (i.e. the existing
  all-companies path is unchanged for the default case).

## Frontend

- `dux_groupview/public/js/trust_selector.js` -- self-contained
  `DuxTrustSelector` class:

  ```
  new DuxTrustSelector(triggerEl, {
      trusts,
      initialSelection,
      onApply(selectedCompanies),
      onCancel,
  })
  ```

  Public methods: `open()`, `close()`, `getSelection()`,
  `setSelection(arr)`. Tri-state checkboxes via inline SVG. Search
  filters case-insensitively. Click-outside and Esc cancel.

- `dux_groupview/public/css/trust_selector.css` -- popover styling
  (white bg, light border, 620 px wide, max-height 480 px scrollable
  list, tri-state checkbox variants, primary Apply / secondary
  Cancel).

- `dux_groupview/dux_groupview/page/groupview/groupview.js`:
  - On boot, read the localStorage scope (or default to all companies).
  - Mount the selector against the header pill, passing trusts from
    `get_scope_options`.
  - Pass `companies` (or `null` for full scope) to all data-fetch
    calls. Use `get_spotlight_cards` when scope is full,
    `get_spotlight_cards_filtered` otherwise.
  - On Apply: persist to localStorage, re-fetch spotlight + pivot,
    update the pill summary. Show a brief loading skeleton on the
    affected sections during the fetch.

## Tests

`dux_groupview/dux_groupview/tests/test_pivot_filter.py`:

1. `test_get_pivot_data_filters_by_companies` -- subset filter
   reflected in response trusts + balances.
2. `test_get_pivot_data_companies_intersected_with_user_permissions`
   -- a user with permission on 2 of 3 requested companies sees only 2.
3. `test_get_spotlight_cards_filtered_returns_same_shape_as_cached` --
   filtered endpoint with the full company set agrees with the cached
   endpoint.
4. `test_get_spotlight_cards_filtered_subset` -- card values for a
   subset agree with an independent SQL aggregation over the same
   subset.

## Performance budget

- Scope-change re-render: < 1 sec on dev, < 2 sec stop-condition.
- Spotlight filtered aggregation: 6 cards × indexed query against
  `tabDGV TB Snapshot Row` -- expected < 500 ms on dev seed (parity
  with Phase 2/3 patterns).
- Pivot filter: same budget as Phase 3 `get_pivot_data` (< 500 ms
  dev, < 1.5 sec production-shaped).

## Out of scope (Phase 5 / later)

- Per-Frappe-user persistence of scope (logged as Q11).
- Saved named scopes / shareable views.
- Mobile-specific selector (Phase 6 PWA).
- Editing the trust definitions in the UI.
