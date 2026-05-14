# Per-account drill expansion in account drill panel

**Status:** v0.1 draft — awaiting HALT 1 sign-off
**Branch:** `feat/per-account-drill-expand`
**Estimated duration:** 1–1.5 working days
**Depends on:**
- Phase 4 close at `2af86b6` on `main` (drills + GL drill + focus mode shipped)
- Account drill panel and `account_drill_v1` API (Phase 4 commit 3)
- GL drill page and `gl_drill_v1` API (Phase 4 commit 4 / HALT 2.5)

---

## 1. Goal

Insert one navigation level between the account drill panel's by-company
breakdown and the GL drill page. Today the panel shows one row per
company with that company's contribution to the card total, and the
"View GL entries" action opens GL drill scoped to all matched accounts
in the company. We want the user to expand a single company row inline
to see its **per-account breakdown** at the snapshot date, and click
one account row to drill to GL filtered to that single account.

This is a drill UX enhancement built entirely on existing snapshot data
and the GL drill page's existing `account_names` filter. No schema
change, no cards-system change, no `_drill` reader path involvement.

## 2. Non-goals

Deferred or out of scope; surfaced explicitly so the diff stays narrow:

- **Not changing the cards system.** No edits to `spotlight/cards.py`,
  card predicates, `cards_v1.py`, or `HEADLINE_CARD_NAMES`.
- **Not changing the GL drill page UI** beyond reading one additional
  URL param (which it already reads — `account_names`). No new HALT
  2.5 filters, no new breadcrumb shape, no new sort options.
- **Not adding new scope kinds.** This task does not introduce a
  per-account scope (`account_in_company:...`); GL drill remains
  scoped by `card_id` / `account:name` / `subtree:name` and is
  further narrowed by the existing `account_names` filter.
- **Not adding party / cost-center / project filters.** Those are
  separate concerns.
- **Not refactoring `account_drill_v1.py` or `account_drill.js`** beyond
  what's needed to wire the new endpoint + new row state in the panel.
- **Not touching the `_drill` reader path.** This feature reads only
  from `tabDGV TB Snapshot Row`, never `tabGL Entry`.
- **Not adding any cards-editor UI.**

## 3. Current state

**Account drill panel today (`api/account_drill_v1.py`,
`public/js/account_drill.js`).** The panel opens on a card click or a
pivot-cell click. It calls `get_account_breakdown(scope, accounts,
scope_label, as_of_date, companies)` which returns one row per company
that has any non-zero value across the 12-month trend window:

```json
{
  "scope_label": "Sundry creditors",
  "group_total": 12062715469.78,
  "is_party_trackable": true,
  "trend_12mo": [{"month": "2025-06", "value": 8.1e9}, ...],
  "by_company": [
    {"company": "Ankush Shikshan Sanstha", "value": 2.4e9,
     "sparkline": [...12 values...]},
    ...
  ]
}
```

Per-account data is never exposed by this endpoint. The CSV export
sibling `export_account_breakdown_csv` does query per-(company,
account) rows from the snapshot (lines 390–413), but only as a
download payload; the panel never receives them.

**GL drill scoping (`api/gl_drill_v1.py`).** The HALT 2.5 filter set
(spec §3) already includes `account_names` — a multi-select list of
stripped account names. The SQL clause `a.account_name IN ({ph})` is
joined against `tabAccount` (which is already joined for `root_type`),
so the filter is essentially free at the index level. The cockpit JS
helper `buildGlDrillUrl()` (`account_drill.js:1336`) does NOT currently
write `account_names` to the GL drill URL; that's the only extension
needed.

**Snapshot data we already have.** `tabDGV TB Snapshot Row` carries
one row per `(snapshot_date, company, account)` for leaf accounts only
(CLAUDE.md hard rule 6). The composite index `dgv_pivot` on
`(snapshot_date, company, account)` from Phase 3 covers any query
filtered by date + company + leaf-account list. No new index needed.

## 4. UX design

### 4.1. Layout

The by-company breakdown table currently renders one `<tr>` per
company (name • optional sparkline • value). We add a small chevron at
the start of each row, repurposing the row as a "company group" that
can expand inline:

```
Liquid Cash — ₹17 Cr
┌──────────────────────────────────────────────────────────────────┐
│ ▶  Ankush Shikshan Sanstha           ✦✦✦✦  ₹2.4 Cr               │
│ ▼  GH Raisoni University Amravati    ✦✦✦✦ −₹4.5 Cr               │
│      ICICI Bank A/c 624205012830                  −₹1.59 Cr  →   │
│      WUCBL A/c 21/1579                            −₹0.06 Cr  →   │
│      Petty Cash                                    ₹0.08 L   →   │
│      <up to 200 rows; sort desc by |balance|>                    │
│ ▶  GH Raisoni College Of Engineering ✦✦✦✦ −₹1.4 Cr               │
└──────────────────────────────────────────────────────────────────┘
```

- Chevron `▶` (collapsed) / `▼` (expanded) sits in a new leading column.
  Click target is the entire company row except the value cell (which
  remains tied to the row's tooltip if any). Pressing Enter / Space on
  a focused row also toggles.
- Expanded state inserts a single `<tr class="dgv-drill-account-expand">`
  immediately after the company row, with a nested `<table>` of
  account rows inside one `<td colspan="N">`. This keeps the parent
  table's column widths stable.
- Per-account row layout: account_name (left, truncated at ~50 chars
  with title attribute carrying the full name) • value (right) • `→`
  affordance hinting "click to view GL entries."
- Whole account row is the click target → GL drill scoped to that
  one account in that one company.

### 4.2. Expand / collapse states

- **Collapsed (default).** Only the company row renders.
- **Loading (first expand).** Chevron stays as `▶` (or rotates with
  a CSS animation), and an inline `<tr>` shows a single skeleton row
  ("Loading accounts…") in the expansion slot. Single fetch per
  company per panel-open lifecycle.
- **Loaded.** Account rows render. Chevron rotates to `▼`. State is
  held in component-local cache (`Map<company, accounts[]>`); a second
  expand of the same company is instant.
- **Collapsed-after-load.** Account `<tr>` is unmounted from the DOM
  but the cached row data is retained for re-expansion. Re-expand
  re-mounts from cache without a network call.
- **Closed panel.** All caches discarded; next open of the panel
  starts fresh.

### 4.3. Sort order and tie-break

Per-account rows sort by `abs(balance) DESC, account_name ASC`. This
matches the existing by-company sort (`account_drill_v1.py:137`) and
puts the biggest contributors at the top. Ties (same absolute
balance) are broken by `account_name` ascending so the order is stable
across requests.

### 4.4. Empty / error / truncation states

- **Empty (zero matching accounts in this company).** Should not
  happen at runtime — a company appears in the by-company list iff at
  least one matched leaf in that company has non-zero balance over
  the trend window. Defensive copy: `"No accounts in this company
  match this card's predicate."` (single italic line in the
  expansion slot).
- **Error (fetch fails).** The expansion slot renders the existing
  error tile via `dgvClassifyError` + `dgvRenderErrorTile`. Retry
  button calls the same fetch with the same `(scope, company)`
  arguments. Tile is scoped to the expansion `<td colspan>`, not the
  whole panel.
- **Truncation (>200 accounts in one company).** Server enforces
  `LIMIT 200` after the ORDER BY. Response includes
  `{truncated: true, total_accounts: N}`. Last row of the expansion
  is a footer line: `"Showing top 200 of N accounts. Export CSV for
  the full list."` The CSV link reuses the existing "Export CSV"
  action in the panel header (already scoped to all per-account
  data). N=200 chosen because: (a) the largest dev seed company has
  ~700 leaf accounts total, but a typical card predicate hits a
  small fraction; (b) 200 is the panel's existing party-list cap
  (Phase 4 commit 3) for consistency; (c) the snapshot query +
  network payload at 200 rows is ~5 KB — well within an instant feel.

## 5. API design

### 5.1. New endpoint: `get_account_breakdown_for_company`

Rationale for a new endpoint vs. extending `get_account_breakdown`:
the existing endpoint's response is the wide-format trend payload for
the whole panel and is cache-friendly. Inlining per-account data into
that response would (a) double its size whether or not the user
expands, defeating the lazy-load goal, and (b) couple two refresh
cycles into one. A separate endpoint also gives the JS a clean fetch
boundary to slot the request-token pattern around.

**Signature.** Lives in `api/account_drill_v1.py`. Whitelisted,
`_require_cockpit_role()`, snapshot-only.

```python
@frappe.whitelist()
def get_account_breakdown_for_company(
    scope=None,
    accounts=None,
    scope_label=None,
    company=None,
    as_of_date=None,
):
```

Args mirror `get_account_breakdown` so the panel can re-use its
existing scope/accounts/scope_label state, plus one new required arg:

- `company` — the single company being expanded. Required. Validated
  against `_resolve_scope(None)` (user's full allowed set) — refuses
  if the company isn't in the user's allowed companies. Refusal is
  the standard `frappe.throw(PermissionError)` from `_require_*`-
  consistent style, _not_ a soft "empty result" — refusing is the
  right signal.
- `scope` + `accounts` — exactly as in `get_account_breakdown`. One
  must be provided. `scope_label` is informational (echoed in the
  response) and required iff `accounts` is provided (same rule).
- `as_of_date` — same default (today site-tz) and same `getdate()`
  normalisation.

### 5.2. Response shape

```json
{
  "company": "GH Raisoni University Amravati",
  "scope_label": "Liquid Cash",
  "as_of_date": "2026-05-12",
  "company_total": -450000000.0,
  "accounts": [
    {
      "account": "ICICI Bank A/c 624205012830 - GHRUA",
      "account_name": "ICICI Bank A/c 624205012830",
      "balance": -15900000.0,
      "currency": "INR"
    },
    {
      "account": "WUCBL A/c 21/1579 - GHRUA",
      "account_name": "WUCBL A/c 21/1579",
      "balance": -600000.0,
      "currency": "INR"
    }
  ],
  "total_accounts": 47,
  "truncated": false
}
```

- `account` is the full Frappe Account name (the foreign key). Used
  by the GL drill URL as a stripped account name via `account_name`.
- `account_name` is `tabAccount.account_name` — the stripped form
  without the company suffix. This is what GL drill's existing
  `account_names` filter matches on.
- `balance` is sign-flipped per `FLIP_ROOT_TYPES` already (so the
  panel renders it directly, no client-side flip). Mirrors what
  `get_account_breakdown` does for `by_company[].value`.
- `company_total` echoes the company-level aggregate so the panel
  can sanity-check (sum of `accounts[].balance` rounded should equal
  `company_total` to within ₹1; flag if not).
- `total_accounts` is the count BEFORE truncation; `truncated` is
  `total_accounts > 200`.
- `accounts` is sorted by `abs(balance) DESC, account_name ASC`.

### 5.3. SQL shape

Same shape as the CSV export's per-account query
(`export_account_breakdown_csv:390-413`), narrowed to one company:

```sql
SELECT
  s.account,
  a.account_name,
  COALESCE(SUM(
    CASE WHEN a.root_type IN ({flip_placeholders})
         THEN -s.balance
         ELSE s.balance
    END
  ), 0) AS balance,
  COALESCE(MAX(a.account_currency), '') AS currency
FROM `tabDGV TB Snapshot Row` s
JOIN `tabAccount` a ON a.name = s.account
WHERE s.snapshot_date = %(snap_date)s
  AND s.company = %(company)s
  AND s.account IN ({leaf_placeholders})
GROUP BY s.account, a.account_name
HAVING ABS(balance) >= 0.01
ORDER BY ABS(balance) DESC, a.account_name ASC
LIMIT 201
```

- One extra row fetched (`LIMIT 201`) to detect truncation without
  a second `COUNT(*)` query. If the result is 201 rows, truncate to
  200 and run a separate `SELECT COUNT(DISTINCT account)` for
  `total_accounts`. If ≤ 200, `total_accounts = len(rows)` and
  `truncated = false`.
- `GROUP BY s.account, a.account_name` — same shape as the CSV
  export, defensive against any future denormalisation that creates
  >1 row per `(date, company, account)`.
- `HAVING ABS(balance) >= 0.01` — drops sub-paise residuals, same
  as the CSV export. Cards predicate may still match accounts with
  rounded-zero balances; hide them.

### 5.4. Index utilisation

The query filters by `s.snapshot_date` + `s.company` + leaf-account
list. The composite index `dgv_pivot` on `(snapshot_date, company,
account)` is a covering match: filter on the first two columns,
range-scan the third via `IN`. No new index. `EXPLAIN` should show
`ref` access on `dgv_pivot` with `Using index condition; Using where;
Using temporary; Using filesort` (the filesort is the ORDER BY on the
small result set — negligible cost).

### 5.5. Malformed scope flag

If `scope` is `None` and `accounts` is `None`, or `company` is
missing / empty, the endpoint sets
`frappe.local.response["malformed_scope"] = True` and raises
`frappe.DoesNotExistError`. Matches the pattern used by `cards_v1`
(Phase 4 commit 6 HALT 6.3) so the panel's existing error
classifier routes the failure through the invalid-scope tile.

## 6. GL drill scoping for the per-account click

The GL drill endpoint already accepts `account_names` as a HALT 2.5
filter. To drill into one account, the panel passes
`account_names=<account_name>` (single element). No backend change.

The only extension is in `buildGlDrillUrl()`
(`public/js/account_drill.js:1336`): accept an optional `account_name`
arg and write it to the URL as
`&account_names=<encodeURIComponent(name)>`. The GL drill page already
reads `account_names` from the URL on load
(`page/gl_drill/gl_drill.js`) and applies it as a chip filter — so the
filter appears active when the page opens, and the GL ledger
immediately scopes to that one account.

### 6.1. Breadcrumb

The GL drill page builds its breadcrumb from `scope_label` returned by
`get_gl_entries()`. With `account_names` set, the existing chip-based
filter already labels "Account: ICICI Bank A/c 624205012830" above
the ledger. We don't modify the breadcrumb string; the chip serves
the same purpose (identifies the account being viewed) and keeps the
GL drill page's surface area unchanged.

This deliberately does NOT chain a multi-segment breadcrumb like
`Cockpit / Account drill / <Company> / <Account>`. Two reasons:

1. The GL drill page is reachable from many surfaces (party list,
   focus mode, pivot, drill panel); a four-segment breadcrumb would
   either lie about the navigation history or require per-source
   branching that bloats the page's controller.
2. The existing two-segment breadcrumb (`Cockpit ‹ GL entries`) plus
   the chip filter set already conveys "you're at the GL ledger for
   account X in company Y" — clearly enough for the owner persona.

If user feedback after dev verification says the chip is too subtle,
we revisit in a follow-up; not in this task.

### 6.2. URL bookmarking

The new URL is fully bookmarkable: `as_of=YYYY-MM-DD`,
`scope=card_id`, `companies=<single co>`, `account_names=<one name>`.
Loading the URL fresh produces the same view. No new scope kind, no
schema change to the deep-link contract.

## 7. State management

- **Expanded-companies state:** component-local `Set<company_name>`
  inside the account drill panel's render context. Reset whenever the
  panel is closed (`panelFetchToken` increments).
- **Per-account row cache:** component-local `Map<company_name,
  accountRows[]>`. Populated on first fetch; lookups are
  synchronous. Cache lifetime = panel-open lifetime. Cleared on
  panel close.
- **URL encoding:** No. Expanded-companies state is not URL-encoded.
  This is a deliberate simplification — users open the panel from a
  card or pivot click, drill once, and either close or navigate to
  GL drill. Persisting expansion state across reloads would require
  encoding a variable-length set into the URL, and the value would
  not be high enough to justify the surface area. If users complain,
  we add it in a follow-up.
- **Re-opening the panel for the same card:** fresh state. No
  expansions carry over. Cognitive simplicity beats "remembered"
  state in this UI; the user's hands re-expand the relevant company
  in one click.
- **Multi-company expand:** allowed without limit. Each expand only
  fetches once per panel-open (see cache above). Visually, no cap
  on how many companies are simultaneously expanded — the natural
  scroll of the panel handles it.

## 8. Edge cases

- **Card with zero contributing accounts in any company.** The
  by-company list would already be empty (filter at
  `account_drill_v1.py:128`), so no chevrons render. Not a
  separate state.
- **Single-company panel (drill from focus-mode tile).** Still
  beneficial — the user can see which accounts in their focused
  company are contributing to the card. Same UX, single chevron to
  expand.
- **Account name truncation.** Display in the expansion is
  `account_name`, truncated to ~50 chars with ellipsis and a `title`
  attribute carrying the full name (matches the party-list
  truncation pattern in the panel's existing code). Long LLP /
  property-transfer style names render gracefully.
- **Mobile / narrow viewport.** The expansion table inherits the
  parent table's responsive collapse rules (CSS already in
  `cockpit.css`). Per-account row height is the same as the company
  row height. Tested at 320 px width on dev during step 4.
- **Drill panel re-render mid-expansion.** If a re-render fires
  while a per-account fetch is in flight (e.g. user changes
  `as_of_date`), the in-flight request is dropped via the
  panel-level `panelFetchToken`. Stale responses don't write to the
  cache.
- **User clicks "View GL entries" (panel action bar) while an
  expansion is open.** Unchanged behaviour — opens GL drill
  scoped to all matched accounts in all in-scope companies. The
  expansion is a parallel navigation path, not a replacement.
- **Account row click while truncated.** The 201st+ accounts are
  not rendered, so a click on them is impossible. The CSV export is
  the escape hatch.
- **Sort-tie breakers.** `abs(balance)` is rarely a tie at real
  numbers; tie-break by `account_name ASC` makes the order
  deterministic for tests.

## 9. Performance

- **Query latency.** Single-company snapshot read against `dgv_pivot`
  covering index. Expected: <50 ms p95 even at production scale (the
  snapshot row table is on the order of 10–50 K rows at 5M
  GL-entry scale, since one row per leaf-account-per-company-per-
  snapshot).
- **Endpoint latency target.** <100 ms p95 per company expansion.
- **Network payload.** ~200 rows × ~150 bytes per row = ~30 KB JSON
  uncompressed, ~6 KB gzipped. Within instant-feel.
- **Re-expansion latency.** 0 ms — served from component cache.
- **Server cache contention.** Each expansion hits a single index
  range scan. No global locks, no temp tables of meaningful size.
- **No new index.** `dgv_pivot` already covers the filter set.

## 10. Tests

### 10.1. Backend (`tests/test_account_drill.py` extension)

- `test_per_company_breakdown_returns_per_account_rows` —
  smoke test for `get_account_breakdown_for_company`. Asserts
  response keys, row shape, sort order, sign-flip for an Expense
  card.
- `test_per_company_breakdown_by_account_type_predicate` — predicate
  via `by_account_type` (e.g. `Receivable`) returns the expected
  accounts.
- `test_per_company_breakdown_by_name_pattern_predicate` —
  predicate via `by_root_type_and_name_pattern` returns the
  expected accounts.
- `test_per_company_breakdown_zero_match` — predicate that
  matches no leaves returns `accounts: []`, `total_accounts: 0`,
  `truncated: false`.
- `test_per_company_breakdown_truncation` — synthetic fixture
  with 250 matching accounts; asserts `truncated: true`,
  `total_accounts: 250`, `len(accounts) == 200`.
- `test_per_company_breakdown_disallowed_company` — user lacks
  permission on the requested company; endpoint raises
  `PermissionError`.
- `test_per_company_breakdown_malformed_scope` — empty scope and
  empty accounts: raises `DoesNotExistError` with
  `malformed_scope: true`.
- `test_per_company_breakdown_company_total_sanity` — sum of
  per-account balances equals `company_total` to ₹1 precision.

### 10.2. Frontend (extension to existing panel test surface)

- Chevron toggle on company row expands / collapses inline.
- First expand triggers a fetch; second expand of same company is
  a no-op fetch (served from cache).
- Multi-company simultaneous expand is allowed; each company
  fetches independently.
- Stale fetch dropping via `panelFetchToken` — a panel close while
  a per-account fetch is in flight does not render rows on reopen.
- Account row click builds the right URL: `account_names=<name>`
  appended; `companies=<company>`; existing `scope` + `as_of`.
- Error envelope: server returns 500 → expansion slot renders
  `dgvRenderErrorTile` with retry button.

### 10.3. Manual smoke scenarios on dev

After deploying to `erp.jewonline.in`:

1. Open card `Sundry creditors` → expand the largest-contributor
   company → confirm 10+ account rows visible, sorted by abs balance
   desc. Click the top account → GL drill opens with chip
   `Account: <name>` active.
2. Expand a different company in the same panel without collapsing
   the first → confirm both expansions visible simultaneously.
3. Re-expand a previously-expanded-then-collapsed company →
   instant render, no spinner (cache hit).
4. Close panel, reopen for the same card → expansions are reset.
5. Open card `Financial Exp — Bank` (CACSPU-only on dev) → expand
   CACSPU → both `Financial Exp To Bank - CACSPU` accounts visible
   (the snapshot has the known 2× dup; we accept this on dev).
6. Open a card and try expanding while `as_of_date` is mid-change
   in another tab — no race-induced wrong rows.

## 11. Rollout

**Branch:** `feat/per-account-drill-expand` (already created).

**Commits (7 planned):**

1. `Spec: per-account drill expansion` — `specs/per-account-drill-
   expand.md` only.
2. `Add get_account_breakdown_for_company snapshot endpoint` —
   `api/account_drill_v1.py` + `__init__.py` if a re-export is
   needed.
3. `Tests for get_account_breakdown_for_company` —
   `tests/test_account_drill.py` (extension; new fixtures only if
   needed for the truncation test).
4. `Add chevron + expansion slot rendering to account drill panel` —
   `public/js/account_drill.js` (`renderCompanyBreakdownTable` +
   helpers). No fetch wiring yet.
5. `Wire per-company lazy fetch + caching` — `public/js/
   account_drill.js` (fetch call, `panelFetchToken` integration,
   error tile route).
6. `Wire per-account row click → GL drill with account_names` —
   `public/js/account_drill.js` (`buildGlDrillUrl` extension).
7. `Style chevron + expansion slot` — `public/css/cockpit.css`
   (chevron rotation, expansion `<tr>` indent, account row styling).

If commits 4–6 turn out to be tightly intertwined, they may collapse
to a single commit; PHASE_LOG will reflect what actually shipped.
PR description gets the full per-commit summary either way.

**Dev deployment sequence:**

```bash
# Local: scp the changed files
scp dux_groupview/dux_groupview/api/account_drill_v1.py \
    frappe@187.127.132.58:~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/api/
scp dux_groupview/public/js/account_drill.js \
    frappe@187.127.132.58:~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/public/js/
scp dux_groupview/public/css/cockpit.css \
    frappe@187.127.132.58:~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/public/css/

# On dev: clear-cache + bench build + SIGHUP gunicorn
ssh frappe@187.127.132.58
cd ~/frappe-bench
bench --site erp.jewonline.in clear-cache
bench build --app dux_groupview
kill -HUP $(pgrep -of "gunicorn -b")
```

`bench build` is needed because JS / CSS are bundled. `bench restart`
is NOT used (gotcha #6).

**Verification on dev:** all 6 manual scenarios in §10.3 pass.
Backend test suite (`bench run-tests --module
dux_groupview.dux_groupview.tests.test_account_drill`) green.

**PR:** opens against `main`, NOT auto-merged. User reviews and
merges; Frappe Cloud picks up `main` on its next deploy cycle.

## 12. Open questions

- **Sort order default.** Current proposal: `abs(balance) DESC,
  account_name ASC`. An alternative useful default for a creditor /
  debtor card is `balance ASC` (largest negative first — i.e. largest
  amount owed). I'm going with `abs(balance) DESC` for v1 because
  it works correctly for every card polarity (cash up, finance cost
  up, payable up — `bad_up`/`good_up` indifferent). Open to
  override during HALT 1 if you prefer a per-polarity-aware default.
- **Truncation cap.** 200 is borrowed from the party-list cap.
  Confirming this is the right number, or whether per-account
  expansion (which is usually a smaller set than parties) warrants
  a different cap — e.g. 100 (faster, less scrolling) or 500 (rare
  edge cases not truncated). Default to 200 unless overridden.
- **Truncation footer link.** Should the "Showing top 200 of N…"
  line have a click-through to "view all" via the CSV export, or
  should it just be informational text? Default to informational
  text (one less affordance to test), with the existing panel-level
  "Export CSV" action being the escape hatch.
- **Re-using `account_drill_v1.py` vs new module.** Adding
  `get_account_breakdown_for_company` to the existing file keeps
  related code colocated and shares the `_resolve_scope_to_leaves`
  helper. Alternative: separate file `api/account_drill_v2.py` to
  keep `v1` immutable. Going with extension of `v1` because the
  module name reflects the surface area (account drill), not a
  versioning contract, and the existing tests already import from
  `account_drill_v1`. If the user prefers `_v2` for forward-
  compat hygiene, easy override at HALT 1.
- **`scope_multi_company` flag.** GL drill enforces single-company
  scoping at the API. We pass `companies=[<company>]` (length 1)
  from the per-account click, so this flag should never fire. The
  panel's error envelope already handles it if it does; no new
  code path needed. Flagging for awareness.
- **Index `EXPLAIN` measurement.** I'm confident `dgv_pivot` covers
  the query, but I'll run `EXPLAIN` against the literal SQL on
  dev as part of HALT 2 verification (per gotcha #5 — `EXPLAIN`
  must run against the literal query, not a simplification). If
  the optimiser picks a different path, surface and discuss.
