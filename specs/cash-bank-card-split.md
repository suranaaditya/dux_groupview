# Cash & Bank card split — Liquid cash + Secured loans

**Status:** v0.1 draft — awaiting HALT 1 sign-off
**Branch:** `feat/liquid-cash-secured-loans-split`
**Estimated duration:** ~1 working day
**Depends on:**
- `main` at `f3145d6` (per-account drill expansion merged); that drill
  is the primary visual validation tool for this change.

---

## 1. Goal

Replace the single `cash_and_bank` card (which conflates everything
with `account_type IN ('Bank', 'Cash')` — including bank-loan accounts
that carry liability-shaped negative balances and pollute the
"liquidity" reading) with two predicate-disjoint cards:

- **Liquid cash** — only `Asset`-rooted leaves whose immediate parent
  group is `Bank Accounts` or `Cash in Hand`. By design excludes
  bank-loan accounts (which live under `Secured Loans` / `Bank OD
  A/c` parents) and excludes anything else that happens to be tagged
  `account_type = Bank` but isn't a deposit account.
- **Secured loans** — only `Liability`-rooted leaves whose immediate
  parent group is `Secured Loans` or `Bank OD A/c`.

Same change introduces:

- A new predicate type `by_parent_account_stem_in` to express the
  above (matches on the parent group's account_name stem, not on
  `account_type` or a name LIKE pattern).
- An optional `disabled` flag on card dicts so we can hide cards in
  the cockpit while still computing + caching their values for
  history continuity.
- Three existing cards disabled (`sundry_debtors`, `cash_and_bank`,
  `inter_co_receivable`) — predicates left untouched. `sundry_debtors`
  + `inter_co_receivable` are deferred to a future predicate fix;
  `cash_and_bank` is being replaced by the two new cards but its
  cache rows are kept so the historical sparkline doesn't break.

Visible cards after this ships: **7** (down from 8).

### Known limitations (deliberate, not bugs in v1)

- **Positive Bank OD balance routes the wrong way.** If a Bank OD
  account ever shows a positive balance (the company has deposited
  more than what it owes the bank, turning the OD into a net
  surplus), the `secured_loans` predicate will include it as a
  negative contribution to the loans total rather than routing the
  surplus to `liquid_cash`. Production currently has no OD accounts
  with positive balance, so this is not surfacing. The fix needs a
  `balance_sign` option on the predicate (route by-balance-sign,
  not by-parent-only), which is deferred to a future PR.
- **`fixed_deposits` card is out of scope.** Its predicate may also
  have issues (separate diagnostics in progress); we are not
  touching it in this PR even if discovery surfaces something.
- **Case variants in parent account stems.** Across dev seeds and
  production COAs we've observed casing differences in parent
  names (e.g. `Cash in Hand` vs `Cash In Hand`). The current
  predicate handles this by enumerating variants explicitly in the
  `stems` list — the `liquid_cash` card definition carries both
  spellings. Other potentially-affected stems (`Loans (Liability)`
  vs `Loans (Liabilities)`, `Fixed Deposit` vs `Fixed Deposits`)
  are not used by cards in this PR but may surface in future
  cards. A Phase 5 cards-editor "fuzzy stem matching" feature
  (case-insensitive + trailing-s tolerant) would remove this
  manual enumeration. For now, document case variants in stems
  lists with an inline comment when needed. The
  `liquid_cash` card definition carries such a comment so a
  future "dedupe obvious duplicates" refactor doesn't silently
  drop the production-spelling entry.

## 2. Non-goals

Deferred or out of scope; surfaced so the diff stays narrow:

- **No schema changes.** No new index, no new doctype, no
  denormalisation of `parent_account` onto `tabDGV TB Snapshot
  Row`. The new predicate uses an `IN`-subquery against
  `tabAccount`.
- **No cards-editor UI work.** Card definitions remain hardcoded
  in `spotlight/cards.py` (Phase 5 will move them into a doctype).
- **No sign-flip refresh-path fix.** Problem 1 from earlier
  diagnostics (the refresh-path sign convention bug) is Phase 5
  scope.
- **No card rename Financial → Interest** (deferred).
- **No `inter_co_receivable` predicate fix.** Disabled in this PR,
  fixed in a future PR.
- **No COA reclassification** (Kumar Sir's call, not code).
- **No GL drill changes.** Account-drill panel + per-account drill
  expansion shipped last PR carry over unchanged; new cards plug
  into the same surfaces.

## 3. Current state

**Cards system today (`spotlight/cards.py`).** Eight cards, all
visible: sundry_creditors, sundry_debtors, unsecured_loans,
cash_and_bank, inter_co_receivable, fixed_deposits,
financial_exp_to_bank, financial_exp_to_other. Two predicate types
supported: `by_account_type` (string or list) and
`by_root_type_and_name_pattern` (root_type + SQL LIKE). Definitions
are read by:

- `spotlight_refresh._match_clause` → produces a WHERE clause against
  `tabDGV TB Snapshot Row` (used by `_aggregate` to fill cache rows
  for the value, delta, and 12-slot sparkline at refresh time).
- `cards_v1._resolve_match` → produces a leaf account name list from
  `tabAccount` (used by the cockpit drill panel and the drill page to
  hand off resolved accounts to `account_drill_v1.get_account_breakdown`).
- `cockpit.get_spotlight_cards` (cache path) and
  `_build_filtered_cards_payload` (live-recompute path) iterate
  `CARDS` to assemble the response.

**Cache layer.** `tabDGV Spotlight Cache` has one row per
`(snapshot_date, card_id)` carrying value + delta + delta_percent
+ sparkline_data + computed_at. Refresh writes one row per card per
snapshot date.

**HEADLINE_CARD_NAMES.** A separate map in `cockpit.py` keyed by
card_id → headline-copy string. Required: a guardrail test pins
every card_id has an entry (so headlines never fall back to the raw
card label). Adding a new card requires adding an entry.

## 4. The new predicate: `by_parent_account_stem_in`

### 4.1. Predicate shape

```python
{
    "by_parent_account_stem_in": {
        "stems": ["Bank Accounts", "Cash in Hand"],   # 1..N strings
        "root_type": "Asset",                          # exactly one
    }
}
```

Both `stems` (non-empty list of strings) and `root_type` (single
string) are required. A malformed predicate (empty list, missing
key, non-list, non-string root_type) returns no leaves (same
defensive posture as the existing two predicates' malformed
branches).

### 4.2. Semantics

A leaf account matches iff **all three** hold:

1. `is_group = 0` (leaf, not group)
2. `root_type = <root_type>` (the supplied one)
3. `SUBSTRING_INDEX(parent_account, ' - ', 1) IN (<stems>)` — the
   account's immediate parent group's stripped account_name (the
   part BEFORE the first ` - ` separator) matches one of the
   supplied stems.

### 4.3. SQL — leaf resolution (`cards_v1._resolve_match`)

Mirrors the structure of the existing two branches; uses tabAccount
only.

```sql
SELECT name FROM `tabAccount`
WHERE is_group = 0
  AND root_type = %(root_type)s
  AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN (<stems>)
  AND company IN (<companies>)
```

Returns the full company-suffixed leaf names (e.g.
`ICICI Bank A/c 624205012830 - GHRUA`) suitable as the `accounts`
parameter to `get_account_breakdown`.

### 4.4. SQL — refresh-path aggregation (`spotlight_refresh._match_clause`)

Returns an IN-subquery against `tabAccount` plugged into the
snapshot-row WHERE clause used by `_aggregate`. No JOIN needed; the
subquery resolves leaves up-front and the snapshot-row scan filters
to `account IN (<leaves>)`. Optimiser handles it as a semi-join
(`Using temporary; Using where` on the snapshot row table, with the
tabAccount scan inlined).

```sql
account IN (
  SELECT name FROM `tabAccount`
  WHERE is_group = 0
    AND root_type = %(root_type)s
    AND SUBSTRING_INDEX(parent_account, ' - ', 1) IN (<stems>)
)
```

Note: no company filter in the subquery. Company scope is applied
separately by `_aggregate` (`AND company IN (...)` against the
snapshot row). This matches how `by_account_type` and
`by_root_type_and_name_pattern` work.

### 4.5. Why `SUBSTRING_INDEX(parent_account, ' - ', 1)`

Frappe / ERPNext account names follow the convention
`<account_name> - <company_abbr>`. The first ` - ` separates the
account_name stem from the company abbreviation. Parent group
accounts use the same convention: `Bank Accounts - GHRCE`,
`Cash in Hand - SGREF`, etc. Extracting the part before the first
` - ` gives the account_name stem of the parent — which is what
"Bank Accounts" or "Cash in Hand" refers to.

This is robust to:

- Account names that contain ` - ` after the company-abbr separator
  (rare, but `SUBSTRING_INDEX(..., ' - ', 1)` only takes everything
  before the FIRST occurrence so the company-abbr suffix is what
  gets stripped).
- Group rows: filtered out by `is_group = 0`.
- Root accounts (NULL parent_account): excluded because their
  children are checked, not them.

### 4.6. Case-insensitivity (MySQL collation)

MariaDB / MySQL's default collation on `VARCHAR` columns is
`utf8mb4_general_ci` (case-insensitive). The IN-clause comparison
`SUBSTRING_INDEX(parent_account, ' - ', 1) IN ('Bank Accounts', 'Cash in Hand')`
therefore matches BOTH `"Cash in Hand"` AND `"Cash In Hand"` (and
`"CASH IN HAND"`, etc.) as parent stems. This is **desired behaviour**
given real-world COA data has case inconsistencies — the dev seed
contains both `"Cash in Hand - CACSPU"` and `"Cash In Hand - ASSA"`,
and the production COA likely has similar drift. The predicate
accepts both transparently.

Surfaced during test development (HALT 3): a case-sensitive Python
assertion `stem in {"Cash in Hand"}` failed when the resolver
correctly returned a leaf whose parent stem was `"Cash In Hand"`.
Tests now compare case-insensitively via `.casefold()` so the
predicate's real (case-insensitive) behaviour is what's asserted.

### 4.7. Perf profile

Refresh-path subquery against `tabAccount` runs once per card per
refresh. The two existing predicates (`by_account_type`,
`by_root_type_and_name_pattern`) are pure inline column filters on
the snapshot row (no subquery); the new predicate adds one nested
SELECT against `tabAccount`. MariaDB caches the subquery plan and
typically materialises it as a semi-join after the first hit.

Expected cheap: the parent-stem filter resolves to a small leaf set
(Bank Accounts + Cash in Hand together ≈ tens of leaves per
company; Secured Loans + Bank OD ≈ low single-digits per company),
so the subquery returns hundreds of rows at most across the whole
group. If perf shows up funny during a future investigation, this
subquery is the suspect.

### 4.8. Why a predicate, not a SQL helper

Card predicates are the public contract that `cards.py` cards bind
to. A predicate type is documented, testable, and reusable for
future cards. A one-off SQL helper would couple this card to one
implementation; future Phase 5 cards editor can render predicate
types as form options.

## 5. The `disabled` flag

### 5.1. Card-dict shape

Add an optional key:

```python
{
    "id": "sundry_debtors",
    "label": "Sundry debtors",
    "match": {...},
    "polarity": "bad_up",
    "format": "crore",
    "color": "#3B6D11",
    "disabled": True,
}
```

Default is `False` when absent (`.get("disabled", False)`). No
schema, no doctype field, no migration.

### 5.2. Refresh path — INCLUDES disabled cards

`refresh_spotlight_cache` iterates the full `CARDS` list. Disabled
cards still get cache rows (value + delta + sparkline). Rationale:

- History continuity. Re-enabling a card later shows its sparkline
  with no manual backfill.
- Symmetric. The cache layer is the canonical historical record;
  the UI is the visibility layer.
- Cheap. ~0.1 sec per card per snapshot; disabling a card to skip
  refresh would save tens of milliseconds at most.

### 5.3. Read path — SKIPS disabled cards

Both endpoints filter `CARDS` early:

```python
visible_cards = [c for c in CARDS if not c.get("disabled")]
```

- `cockpit.get_spotlight_cards` — iterates `visible_cards` when
  building the response from cache rows.
- `cockpit._build_filtered_cards_payload` (live recompute) —
  iterates `visible_cards`.
- `cockpit.get_cockpit_headline` — iterates `visible_cards` (so
  headline copy doesn't reference hidden cards).
- `cards_v1.resolve_match_to_accounts` — NOT touched. A drill open
  with a disabled card_id should still resolve (e.g. a deep link to
  an old card_id still works for the panel; only the cockpit GRID
  hides it). If we later want to actively reject disabled
  card_ids, separate PR.

### 5.4. Flipping the flag mid-cache

Toggling `disabled` for an existing card does NOT require a refresh.
The read path filter is the only thing that changes the visible
output. The cache rows are already there. Test pins this: cache
exists for a disabled card; flipping disabled → False on the next
read shows the cached value.

## 6. Cards.py reorganisation

Final CARDS list ordering (8 entries; 3 disabled = 5 visible from
existing + 2 new visible = 7 visible total):

| Order | id | visible | predicate change |
|---|---|---|---|
| 1 | `sundry_creditors` | ✓ | none |
| 2 | `sundry_debtors` | **disabled** | none (future predicate fix) |
| 3 | `unsecured_loans` | ✓ | none (future PR) |
| 4 | `cash_and_bank` | **disabled** | none (kept for cache history) |
| 5 | `inter_co_receivable` | **disabled** | none (future predicate fix) |
| 6 | `fixed_deposits` | ✓ | none (out of scope for this PR) |
| 7 | `financial_exp_to_bank` | ✓ | none |
| 8 | `financial_exp_to_other` | ✓ | none |
| 9 | **`liquid_cash`** | ✓ | NEW (by_parent_account_stem_in) |
| 10 | **`secured_loans`** | ✓ | NEW (by_parent_account_stem_in) |

### 6.1. New card definitions

```python
{
    "id": "liquid_cash",
    "label": "Liquid cash",
    "match": {
        "by_parent_account_stem_in": {
            "stems": ["Bank Accounts", "Cash in Hand"],
            "root_type": "Asset",
        },
    },
    "polarity": "good_up",
    "format": "crore",
    "color": "#185FA5",
},
{
    "id": "secured_loans",
    "label": "Secured loans",
    "match": {
        "by_parent_account_stem_in": {
            "stems": ["Secured Loans", "Bank OD A/c"],
            "root_type": "Liability",
        },
    },
    "polarity": "bad_up",
    "format": "crore",
    "color": "#7B2D26",
},
```

Color choices:
- `liquid_cash` reuses `#185FA5` (the existing `cash_and_bank` blue)
  to preserve visual continuity for users who learned the
  prior card's color.
- `secured_loans` `#7B2D26` (deep red-brown). Distinct from the
  existing palette: not creditor-brown (`#BA7517`), not
  finance-cost red (`#A33B3B`), not financial-orange (`#C46A1F`).
  Signals "owe" without competing with the warm-tone outflow cluster.

### 6.2. HEADLINE_CARD_NAMES entries

Two new entries in `api/cockpit.py` HEADLINE_CARD_NAMES:

```python
"liquid_cash":   "Liquid cash",
"secured_loans": "Secured loans",
```

Both labels match headline copy directly — the labels read
naturally inline (e.g. "Liquid cash up ₹2.3 Cr from last month",
"Secured loans down ₹0.5 Cr from last month"). The existing
divergence pattern (`cash_and_bank` → `Cash position`) was
intentional for the conflated old card; no such divergence needed
for the two new cards.

Cards being disabled (`sundry_debtors`, `cash_and_bank`,
`inter_co_receivable`) keep their HEADLINE_CARD_NAMES entries —
they're still consulted by the headline composer for the cached
historical sparkline data, even though the cards themselves don't
render in the grid. (Implementation note: the headline composer
iterates `visible_cards` per §5.3, so technically the disabled
entries are no longer consulted; but leaving them in is safe and
zero-cost — flipping `disabled` back doesn't require a re-add.)

## 7. Visible result on dev

Expected production-relevant magnitudes (per prior bench-console
diagnostics — values approximate, current snapshot):

- **Liquid cash:** ~₹-17 Cr (Bank Accounts net negative on dev due
  to bank-loan accounts that are NO LONGER under the predicate now
  that we filter by parent stem; the ~-17 figure was from the OLD
  conflated card that mistakenly included loans. After this PR, the
  predicate excludes loans, so the magnitude should be more
  positive than the old card on prod data. **On dev seed: confirm
  experimentally — the dev fixture data has limited variety.**)
- **Secured loans:** ~₹194 Cr (sum of Secured Loans + Bank OD A/c
  leaf balances at the natural Liability sign).

Both values verified during HALT 4 dev deploy via the per-account
drill expansion shipped in the previous PR: clicking a chevron on a
company row in the new cards should show the contributing leaf
accounts. Liquid cash drill must NOT show any HDFC Bank Loan / OD
accounts; Secured loans drill SHOULD show them.

## 8. Tests

### 8.1. New predicate (`tests/test_cards_v1.py` extension)

- `test_by_parent_account_stem_in_returns_leaves_under_named_stems`
  — happy path: resolve `{"stems": ["Bank Accounts"], "root_type":
  "Asset"}`, assert at least one leaf returned, all have
  `parent_account` starting with `"Bank Accounts - "`, all have
  `is_group=0` and `root_type='Asset'`.
- `test_by_parent_account_stem_in_filters_by_root_type` —
  same stem list but wrong root_type returns empty.
- `test_by_parent_account_stem_in_empty_stems_returns_empty` —
  defensive: `stems=[]` → empty list, no SQL error.
- `test_by_parent_account_stem_in_missing_root_type_returns_empty` —
  defensive: `{"stems": ["Bank Accounts"]}` (no `root_type`) →
  empty.
- `test_by_parent_account_stem_in_nonexistent_stem_returns_empty` —
  `{"stems": ["Definitely Not A Real Stem"], "root_type": "Asset"}`
  → empty.

### 8.2. Refresh-path match clause (`tests/test_spotlight.py` extension)

- `test_match_clause_by_parent_stem_aggregates_only_matching_leaves`
  — pin a card with the new predicate; refresh; assert cache row
  value equals an independent SQL aggregation against snapshot
  rows joined to tabAccount with the same parent-stem filter.

### 8.3. `disabled` flag (`tests/test_spotlight.py` + `test_cockpit.py` + `test_cards_v1.py` extensions)

- `test_refresh_writes_cache_for_disabled_cards` — set
  `disabled=True` on a card; refresh; assert that card's cache row
  exists with non-zero value (if predicate matches anything).
- `test_get_spotlight_cards_skips_disabled` — set `disabled=True`
  on a card; cache exists; call `get_spotlight_cards`; the
  disabled card's id is NOT in the response.
- `test_get_spotlight_cards_filtered_skips_disabled` — same but for
  the live-recompute path.
- `test_flipping_disabled_does_not_require_refresh` — refresh with
  disabled=True (cache row created); flip disabled=False; read
  without re-refresh; cached value appears in response.
- `test_resolve_match_to_accounts_works_for_disabled_card`
  (`test_cards_v1.py`) — the drill-resolver does NOT reject
  disabled card predicates. Confirms bookmarked deep-links to old
  card_ids continue to resolve. Pins the cross-surface contract
  decision from spec §10 Q1.
- `test_headline_excludes_disabled_cards` (`test_cockpit.py`) —
  set `disabled=True` on a card whose cache has the largest delta;
  call `get_cockpit_headline`; assert the disabled card is NOT
  named in the headline copy. Pins the no-leak decision from spec
  §10 Q2.

### 8.4. Regression — existing 8 cards' shape unchanged

- `test_existing_cards_visible_count` — after disable changes, the
  default response from `get_spotlight_cards` has exactly 7
  card_ids (5 unchanged visible + 2 new): `sundry_creditors`,
  `unsecured_loans`, `fixed_deposits`, `financial_exp_to_bank`,
  `financial_exp_to_other`, `liquid_cash`, `secured_loans`. The
  3 disabled card_ids (`sundry_debtors`, `cash_and_bank`,
  `inter_co_receivable`) are NOT in the response.
- `test_existing_cards_predicates_unchanged` — pin each existing
  card's predicate dict (deep equality) so a future refactor can't
  silently change any of them.
- `test_headline_card_names_covers_all_visible_cards` — the
  existing `test_friendly_names_cover_all_cards` regression test
  should already cover this; just verify it still passes with the
  2 new entries.

### 8.5. Sundry-creditors-counting tests (the `len(CARDS)` tests
from the previous PR)

`test_refresh_spotlight_cache_creates_one_row_per_card` and
`test_refresh_spotlight_cache_idempotent` both assert
`len(rows) == len(CARDS)`. After this PR, `len(CARDS)` increases
from 8 to 10. These tests should continue to pass — the assertion
is dynamic.

`test_get_spotlight_cards_falls_back_when_cache_empty` and
`test_friendly_names_cover_all_cards` also reference card counts;
the dynamic `len(CARDS)` makes them resilient.

## 9. Rollout

**Branch:** `feat/liquid-cash-secured-loans-split` (already created).

**Commits planned (5):**

1. `Spec: cash & bank card split (liquid cash + secured loans)` —
   the spec doc only.
2. `Add by_parent_account_stem_in predicate + disabled flag` —
   backend changes:
   - `spotlight/cards.py` — disable 3 existing, add 2 new, update
     module docstring with the new predicate type.
   - `api/cards_v1.py` — new `_resolve_match` branch.
   - `snapshots/spotlight_refresh.py` — new `_match_clause` branch.
   - `api/cockpit.py` — read paths skip disabled, two new
     HEADLINE_CARD_NAMES entries.
3. `Tests for new predicate + disabled flag` —
   `tests/test_cards_v1.py`, `tests/test_spotlight.py`,
   `tests/test_cockpit.py` extensions.
4. (optional) `Document predicate + disabled in cards.py docstring`
   — module docstring catch-up. May fold into commit 2 if small
   enough.

**Dev deployment sequence:**

```bash
# Local: scp the changed Python files
scp dux_groupview/dux_groupview/spotlight/cards.py \
    frappe@187.127.132.58:/tmp/cards.py
scp dux_groupview/dux_groupview/api/cards_v1.py \
    frappe@187.127.132.58:/tmp/cards_v1.py
scp dux_groupview/dux_groupview/api/cockpit.py \
    frappe@187.127.132.58:/tmp/cockpit.py
scp dux_groupview/dux_groupview/snapshots/spotlight_refresh.py \
    frappe@187.127.132.58:/tmp/spotlight_refresh.py

# On dev: move into place + clear-cache + SIGHUP gunicorn
ssh frappe@187.127.132.58
mv /tmp/cards.py            ~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/spotlight/cards.py
mv /tmp/cards_v1.py         ~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/api/cards_v1.py
mv /tmp/cockpit.py          ~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/api/cockpit.py
mv /tmp/spotlight_refresh.py ~/frappe-bench/apps/dux_groupview/dux_groupview/dux_groupview/snapshots/spotlight_refresh.py
cd ~/frappe-bench
bench --site erp.jewonline.in clear-cache
kill -HUP $(pgrep -of "gunicorn -b")

# Trigger spotlight cache refresh so new card_ids get cache rows
bench --site erp.jewonline.in execute \
  dux_groupview.dux_groupview.snapshots.spotlight_refresh.refresh_spotlight_cache \
  --kwargs '{"snapshot_date": "<latest>"}'
```

No `bench build` needed — Python-only change; no JS / CSS edits.
No `bench migrate` — no schema change.

**Verification on dev:**

- 7 cards visible on `/app/groupview` (3 hidden via `disabled`).
- Liquid Cash + Secured Loans values match independent SQL
  aggregation.
- Per-account drill on the new cards: chevron expansion shows
  contributing leaf accounts. Liquid cash drill MUST NOT include
  bank-loan accounts; Secured loans drill SHOULD include them.
- Backend test suite green: `bench --site erp.jewonline.in
  run-tests --module dux_groupview.dux_groupview.tests.test_spotlight`,
  `... test_cards_v1`, `... test_cockpit`.

**PR:** opens against `main`, NOT auto-merged. User reviews and
merges; Frappe Cloud picks up `main` on its next deploy cycle.

## 10. Open questions

1. **Should `cards_v1.resolve_match_to_accounts` reject disabled
   cards' predicates?** Current proposal: NO. A deep-link to the
   panel for an old card_id (e.g. a saved bookmark for the now-
   disabled `cash_and_bank`) should still resolve and open the
   drill — only the cockpit GRID hides the card. If you'd rather
   reject at the resolver, easy override at HALT 1.
2. **Should `cockpit.get_cockpit_headline`'s composer skip disabled
   cards?** Current proposal: YES. The headline narrates the most
   significant deltas; surfacing a "Cash & bank up ₹X" line for a
   disabled card would confuse users who see no such card. If you
   want disabled cards to still appear in headlines (e.g. as
   transition copy), easy override at HALT 1.
3. **Color for `secured_loans` = `#7B2D26`.** Deep red-brown,
   distinct from the warm-outflow cluster (creditor brown, finance
   red, financial orange). If you'd prefer a different signal
   (e.g. desaturated grey like `#5F5E5A` to match `unsecured_loans`
   for visual grouping of "loan" cards), easy swap at HALT 1.
4. **Spec doc path.** `specs/cash-bank-card-split.md` (kebab-case
   per project convention). Confirm or rename.
5. **Module docstring update in `cards.py`.** Should the new
   predicate type be documented in the module docstring at the top
   of `cards.py` (mirroring the existing two)? Default: YES, in
   the same commit that adds the predicate. Skip if you'd rather
   keep the spec as the canonical predicate-types reference.
