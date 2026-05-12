# Phase 4 commit 4 — Filter UI (HALT 2.5)

**Status:** v0.1 (with amendments — not version-bumped; clarifications only)
**Branch:** `phase-4-drills` (continuing; no new branch)
**Estimated duration:** 1 working day
**Depends on:**
- Phase 4 commit 4 spec v0.6 (`specs/phase-4-commit-4-gl-drill.md`)
  — HALT 1 (gl-drill page + `get_gl_entries`) and HALT 2 (CSV
  exports) are merged into the same `phase-4-drills` working tree.
- HALT 2 surfaced the filter UI gap during browser review on
  2026-05-08; see master-spec §15.5.

This spec is the contract for HALT 2.5 implementation. It extends
the master spec with a five-filter UI + URL contract + server-side
endpoint extensions. After HALT 2.5 ships, HALT 3 (View All Parties)
becomes HALT 4 in the renumbered sequence per master-spec §12.

**Amendments since first draft (post-Aditya review, 2026-05-08):**

Four clarifications applied inline; no semantic changes worth a
version bump. The spec history reads top-down with this note as the
breadcrumb.

- §4 Q1 — added explicit EXPLAIN-check fail criterion mirroring
  master-spec §10: the new `account_names` + `voucher_types`
  predicates must NOT introduce `Using temporary` or `Using
  filesort` to the inner-JOIN plan. If HALT 2.5.3's EXPLAIN shows either,
  halt for an index-fix conversation before shipping.
- §5 — added explicit "filters reset on scope change" rule. Filters
  are per-scope and not carried across; navigating from one drill
  to another via "View GL entries" starts with fresh filter state.
- §7.3 — pinned active-filter-chip max-width at 200px with
  text-overflow:ellipsis and a `title` attribute for the full
  selected value on hover. Long account names or company lists
  would otherwise blow out the chips row.
- §12 — halt-point numbering changed from `H1`/`H2`/`H3`/`H4` to
  `HALT 2.5.1`/`2.5.2`/`2.5.3`/`2.5.4`. The renumbering makes the
  insertion between master-spec HALT 2 and HALT 3 visible in the
  git log (anyone reading later sees that filters got slotted
  in mid-sequence rather than treated as their own commit).

The two design picks Aditya called out at sign-off (§3.5 voucher-
types ride-along on `get_gl_entries` response; §3.3 `from_date`
unbounded below) were already the spec's positions and need no
inline edit; sign-off recorded here for the audit trail.

---

## 1. Goal

Add filter controls to the GL drill page that let users narrow the
GL-entry list within an existing scope. Pills + dropdowns + date
range, with URL-persisted state for shareability and refresh-safety.

The drill scope (set by the originating click — pivot leaf, spotlight
card, or subtree) defines the *outer envelope* of rows the page can
show. Filters narrow within that envelope; they cannot widen it. A
user with permission to companies A and B who arrives at the page
via a card scope resolving to 30 leaves cannot use the filter UI to
suddenly see leaves not in those 30 or companies outside {A, B}. The
master-spec scope-resolution path (`_resolve_scope` + permission
intersection) still runs first; filters layer on top.

## 2. Non-goals (for HALT 2.5)

Deferred to Phase 5 unless explicitly listed. Calling these out so
the implementation diff stays focused.

- **Filtering on the cockpit pivot.** Pivot has trust-selector +
  search; that's its filter surface. Different product.
- **Filtering on the account-drill panel.** Panel is a glance view;
  filters belong on the full drill page.
- **Saved filter presets.** "Save this filter as 'Sundry Creditors
  Q1 review'" is a Phase 5 doctype-backed feature.
- **Filter-exclusion logic** (`account_names != X`, "everyone except
  TDS Payable"). v1 is include-only.
- **Free-text search on remarks / voucher_no.** No predicate, no UI.
- **Bulk operations on filtered results** (mark, annotate, export
  to multi-sheet workbook). Out of scope.

## 3. Filter set (v1)

Five filters, in toolbar order. Each filter section includes:
visibility rule, default state, URL contract, server-side handling.

### 3.1 Company (multi-select dropdown)

- **Visibility:** when the resolved scope spans more than one
  company (`scope_fanout.n_companies > 1`).
- **Default selection:** all companies from the resolved scope's
  permission-intersected list (i.e., the existing `companies` URL
  param if present, else the full allowed set).
- **URL param:** existing `companies=A,B,C`. Behavior unchanged
  (no new param needed). Empty value or omitted → all-companies
  fallback already implemented in master-spec HALT 1.
- **Server side:** existing `g.company IN (...)` clause in
  `_build_where_clause` already handles this. No SQL change.
- **UI input:** standard multi-select dropdown with "Select all" /
  "Clear" actions. Search-as-you-type when company list > 10.
- **Notes:** This filter "exists today" via the URL — HALT 2.5 is
  the first time it gets a UI input. No backend work for this one.

### 3.2 Account name (multi-select dropdown)

- **Visibility:** when the resolved scope contains more than one
  unique `account_name` after company-suffix stripping (e.g., card
  `sundry_creditors` resolves to leaves "Creditors", "Employee
  Advances" — both Payable-typed → 2 unique account_names → show
  filter).
- **Default selection:** all unique account_names from the
  resolved scope (filter is no-op when default).
- **URL param:** new `account_names=Creditors,Employee Advances`.
  Comma-separated, URL-encoded values.
- **Display form:** stripped account_name — *"Creditors"* not
  *"Creditors - SGREF"*. The (company, account) full-suffixed form
  appears in the row table + group divider chip; the filter UI
  works on the conceptual account_name.
- **Server side:** new clause appended to `_build_where_clause`
  *after* scope resolution: `AND a.account_name IN (...)`. Joined
  via the existing `tabAccount a` JOIN; no extra round-trip. Q1
  resolution below.
- **UI input:** multi-select dropdown ordered by account_name
  alphabetically. Search-as-you-type when list > 10 entries.

### 3.3 Date range (`from_date` / `to_date`)

- **Visibility:** always (no triggering condition).
- **Default state:** `from_date = null` (treats as inception, i.e.,
  no lower bound), `to_date = as_of_date` (matches existing
  master-spec HALT 1 behavior of "all entries on or before
  as_of_date").
- **URL params:** new `from_date=YYYY-MM-DD` and
  `to_date=YYYY-MM-DD`. Both optional.
- **Clamping rule:** `to_date` is capped at `as_of_date` server-
  side. A user can't widen the upper bound past the cockpit's
  snapshot date. If `to_date > as_of_date` is supplied, server
  silently clamps to `as_of_date` and includes a `clamped_to_date`
  field in the response so the UI can flash a tooltip
  ("Date range capped at as-of date"). UI defensively clamps in
  the date-picker too, but server is the trust boundary.
- **`from_date` clamping:** `from_date` is NOT clamped to a lower
  bound — accepting any historic date keeps the filter intuitive.
  If `from_date > to_date` (after to_date clamping), the server
  returns zero rows and the UI surfaces an inline validation
  message ("From date must be on or before to date").
- **Server side:** master-spec HALT 1 had
  `AND g.posting_date <= %(as_of_date)s`. v0.6 / HALT 2.5 expands
  to:
  ```
  AND g.posting_date <= %(effective_to_date)s
  AND (%(from_date)s IS NULL OR g.posting_date >= %(from_date)s)
  ```
  where `effective_to_date = LEAST(as_of_date, COALESCE(to_date, as_of_date))`.
- **UI input:** two date pickers side by side. The `to_date` picker
  has its `max` attribute set to `as_of_date` for client-side
  clamping. `from_date` has no `min` (accept any past date).

### 3.4 Party (autocomplete input)

- **Visibility:** when the master-spec `is_party_trackable`
  evaluation returns True for the resolved scope (i.e., the scope
  contains accounts of `account_type` in
  `PARTY_TRACKABLE_ACCOUNT_TYPES`).
- **Default selection:** none (filter inactive). URL `party=` and
  `party_type=` already supported by master-spec HALT 1 — the
  party_drill → gl-drill deep link in HALT 4 will populate them.
- **URL params:** existing `party=` and `party_type=`. No change.
- **Server side:** existing branch in `_build_where_clause` (HALT 1)
  already handles the filter. No SQL change.
- **UI input:** single-text autocomplete input. v1 single-select
  only (multi-party deferred). Suggestions sourced from the
  `_distinct_parties_in_scope` server endpoint (new — see §6.3).
- **Clearing:** existing "× Remove party filter" chip on the GL page
  hero (HALT 1) still works. Adding the autocomplete input does
  not deprecate the chip — both clear the same URL params.

### 3.5 Voucher type (multi-select, COLLAPSIBLE under "Advanced")

- **Visibility:** always available, but hidden by default behind a
  collapsible "Advanced filters" disclosure. Power-user feature.
- **Default selection:** all voucher types in scope (filter is
  no-op when default).
- **URL params:** new `voucher_types=Journal Entry,Payment Entry`.
- **Available types:** distinct `voucher_type` values from the
  current scope. Two implementation paths:
  1. Add to `get_gl_entries` response: a new `voucher_types_in_scope`
     list field. Cheap (one extra `SELECT DISTINCT`); avoids a
     separate round-trip; UI gets the list with the data.
  2. Separate endpoint `gl_drill_v1.get_voucher_types_in_scope`.
     Slightly cleaner separation but adds a request.

  **Spec picks option 1**: ride along on the existing
  `get_gl_entries` response. Implementation is one extra SQL
  round-trip with the same WHERE clause but `SELECT DISTINCT
  voucher_type` instead of the windowed read. Rendered list is
  cached client-side until the scope or other filters change.
- **Server side:** new clause in `_build_where_clause`:
  `AND g.voucher_type IN (...)` when populated.
- **UI input:** multi-select dropdown inside an expandable
  "Advanced filters" section. Section auto-expands when
  `voucher_types` is non-default in the URL on first load (so a
  shared link with a vouchertype filter is visible without
  fishing).

## 4. Resolved open questions

The three Q's flagged in master-spec §15.5, resolved per Aditya's
recommendations.

### Q1. Account-name filter — SQL-narrow vs client-side?

**Resolution: SQL-narrow.** Append `AND a.account_name IN (...)` to
`_build_where_clause` after scope resolution.

- *Why not client-side*: pagination math relies on `total_entries`
  which the server computes from a count of matching rows. Client-
  side post-filtering would mean "page 1 of 100" displays 47 rows
  after the client drops 53, and the pager still shows 100 pages
  → broken UX. Same problem with running balance — client-side
  filter happens AFTER the windowed cumulative is computed, so
  visible rows would have running balances that include rows the
  user filtered out.
- *Why server-side is cheap*: the WHERE clause already JOINs
  `tabAccount a` for `root_type` (used in `signed_amount` CASE).
  Adding `AND a.account_name IN (...)` is one filter on the same
  joined table — no new index needed. Optimizer happily pushes
  the predicate.
- *EXPLAIN check at HALT 2.5.3 verify*: confirm the
  `dgv_party_drill` or `dgv_snapshot_aggregation` plan from HALT 1
  doesn't degrade when the new clauses are added.

  **Fail criterion (mirrors master-spec §10).** The verification
  fails — implementation halts for an index-fix discussion before
  shipping — if the inner-JOIN's EXPLAIN shows either of:

  1. `Using temporary` in the Extra column for the JOIN to
     `tabAccount` or `tabGL Entry`, OR
  2. `Using filesort` on the same.

  The HALT 1 baseline (recorded in master-spec §10) is `type=ref`
  with `key=dgv_party_drill`, `Using index condition; Using where`,
  no temporary/filesort. The new `account_names IN (...)` and
  `voucher_types IN (...)` predicates filter on already-joined
  columns (`tabAccount.account_name`, `tabGL Entry.voucher_type`)
  — no new index dimension required. Expected outcome: same plan
  with one extra `Using where` predicate fold-in. If reality
  diverges, it's a load-bearing surprise worth a halt.

  **Fail-criterion is symmetric with HALT 1.** "Wrong index used"
  is NOT a fail (the optimizer can pick either of the two
  candidate `tabGL Entry` indexes). "No index used" or "filesort
  introduced" IS a fail.

### Q2. URL-persisted vs localStorage-sticky?

**Resolution: URL-persisted.**

- *Why URL wins*: shareability ("send Kumar Sir this exact view")
  + refresh-safety + browser-back integration (`pushState` already
  in HALT 1) + matches every other surface in the cockpit.
- *Why not also localStorage*: dual persistence introduces
  precedence questions ("URL says X, localStorage says Y; user
  arrived from a deep link — which wins?"). Fail simple.
- *Future option for Phase 5*: a server-side `DGV User Preferences`
  doctype could hold per-user defaults that the page applies when
  no URL params are set. Same pattern as the cockpit scope
  selector's planned migration. Not now.

### Q3. Date-pair widget integrated with `as_of_date` or separate?

**Resolution: Separate `from_date` / `to_date` params.**

- *Why separate*: `as_of_date` is a *scope* concern — it's the
  cockpit's snapshot date that propagates everywhere (pivot,
  cards, drill panel, GL page). Date range is a *filter* — narrows
  within a scope. Conflating them would make the URL ambiguous
  (which date is the snapshot? which is the filter bound?).
- *as_of_date stays as-is*: the cockpit's snapshot date selector
  is the only thing that should change `as_of_date`. The GL drill
  page's date range filters operate strictly within the
  `[..., as_of_date]` envelope (`to_date` clamped, `from_date`
  unbounded below).
- *UI consequence*: the date filter's `to_date` defaults to
  `as_of_date` and the date picker's `max` is set to `as_of_date`.
  Visually clear that the as-of-date is the *envelope* and the
  date filter is the *narrow within*.

## 5. URL contract

Final canonical URL shape (params in this order in `buildXxxUrl`
helpers; the parser accepts any order):

```
/app/gl-drill
  ?scope=<scope_id>
  &as_of=<iso>
  &companies=<csv>
  &account_names=<csv>
  &from_date=<iso>
  &to_date=<iso>
  &party=<name>
  &party_type=<type>
  &voucher_types=<csv>
  &page=<n>
  &page_size=<n>
  &sort=<key>
```

Param ordering rationale: scope → envelope (as_of, companies) →
filters in toolbar order (account_names, dates, party, vouchertypes)
→ pagination/display (page, page_size, sort). Order doesn't affect
correctness; it's for human readability when copying URLs around.

**Empty / default values omitted.** A URL with `from_date=` or
`account_names=` and no value is treated as "filter not set" — the
helper omits empty params. This keeps default-state URLs short
(matches HALT 1 behavior).

**Comma-encoding.** Multi-value params (`companies`, `account_names`,
`voucher_types`) use comma-separated lists. Values containing commas
are NOT supported in v1 (no real-world account names or voucher
types contain commas in ERPNext). If an edge case appears, escalate
to JSON-encoded array param (matches `accounts=` from HALT 1).

**Filters reset on scope change.** Filters are *per-scope*: when the
user navigates from one scope to another (e.g., closes the GL drill
page and opens a fresh one via "View GL entries" from a different
account-drill panel), the new page boots with default filter state.
The new URL is built by `account_drill.js`'s `buildGlDrillUrl` (HALT
1) which only emits `scope=`, `as_of_date=`, `companies=` — none of
the HALT 2.5 filter params. The receiving GL page parses the URL
and finds those params absent → renders with default filters.

Why reset rather than carry over:

1. **Coherence.** A user filtering by `account_names=Creditors` on
   a Sundry-Creditors-card scope, then drilling into a Cash-card
   scope, would otherwise see "0 results" because Cash leaves never
   match `Creditors`. Confusing failure mode.
2. **Permission shape.** Filters resolve against the new scope's
   resolved leaf list. Carrying old filters introduces edge cases
   where a previously-valid `account_names` value isn't in the new
   scope's universe and silently drops to no-op.
3. **URL portability.** A shared filter URL is meant to be
   re-rendered exactly as captured. Cross-scope carry-over would
   make filter URLs context-dependent ("if you arrived from card X,
   this filter applies; from card Y, it doesn't").

The UI "active filter chip" row + Clear-filters button (§7.3) remain
the only mechanisms for clearing filters within a scope. Cross-scope
clearing is automatic via the URL-build path.

If a future Phase 5 use case argues for carry-over (e.g., "remember
my voucher_type preference"), that's a per-user-pref concern and
belongs on the same `DGV User Preferences` doctype that picks up
sticky scope from §15.5 Q2 of the master spec — not in the URL
contract.

## 6. Server-side endpoint extensions

### 6.1 `get_gl_entries` — extended signature

Adds new params; defaults preserve HALT 1 behavior. Backward-
compatible with HALT 1 callers.

```python
get_gl_entries(
    scope=None, accounts=None, as_of_date=None, companies=None,
    party=None, party_type=None,
    page=1, page_size=None, sort='posting_date_asc',
    # NEW in HALT 2.5:
    account_names=None,    # JSON-string OR list[str]
    from_date=None,        # ISO date string
    to_date=None,          # ISO date string
    voucher_types=None,    # JSON-string OR list[str]
)
```

### 6.2 Response shape additions

```
{
  ...existing HALT 1 fields...,
  "voucher_types_in_scope": ["Journal Entry", "Payment Entry", ...],
  "clamped_to_date": <bool>,             # true if to_date exceeded as_of_date
  "filter_state": {                      # echoed back for UI sanity
    "account_names": [...] | null,
    "from_date": "YYYY-MM-DD" | null,
    "to_date": "YYYY-MM-DD" | null,
    "voucher_types": [...] | null,
  },
}
```

`filter_state` echo is debug-friendly and makes the new state
inspectable from browser devtools without re-parsing the URL.

### 6.3 New endpoint `gl_drill_v1.get_filter_metadata`

Used by the UI on first page load to populate dropdown options for
account_names + voucher_types + party autocomplete suggestions —
without waiting for a full `get_gl_entries` round-trip.

```python
@frappe.whitelist()
def get_filter_metadata(scope=None, accounts=None, as_of_date=None,
                       companies=None):
    """Return the dropdown population metadata for the filter UI.
    
    Output:
      {
        "account_names": [{"name": "Creditors", "company_count": 13}, ...],
        "voucher_types": ["Journal Entry", "Payment Entry", ...],
        "scope_fanout": {"n_accounts": ..., "n_companies": ...},
      }
    """
```

Cheap: one `SELECT DISTINCT` against the resolved leaf set
(`tabAccount`) for account_names, one against `tabGL Entry` for
voucher_types in scope. Cached client-side until scope changes.

Party autocomplete is its own endpoint — same pattern as Frappe's
built-in Link field autocomplete. Defer to implementation time;
might just reuse `frappe.client.get_list("Customer", ...)` /
`Supplier` with permission scoping.

### 6.4 `export_gl_entries_csv` — same extension

CSV export accepts the same new params and applies the same WHERE
clause. Filter state is honored — the exported file matches the
on-screen view, not the unfiltered scope. (See §8 for filename
convention change.)

## 7. UI design

### 7.1 Toolbar layout (≥800px viewport)

Three rows in the existing `.dgv-gl-toolbar` container:

```
┌──────────────────────────────────────────────────────────────┐
│ Sort ▼   Per page ▼   ┃   Export CSV   23 / 1432   ← →   │ ← row 1 (existing HALT 1+2)
├──────────────────────────────────────────────────────────────┤
│ Companies (3) ▼   Accounts (5) ▼   From [date] To [date]    │ ← row 2 (filters)
│ Party [autocomplete...]   ▸ Advanced filters                │
├──────────────────────────────────────────────────────────────┤
│ Active: Companies × 3 · Accounts × 2 · Apr 1 → May 8      │ ← row 3 (chips, when any filter non-default)
│                                          [Clear filters]    │
└──────────────────────────────────────────────────────────────┘
```

When "Advanced filters" expands, the voucher-type multi-select
appears below party autocomplete in row 2.

### 7.2 Toolbar layout (≤800px viewport)

Existing row 1 stays. Filters collapse into a single button:

```
┌────────────────────────────────────────┐
│ Sort ▼   Per page ▼                    │ ← row 1
│                Export   23/1432  ← →   │
├────────────────────────────────────────┤
│ [Filters (3)] ◀ active count badge     │ ← row 2
└────────────────────────────────────────┘
```

Tapping `[Filters (3)]` opens a bottom-sheet drawer containing the
five filters stacked vertically. "Apply" + "Clear filters" actions
at the bottom of the sheet. Sheet closes on Apply (commits to URL)
or backdrop tap (discards draft).

### 7.3 Active filter chips (both viewports)

When any filter is non-default, a chip row renders below the
toolbar (or above the table on mobile). Each chip:

- Shows the filter name + summary: `Companies × 3`, `Accounts × 2`,
  `Apr 1 → May 8`, `Party: Sun Infotech Solutions`.
- Has an inline `×` button that clears that filter only.
- The "Clear filters" button (right-aligned) clears all active
  filters at once — only renders when ≥1 filter is non-default.

**Chip width cap (mobile + desktop).** A single party name like
`Sun Infotech Solutions Pvt Ltd (Mumbai Branch)` or a long
account name selection rendering as `Accounts: Capital Work In
Progress, Stock Received But Not Billed, Customer Advances`
would otherwise blow the chip row off-screen — especially on
the ≤800px bottom-sheet variant. Each chip therefore caps at
`max-width: 200px` with text overflow handled by ellipsis. The
full selected value lives on the chip's `title` attribute so
hovering (desktop) or long-pressing (mobile) reveals the full
text.

CSS pattern:

```css
.dgv-gl-filter-chip {
    max-width: 200px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    /* … existing chip styling … */
}
```

HTML pattern:

```html
<span class="dgv-gl-filter-chip" title="Accounts: Capital Work In Progress, Stock Received But Not Billed, Customer Advances">
    Accounts × 3
</span>
```

The `×` clear-this-filter button stays outside the ellipsized
inner span so it remains tappable even when text is truncated.

### 7.4 Active filter count badge

The mobile `[Filters (N)]` button and the desktop "Advanced
filters" disclosure both show a count badge of how many filters
are currently non-default. Voucher-types behind "Advanced" counts
toward the overall badge but uses its own sub-badge inside the
disclosure header.

### 7.5 Empty states

- **Filters yield zero rows**: existing empty state ("No GL entries
  for this scope as of <date>.") with an additional line: *"Try
  clearing one or more filters."* + a "Clear filters" button
  inline.
- **`from_date` invalid (after `to_date`)**: server returns zero
  rows + the UI shows an inline validation message above the
  table: *"From date must be on or before to date."*
- **`to_date` clamped (UI received `clamped_to_date: true`)**:
  flash a one-time tooltip on the `to_date` field: *"Date range
  capped at as-of date (May 8, 2026)."*

## 8. Interaction with Export CSV

CSV export honors all current filter state — same params passed to
`export_gl_entries_csv`. The exported file matches what's on
screen (modulo pagination — export streams full set up to 50K cap).

**Filename change.** Previous (HALT 2):
```
gl_entries_<scope>_<as_of>_<HHMMSS>.csv
```

New (HALT 2.5):
```
gl_entries_<scope>[_filtered]_<as_of>_<HHMMSS>.csv
```

The `_filtered` segment is inserted only when at least one of
`account_names`, `from_date`, `to_date`, `voucher_types` is
non-default. (Companies and party are not considered "filters" for
this purpose because they were always part of the scope/URL
contract — only the new HALT 2.5 filters trip the marker.)

Examples:
- `gl_entries_current-liabilities_2026-05-08_155739.csv` — no filters
- `gl_entries_current-liabilities_filtered_2026-05-08_155739.csv` — at least one filter active

The 50K cap continues to apply post-filter. Filters can take a
>50K scope below the cap, in which case the export proceeds
normally instead of throwing.

## 9. Interaction with pagination

- **Any filter change resets `page` to 1.** Otherwise the user
  sees "Showing 451–500 of 47" because their old offset is past
  the new total.
- **Page size selector unchanged.** Filter changes don't reset
  page_size.
- **URL `pushState` on every filter change** so back/forward steps
  through filter history. Same pattern as HALT 1's sort/page
  navigation.

## 10. Interaction with group dividers

No change to HALT 1 / spec v0.5 behavior. Dividers visible when
sort is `posting_date_*`; hidden when sort is `amount_*`. The
divider chip continues to label `(company, account_name)`. After
HALT 2.5 the displayed `account_name` may be one the user actively
selected via the filter — in which case the chip is informational
("yes, this is the account you filtered to") rather than
discoveringthat the scope spans multiple.

## 11. Tests

### 11.1 Server-side (added to `tests/test_gl_drill.py`)

8 new tests, scoped to HALT 2.5 surfaces:

1. `test_get_gl_entries_account_names_filter` — `account_names=['Creditors']`
   returns only rows whose joined `tabAccount.account_name` matches.
2. `test_get_gl_entries_from_date_filter` — entries before `from_date`
   excluded; `total_entries` reflects the narrowed count.
3. `test_get_gl_entries_to_date_filter` — entries after `to_date`
   excluded.
4. `test_get_gl_entries_to_date_clamps_to_as_of_date` — passing
   `to_date > as_of_date` returns same rows as no `to_date`,
   plus `clamped_to_date=True` in response.
5. `test_get_gl_entries_voucher_types_filter` — narrows by
   `voucher_type IN (...)`; response's `voucher_types_in_scope`
   reflects the unfiltered universe (so the dropdown options
   don't shrink to just the active selection).
6. `test_get_gl_entries_filters_combined` — applying account_names +
   from_date + voucher_types together: result is the intersection.
7. `test_export_gl_entries_csv_honors_filters` — CSV body row
   count matches `total_entries` of the same filter combination.
8. `test_export_gl_entries_csv_filename_includes_filtered_segment` —
   filename has `_filtered_` when any HALT 2.5 filter active;
   omits otherwise.

### 11.2 Client-side smoke tests

JS unit tests aren't established in this repo (HALT 1 used browser
console smoke tests). Same pattern here. Five smoke tests for the
URL builder to be exercised in browser console at HALT 2.5.3:

```js
// 1. No filters → existing URL shape unchanged
buildExportCsvUrl({...basicState});
// → '/api/method/...?scope=...&as_of=...'

// 2. account_names only → comma-encoded
buildExportCsvUrl({...basicState, account_names: ['Creditors', 'Employee Advances']});
// → contains '&account_names=Creditors%2CEmployee%20Advances'

// 3. Date range only
buildExportCsvUrl({...basicState, from_date: '2026-04-01', to_date: '2026-05-08'});
// → contains '&from_date=2026-04-01&to_date=2026-05-08'

// 4. All filters combined → all params present
// 5. Empty-string filter values → omitted from URL
```

## 12. Halt points

Numbered `HALT 2.5.x` so the renumbering remains visible in the
git log: filters were inserted between master-spec HALT 2 (CSV) and
HALT 3 (View All Parties), pushing the latter to HALT 4. Anyone
reading the commit history later sees the insertion structurally
rather than having to reconstruct it.

1. **HALT 2.5.1 — Filter spec** (this document) — Aditya reviews;
   spec re-versions to v0.2 if changes needed.
2. **HALT 2.5.2 — Implementation** — server endpoint extensions
   (`get_gl_entries` + `get_filter_metadata` + `export_gl_entries_csv`),
   URL contract on the page, JS state mgmt + filter UI components.
   No CSS yet beyond reused tokens.
3. **HALT 2.5.3 — Visual verification + EXPLAIN** at desktop and
   800px viewport. Browser-console URL-builder smoke tests run.
   EXPLAIN against the inner JOIN with the new predicates active
   per §4 Q1 fail criterion. Halt for index discussion if
   `Using temporary` or `Using filesort` appears.
4. **HALT 2.5.4 — Tests + full suite** — 8 new server-side tests;
   full suite stays green at 155+ (current 147 + 8 new).

After HALT 2.5.4 sign-off → proceed to **HALT 4 (View All Parties)**
— what was the original HALT 3 in master-spec, renumbered after
HALT 2.5 insertion.

## 13. Out of scope (Phase 5 follow-ups)

Each is its own future spec; flagged here so the boundary of v1
filter UI is clear.

- **Saved filter presets.** Per-user named presets backed by
  `DGV User Preferences` doctype. Click a preset → URL fills in.
- **Filter exclusion logic** (`NOT account_name`, `NOT party`).
  Doubles the filter UI complexity; defer.
- **Free-text search** on remarks / voucher_no. Needs a full-text
  index decision (MariaDB FULLTEXT vs application-level LIKE).
- **Filter on amount range** (e.g., entries > ₹10L). Useful for
  audit but not a v1 must-have.
- **Filter on `against_voucher`** (only entries that net against a
  specific voucher). Niche workflow.
- **Cross-scope filter chaining** (apply this filter set to other
  scopes too). The URL contract supports it accidentally — if you
  copy a filter URL and edit `scope=`, the filters travel. v1
  doesn't surface this in UI.

## 14. Open questions

None at v0.1 finalization. Q1 / Q2 / Q3 from master-spec §15.5
resolved inline above (§4.1 / §4.2 / §4.3).

If implementation surfaces a new question, it lands here and the
spec is re-versioned to v0.2.
