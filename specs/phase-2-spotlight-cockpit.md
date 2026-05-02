# Phase 2 — Spotlight Cards & Cockpit Foundation

Archived spec for Phase 2, as agreed with Aditya on 2026-05-03.
Linkifier artifacts from the original chat message have been
normalised; the substance is unchanged.

---

## Goal

Replace the placeholder /groupview page with a real cockpit landing
screen: a top filter bar with a date selector, six spotlight cards
showing live aggregations from the snapshot cache, sparklines from
historical snapshots, and a "no drill yet" placeholder for card
clicks.

End state: the owner opens /groupview and sees six cards with real
RGI-style numbers. Selecting a different snapshot date re-renders all
cards. Sparklines show the rolling 6-month trend per card. Numbers
come from a pre-aggregated `DGV Spotlight Cache` table that refreshes
after every TB snapshot.

## Hard rule (tightened)

- Phase 1 rule: "Never query `tabGL Entry` directly from any UI code path."
- Phase 2 rule: spotlight code reads ONLY from `tabDGV TB Snapshot Row`
  and `tabDGV TB Snapshot`. Cockpit JS reads ONLY from `tabDGV Spotlight
  Cache` (via the API). Two-layer cache; both layers read-only at this
  phase.

## Resolved interpretive choices

- **`by_account_type` accepts string OR list.** The spec said
  "by_account_type = 'Bank' or 'Cash' (handle multi-value match)".
  Implemented by allowing `account_type` in the strategy config to be
  a string (single match) or a list (IN-clause match). Existing
  single-value cards (Sundry Creditors, Sundry Debtors) keep the
  string form.
- **Delta period = "latest snapshot in the prior calendar month".**
  Defined as the snapshot with the largest snapshot_date strictly less
  than the first day of the target snapshot's month. With a backfilled
  month-end on dev, this resolves to the prior month-end.
  `delta = natural_value(target) - natural_value(prior)`. If the prior
  snapshot is missing, `delta = 0`.
- **Sparkline = 6 most recent month-end snapshots whose date <= target
  snapshot date.** Older months padded with `null` if fewer than 6
  exist. The current snapshot is included only if it lands on a
  month-end; otherwise the rightmost point is the most recent
  completed month.
- **Sign convention.** Phase 1 stores raw `Dr - Cr` in `balance`.
  Spotlight values are stored already sign-corrected so positive = the
  natural / healthy direction. Implementation: per-row CASE in SQL --
  `SUM(CASE WHEN root_type IN ('Liability', 'Equity', 'Income') THEN
  -balance ELSE balance END)`.
- **Polarity is UI-only metadata.** Stored value never depends on
  polarity. Tested by `test_polarity_does_not_affect_value`.
- **Spotlight refresh failure does not roll back TB snapshot.** TB is
  canonical, spotlight is derivative. Wrappers log the spotlight
  failure but return the TB snapshot result unchanged.
- **finalize_past_snapshots does not touch spotlight cache.** Locking
  TB snapshots as immutable doesn't lock cache; cache can always be
  regenerated.

## Doctype: DGV Spotlight Cache

| Field | Type | Notes |
|---|---|---|
| card_id | Data | indexed, stable string identifier |
| snapshot_date | Date | indexed |
| value | Currency | already sign-corrected (positive = natural side) |
| delta | Currency | current value - prior month value |
| delta_percent | Float | same change as percent |
| sparkline_data | Long Text | JSON array of 6 floats (or nulls), oldest first |
| computed_at | Datetime | when this row was generated |
| card_definition_hash | Data | "phase2-hardcoded" for now; Phase 5 will use real hashes |

- Naming: `format:{card_id}-{snapshot_date}` -> e.g. `cash_and_bank-2026-05-03`
- Composite index via patch: `(card_id, snapshot_date)`.
- Permissions: System Manager full; GroupView Owner / Viewer read.

## Card definitions (`spotlight/cards.py`)

Six cards in this fixed order:

| id | label | match | polarity | format | color |
|---|---|---|---|---|---|
| sundry_creditors | Sundry creditors | by_account_type Payable | neutral | crore | #BA7517 |
| sundry_debtors | Sundry debtors | by_account_type Receivable | bad_up | crore | #3B6D11 |
| unsecured_loans | Unsecured loans | by_root_type_and_name_pattern Liability `%Unsecured Loan%` | neutral | crore | #5F5E5A |
| cash_and_bank | Cash & bank | by_account_type [Bank, Cash] | good_up | crore | #185FA5 |
| inter_co_receivable | Inter-co receivable | by_root_type_and_name_pattern Asset `%Inter%Compan%` | neutral | crore | #534AB7 |
| fixed_deposits | Fixed deposits | by_root_type_and_name_pattern Asset `%Fixed Deposit%` | good_up | crore | #534AB7 |

On dev seed, cards 3 / 5 / 6 produce zero values (no matching account
names). Expected; covered by `test_zero_match_card_returns_zero`.

## Refresh function

`dux_groupview.dux_groupview.snapshots.spotlight_refresh.refresh_spotlight_cache(snapshot_date=None)`

- Reads only `tabDGV TB Snapshot Row` and `tabDGV TB Snapshot`. Never
  `tabGL Entry`.
- One transaction wrapping all 6 cards. On failure, full rollback
  (no partial cache).
- Idempotent: upserts by `name = '{card_id}-{snapshot_date}'`.
- Performance target: < 2 sec on dev seed (6 cards × 6 historical
  aggregations + 6 current aggregations + 6 prior-month deltas).

## Scheduler wiring

`refresh_tb_snapshot_business_hours` and `refresh_tb_snapshot_off_hours`
call `refresh_spotlight_cache(snapshot_date=today())` after a successful
TB refresh. Spotlight failures are caught and logged via
`frappe.log_error`; TB result is returned unchanged.

`backfill_snapshots` calls `refresh_spotlight_cache(snapshot_date=date)`
after each successful refresh in its loop. Same swallow-and-log
behaviour.

## Cockpit page

Layout per the mockup: top bar with date selector, age pill, edit
placeholder; SPOTLIGHT title; 6 cards in a 3-column grid (2 columns
on narrow viewports); footer.

Card click handler: `frappe.show_alert("Drill into [label] coming in
Phase 4", indicator="blue")`. No navigation.

## API

`dux_groupview.dux_groupview.api.cockpit`:

- `get_available_snapshot_dates()` -> up to 30 dates, newest first,
  `status = Complete` only.
- `get_spotlight_cards(snapshot_date)` -> array of 6 card objects with
  cache + definition + server-rendered formatted strings.
- `get_snapshot_age(snapshot_date)` -> `{generated_at, age_seconds, status}`.

All require GroupView Viewer or higher (System Manager satisfies).

## Performance targets

- `refresh_spotlight_cache`: < 2 sec on dev (50K source rows, 13
  snapshots).
- /groupview first paint: < 500 ms on dev (target 200 ms with margin).
- Browser: confirm via DevTools Network tab.

## Verification SQL (gold standard)

For each card, the cached value must equal an independent SQL
aggregation against `tabDGV TB Snapshot Row` with the card's match
criteria and the same sign-flip rule. Test
`test_spotlight_value_matches_direct_aggregation` enforces this.
