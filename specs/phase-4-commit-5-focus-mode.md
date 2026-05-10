# Phase 4 commit 5 — Focus Mode (HALT 5.x)

**Status:** v0.1 draft — awaiting HALT 5.1 sign-off
**Branch:** `phase-4-drills` (continuing; no new branch)
**Estimated duration:** 2 working days
**Depends on:**
- Phase 4 commits 1–4 merged in `phase-4-drills` (HEAD `8400084`).
- Master spec `specs/phase-4-drills.md` §4.4 (the original focus-mode
  sketch). This spec **expands** §4.4 along three dimensions and
  reconciles the deltas inline (see §15).

This spec is the contract for HALT 5.x implementation. After HALT 5.4
ships, commit 6 (spotlight card click polish, per master-spec §9 step 7)
becomes the next workstream.

---

## 1. Goal

Give the user a **single-scope vertical view** of the cockpit's data:
the cockpit pivot is wide-by-design (companies as columns, accounts as
rows) which is the right shape for cross-company comparison but the
wrong shape for "show me everything happening *inside* this one
company at full account depth." Focus mode is the second view.

Two flavors in v1:

1. **Company focus** — one company, full COA depth, summary tiles for
   the five accounting top-lines.
2. **Trust focus** — all companies in one trust, aggregated to a single
   account-hierarchy view (roll-ups across the trust's companies),
   same five summary tiles, plus a small per-company strip at the
   top of the body so the user can see how the trust's companies
   contribute to each account.

Both flavors are read-only reflows of existing snapshot data; no new
GL Entry reads, no new doctypes, no schema changes.

## 2. Non-goals (for HALT 5.x)

Deferred to Phase 5 unless explicitly listed. Calling these out so the
implementation diff stays focused.

- **Multi-company / multi-trust focus.** Single-scope only. "Show me
  CACS Pune + GHRCE side-by-side at full depth" is the cockpit pivot's
  job; no need to duplicate.
- **Custom account hierarchies / Schedule III mapping.** Focus mode
  uses the COA tree the snapshot already exposes; alternate
  hierarchies are Phase 5+.
- **Editable summary tiles.** Five fixed tiles for v1 (Assets,
  Liabilities, Income, Expenses, Net surplus). The configurable
  per-card editor lives in Phase 5 alongside the spotlight-card
  editor.
- **Drill-down from a tile.** Tiles are display-only in v1; clicking
  one is a no-op. Phase 5 may wire tile-click → account drill panel
  scoped to the tile's root subtree.
- **Account row inline edit / annotation.** Read-only.
- **PDF export.** CSV only in v1, mirroring commits 2–4.
- **"Compare to last period"** column or delta arrows on the rows.
  Single-snapshot view in v1; comparison is Phase 5.
- **Sticky URL state across cockpit reloads.** Focus is captured in
  the URL when entered (so links are shareable) but exiting focus
  drops the param; the cockpit doesn't remember a user's last
  focused company across page reloads. (Sticky scope is the
  cockpit's job, not focus mode's.)

## 3. Triggers — entering focus mode

Two entry points, both via the cockpit pivot. Per Q1 resolution
(§4.1), each is an **explicit-button click**, not a single-click on
the header text.

### 3.1 Company focus

The pivot's company column header is currently a static `<th>` with
the company name. v1 adds a small inline **"Focus →"** button to the
right of each leaf-company header (rendered on hover at desktop;
always visible at touch viewports).

- **Click:** enter company focus for that company. URL gains
  `focus=<company_name>` (URL-encoded).
- **Affordance:** the button's `title` attribute reads "View this
  company at full depth." First-time users discover; repeat users
  click without thinking.

### 3.2 Trust focus

Trust group headers (the wider header spanning the companies in a
trust, rendered by Phase 3.5 trust-grouping) gain the same inline
**"Focus →"** button.

- **Click:** enter trust focus for that trust. URL gains
  `focus_trust=<trust_name>` (URL-encoded).
- The existing trust-selector behavior (commit 3 wiring) is **not
  touched** by this button. Clicking the trust group header *text*
  still narrows the trust selector (current behavior); clicking the
  Focus button enters focus mode (new behavior). Two distinct
  affordances, two distinct interactions.

### 3.3 Direct-URL entry

Pasting a focus URL into the address bar is a valid entry point.
Cockpit on load checks for `focus=` or `focus_trust=` in the URL; if
present, it boots straight into focus mode after the initial pivot
fetch completes (so the cockpit's normal scope/as-of state is intact
underneath, ready to revert to on exit).

## 4. Resolved open questions

The five Q's flagged in the brief (and a sixth that emerged during
drafting), with recommended resolutions. Each has a one-line
rationale; the deeper "why" is in the cited section.

### 4.1 Q1 — Trigger mechanism: single-click vs explicit button?

**Resolution: explicit "Focus →" button.** §3 details.

- *Why not single-click on the header text*: the header is currently
  used for sort + (planned) trust-narrow; loading focus mode on a
  generic single-click would surprise users who expected sort. We'd
  also lose the discoverability win: a header that "looks the same
  but does a different thing" is invisible UX.
- *Why button*: explicit, discoverable on hover (`Focus →`),
  preserves the header's sort/narrow behavior, doesn't fight muscle
  memory of users coming from the stock TB report.
- *Cost*: ~6px of header-row width per company column. Acceptable;
  the trust header has plenty of slack.

### 4.2 Q2 — Trust focus visual model: companies-as-columns or aggregated?

**Resolution: aggregated account hierarchy with a per-company strip
at the top.** Single-column body view; the strip lets the user see
which companies contribute to a trust without horizontal scroll.

- *Why not companies-as-columns within a trust*: that's the cockpit
  pivot, narrowed. Already available via the trust selector +
  cockpit. Reproducing it inside focus mode would be a duplicate
  product.
- *Why aggregated body*: the "single trust at full depth" question is
  about the trust as a financial unit. Showing one row per account
  with the cross-trust sum is the answer to that question.
- *Why per-company strip*: gives the next-question answer ("which
  company drives this trust's net profit?") inline, without forcing
  a click-and-back. The strip is small (5 tiles row + one
  collapsible-by-default per-company-tile row); collapsed by default
  on mobile.

### 4.3 Q3 — Account depth: full or honor cockpit collapse-state?

**Resolution: always full depth in focus mode.** Depth control is
hidden in focus mode's toolbar (greyed/removed; user can't change it).

- *Why full*: focus mode is the "give me everything" view. Honoring a
  cockpit collapse default would defeat the purpose — a user with
  cockpit at Depth=2 entering focus would still see only top-level
  rollups.
- *Why hide the depth control*: a depth selector that is always
  Depth=All is just visual noise. Removed in focus toolbar; restored
  on exit.
- *State preservation*: the user's pre-focus depth is preserved (per
  master-spec §4.4 "depth save/restore"). Exiting focus restores the
  cockpit to its prior depth — same as the §4.4 contract for
  company focus, extended to trust focus by symmetry.

### 4.4 Q4 — Summary tiles: 5 fixed or configurable?

**Resolution: 5 fixed tiles for v1.** Configurability deferred to
Phase 5.

The five tiles (in left-to-right order):

| # | Tile name      | Aggregation rule |
|---|----------------|------------------|
| 1 | Total Assets   | Sum balance of all accounts where root_type=Asset |
| 2 | Total Liabilities | Sum balance of all accounts where root_type=Liability (sign-flipped) |
| 3 | Total Income   | Sum balance of all accounts where root_type=Income (sign-flipped) |
| 4 | Total Expenses | Sum balance of all accounts where root_type=Expense |
| 5 | Net Surplus    | Total Income − Total Expenses (computed from tiles 3 and 4 above; not from a separate aggregation) |

Sign convention: the same `FLIP_ROOT_TYPES = {'Liability', 'Equity',
'Income'}` used by the pivot (per CLAUDE.md hard rule 7's "spotlight
aggregates by predicate, never by hierarchy" and the existing
spotlight `cards_v1.py` aggregator). Tiles roll up *leaf* rows from
the snapshot, not group-account rollups, to avoid double-counting.

- *Why 5*: matches the standard P&L + Balance-Sheet quick-glance
  decomposition that owners expect from a cockpit.
- *Why not configurable in v1*: would require a tile-editor doctype
  + UI; same scope as the spotlight-card editor planned for Phase 5.
  Bundling both into Phase 5 keeps the editor's doctype consistent.

### 4.5 Q5 — Performance: same partition concerns as commit 4 GL drill?

**Resolution: pin to snapshot reads only.** No `tabGL Entry` reads
in the focus-mode endpoint. This sidesteps the GL-drill partition
discussion entirely.

- *Why snapshot is enough*: focus mode displays per-account
  balances at a single as-of date. The snapshot has exactly that.
  The pivot already serves the same data shape via
  `get_pivot_data`; focus mode is a reflow, not a deeper read.
- *EXPLAIN-check at HALT 5.3*: confirm focus-mode SQL doesn't
  introduce `Using temporary` or `Using filesort` against the
  `dgv_pivot (snapshot_date, company, account)` covering index. The
  baseline is the existing pivot query plan from Phase 3.5.
- *Performance target* (§9): match the master-spec §6 "Focus mode
  entry < 400 ms p95" line.

### 4.6 Q6 (emerged during drafting) — Drill panel from focus mode rows?

**Resolution: yes, click any account row → existing drill panel
opens scoped to (account, focused company-or-trust-companies).**

- *Why*: the drill panel (commits 2–3) is the right next step from
  "this account's balance is interesting." Skipping it would force
  users to exit focus mode + click in the cockpit → twice the steps.
- *Implementation cost*: zero new server work. The panel takes
  `(scope, as_of_date, companies)` already; focus mode wires the
  click to call the panel with `companies = [<focused company>]`
  for company focus, or `companies = <trust's companies>` for trust
  focus.
- *Subtle*: the panel's "View GL entries" button (HALT 1 of commit
  4) builds a URL with the same `companies` list. So GL drill
  opened from focus mode is correctly scoped to the focused
  company/trust without any extra wiring. Verified at HALT 5.3.

## 5. URL contract

Focus mode adds two URL params to the cockpit. They are mutually
exclusive (a URL with both is an error; the receiver picks
`focus_trust` and ignores `focus`, with a console warning).

```
/app/groupview
  ?as_of=<iso>
  &trust=<csv>            # existing cockpit param (trust selector)
  &focus=<company>        # NEW — company focus
  &focus_trust=<trust>    # NEW — trust focus (mut. exclusive with focus)
```

**Cockpit state preserved.** Entering focus does not modify any
existing cockpit state in the URL (`as_of`, `trust`, `depth`,
`format`). Exiting focus drops only `focus` / `focus_trust`; everything
else is untouched. So a user entering focus from a 3-trust scope
returns to the same 3-trust scope on exit.

**Comma-encoding.** Both new params are single-valued (one company,
one trust); commas in the value are not supported. (No real
ERPNext company or trust name contains a comma.)

**`pushState` semantics.** Entering focus calls `pushState` (so
back-button exits focus). Exiting via the × or ESC key does NOT push
a new state — it does `history.back()` if the immediately preceding
state was the cockpit-pre-focus, otherwise `replaceState` to drop the
focus param. This keeps the back-button intuitive.

**Deep-link entry.** Pasting a focus URL with no prior cockpit history
boots focus mode after the initial pivot fetch (§3.3). Exit in this
case `replaceState`s to the same URL without focus params.

## 6. Server-side endpoint

### 6.1 New endpoint `cockpit.get_focused_view`

Lives in `dux_groupview/dux_groupview/api/cockpit.py` (alongside
`get_pivot_data`, not a new module — focus mode is a cockpit-shape
concern, not a separate drill).

```python
@frappe.whitelist()
def get_focused_view(
    as_of_date,
    company=None,        # exclusive with trust
    trust=None,          # exclusive with company
):
    """Return the focused-view payload for one company or one trust.

    Reads from `DGV TB Snapshot Row` only. No tabGL Entry access.

    Output:
      {
        "scope_type": "company" | "trust",
        "scope_label": "GHRCE" | "Raisoni Education Trust",
        "as_of_date": "2026-05-08",
        "companies": ["GHRCE"] | ["GHRCE", "GHRCEMN", ...],
        "summary_tiles": {
          "total_assets":      <int rupees>,
          "total_liabilities": <int rupees>,
          "total_income":      <int rupees>,
          "total_expenses":    <int rupees>,
          "net_surplus":       <int rupees>,
        },
        "accounts": [          # sorted depth-first by COA
          {
            "name":       "Cash",
            "parent":     "Cash & Bank",
            "depth":      3,
            "root_type":  "Asset",
            "is_group":   false,
            "balance":    <int rupees, sign-flipped per FLIP_ROOT_TYPES>,
            "by_company": {  # only present when scope_type=="trust"
              "GHRCE":   <int>,
              "GHRCEMN": <int>,
            },
          },
          ...
        ],
      }
    """
```

### 6.2 Implementation notes

- **Scope resolution.** Single allowed `company` (and confirmed in
  the user's `_allowed_companies()`) → `companies = [company]`. Single
  allowed `trust` (looked up via the existing trust→companies map
  used by `get_scope_options`) → `companies = <trust's allowed
  companies, intersected with permission>`.
- **Snapshot read.** One `SELECT` against `tabDGV TB Snapshot Row`
  filtered to `(snapshot_date, company IN companies)`. Same shape as
  `get_pivot_data`'s underlying query; copy/extract the helper if it
  isn't already extracted.
- **Group totals**. Per CLAUDE.md hard rule 6, group/parent rows are
  computed at request time by bubbling leaf balances up the
  ancestor chain. Reuse the existing helper from `pivot.py`; do not
  add group rows to the snapshot.
- **Summary-tile aggregation**. Run on the **leaf** rows of the
  result, not on the bubbled-up totals (per CLAUDE.md hard rule 7).
  Single Python pass; cost is negligible (~700 leaves).
- **Sign convention**. `FLIP_ROOT_TYPES` applied during bubble-up
  AND during tile aggregation. Tile values are user-facing
  positives (Liabilities reads as a positive number, not negative).
- **Per-company strip (trust focus only)**. The `by_company` dict on
  each account row carries the per-company contribution to that
  account's aggregate balance. Cost: in-memory groupby on the
  already-fetched snapshot rows. No extra SQL.
- **`is_group` flag**. Present on each row so the UI can render
  group rows with bolder typography. Sourced from `tabAccount.is_group`
  via JOIN, same as Phase 3.5's pivot.
- **Permission scoping.** `_allowed_companies()` intersected before
  query. If the user's allowed companies don't include the focused
  company / any of the focused trust's companies, the endpoint
  returns `frappe.PermissionError`.

### 6.3 New endpoint `cockpit.export_focused_view_csv`

Mirrors the `export_gl_entries_csv` pattern from commit 4. Same WHERE
clause as `get_focused_view`, streamed as CSV via
`frappe.local.response.filecontent`.

**Columns** (per the brief, with Q6-emerged refinements):

| Column         | Source                          | Notes |
|----------------|---------------------------------|-------|
| Account        | `account_name`                  | Stripped of company suffix (e.g., "Creditors", not "Creditors - SGREF") |
| Root type      | `tabAccount.root_type`          | "Asset" / "Liability" / "Income" / "Expense" / "Equity" |
| Depth          | int (1..N)                      | COA depth, useful for spreadsheet outline grouping |
| Balance        | raw decimal (no Indian grouping)| Sign-flipped per FLIP_ROOT_TYPES, matching on-screen value |

(See Q-emerged-3 in §15 for why "Currency" was dropped from the
brief's initial column list.)

**Filename**: `focused_view_<company-or-trust-slug>_<as_of>_<HHMMSS>.csv`

For trust focus, the per-company strip is NOT exported in v1 (would
explode the column count and make the CSV awkward in spreadsheets).
A future Phase 5 follow-up could add a "wide" CSV that includes
per-company columns; v1 ships with the aggregated single-column
shape only.

**50K-row cap**: not relevant here (max ~700 accounts × 1 trust =
700 rows). Cap inherited from commit 4 helper for safety, but won't
trip in practice.

## 7. UI design

### 7.1 Focus-mode page chrome

Focus mode replaces the cockpit pivot table area with the focused
view. The cockpit's top bar (logo, scope selector, as-of-date picker,
format toggle) is preserved unchanged. The cockpit's depth selector
is **hidden** during focus (per Q3, §4.3) and restored on exit.

A new **focus-mode banner** spans the toolbar row immediately above
the focused content:

```
┌────────────────────────────────────────────────────────────────────┐
│ ← Back to cockpit                Focusing: GHRCE                 × │
└────────────────────────────────────────────────────────────────────┘
```

- **Left**: chevron + "Back to cockpit" link.
- **Center**: the focus label, format `Focusing: <company name>` for
  company focus or `Focusing: <trust name> (5 companies)` for trust
  focus. The companies-count parenthetical helps users know what
  scope they're in without having to click around.
- **Right**: × close button (mirrors panel/modal close conventions).

Both "← Back" and "×" exit focus mode (clear URL params, restore
prior depth, re-render cockpit).

### 7.2 Summary tiles

Five tiles in a single row at desktop (≥800px viewport), stacked at
mobile.

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ ASSETS      │  │ LIABILITIES │  │ INCOME      │  │ EXPENSES    │  │ NET SURPLUS │
│ ₹14.20 Cr   │  │ ₹8.50 Cr    │  │ ₹6.20 Cr    │  │ ₹4.10 Cr    │  │ ₹2.10 Cr    │
│             │  │             │  │             │  │             │  │ green/red   │
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
```

- **Format**: respects the cockpit's current format toggle (Crore /
  Lakh / Full). Reuses the `formatIndian` helper.
- **Net surplus tile**: positive → green; negative → red; zero →
  neutral. Same coloring as the spotlight delta arrows.
- **No click affordance** in v1 (per §2 non-goals).

### 7.3 Account rows (body)

Single-column vertical view. Each row:

```
[indent per depth] [▸/▾ if is_group] Account name        ₹ X.XX Cr
                                     root_type chip
```

- **Depth indentation**: 16px per depth level.
- **Group rows** (`is_group=true`): bolder typography, expand/collapse
  chevron. **Expanded by default** (per Q3, §4.3 — focus mode is
  full-depth view). User can still collapse a group manually after
  entry; collapse state is local to the focus-mode session and is
  not URL-persisted in v1.
- **Leaf rows**: no chevron; the row is clickable → drill panel
  opens (per Q6, §4.6).
- **Sign convention**: balances rendered with FLIP_ROOT_TYPES
  applied (so Liabilities, Income, Equity show as positive numbers).
  Same as the cockpit pivot.
- **Color coding**: matches the cockpit pivot — red for adverse
  movements (negative balance on a typically-positive account, or
  vice versa), neutral for in-line.

### 7.4 Trust focus — per-company strip

For trust focus only, after the summary tiles row, before the
accounts body, render a thin horizontal strip:

```
┌──────────┬──────────┬──────────┬──────────┬──────────┐
│ GHRCE    │ GHRCEMN  │ GHRCAS   │ GHRBSON  │ GHRJALG  │
│ ₹4.20 Cr │ ₹3.50 Cr │ ₹2.10 Cr │ ₹1.90 Cr │ ₹0.40 Cr │
└──────────┴──────────┴──────────┴──────────┴──────────┘
```

- **Values**: each company's contribution to the trust's Net Surplus
  (the right-most summary tile). Same sign convention.
- **Width**: each cell sized to fit content; horizontal scroll if the
  trust has more companies than fit.
- **Click affordance**: clicking a per-company cell **switches focus
  mode** from trust focus to company focus for that company. The URL
  swaps `focus_trust=<X>` for `focus=<company>`. Back button returns
  to trust focus.
- **Mobile (≤800px)**: collapsed-by-default disclosure ("▸ 5
  companies in trust"), expands to a vertical list.

### 7.5 Toolbar additions

A single new button in the focus-mode banner row, right of "× close":

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ← Back to cockpit       Focusing: GHRCE             [Export CSV]          × │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Export CSV**: triggers `export_focused_view_csv` with the current
  scope. Same UX as commit 4 CSV exports — browser download.

### 7.6 Empty / edge states

- **Trust has zero allowed companies for the user**: focus mode
  refuses to enter; cockpit toast: "You don't have permission for
  any companies in this trust."
- **Snapshot is empty for the as-of date**: focus mode renders the
  banner + zero tiles + empty body with "No data for this date."
  message inline. Does not error.
- **`focus=` and `focus_trust=` both present in URL**: console
  warning, prefer `focus_trust`, ignore `focus`.
- **Unknown company / trust name in URL**: focus mode refuses to
  enter; cockpit shows a toast: "Company / trust not found." URL
  param is dropped via `replaceState`.

## 8. Interaction with other features

### 8.1 Drill panel (commits 2–3)

Already covered in §4.6. Clicking any leaf account row in the body
opens the existing drill panel scoped to (that account, focused
companies list). No changes to the panel itself.

### 8.2 GL drill page (commit 4)

Inherits scope through the drill panel: panel's "View GL entries"
button builds a `gl-drill?scope=...&companies=...` URL with the
focused-companies list. No changes needed in `gl_drill_v1.py` or the
gl-drill page.

### 8.3 Party drill / party-list page (commits 2 + 4)

Same pattern. Panel's "View all parties" button uses the focused-
companies list. No changes needed in `party_drill_v1.py` or the
party-list page.

### 8.4 Spotlight cards (Phase 2 + 4 commit 6)

Out of scope for this commit. Spotlight cards are positioned above
the cockpit pivot, not inside focus mode. Focus mode hides the
spotlight strip during entry (it's a cockpit-overview feature, not a
focused-scope feature) and restores it on exit. Phase 4 commit 6
will polish card click behavior; that interaction is unaffected by
focus mode.

### 8.5 Trust selector (Phase 3.5)

The cockpit's trust selector behaves differently depending on the
focus flavor:

- **Trust focus.** Selector is **frozen** (visually disabled, with a
  title attribute "Exit focus mode to change trust scope"). Restored
  on exit. Locking matches the focused scope; allowing a change
  would create an immediate incoherence between the selector value
  and the locked-in trust focus.
- **Company focus.** Selector is **live**, but any change to the
  trust selector **auto-exits focus mode** and returns the user to
  the cockpit pivot at the new trust scope. Reasoning: the trust
  selector is cockpit-level state; if a user changes it while
  focused on a company that may not even belong to the newly-
  selected trust, the on-screen state becomes incoherent (focus
  banner says "Focusing: GHRCE" while the underlying scope no longer
  contains GHRCE). Auto-exit keeps the interaction model clean —
  one explicit cockpit-state change yields one predictable outcome
  (back to pivot, new scope applied).

  Implementation: the trust-selector change handler checks for an
  active company-focus URL param; if present, it strips `focus=`
  from the URL via `replaceState`, restores the pre-focus depth,
  and re-renders the cockpit with the new trust scope. No toast
  needed — the focus banner disappearing is a clear enough signal.

  Trust focus does not need this branch (selector is frozen there).

### 8.6 Format toggle (Phase 3.5)

The format toggle (Crore/Lakh/Full) applies live to focus mode —
tiles + body + per-company strip all re-render on toggle. No
URL change.

## 9. Performance

### 9.1 Targets

| Operation                         | Target (p95) | Approach |
|-----------------------------------|--------------|----------|
| Focus mode entry (company)        | < 400 ms     | One snapshot read + Python aggregation |
| Focus mode entry (trust, 13 cos)  | < 600 ms     | One snapshot read + Python aggregation |
| Tile re-format (Cr/L/Full toggle) | instant      | Client-only |
| Body collapse/expand              | instant      | Client-only |
| Per-company strip click → swap    | < 400 ms     | Same as company-focus entry |
| CSV export                        | < 2 s        | ~700 rows; Python streaming |

### 9.2 EXPLAIN check at HALT 5.3

Per CLAUDE.md gotcha 5, run EXPLAIN against the literal SQL the
endpoint executes (not a simplified version). Baseline: existing
Phase 3.5 pivot query plan against `dgv_pivot (snapshot_date,
company, account)`.

**Fail criterion** (mirrors commit 4 Q1 fail criterion):
implementation halts for an index discussion before shipping if the
EXPLAIN output for the focused-view query shows:

1. `Using temporary` against `tabDGV TB Snapshot Row`, OR
2. `Using filesort` against the same.

The new query is shape-equivalent to the existing pivot read — same
table, same indexed columns, narrower predicate (one company or one
trust's companies vs the cockpit's all-allowed list). Expected
outcome: same plan, same index. If reality diverges, halt.

## 10. Tests

### 10.1 Server-side (new file `tests/test_focused_view.py`)

10 new tests, scoped to HALT 5.x surfaces:

1. `test_get_focused_view_company` — single company, returns
   correct accounts list + balances + tiles.
2. `test_get_focused_view_trust` — single trust, returns aggregated
   accounts + per-company `by_company` dict + tiles.
3. `test_get_focused_view_tiles_match_pivot_aggregate` — gold-
   standard reconciliation: tiles for company-focus equal the same
   sums computed from `get_pivot_data` for that company.
4. `test_get_focused_view_tiles_sign_convention` — Liabilities and
   Income tiles read positive (sign-flipped); Assets and Expenses
   read positive (natural). Net surplus = Income − Expenses.
5. `test_get_focused_view_company_not_in_permissions` — raises
   `PermissionError`.
6. `test_get_focused_view_trust_partial_permission` — user with
   permission for 3 of 5 companies in a trust; tiles + accounts
   only reflect those 3.
7. `test_get_focused_view_both_company_and_trust_specified` —
   raises `ValueError` (mut. exclusive).
8. `test_get_focused_view_unknown_company` — raises
   `frappe.DoesNotExistError`.
9. `test_export_focused_view_csv_company` — CSV row count equals
   leaf-row count of the focused view; sign convention matches
   on-screen.
10. `test_export_focused_view_csv_filename_format` —
    `focused_view_<slug>_<as_of>_<HHMMSS>.csv` for both company and
    trust scopes.

### 10.2 Client-side smoke tests

JS unit tests aren't established in this repo. Same console-driven
pattern as commits 3.5 / 4.

Browser smoke tests run at HALT 5.3:

1. Click "Focus →" on a company column → URL updates with `focus=`,
   focus mode renders, tiles populate, body lists accounts at full
   depth.
2. Click "Focus →" on a trust group header → URL updates with
   `focus_trust=`, per-company strip renders, body shows aggregated
   accounts.
3. Click an account row in focus body → drill panel opens scoped
   correctly.
4. Click "View GL entries" in the drill panel → gl-drill page opens
   with `companies=<focused list>`.
5. Click ESC or × in focus banner → exits focus, cockpit restored
   with prior depth.
6. Click per-company strip cell in trust focus → swaps to company
   focus.
7. Format toggle (Cr/L/Full) updates tiles + body live.
8. Direct-URL entry: paste `?focus=GHRCE&as_of=...` into address
   bar → cockpit boots, focus mode renders.
9. Direct-URL entry: paste with both `focus=` and `focus_trust=` →
   console warning, `focus_trust` wins.
10. Click "Export CSV" → file downloads, contents match on-screen
    rows.

## 11. Halt points

Numbered `HALT 5.x` so the commit-5 phase is visible in git log.

1. **HALT 5.1 — Spec sign-off** (this document) — Aditya reviews,
   spec re-versions to v0.2 if changes needed. **No code yet.**
2. **HALT 5.2 — Server endpoint + tests** — `get_focused_view` and
   `export_focused_view_csv` in `cockpit.py` (or extracted to
   `focused_view_v1.py` if it grows past ~300 lines). 10 server
   tests pass; full suite stays green.
3. **HALT 5.3 — Page implementation + EXPLAIN + browser smoke** —
   focus banner, summary tiles, body rendering, per-company strip,
   triggers, drill-panel integration. EXPLAIN check on the focused-
   view query. Browser smoke tests 1–9 above.
4. **HALT 5.4 — CSV export wiring + tests + commit prep** — wire
   the export button, run smoke test 10, finalize PHASE_LOG.md
   entry, ready for commit and push.

After HALT 5.4 sign-off → proceed to **commit 6 (spotlight card
click polish)** per master-spec §9 step 7.

## 12. Out of scope (Phase 5 follow-ups)

Each is its own future spec; flagged here so the v1 boundary is clear.

- **Configurable summary tiles.** Per-trust or per-company tile
  customization; doctype-backed editor. Bundled with Phase 5
  spotlight-card editor.
- **Tile click → drill.** Click "Total Liabilities" → drill panel
  scoped to the Liabilities subtree.
- **Period comparison column.** "vs. last quarter" or "vs. last
  year" inline on each row + tile.
- **PDF export.** v1 is CSV-only.
- **Sticky last-focus.** Cockpit doesn't remember a user's last
  focused company across reloads in v1. Could be a per-user pref
  in Phase 5.
- **Focus-mode-specific URL hash for body collapse state.** v1
  doesn't persist body collapse state in the URL.
- **Multi-scope focus.** Multi-company or multi-trust focus.
- **Per-company strip in CSV.** Trust-focus CSV is single-column;
  a Phase 5 wide-CSV could include per-company columns.
- **Edit account annotations from focus mode.** Read-only in v1;
  annotations land in Phase 5 alongside the `DGV Annotation`
  doctype.

## 13. Open questions

None at v0.1 finalization. Q1–Q5 from the brief and Q6 emerged
during drafting are resolved inline above (§4.1–§4.6).

If implementation surfaces a new question, it lands here and the
spec is re-versioned to v0.2.

## 14. Code-side touch points (for implementer)

Files touched in HALT 5.2 + 5.3 + 5.4. Listed for orientation
only — implementation may add files not anticipated here.

- `dux_groupview/dux_groupview/api/cockpit.py` — new
  `get_focused_view` and `export_focused_view_csv` endpoints.
- `dux_groupview/dux_groupview/api/utils.py` — possibly add a
  `_resolve_focused_companies(company, trust)` helper if the
  scope-resolution logic doesn't fit cleanly in cockpit.py.
- `dux_groupview/dux_groupview/page/groupview/groupview.js` — focus
  banner, tiles, body rendering, triggers (Focus → buttons in
  pivot headers), URL handling, drill-panel integration, ESC/×
  handling, per-company strip.
- `dux_groupview/dux_groupview/page/groupview/groupview.json` — no
  change expected (focus mode is a JS-only reflow of the existing
  page).
- `dux_groupview/public/css/cockpit.css` (if it exists; otherwise
  styles inline in groupview.js) — focus banner, tiles, strip,
  body row styles.
- `dux_groupview/dux_groupview/tests/test_focused_view.py` — new
  test file (10 tests).

## 15. Reconciliation with master-spec §4.4

Master-spec `phase-4-drills.md` §4.4 sketched focus mode in 26
lines. This commit-5 spec **expands** that sketch along three
dimensions; deltas are flagged here so the divergence is auditable.

| Aspect              | §4.4 sketch                | Commit 5 spec                          |
|---------------------|----------------------------|----------------------------------------|
| Triggers            | Leaf company column header click | Explicit "Focus →" button on company headers AND trust group headers (per Q1, §4.1) |
| Scope flavors       | Company only               | Company + trust (per brief expansion) |
| Endpoint            | Reuses `get_pivot_data`    | New `get_focused_view` endpoint (per §6.1; cleaner separation, allows tile aggregation server-side) |
| URL state           | Hash (`#focus_company=`)   | Query params (`?focus=` / `?focus_trust=`) (per §5; matches cockpit's other URL state, copy-paste shareable) |
| Summary tiles       | 4 (Assets, Liabilities, Net surplus, Cash & bank) | 5 (Assets, Liabilities, Income, Expenses, Net surplus) (per Q4, §4.4 — drops Cash & bank in favor of the standard accounting top-lines; Cash & bank is already a spotlight card) |
| Pivot rendering     | "1 Balance column"         | Single-column vertical body with depth indentation, not the pivot grid (per §7.3; cleaner reflow) |
| CSV export          | Not mentioned              | Yes (per brief, §6.3) |
| Drill integration   | Not mentioned              | Yes — leaf row click → drill panel (per Q6, §4.6) |
| Depth control       | "Depth auto-jumps to full; depth control still works" | Hidden during focus (per Q3, §4.3) |
| Trust selector      | Not mentioned              | Locked during trust focus, free during company focus (per §8.5) |
| Performance target  | < 400 ms p95               | < 400 ms p95 (company), < 600 ms p95 (trust 13 cos) — same target for company, new line for trust |

**Why divergence is acceptable.** §4.4 was a 26-line sketch in a
6-commit master spec; commit-level specs have always extended their
master-spec sections (commit 4's filter spec was a similar
expansion of master-spec §4.3). The deltas are all *additive* —
nothing in §4.4 is contradicted; trust focus, the dedicated
endpoint, and CSV export are net-new surfaces. The depth control
change (hide vs keep) is a UX call that emerged from the Q3
analysis (a depth selector that's always-Depth=All is visual
noise); recorded here so future-Aditya knows we deliberately
diverged.

If Aditya's review at HALT 5.1 pushes back on any of these deltas,
the spec re-versions to v0.2 with the resolved position.
