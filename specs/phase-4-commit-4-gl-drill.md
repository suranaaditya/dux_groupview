# Phase 4 commit 4 — GL drill page + CSV export + view all parties

**Status:** v0.4, finalized — HALT 2 (CSV) implementation in progress
**Branch:** `phase-4-drills` (continuing; no new branch)
**Estimated duration:** 1–2 working days
**Depends on:** Phase 4 commits 1, 2, 2.5, 3, 3.1 (all on `phase-4-drills`); side PR #10 (trust-subset seed) and #11 (augmented AP/AR seed) — both merged to main.

This spec extends the master Phase 4 spec (`specs/phase-4-drills.md`).
Sections labelled §4.x reference the master spec; sections numbered
without a prefix are local to this commit.

**Changes from v0.3:**
- §5.1 / §8 — default sort flipped from `posting_date_desc` to
  `posting_date_asc`. Reason: HALT 1 visual review surfaced that
  newest-first display reads as "jumbled" when the running balance
  column accumulates per `(company, account)` partition in (date
  ASC, name ASC) order — every row shows balance AFTER its
  transaction, but scanning down the page the values move backward
  in time and fluctuate. Every accounting ledger Aditya works
  with (Tally, ERPNext stock TB, QuickBooks) defaults to
  oldest-first specifically because running balance reads as a
  natural accumulator down the column. `posting_date_desc` stays
  available in the toolbar for "what's most recent" queries; only
  the default changes.
- §12 — HALT plan renumbered: HALT 2.5 (filter UI) inserted
  between HALT 2 (CSV) and HALT 3 (View All Parties). Filter UI
  surfaced as a real gap during HALT 1 review (in-page company /
  account-name / date-range filters; URL-only scope was the only
  way to narrow before). Out of scope for HALT 2 to keep that
  diff focused on CSV; speced and built in HALT 2.5 before
  HALT 3 begins.
- §15 — added "filter set" as the active open question to be
  resolved by `specs/phase-4-commit-4-filters.md` ahead of HALT 2.5.

**Changes from v0.2:**
- §5.1 — default `page_size` 50 → 100 (ergonomic for desktop with the
  running-balance column visible). Max `page_size` 200 → 1000 (above
  1000 the user is in CSV-export territory, not paginated browsing).
- §5.1 — sort key names renamed `date_desc` / `date_asc` →
  `posting_date_desc` / `posting_date_asc` to disambiguate from
  `creation` date (ERPNext's tabGL Entry has both). `amount_*` keys
  unchanged.
- §5.1 — added 50K hard cap to `get_gl_entries` itself (was export-only
  in v0.2 — that was wrong: a deep-paginated reader could land on a
  >50K-row scope and degrade silently). New `is_truncated` boolean in
  the response signals when truncation has occurred so the page can
  show a banner. Export endpoint (§5.2) keeps the HTTP 400 throw
  because a CSV download has no in-band channel for a flag.
- §8 — pagination table updated to match new defaults (50, 100,
  250, 1000 page sizes; default 100; new sort key names).
- §10 — softened "Index dependency" claim. Spec no longer asserts
  WHICH `tabGL Entry` covering index the optimizer will pick; both
  candidates (`dgv_snapshot_aggregation` from Phase 3,
  `dgv_party_drill` from Phase 4 commit 2) cover the access pattern.
  EXPLAIN at perf measurement time documents the actual choice.
  (v0.2 hand-waved this; v0.1 was silent.)

**Changes from v0.1:**
- §5.4 — added "Mode args contract (canonical)" subsection pinning the
  per-mode allow-lists for `page`, `page_size`, `offset`, `sort` and
  the response shape difference (page mode adds `total_pages` and
  `scope` echo).
- §6.1 — added scope-fanout banner (Q1 resolution): triggers at
  `N_accounts > 5` OR `N_companies > 1` with the verbatim wording
  *"GL entries across N accounts × M companies. Running balance
  resets per (account, company)."*
- §6.1 — added running-balance UI affordance: subtle horizontal
  divider + `"<company> • <account>"` chip at every (company,
  account) transition in date-sorted views.
- §6.2 — added Q2 resolution: empty-state copy for the
  `is_party_trackable=False` URL-hand-craft case
  (*"This account doesn't track parties. Party balances aren't
  applicable for non-trackable accounts like Cash, Bank, or Income."*).
- New §13 "Known limitations" — moved card-id stability (Q3) here
  with a `# TODO(phase-5)` marker. Q1/Q2/Q4/Q5 inlined into their
  respective sections.
- §14/§15 renumbered.

---

## 1. Goal

Wire the three stub buttons left over from commit 3 into real
functionality. After commit 4, every "→" or "View all" affordance in the
account drill panel and full page produces a working destination:

1. **"View GL entries →"** → opens `/app/gl-drill?scope=…` with a
   paginated GL transaction list, running balance per (company, account),
   sortable, with CSV export.
2. **"Export CSV"** → downloads a CSV of the *current account-drill*
   view (the by-company breakdown, not GL entries). Same scope, same
   `as_of`, same companies.
3. **"View all →"** (party section) → opens
   `/app/party-list?scope=…` with the full paginated party list for
   the same scope, sortable, with CSV export. Each row click drills
   into `/app/gl-drill` with the same scope plus a party filter.

These three deliverables ship in one commit because:

- They share an architectural pattern (Frappe page + deep-link URL +
  component reuse from `window.dgvDrill`).
- They are causally dependent: the action bar buttons live on the
  account drill panel/page, both party-list rows and account-drill rows
  link into GL drill, and CSV export reuses the same query the page
  renders. Splitting would force throwaway stubs between commits.
- The diff is bounded: ~1 new API module, two new pages of the same
  shape, a small extension to `party_drill_v1.py`, and three JS handlers
  replacing three stubs.

## 2. Non-goals

- **Drill-from-GL-row → voucher / transaction detail page.** The GL
  page links each row's `voucher_no` to the standard ERPNext voucher
  view via `frappe.set_route` — that's existing platform behavior, not
  a feature we own.
- **GL filter UI** (date range picker, party autocomplete, custom
  account multi-select). v1 ships with the URL-encoded scope as the
  only filter; "narrow further" is a Phase 5 deliverable.
- **Saved CSV export profiles or scheduled exports.** Out of scope.
- **Party drill → party-detail page.** A party row's click target is
  the GL page filtered by `party` + `party_type`, NOT a new
  party-summary page. (We could add one later if asked.)
- **Subtree-wide running balance.** Per Aditya's clarification: a
  running balance that crosses (company, account) boundaries doesn't
  correspond to anything in accounting practice. Running balance
  resets per (company, account); the GL page already groups
  visually by (company, account) via its sort.
- **Server-side CSV streaming for >50K rows.** The 50K cap is a UX
  guard, not a technical limit. If real users hit it, we revisit in
  Phase 5 with chunked download.
- **Pre-formatted Indian-grouped numbers in CSV cells.** CSV is data
  interchange; presentation lives in the rendered UI. Cells contain
  raw numeric values (`4500000.00`, not `"45,00,000.00"`) so the
  spreadsheet app's numeric type and locale formatting work
  correctly. Indian grouping stays on screen, not in the file.

## 3. Scope round-trip — `scope_id` URL contract

**Decision:** URL params use `scope=<scope_id>` (a label/canonical
identifier), NOT a JSON-stringified `ScopeSpec`. The new pages mirror
the existing `/app/account-drill` URL contract exactly; the encoding
is already implemented and tested in commit 3 at `account_drill.js`
lines 884–934.

### scope_id forms (carried over from commit 3, unchanged)

| Form                  | Example                                | Source                  |
|-----------------------|----------------------------------------|-------------------------|
| `<card_id>`           | `sundry_creditors`                     | spotlight card click    |
| `account:<acct_name>` | `account:Sundry Creditors`             | pivot leaf row click    |
| `subtree:<acct_name>` | `subtree:Application of Funds (Assets)`| pivot group row click   |

### Resolution path on the new pages

Both `/app/gl-drill` and `/app/party-list` parse the URL via
`window.dgvParseAccountDrillHash` (already exported in commit 3,
account_drill.js:909). The resulting `{scope: {kind, id}, as_of_date,
companies}` shape is then translated into:

- `kind === 'card'` → call `cockpit.get_spotlight_cards[_filtered]`,
  find by `card_id`, then `cards_v1.resolve_match_to_accounts(match)`
  to obtain the leaf list. (Two-step, identical to
  account-drill page lines 130–174.)
- `kind === 'account'` → pass `scope: {type: 'account', value: <id>}`
  to the API.
- `kind === 'subtree'` → pass `scope: {type: 'subtree', value: <id>}`.

URL length stays bounded: scope_id is at worst ~80 chars (longest
account names on RGI ~60); a 4-trust scope adds ~120 chars in
`companies=`. Well under the 2K browser URL limit.

## 4. The three stubs being replaced

Verbatim quotes from `dux_groupview/public/js/account_drill.js` so we
agree on the wiring points before code lands.

### 4.1 `stubGlDrill` (lines 837–843)

```js
function stubGlDrill(ctx, args) {
    console.log('[dux_groupview] View GL entries clicked', { ctx: ctx, args: args });
    frappe.show_alert({
        message: 'GL drill coming in commit 4.',
        indicator: 'blue',
    }, 4);
}
```

**Called from:**
- `bindActionBar` (account_drill.js:823) — panel "View GL entries →" button
- Account-drill full page (`page/account_drill/account_drill.js:285–286`) — same button on the page

**New behavior:** build a `/app/gl-drill?scope=…&as_of=…&companies=…`
URL from `args` (panel) or `state` (page) using the existing
`buildDrillUrl` helper pattern, then `window.location.href = url`.
No popup, no toast — the action is "go to the GL page".

### 4.2 `stubExportCsv` (lines 845–851)

```js
function stubExportCsv(ctx, args) {
    console.log('[dux_groupview] Export CSV clicked', { ctx: ctx, args: args });
    frappe.show_alert({
        message: 'CSV export coming in commit 4.',
        indicator: 'blue',
    }, 4);
}
```

**Called from:**
- `bindActionBar` (account_drill.js:824) — panel "Export CSV" button
- Account-drill full page (`page/account_drill/account_drill.js:287–288`) — same button on the page

**New behavior:** trigger a `window.location.href = '/api/method/'
+ <export_endpoint> + '?…'` download. The endpoint returns
`Content-Type: text/csv; charset=utf-8` with
`Content-Disposition: attachment; filename="<scope_label>—as-of-…csv"`.
**This export is the by-company breakdown of the account drill**, not
GL entries. The GL page has its own export button.

### 4.3 `stubViewAllParties` (lines 853–859)

```js
function stubViewAllParties(args) {
    console.log('[dux_groupview] View all parties clicked', { args: args });
    frappe.show_alert({
        message: 'Full party list coming in commit 4.',
        indicator: 'blue',
    }, 4);
}
```

**Called from:**
- `bindPartyViewAll` (account_drill.js:828–830) — panel "View all →" button
- Account-drill full page (`page/account_drill/account_drill.js:309–311`) — same button on the page

**New behavior:** build a `/app/party-list?scope=…&as_of=…&companies=…`
URL the same way as `stubGlDrill` and navigate. Party-list page
fetches paginated parties (mode="page") and renders them with click-
to-drill into GL.

## 5. API contracts

### 5.1 `gl_drill_v1.get_gl_entries` (new, whitelisted)

**File:** `dux_groupview/dux_groupview/api/gl_drill_v1.py`

**Module suffix:** `_drill` — satisfies architecture rule (a). Reads
`tabGL Entry` directly under the rule amendment in `CLAUDE.md` (Phase
4 rule, all of (a)–(g) hold; same justification as
`party_drill_v1.py`).

**Signature:**

```
get_gl_entries(
  scope=None,           # Phase 4 ScopeSpec, JSON-string OR dict
  accounts=None,        # pre-resolved leaf list (used by card path)
  as_of_date=None,      # defaults to today; only entries with posting_date <= this
  companies=None,       # JSON-string OR list; resolved via _resolve_scope
  party=None,           # optional (party, party_type) filter
  party_type=None,
  page=1,
  page_size=100,        # default 100, max 1000 (above 1000 use CSV export)
  sort='posting_date_asc',  # one of: posting_date_asc (default), posting_date_desc, amount_desc, amount_asc
)
```

**50K hard cap (`is_truncated`).** `total_entries` reports the *actual*
count of matching rows (no cap). When that count exceeds 50,000, the
response slices the data — only the first 50,000 entries (in the
caller's chosen sort order) are queried for the windowed read, and
`is_truncated: true` flags the cap to the page so it can show a
"showing first 50,000 of N — narrow scope or use CSV export" banner.
Below the cap, `is_truncated: false` and `total_entries` is the same
count the page paginates against.

A non-cap behavior was considered (always serve the full set) but
ruled out at v0.3: the windowed query computes running balance over
the *entire* result before pagination slices it (see "Running balance
computation" below). On a 200K-row scope, that's 200K window
computations + sort + slice for every page request, even page 1. The
cap protects every paginated reader from that cliff. Export endpoint
(§5.2) keeps the HTTP 400 throw because a CSV download has no
in-band channel for a flag.

**Output shape:**

```
{
  "total_entries": <int>,         # actual row count (no cap)
  "is_truncated": <bool>,         # true iff total_entries > 50000
  "page": <int>,
  "page_size": <int>,
  "scope_label": <str>,
  "scope_fanout": {               # for the §6.1 banner; computed once per request
    "n_accounts": <int>,          # resolved leaf count
    "n_companies": <int>,         # len(allowed) after permission intersection
  },
  "entries": [
    {
      "name": "<gl-entry-id>",
      "posting_date": "YYYY-MM-DD",
      "company": <str>,
      "account": <str>,                  # full company-suffixed name
      "voucher_type": <str>,
      "voucher_no": <str>,
      "party_type": <str|null>,
      "party": <str|null>,
      "debit": <float>,
      "credit": <float>,
      "signed_amount": <float>,           # natural-side: applies FLIP_ROOT_TYPES
      "running_balance": <float>,         # cumulative natural-side, partitioned by (company, account), ordered by posting_date asc, name asc
      "remarks": <str|null>,
      "remarks_truncated": <bool>,        # true if remarks > 200 chars (UI shows tooltip)
    },
    ...
  ]
}
```

**Running balance computation:**

```sql
SUM(signed_amount) OVER (
    PARTITION BY g.company, g.account
    ORDER BY g.posting_date ASC, g.name ASC
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

`signed_amount` here is the same `CASE WHEN root_type IN
FLIP_ROOT_TYPES THEN credit-debit ELSE debit-credit END` expression
already used in `party_drill_v1.py:140–142`. The window MUST run on
the *unpaginated* result to be correct; we wrap the windowed query in
a derived table and apply `LIMIT/OFFSET` outside it. (Yes, this is
slightly wasteful at high offsets — see §10 perf.)

**`name` as the secondary sort key** breaks ties when multiple
entries share `posting_date`; `tabGL Entry.name` is the integer
auto-name and gives a stable order across queries. Required for
running balance determinism.

**Sort options for the *display* (outer ORDER BY):**

| `sort`                | ORDER BY                                              |
|-----------------------|-------------------------------------------------------|
| `posting_date_asc`    | posting_date ASC, name ASC (default — natural ledger order; running balance reads as accumulator down the column) |
| `posting_date_desc`   | posting_date DESC, name DESC                          |
| `amount_desc`         | ABS(signed_amount) DESC, posting_date DESC, name DESC |
| `amount_asc`          | ABS(signed_amount) ASC, posting_date DESC, name DESC  |

The `posting_date_` prefix is verbose but disambiguates from
`creation` date (also on `tabGL Entry`). v0.2 used `date_desc` /
`date_asc`; v0.3 renames before any caller depends on the short form.

`amount_*` sorts use `ABS()` for the same reason
`get_party_breakdown` does in commit 3.1 (line 123): a Cr-side journal
on a Dr-natural account shouldn't sort below a small Dr-side entry.
This means the running balance column will look "out of order" when
the sort isn't date-based — which is intuitive: the column reads as
"running balance up to this entry's date", not "running over the
displayed sequence".

### 5.2 `gl_drill_v1.export_gl_entries_csv` (new, whitelisted)

**Signature:** identical to `get_gl_entries` minus pagination args.
Returns an HTTP response with:

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="gl-drill—<scope-slug>—<as-of>.csv"`
- Body: CSV with header row + data rows.

**50K row cap (consistent with §5.1).** If the resolved `total_entries`
exceeds 50,000, the export endpoint throws HTTP 400 with a message:
*"Scope spans N entries; CSV export is capped at 50,000. Narrow the
scope (date range, fewer companies, single account) and try again."*

A CSV download has no in-band channel for an `is_truncated` flag —
unlike `get_gl_entries` which returns JSON and can include a boolean,
the CSV body is the data and that's it. Throwing 400 is the cleanest
UX (HTTP attachment download fails visibly, the page surfaces the
error message) versus silently truncating to 50K and producing a CSV
that pretends to be complete.

**Cell format:**
- Numeric columns (`debit`, `credit`, `signed_amount`,
  `running_balance`) are raw decimals: `4500000.00`. NO Indian comma
  grouping. NO currency symbol.
- Date columns are ISO `YYYY-MM-DD`.
- String columns are quoted only when they contain `,`, `"`, or
  newlines (Python `csv` module default, `QUOTE_MINIMAL`).

### 5.3 Account-drill CSV export (lives in `account_drill_v1.py`, not `gl_drill_v1.py`)

The "Export CSV" button on the account drill panel/page exports the
**by-company breakdown** of the current drill, not GL entries.
Different shape, different file.

**Signature:** identical to `get_account_breakdown` (no pagination).

**Implementation:** add a sibling endpoint `account_drill_v1.
export_account_breakdown_csv` that:
1. Calls `get_account_breakdown` internally.
2. Emits a CSV with columns: `company`, `value`, plus 12 sparkline
   columns labeled `<YYYY>-<MM>` (one per month).
3. One row per company, then a final `Total` row.

This keeps the CSV focused on what the user is actually seeing on
screen, not a large transaction dump.

### 5.4 `party_drill_v1.get_party_breakdown` extension — `mode` arg

Per Aditya's clarification: extend the existing function rather than
fork. Same SQL shape, same sub-rupee filter (commit 3.1: `HAVING
ABS(balance) >= 1`), same `is_group_company` flag computation.

**New signature:**

```
get_party_breakdown(
  scope=None, accounts=None, as_of_date=None, companies=None,
  page=1, page_size=None,
  sort='balance_desc',
  mode='card',                   # NEW: 'card' (default) | 'page'
)
```

**Mode semantics:**

| Aspect          | `mode='card'` (existing default)         | `mode='page'`                                    |
|-----------------|------------------------------------------|--------------------------------------------------|
| Default page_size | 10 (DEFAULT_PAGE_SIZE)                | 50                                               |
| Max page_size   | 200                                      | 200                                              |
| Sort options    | balance_desc, balance_asc, name_asc      | balance_desc, balance_asc, name_asc, name_desc   |
| Output shape    | `{total_parties, page, page_size, parties}` | same                                          |
| Behaviour       | Unchanged from commit 3.1.               | Same SQL, same filter; only knobs differ.        |

`name_desc` is added for the page mode only because the page exposes
sort controls and offering only `name_asc` is asymmetric. `card` mode
keeps its current allow-list to avoid changing existing callers.

#### Mode args contract (canonical)

The table above describes effective behavior. The contract below
describes what the API accepts and rejects per mode — the strict
allow-list that implementation and tests pin against.

**`mode='card'` (default, backward-compatible)**
- `page` accepted (default 1); `page_size` accepted, default 10,
  callers (panel, account-drill page) override to 5 and 8 respectively.
- `sort` allow-list: `balance_desc` (default), `balance_asc`,
  `name_asc`. Other values silently fall back to `balance_desc`
  (matches commit 3.1 behavior).
- `offset` is computed from `page` × `page_size`; passing `offset`
  directly is ignored. Documented for the test surface; matches the
  pre-commit-4 contract exactly.
- Returns: `{total_parties, page, page_size, parties}`. No `scope`
  echo, no `total_pages`. Adding `total_pages` would be additive and
  safe but stays off in `card` mode to keep the existing wire shape
  byte-identical.

**`mode='page'`**
- `page` required (no default); `page_size` required, max 500 (raised
  from `card`'s 200 to support a "show 200 per page on a large
  party list" view without round-tripping for the long tail).
- `sort` allow-list: `balance_desc` (default), `balance_asc`,
  `name_asc`, `name_desc`. Unknown values fall back to default.
- `offset` accepted as a power-user override; if both `page` and
  `offset` are passed, `offset` wins. The page UI uses `page`; the
  affordance is for headless / scripted callers.
- Returns: `{total_parties, page, page_size, parties, total_pages,
  scope}` where `scope` echoes the resolved scope back so the caller
  can verify what the server interpreted (useful when a card_id
  resolved to a different leaf set than expected — see §13 known
  limitations on card-id stability).

**Backward compatibility:** existing callers (panel, account-drill
page) pass no `mode` arg; default `'card'` preserves current
behavior. New page passes `mode='page'`.

**Pagination semantics for page mode:** standard offset pagination
(`LIMIT page_size OFFSET (page-1)*page_size`). Total count via the
existing `_count_parties` helper. Page mode also returns a `total_pages
= ceil(total_parties / page_size)` field for convenience — additive,
won't break the panel which ignores it.

### 5.5 New endpoint: `party_drill_v1.export_party_breakdown_csv`

Same args as `get_party_breakdown` minus pagination. 50K cap. CSV
columns: `party_type`, `party`, `balance`, `company_count`,
`is_group_company` (as `Yes`/`No`).

## 6. Pages

### 6.1 `/app/gl-drill`

**Files:**

- `dux_groupview/dux_groupview/page/gl_drill/__init__.py`
- `dux_groupview/dux_groupview/page/gl_drill/gl_drill.json` — page
  registration with role gate (`GroupView Viewer`, `GroupView Owner`,
  `System Manager`).
- `dux_groupview/dux_groupview/page/gl_drill/gl_drill.py` — stub
  controller, just a `ping()` like account-drill.
- `dux_groupview/dux_groupview/page/gl_drill/gl_drill.js` — page
  shell + render. ~250 lines.

**URL:** `/app/gl-drill?scope=<scope_id>&as_of=<iso>&companies=<csv>&party=<str>&party_type=<str>`

The `party` and `party_type` params are optional; passed when the
party-list page links into GL drill for one party.

**Layout:** breadcrumb (`Cockpit / Account drill / GL entries`) →
header (scope label + "as of" date + scope sub-line) → **scope-fanout
banner (conditional, see below)** → toolbar (sort dropdown, page
size selector, "Export CSV" button) → table → footer (pagination
controls).

**Scope-fanout banner (Q1 resolution).** When the resolved scope
expands across many accounts and/or companies, a one-line banner sits
between the header and the toolbar to make the fan-out — and the
running-balance reset rule — visible to the user.

- **Trigger:** `N_accounts > 5` OR `N_companies > 1` (either
  condition; subtree scopes commonly trip both, account scopes with
  multi-company scope trip just the second).
- **Wording (verbatim):** `"GL entries across N accounts × M
  companies. Running balance resets per (account, company)."`
  Substitute the integers; do not pluralise (`"1 accounts"` is a
  defect we accept for v1 simplicity — the banner is information
  density not prose).
- **Styling:** subtle amber-tint background, no icon, single line,
  same horizontal padding as the toolbar. Not dismissible — it's a
  data-shape indicator, not a notification.
- **Counts:** `N_accounts` is the resolved leaf count from
  `_resolve_scope_to_leaves`; `N_companies` is `len(allowed)` after
  permission intersection. Computed server-side and returned in the
  `get_gl_entries` response as `scope_fanout: {n_accounts, n_companies}`
  so the page doesn't have to call a separate endpoint.

**Running-balance UI affordance (group divider).** Because the running
balance resets at each (company, account) boundary (see §5.1), the
table needs a visual cue at every reset or the column reads as
"random numbers" to a user scanning rows. Implementation:

- Insert a thin horizontal divider (`border-top: 1px solid var(--dgv-divider-strong)`) between
  consecutive rows whose `(company, account)` tuple differs from the
  previous row's tuple. Within a group, no divider.
- **Group label** sits on the divider as a small left-aligned chip:
  `"<company> • <account>"`. Same Geist-mono micro-type already used
  for the trend axis labels (12px / 500 weight / muted color).
- The label hides when the table is sorted by `amount_*` (group
  boundaries lose meaning when rows aren't grouped by the partition).
  The horizontal divider also hides in that case; running-balance
  column reads as a per-row figure with no group context, which
  matches the "ABS sort" mental model.
- For `date_*` sorts the rows are NOT pre-grouped by (company,
  account) in the SQL — the SQL `ORDER BY` is `posting_date,
  name`. So divider/label appear at every (company, account)
  *transition* in the displayed sequence, not at fixed group
  blocks. This means the same (company, account) pair can produce
  multiple labels if rows interleave by date. Acceptable; matches
  what the user sees ("date-sorted GL with reset markers").

**Empty / error states:**

- No scope param → "Missing scope parameter. Open this page from a
  drill panel or account drill page." (mirrors account-drill page).
- Scope resolves to zero accounts → "No accounts in scope match." +
  link back to cockpit.
- Zero entries in date range → "No GL entries for this scope as of
  <date>." + sort/date suggestion.

### 6.2 `/app/party-list`

**Files:** same shape as gl_drill, in
`dux_groupview/dux_groupview/page/party_list/`.

**URL:** `/app/party-list?scope=<scope_id>&as_of=<iso>&companies=<csv>`

**Layout:** breadcrumb (`Cockpit / Account drill / Parties`) →
header → toolbar (sort dropdown, page size selector, "Export CSV"
button) → table (party name with `is_group_company` badge, balance
right-aligned with `formatRupeesIndian`, company_count column,
clickable rows) → footer (pagination).

**Row click:** navigates to `/app/gl-drill?scope=…&party=<party>
&party_type=<party_type>` (preserves scope + adds party filter).

**Empty / error states:** mirror gl_drill's structure. Specifically:

- **`is_party_trackable=False` (Q2 resolution).** The page is reachable
  via hand-crafted URL even when the scope's accounts are not party-
  trackable (the panel's "View all parties" button only renders when
  party data exists, but the URL itself is a public surface). Render
  an empty state, NOT a 404 — the URL is well-formed, the scope just
  has no party data. Wording (verbatim): *"This account doesn't
  track parties. Party balances aren't applicable for non-trackable
  accounts like Cash, Bank, or Income."* Mirrors the panel's
  by-party hide rationale (account_drill_v1.py:_is_party_trackable
  returns False → panel hides the section; the page surfaces the
  same message inline).
- **Zero parties after sub-rupee filter** (the `HAVING ABS(balance) >= 1`
  from commit 3.1 strips them all): "No parties with non-zero balance
  in this scope as of <date>." Same back-link pattern.
- Other error states (missing scope, scope resolves to no accounts)
  mirror gl_drill's.

### 6.3 Component reuse

Both pages use the existing `window.dgvDrill` exports where they
share rendering with the panel:

- `formatRupeesIndian` for in-screen amounts (party-list balance,
  GL signed_amount column).
- `formatLongDate` for the as-of header.
- `escapeHtml` for safe interpolation.

New helpers added to `window.dgvDrill`:
- `formatRupeesPlain(n)` — raw decimal with dot separator, no symbol,
  used in CSV cell construction (server is authoritative; this is for
  any client-side preview that might be needed). *Tentative — may not
  be needed; defer until implementation.*
- `formatGlAmount(n, mode='signed')` — variant of `formatRupeesIndian`
  that always shows sign, used in GL entries table.

## 7. JS handler wiring (replacing the three stubs)

**Replace bodies in `account_drill.js`:**

```js
function stubGlDrill(ctx, args) {
    // Compose URL from current request state. ctx may carry resolved
    // accounts (card path) but the URL contract uses scope_id; if
    // ctx.scope is missing we reconstruct from currentRequest.
    const url = buildGlDrillUrl(args);
    window.location.href = url;
}

function stubExportCsv(ctx, args) {
    // Account-drill CSV export. Endpoint returns the file with
    // Content-Disposition: attachment, so a plain navigation works.
    const url = buildAccountDrillCsvUrl(args);
    window.location.href = url;
}

function stubViewAllParties(args) {
    const url = buildPartyListUrl(args);
    window.location.href = url;
}
```

Three new helpers parallel to `buildDrillUrl` (account_drill.js:884):
`buildGlDrillUrl`, `buildPartyListUrl`, `buildAccountDrillCsvUrl`.
Each takes the same `args` shape (`source`, `card_id`, `scope`,
`scope_label`, `as_of_date`, `companies`).

Stub function NAMES stay (`stubGlDrill` etc.) to avoid renaming the
exports in `window.dgvDrill` — the names are an internal label, the
"stub" wording in the comment is what changes.

The function-name shadowing is a deliberate trade-off: renaming would
ripple into `bindActionBar`, `bindPartyViewAll`, the page-side
`addEventListener` calls, and `window.dgvDrill` exports. None of those
are test surfaces. Keeping the names contains the diff to function
bodies + new helpers.

## 8. Sort & pagination semantics

| Page          | Default sort         | Page sizes                  | URL params for sort/page         |
|---------------|----------------------|------------------------------|----------------------------------|
| `gl-drill`    | `posting_date_asc`   | 50, 100 (default), 250, 1000 | `&sort=…&page=…&page_size=…` |
| `party-list`  | `balance_desc`       | 25, 50 (default), 100, 200   | `&sort=…&page=…&page_size=…` |

GL drill jumps to 1000 at the upper end because the running-balance
column makes the table denser per row and a power user scanning a
specific account-month wants to see the whole window without
paginating. Party list keeps the lower 200 ceiling — party rows are
chunky (name + balance + group badge) and 200 is already a lot to
scan visually.

Sort/page params persist in the URL so refresh and copy-link both
preserve view state. "Export CSV" ignores `page`/`page_size` (full
result) but respects `sort` (so the CSV opens in the order the user
chose on screen).

## 9. CSV format reference

Header row + data rows. UTF-8, no BOM (Excel reads UTF-8 cleanly on
modern versions; if Indian users hit display issues we add the BOM
later). `\n` line endings.

### gl-drill CSV columns

```
posting_date,company,account,voucher_type,voucher_no,party_type,party,debit,credit,signed_amount,running_balance,remarks
```

### party-list CSV columns

```
party_type,party,balance,company_count,is_group_company
```

### account-drill CSV columns

```
company,value,2025-06,2025-07,…(12 months oldest→newest)…,2026-05
```

Plus a final row where `company = "Total"` and the value column
contains the group total. Sparkline columns sum across companies for
that month (matches the trend chart's value).

## 10. Performance targets

| Operation                                              | Dev (1.1M entries)      | Prod (5M entries)         | Target |
|--------------------------------------------------------|-------------------------|---------------------------|--------|
| `get_gl_entries` p1, page_size=50                      | TBD on dev              | TBD                       | < 500 ms |
| `get_gl_entries` page=200, sort=amount_desc            | TBD                     | TBD                       | < 1 s    |
| `export_gl_entries_csv` (10K rows)                     | TBD                     | TBD                       | < 3 s    |
| `get_party_breakdown` mode=page, p1, page_size=50      | TBD                     | TBD                       | < 500 ms |
| `export_party_breakdown_csv` (1K parties)              | TBD                     | TBD                       | < 1 s    |
| GL page first paint                                    | TBD                     | TBD                       | < 800 ms |
| Party-list page first paint                            | TBD                     | TBD                       | < 600 ms |

Numbers fill in at the per-halt-point verification step.

**Index dependency.** Two `tabGL Entry` indexes can serve this query:

- `dgv_snapshot_aggregation` `(is_cancelled, docstatus, company,
  account, posting_date)` — Phase 3 covering index, originally added
  for the refresh aggregation.
- `dgv_party_drill` — Phase 4 commit 2 supplementary index, ordered
  for `(account, company, party)` access.

Either is a valid pick for `get_gl_entries`. The optimizer's choice
depends on filter selectivity (small leaf list and small company set
favors the party-drill ordering; broad subtree favors the snapshot
ordering) and statistics. Spec does NOT pre-commit to one. The
HALT 1 EXPLAIN step records which index the optimizer actually picks
on the trust-subset seed and why; that observation lands in PHASE_LOG.
The verification fail criterion is "neither index used" (full table
scan) or `Using temporary; Using filesort`, NOT "wrong index used".

**Window function vs. self-join for running balance:** MariaDB 10.2+
supports `SUM() OVER`. ERPNext v16 dev runs MariaDB 10.6 (verified
via Phase 1). No fallback path needed.

**High-offset pagination cost:** the windowed query computes the full
running balance over the entire result, then `LIMIT/OFFSET` slices it.
For a 50K-row result this means computing all 50K running balances
to show page 200 (rows 9951–10000). Acceptable at v1; if real users
go past page 100 we revisit with cursor-based pagination keyed on
`(posting_date, name)`.

## 11. Testing strategy

**Unit (`tests/test_gl_drill.py`, new):**

1. `test_get_gl_entries_returns_paginated_shape` — fields present,
   pagination metadata correct.
2. `test_running_balance_partitioned_by_company_account` — gold
   standard: hand-compute running balance for one (company, account)
   from raw rows, assert equality with windowed result.
3. `test_running_balance_uses_natural_side_sign` — Liability /
   Equity / Income flipped; parity with `account_drill` SQL via the
   same `FLIP_ROOT_TYPES` source.
4. `test_sort_amount_desc_uses_abs` — verify the sort puts a -₹1L
   entry above a +₹100 entry.
5. `test_party_filter_narrows_results` — `party=`, `party_type=`
   args reduce row count.
6. `test_export_returns_csv_content_type` — response headers correct.
7. `test_export_50k_cap` — calling export with > 50K matching entries
   raises HTTP 400 with the expected message.
8. `test_csv_cells_contain_raw_numerics` — parse the CSV back via
   Python's `csv` module, assert numeric columns parse as floats
   (i.e. no commas).
9. `test_permission_filter` — User Permissions intersection enforced
   the same way `get_party_breakdown` enforces it.

**Unit (`tests/test_party_drill.py`, extend):**

10. `test_get_party_breakdown_mode_page_defaults` — page_size=50,
    name_desc allowed.
11. `test_get_party_breakdown_mode_page_total_pages` — `total_pages`
    field is `ceil(total_parties / page_size)`.
12. `test_get_party_breakdown_mode_card_unchanged` — existing
    behavior pinned (regression guard).
13. `test_export_party_csv_columns` — header row matches §9.

**Manual verification (halt-point checklist, Aditya):**

- Click "View GL entries" from panel and from page; URLs render and
  table populates.
- Sort + paginate; URL params update; refresh preserves state.
- Click a `voucher_no` link → ERPNext voucher view opens (this is
  free via `frappe.set_route`).
- "Export CSV" from panel → file named `account-drill—<scope>.csv`,
  cell `B2` is a number not text in Excel/Sheets (the Excel parsing
  test).
- "View all parties" from panel → list page populates; click a row →
  GL page opens with party filter visible in URL.
- "Export CSV" from GL page and party list page → respective files
  download with raw numeric cells.

**`tabGL Entry` audit:** `grep -rn "tabGL Entry"` across commit-4
files (`gl_drill_v1.py`, page files, JS) — only the `_drill`-suffixed
API may contain real queries; everything else is docstring mentions
of the rule.

## 12. Halt points

The cadence below replaces the v0.1 "three-halt" plan. HALT 2.5 was
inserted in v0.4 after HALT 1 review surfaced an in-page filter UI
gap that wasn't speced.

1. **Spec halt** — review the spec and verification artifacts; sends
   edits. Spec re-versioned each round.
   - v0.1 → v0.2 (5 resolutions + mode-args contract + running-balance
     UI affordance)
   - v0.2 → v0.3 (page-size 100/1000, posting_date_* sort keys,
     50K cap on get_gl_entries, EXPLAIN softening)
   - v0.3 → v0.4 (sort default flip, HALT 2.5 insertion, filters
     open question)
1. **HALT 1 — GL drill page + window function + pagination** —
   `get_gl_entries`, the gl-drill page, `stubGlDrill` wiring, perf
   measurement, EXPLAIN. Aditya verifies in browser.
   *Status: complete; 140/140 tests green; perf accepted as a known
   v1 limitation per HALT 1 closing decisions.*
2. **HALT 2 — CSV export** — `export_account_breakdown_csv` (in
   `account_drill_v1.py` per Q4), `export_gl_entries_csv` (in
   `gl_drill_v1.py`), wire all three "Export CSV" buttons (panel,
   account-drill page, gl-drill page). 50K hard cap on GL entries
   CSV; raw decimal cells; ISO dates; filename
   `<type>_<scope_id>_<as_of_date>_<timestamp>.csv`.
3. **HALT 2.5 — Filter UI** *(new in v0.4)* — separate spec at
   `specs/phase-4-commit-4-filters.md`. In-page filtering for the
   gl-drill page (and possibly party-list once HALT 3 lands):
   company multi-select, account-name multi-select, date range
   (`from_date` / `to_date` rather than only the current `as_of`
   upper bound), party autocomplete UI (URL `?party=` already
   supported by HALT 1, just no input). Open questions in §15.5.
4. **HALT 3 — View All Parties** — party-list page + extend
   `get_party_breakdown` with `mode='page'`, wire the panel's
   "View all parties" link.

Each halt is a separate review cadence. No commit between halts;
the bundled commit lands only after HALT 3 sign-off, and that's
when PHASE_LOG.md gets the consolidated entry.

## 13. Known limitations

Carried into v1 by deliberate decision; flagged for the appropriate
future phase to revisit.

### 13.1 Card-id stability across Phase 5 editor changes

Shareable URLs of the form `/app/gl-drill?scope=<card_id>` and
`/app/party-list?scope=<card_id>` resolve `card_id` server-side at
view time via `cards_v1.resolve_match_to_accounts`. If a future
Phase 5 card editor edits the `match` predicate behind a stable
`card_id`, an old shared link will silently resolve to a different
leaf set than the original sender intended.

Mitigations considered and rejected for v1:
- **Snapshot the leaf list into the URL.** Fails the URL-length
  budget — a card resolving to 80 accounts would push the URL past
  2K chars on multi-trust scopes.
- **Hash the predicate into the URL and reject mismatched links.**
  Workable but couples the URL to internal predicate shape; would
  break when Phase 5 introduces a card-version table even for
  cosmetic predicate edits.

For v1 we accept the silent re-resolution. Phase 5's card editor
will need to address this — likely via a `card_version` field that
participates in the URL (`scope=sundry_creditors@v3`) and a
deprecation table for old `(card_id, version)` pairs.

```
# TODO(phase-5): card-id stability — see specs/phase-4-commit-4-gl-drill.md §13.1
```

(Marker comment added in `cards_v1.resolve_match_to_accounts` and in
the URL-construction helpers in `account_drill.js` so it's grep-able
when the editor work begins.)

## 14. Out of scope (for follow-up commits or Phase 5)

- Cursor-based pagination on GL drill (only if perf data forces it).
- Customizable date range / "show last N days" picker on GL drill.
- Export profiles (saved column choices).
- Direct drill from a GL row's `account` cell into the account drill
  for that account.
- Mobile / small-viewport layout (Phase 4 commit 5+ owns this; out of
  scope here).

## 15. Open questions

### 15.1 — Resolved (v0.1 → v0.2)

Q1 (subtree banner), Q2 (party-list empty state), Q4 (CSV location),
Q5 (mode naming) — all inlined into §6.1, §6.2, §5.5, §5.4. Q3
(card-id stability) moved to §13.1 known limitations with a
`# TODO(phase-5)` marker.

### 15.2 — Resolved (v0.2 → v0.3)

Page size, max page size, 50K cap behavior, sort key naming, EXPLAIN
target — all resolved per the v0.3 change log at the top of this spec.

### 15.3 — Resolved (HALT 1 closing review, v0.3 → v0.4)

- **Perf on huge subtree scopes (25–90 sec)**: accepted as a known
  v1 limitation. To revisit on real production click data in 6
  weeks. Documented in PHASE_LOG.md at the HALT 3 commit.
- **Index choice (`dgv_party_drill` vs `dgv_snapshot_aggregation`)**:
  deferred to a Phase 5 perf sweep.
- **Fanout-banner threshold**: tightened from `N_accounts > 5 OR
  N_companies > 1` (per spec §6.1) to `N_accounts > 20 OR
  N_companies > 5` per HALT 1 visual review — the looser threshold
  tripped on every multi-company drill including 12-leaf 26ms
  loads, drowning the signal. Truncation banner remains a separate
  trigger and is unchanged.

### 15.4 — Resolved (HALT 1 visual review → v0.4)

- **Default sort**: flipped `posting_date_desc` → `posting_date_asc`
  (see §5.1 sort table + this spec's v0.4 change log). Running
  balance reads as a natural accumulator down the column in the
  default view.

### 15.5 — Active (HALT 2.5 spec round)

- **Filter set on the gl-drill page** — to be specced at
  `specs/phase-4-commit-4-filters.md` ahead of HALT 2.5
  implementation. Open sub-questions (initial sketch; the spec
  round will firm these up):
  - Company multi-select: when scope spans >1 company, surface as
    a dropdown with the resolved companies pre-selected. URL state.
  - Account-name multi-select: when scope resolves to >1 unique
    `account_name`, surface as a dropdown. SQL-side narrow (cheaper)
    or client-side post-pagination (broken across pages)?
  - Date range: separate `from_date` / `to_date` URL params.
    Currently only `as_of_date` (upper bound). `from_date=null`
    implies inception.
  - Party autocomplete: URL `?party=` already supported by HALT 1
    code (party_drill links into gl-drill that way). The UI
    affordance to TYPE a party name doesn't exist yet.
  - Voucher type (multi-select, in an "advanced" collapsible
    section) — useful for accountants chasing one source-doc
    type. Defer if it bloats the toolbar; nice-to-have not
    must-have.
  - URL persistence vs localStorage stickiness: shareable URLs
    (URL) or per-user defaults (localStorage)? Current pattern
    is URL for everything; consistency argues URL.

If implementation of HALT 2 surfaces a new question, it lands here
and the spec is re-versioned to v0.5.
