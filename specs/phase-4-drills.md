# Phase 4 — Drills

**Status:** v3.2, ready for commit 1
**Estimated duration:** 6–8 working days
**Depends on:** Phase 0, 1, 2, 3, Option A, 3.5 (all merged to main)

**Changes from v3.1:**
- Fixed bench-execute path in §9 commit 2: corrected double-nesting
  prefix and module location (`snapshots/spotlight_refresh.py`, not
  `spotlight/spotlight_refresh.py` — `spotlight/` holds card
  definitions, refresh function lives in `snapshots/`).
- §4.2 `tabAccount` size note corrected: ~7,123 rows on RGI-DEMO
  (~120 unique account names × 59 companies), not "a few hundred".
  JOIN cost still negligible because `a.name = g.account` hits the
  primary key.
- §4.2 added explicit verification task to commit 2 for
  `is_group_company` party-name matching: spot-check 2–3 known group
  company names on dev to confirm the Customer / Supplier record
  name matches the Company record name exactly. If mismatched, party
  drill needs a normalisation step plus its own test.

**Changes from v3:**
- §4.2 party drill split into two endpoints:
  `get_party_breakdown` (group by party across companies) and
  `get_party_company_breakdown` (group by company for one party).
  Disambiguation popover uses the second.
- §4.2 SQL fixed: JOIN to `tabAccount` because `root_type` lives on
  the account, not on `tabGL Entry`. Sign convention reads
  `a.root_type` from the joined account.
- §4.4 focus mode: prior depth stashed on entry, restored on exit
  (one-line state addition; ESC at full depth no longer leaves the
  cockpit at Depth=All when user came in at Depth=3).
- §4.6 scope schema: added safety note that `name_pattern` values
  are server-defined in v1 and must be parameterised via
  `frappe.db.escape` if Phase 5's card editor lets users author them.
- §9 commit 2: added explicit deploy step to regenerate spotlight
  cache after bumping `SPARKLINE_LENGTH` from 6 to 12.
- §11 Q17 timing: clarified — allow-list is a commit 1 deliverable
  consumed by commit 2's `is_party_trackable` implementation.
- §9 commit 3: noted potential split into 3a (panel + iPad) and
  3b ("View all" pages) if review size grows past comfortable.

---

## 1. Goal

Make the cockpit's numbers clickable. After Phase 3.5 the cockpit
surfaces aggregated balances at multiple scopes (group, trust, entity,
account) but every number is a dead-end. Phase 4 adds three drill
paths so users can move from "I see something interesting" to "I see
the underlying detail" without leaving the cockpit's mental model.

The three drills are stratified by depth:

1. **Account drill** — one account scope, broken down across all companies in scope, with 12-month trend, and (for party-trackable accounts) a By-party breakdown
2. **GL drill** — one (account, company, optional party) tuple, broken down to actual transactions
3. **Focus mode** — one company, full TB with summary tiles (technically a reflow, not a drill)

Plus two convenience actions that piggyback on existing components:

- **Trust column header click** → sets trust selector to that trust
- **Spotlight card click** → opens account drill with the card's scope

## 2. Non-goals

- **Entity drill as a separate panel.** Replaced by focus mode.
- **TB Doctor.** Anomaly detection — Phase 5.
- **Inter-Company Matcher.** Workflow tool — later dedicated phase. Phase 4's party drill on inter-co accounts is the data-surfacing layer the matcher will eventually consume.
- **Drill chaining inside panels.** Account drill → click company row will deep-link to GL drill (new tab), not replace the panel.
- **Custom date ranges in GL drill.** v1 ships with FY-to-date as default and a date pair input.
- **Dr/Cr split column in focus mode.** v1 reuses single-balance pivot rendering. Deferred to Phase 5.
- **Reconciliation hint on inter-co panels.** That's the matcher's job.
- **Party-level snapshot tables.** Party data is read on-demand via party drill APIs.

## 3. Architecture rule amendment

The `CLAUDE.md` rule "all UI reads from snapshots, never from
`tabGL Entry`" must be amended for Phase 4. The amended rule:

> **Cockpit reads** (top-level pivot, spotlight cards, account drill,
> focus mode summary tiles) come from `DGV TB Snapshot Row` and
> `DGV Spotlight Cache` only.
>
> **Drill reads** may query `tabGL Entry` directly only when **ALL**
> of the following hold:
>
> (a) the API is named with a `_drill` suffix,
> (b) it respects User Permissions on Company at API entry,
> (c) it uses the existing covering index from Phase 3,
> (d) it paginates results > 100 rows (after grouping for aggregated reads),
> (e) it scopes to **a single account or single account-subtree** AND
>     a bounded set of companies (1 to N where N is the user's allowed
>     companies); never wildcards across accounts,
> (f) it is **read-only** — no INSERT, UPDATE, DELETE, or MERGE on
>     `tabGL Entry` is permitted under any circumstances,
> (g) every row returned passes `_allowed_companies()` check.

GL drill and party drill (both endpoints) satisfy this rule.

This amendment goes in `CLAUDE.md` as part of Phase 4 commit 1.

## 4. Component / API design

### 4.1 Account drill

**Entry points:**
- Click a row in the pivot
- Click a spotlight card

**API:** `dux_groupview.api.account_drill_v1.get_account_breakdown`

```
Input: {
  scope: <ScopeSpec — see §4.6>,
  as_of_date: "YYYY-MM-DD",
  companies: <list of company names; resolved server-side via _resolve_scope>
}

Output: {
  scope_label: "Sundry creditors",
  group_total: 80000000,             // raw rupees
  is_party_trackable: true,          // signals UI to also call party_drill
  trend_12mo: [
    { month: "2025-04", value: 72000000 },
    ...
  ],
  by_company: [
    { company: "GHRCE",   value: 14000000, sparkline: [...12 vals...] },
    { company: "GHRCEMN", value:  9000000, sparkline: [...] },
    ...
  ]
}
```

**Reads from:** `DGV TB Snapshot Row` only.

**Scope resolution:** Reuses `_resolve_scope` from `api/pivot.py`.

**Format:** Raw rupees. Client uses existing `formatNumber` /
`formatIndian` helpers.

**`is_party_trackable` flag:** Computed from the resolved leaf
accounts' `account_type`. Allow-list is a commit 1 deliverable
(see Q17, §11). Default proposal:
`('Receivable', 'Payable', 'Loan')`. When true, the panel UI fires
`party_drill_v1.get_party_breakdown` in parallel and renders the
By-party section.

**Performance target:** < 600 ms p95 on production scale.

**Sparkline length:** 12 points (consistent with spotlight after
the Phase 4 commit 2 bump from 6 to 12).

### 4.2 Party drill (new)

Two endpoints in
`dux_groupview/dux_groupview/api/party_drill_v1.py`. Both share SQL
shape and index strategy; they differ only in GROUP BY.

#### 4.2.1 `get_party_breakdown` — group by party, across companies

**Entry point:** Fired automatically by the account drill panel when
`is_party_trackable=true`. Runs in parallel with `account_drill_v1`.

```
Input: {
  scope: <ScopeSpec, same as account drill>,
  as_of_date: "YYYY-MM-DD",
  companies: <list>,
  page: <int, default 1>,
  page_size: <int, default 10, max 200>,
  sort: "balance_desc" | "balance_asc" | "name_asc"
}

Output: {
  total_parties: 142,
  page: 1,
  page_size: 10,
  parties: [
    {
      party_type: "Supplier",         // or "Customer" / "Employee"
      party: "Asha Stationers",
      balance: 6200000,               // raw rupees, sum across companies in scope
      company_count: 8,               // distinct companies this party appears in
      is_group_company: false         // true if party name matches a group company
    },
    ...
  ]
}
```

#### 4.2.2 `get_party_company_breakdown` — group by company, for one party

**Entry point:** Fired by the disambiguation popover when a user clicks
a party row that has `company_count > 1`.

```
Input: {
  scope: <ScopeSpec>,
  as_of_date: "YYYY-MM-DD",
  companies: <list>,
  party: <string>,
  party_type: <string>
}

Output: {
  party: "Vidarbha Lab Supplies",
  party_type: "Supplier",
  total_companies: 5,
  by_company: [
    { company: "GHRCE",   balance: 8400000 },
    { company: "GHRCEMN", balance: 6200000 },
    ...
  ]
}
```

#### Shared SQL + index notes

**Reads from:** `tabGL Entry` JOIN `tabAccount` (per amended rule §3).

**Sign convention via JOIN:**

```sql
SELECT
  g.party_type,
  g.party,
  SUM(CASE WHEN a.root_type IN ('Liability', 'Equity', 'Income')
           THEN g.credit - g.debit
           ELSE g.debit - g.credit END) AS balance,
  COUNT(DISTINCT g.company) AS company_count
FROM `tabGL Entry` g
JOIN `tabAccount` a ON a.name = g.account
WHERE g.account IN (<resolved leaf accounts>)
  AND g.company IN (<allowed companies>)
  AND g.posting_date <= %s
  AND g.is_cancelled = 0 AND g.docstatus = 1
  AND g.party IS NOT NULL AND g.party != ''
GROUP BY g.party_type, g.party        -- swap to g.company for 4.2.2
ORDER BY balance DESC
LIMIT %s OFFSET %s
```

`a.root_type` is the standard ERPNext field on `tabAccount`; the JOIN
is necessary because `tabGL Entry` does not store root_type or
account_root.

**Sign convention parity:** This `CASE WHEN root_type IN
('Liability','Equity','Income')` logic must match the snapshot
refresh's net-side convention exactly. Verify in commit 2 by reading
the snapshot refresh SQL and asserting the gold-standard
reconciliation invariant in tests.

**`is_group_company`:** Computed in Python after the query by
intersecting `party` against the known group company name list.

**Performance target:** < 600 ms p95 on production scale.

**Index check:** The Phase 3 covering index
`(is_cancelled, docstatus, company, account, posting_date)` does not
optimally cover the `GROUP BY party_type, party` (or `GROUP BY company`).
EXPLAIN in commit 2; if needed, add a supplementary index on
`(account, party_type, party, posting_date)` via patch. Verify the
JOIN to `tabAccount` doesn't degrade index usage — `tabAccount` has
~7,123 rows on RGI-DEMO (~120 unique account names × 59 companies),
JOIN hits the primary key on `a.name = g.account` so cost is
negligible.

**`is_group_company` verification (commit 2 task):** Matching `party`
against the group company name list assumes the Customer / Supplier /
Employee record (which is what `party` stores) shares the exact name
string with the Company record. In ERPNext that's typically true when
group companies are mirrored as customers/suppliers, but not
guaranteed — someone could create a Customer called "GHRCE" while the
Company is "GH Raisoni College Of Engineering". Commit 2 must
spot-check 2–3 known group-company customer/supplier names on dev to
confirm exact match. If mismatched, add a normalisation step (lower +
strip + abbr handling) and a unit test covering the normalised path.

### 4.3 GL drill

**Entry point:** Click a cell in the pivot, OR click a company row in
the account drill panel, OR click a party row in the party section
(party becomes a URL filter; multi-company parties go through the
disambiguation popover first). Always opens new tab.

**API:** `dux_groupview.api.gl_drill_v1.get_gl_entries`

```
Input: {
  account: <string>,
  company: <string>,
  party: <optional string>,
  party_type: <optional string>,
  from_date: "YYYY-MM-DD",
  to_date: "YYYY-MM-DD",
  page: <int, default 1>,
  page_size: <int, default 50, max 200>,
  search: <optional string for voucher number filter>,
  voucher_type_filter: <optional list>
}

Output: {
  total_entries: 1247,
  page: 1,
  page_size: 50,
  opening_balance: 0,
  closing_balance: 14200000,
  entries: [
    {
      posting_date: "2025-04-02",
      voucher_type: "Purchase Invoice",
      voucher_no: "PINV-25-0042",
      voucher_link: "/app/purchase-invoice/PINV-25-0042",
      party: "Asha Stationers",
      remarks: "...",
      debit: 0,
      credit: 2840000,
      running_balance: 2840000     // raw rupees, via window function
    },
    ...
  ]
}
```

**Reads from:** `tabGL Entry` directly (per amended rule §3).

**Running balance:** SQL window function so paginated pages are
correct without re-fetching:

```sql
SELECT
  posting_date, voucher_type, voucher_no, party, remarks, debit, credit,
  @opening + SUM(debit - credit)
    OVER (ORDER BY posting_date, name
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    AS running_balance
FROM `tabGL Entry`
WHERE company = %s AND account = %s
  AND posting_date BETWEEN %s AND %s
  AND is_cancelled = 0 AND docstatus = 1
  [AND party = %s AND party_type = %s]
ORDER BY posting_date, name
LIMIT %s OFFSET %s
```

`@opening` computed in a separate query as `SUM(debit - credit)` for
entries before `from_date` (with same party filter if present).

**Page route:** `/groupview/gl-detail?account=...&company=...&from=...&to=...&party=...`

**Party row click resolution:**
- 1 company in scope for party → open GL drill directly
- N>1 companies → fire `get_party_company_breakdown`, render
  disambiguation popover, user picks one → GL drill opens with
  that company

**CSV export:** Generate in memory, serve via
`frappe.local.response.filecontent`. 50K-row hard cap with helpful
error specifying what to narrow.

**Performance:** < 1 s first page, < 500 ms subsequent pages,
< 10 s for ≤ 50K row CSV.

### 4.4 Focus mode

**Entry point:** Click a leaf company column header in the pivot.

**API:** Reuses `get_pivot_data` with `companies=[<one>]`,
`depth=full`. No new endpoint.

**Approach:** Server fetch on entry. Small payload (one column, all
rows, full depth) so response is fast.

**UI changes:**
- Pivot reflows: 1 Balance column (no Dr/Cr split)
- Depth auto-jumps to full; depth control still works
- Summary tiles row above pivot: Total assets, Total liabilities,
  Net surplus, Cash & bank — computed client-side from the focus
  fetch using §4.5 helper
- Focus pill in toolbar (amber, × dismisses)
- ESC dismisses
- URL hash carries `focus_company` for shareable state

**Depth state:** On focus entry, stash the user's pre-focus depth.
On focus dismiss (× or ESC), restore that depth. Without this, a
user who entered focus at Depth=3 finds their cockpit at Depth=All
after exit, which is a regression of their earlier choice.

**Performance target:** < 400 ms p95.

**Trust header click:** Sets trust selector to that trust, clears
focus mode if active. No new API.

### 4.5 Subtree resolution helper (extracted)

`_walk_subtree_leaves(parent_account_name, company)` — returns all
leaf descendants of a parent account.

Currently lives inside `_build_accounts_and_balances` in
`api/pivot.py`. Factor out in commit 1, place in
`dux_groupview/dux_groupview/api/utils.py` next to `_resolve_scope`.

Refactor pivot code to use it; verify nothing changes via Phase 3's
gold-standard pivot test.

### 4.6 Scope schema

```python
ScopeSpec = (
    {"type": "account",      "value": "<exact account name>"} |
    {"type": "subtree",      "value": "<parent account name>"} |
    {"type": "name_pattern", "value": "<SQL LIKE pattern>"}
)
```

Three types, no fourth. Party filtering is handled by the separate
party drill API, not by scope type.

**Safety note:** ScopeSpec values are server-defined (`cards.py` and
similar code-side definitions) in v1. Never bound from user input.
If Phase 5's card editor allows users to author `name_pattern`
values, parameterise via `frappe.db.escape` at that point to prevent
SQL injection. Until then, the values are trusted.

### 4.7 Spotlight card click

Each card in `dux_groupview/dux_groupview/spotlight/cards.py` gets a
`scope` field of type `ScopeSpec`. Card click → cockpit calls
`get_account_breakdown` → panel opens.

**Card scope assignments** (subject to commit 1 COA inspection):
- Sundry creditors → subtree "Sundry Creditors"
- Sundry debtors → subtree "Sundry Debtors"
- Unsecured loans → subtree "Unsecured Loans" (parent name TBD)
- Cash & bank → subtree "Cash & Bank"
- Inter-co receivable → subtree "Branch & Division" (or actual COA
  name — RGI's pre-Phase-2 inter-co holding account). Card must be
  re-scoped after Phase 2 inter-co migration to Inter-Company JV.
- Fixed deposits → subtree "Fixed Deposits" (parent name TBD)

## 5. UI flow

```
Cockpit
├── Click pivot row     → Account drill panel slides in
├── Click pivot cell    → GL drill page in new tab
├── Click leaf col hdr  → Focus mode (pivot reflow, no panel)
├── Click trust col hdr → Trust selector jumps
└── Click spotlight card → Account drill panel slides in

Account drill panel
├── Title: scope label
├── Group total + 12-month trend
├── By company (always present)
│   ├── Top 10 with sparklines
│   └── "View all 59 →" affordance if > 10
├── By party (when is_party_trackable=true; loads in parallel)
│   ├── Top 10 sorted by balance desc
│   ├── Party type pill (Supplier/Customer/Employee/Group co)
│   ├── "Across X cos" column
│   └── "View all N →" affordance opens paginated parties page
├── Click company row → GL drill new tab
├── Click party row   → 1 co: GL drill direct. N>1 cos:
│                       disambiguation popover → GL drill
└── ESC or × dismisses

Focus mode
├── Toolbar: amber focus pill (× dismisses)
├── Summary tiles row
├── Pivot: 1 Balance column, depth=full
└── ESC dismisses (restores prior depth)

GL drill page (/groupview/gl-detail)
├── Header chips: account, company, [party if filtered], date range
├── Filter bar: search, voucher type
├── Paginated table
├── Voucher links → source doc
├── CSV export (50K cap)
└── Pagination
```

## 6. Performance targets

| Operation                             | Target (p95) | Approach |
|---------------------------------------|--------------|----------|
| Account drill API                     | < 600 ms     | Snapshot read |
| Party drill — by party                | < 600 ms     | GL aggregated, JOIN tabAccount |
| Party drill — by company (one party)  | < 300 ms     | Smaller scope, same shape |
| Account drill panel open (combined)   | < 700 ms     | Two APIs in parallel |
| GL drill first page                   | < 1 s        | Window function, indexed |
| GL drill subsequent pages             | < 500 ms     | Same query, different OFFSET |
| GL drill CSV export (≤ 50K rows)      | < 10 s       | In-memory + filecontent |
| Focus mode entry                      | < 400 ms     | Server fetch, single company |
| Trust header click                    | < 100 ms     | Client-only |
| Disambiguation popover open           | < 300 ms     | get_party_company_breakdown |

All measured on RGI-DEMO 5M-entry seed before merge. Logged to
PHASE_LOG.md.

## 7. Doctypes & schema changes

**No new doctypes.**

**Modified:** Card definitions in `cards.py` get a `scope` field.

**Indexes to verify (not necessarily add):**
- `DGV TB Snapshot Row (company, account, snapshot_date)` — account drill historical
- `tabGL Entry (is_cancelled, docstatus, company, account, posting_date)` — Phase 3, covers GL drill
- `tabGL Entry` party group-by — likely needs supplementary index
  on `(account, party_type, party, posting_date)` for party drill.
  Confirm via EXPLAIN in commit 2.

## 8. Test plan

**Unit tests:**
- `test_account_drill_api.py` — three scope types, permission filtering, sparkline correctness, `is_party_trackable` flag across account types
- `test_party_drill_api.py` — pagination, sort, sign convention agreement with snapshot path, `is_group_company` flag, multi-company parties, zero-balance edge case, both endpoints
- `test_gl_drill_api.py` — pagination, search, voucher filter, CSV export, permission filtering, opening/closing balance, **window function running balance correctness across pages**, party filter correctness
- `test_focus_mode_state.py` — URL hash round-trip, summary tile math, **prior depth restored on dismiss**
- `test_subtree_helper.py` — leaf walking against COA fixtures

**Integration tests:**
- `test_drill_correctness.py` — gold-standard invariants:
  - `account_drill.group_total == sum(account_drill.by_company values) == pivot cell aggregated`
  - `account_drill.group_total == sum(party_drill.parties[].balance)` for party-trackable accounts
  - `sum(party_drill_for_one_party.by_company values) == party's total balance from default party drill`
  - GL drill closing balance == account drill by_company value for that company

**Browser tests on RGI-DEMO:**
- Card click with party-trackable scope → both sections render
- Card click with non-party-trackable scope → only by-company section
- Party row click with single-company party → opens GL drill directly
- Party row click with multi-company party → popover, pick, GL drill
- Cr/L/Full toggle updates all values immediately
- Permissions: limited-access user sees only permitted companies in both sections
- iPad viewport: panel becomes full-width modal
- "View all" links open dedicated pages
- Focus mode: enter at Depth=3, exit, verify cockpit returns to Depth=3

## 9. Commit plan

1. **Architecture rule + helper extraction + COA inspection.** Update
   CLAUDE.md with §3 amendment. Extract `_walk_subtree_leaves` to
   `api/utils.py`, refactor pivot code to use it, verify Phase 3
   gold-standard pivot test still passes. Inspect COA on dev to
   confirm card scope assignments and Q17 allow-list.
   **No code-path changes** at end of this commit — just helpers
   moved, one .md file edited, and findings documented.

2. **Account drill API + Party drill APIs (both endpoints) + tests.**
   Both modules tested together via the gold-standard reconciliation
   invariant. Includes spotlight `SPARKLINE_LENGTH` bump 6 → 12.
   Includes the `is_group_company` exact-match verification task from
   §4.2 (spot-check 2–3 group cos on dev; add normalisation if needed).
   **Deploy step (must run after merge):**
   `bench --site erp.jewonline.in execute dux_groupview.dux_groupview.snapshots.spotlight_refresh.refresh_spotlight_cache`
   to regenerate cached sparklines with 12 points. Note the doubled
   `dux_groupview.dux_groupview` prefix (Frappe's standard
   double-nesting — see CLAUDE.md gotcha) and that the refresh
   function lives under `snapshots/`, not `spotlight/`
   (`spotlight/cards.py` holds card definitions; the refresh function
   is in `snapshots/spotlight_refresh.py`). Without this step,
   spotlight cards keep showing 6-point sparklines until the next
   scheduled refresh. Document in commit message + PHASE_LOG.md.

3. **Account drill panel UI + iPad responsive + "View all" pages.**
   Slide-in panel desktop, full-width modal at < 900 px. Both
   sections (By company always, By party when trackable). "View all"
   pages for each dimension. Wired to pivot row clicks.
   **If review size grows past comfortable**, split into:
   - 3a — panel UI + iPad responsive + pivot row wiring
   - 3b — "View all" pages (company dimension, party dimension)

4. **GL drill API + page route + tests.** Window function running
   balance, party filter param, CSV export.

5. **GL drill wiring.** Pivot cell click, account drill company row
   click, account drill party row click (with disambiguation
   popover via `get_party_company_breakdown`).

6. **Focus mode.** Column header click, server fetch, reflow, tiles,
   focus pill, URL hash, **depth save/restore**. Trust header click
   + focus clear.

7. **Spotlight card scope wiring.** Add `scope` field per commit 1
   COA findings.

8. **End-to-end browser review on RGI-DEMO.**

9. **Performance verification** — measure all §6 targets.

10. **Phase 4 close.**

## 10. Open questions

### Q13 — Inter-co receivable scope shape — **Resolved**
No fourth scope type needed. Card uses subtree on RGI's actual
inter-co holding account, confirmed in commit 1.

### Q14 — CSV export size cap — **Resolved**
Hard error at 50K rows with helpful message.

### Q15 — Focus mode + trust filter interaction — **Resolved**
Clicking a different trust while focused clears focus and jumps to
that trust's all-companies view.

### Q16 — iPad / narrow viewport — **Resolved**
Panel becomes full-width modal at < 900 px. Built into commit 3.

### Q17 — Party-trackable account allow-list — **Resolves with commit 1 COA inspection**
The `account_type` allow-list is a **commit 1 deliverable**:
inspection of RGI's actual COA produces the final list. Commit 2
implements `is_party_trackable` against that list. v3.1 proposes
`('Receivable', 'Payable', 'Loan')` as starting list; commit 1
expands or trims based on findings.

## 11. What halts before code

1. ✓ Aditya signs off on §3 architecture rule (covers two `_drill` APIs)
2. ✓ Aditya signs off on §4 API contracts including v3.1 corrections
3. ✓ Aditya signs off on focus mode single-column Balance + depth restore
4. ✓ Aditya signs off on commit 2 bundling account + both party drill endpoints
5. Q17 resolves with commit 1's COA inspection (is a commit-1 deliverable, not a pre-commit blocker)

All approval signals received in v3 review. Awaiting "go" to start commit 1.
