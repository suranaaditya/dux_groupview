# Phase 4 — Commit 1 Findings

**Branch:** `phase-4-drills` (off `origin/main` at `9cd0e52`)
**Spec consulted:** `specs/phase-4-drills.md` (v3.2)
**COA / data inspected on:** `erp.jewonline.in` (dev), RGI-DEMO synthetic seed
**Date:** 2026-05-04

This document captures findings from the three commit-1 deliverables:
two procedural notes (the §4.5 spec discrepancy and a Phase 3 test
failure surfaced by the verification step), plus the three required
findings from §11 of the prompt (card scope assignments, Q17
account_type allow-list, group-company name match check).

---

## Procedural notes

### Note A — §4.5 spec discrepancy: nothing inlined to extract

The v3.2 spec §4.5 says:

> `_walk_subtree_leaves(parent_account_name, company)` — returns all
> leaf descendants of a parent account. **Currently lives inside
> `_build_accounts_and_balances` in `api/pivot.py`.** Factor out in
> commit 1, place in `dux_groupview/dux_groupview/api/utils.py` next
> to `_resolve_scope`. Refactor pivot code to use it; verify nothing
> changes via Phase 3's gold-standard pivot test.

This is incorrect. `pivot.py` does not contain a parent→leaves walk.
`grep -nE 'leaf|subtree|descendant|is_group\s*=\s*0|lft|rgt'
pivot.py` returns only comments referring to Phase 3.5's
**leaves→ancestors** bubble-up aggregation. There is no inline walk
to extract.

**What was actually done in commit 1:** the helper was written
**fresh** in `dux_groupview/dux_groupview/api/utils.py`, against
`tabAccount.lft`/`rgt` (nested-set columns). `pivot.py` is untouched —
nothing references the new helper yet (commits 2-7 will).

`_resolve_scope` likewise **was not moved** into `utils.py`. It
remains in `api/pivot.py`. Per the prompt's "no while-I'm-here
cleanups" rule, moving it is out of scope. A future commit can
centralise both helpers in `utils.py` if it becomes useful; for now
the new module contains only `_walk_subtree_leaves` plus a docstring
explaining the layout.

This corrects the spec's premise but does not change commit 1's
deliverable shape: a new helper in `utils.py`, no behaviour change
in `pivot.py`.

### Note B — Phase 3 gold-standard test fails on `origin/main` (pre-existing)

The prompt's deliverable 2 verification step says:

> **Verification — this is the critical step:** Run Phase 3's
> gold-standard pivot correctness test... The test must pass
> unchanged after the refactor. If output drifts even by one rupee,
> stop and surface the diff — do not "fix" by adjusting the test.

Result: `test_pivot.test_pivot_data_matches_snapshot` **fails on
`origin/main` itself**, before commit 1 makes any changes to code
paths. The drift is not introduced by commit 1 — `pivot.py` is
unchanged and `utils.py` (new file) is not imported anywhere yet.

**Failure:**

    AssertionError: -177403293.31 != 0.0 within 2 places
    (177403293.31 difference) :
    Pivot value for "Unsecured Loans" × "GHR CACS Pune":
    got -177,403,293.31, expected 0.0.

**Root cause:** Phase 3.5 (PR #7) changed the semantics of
`data["balances"][account_name][company]` from "direct snapshot rows
summed by account_name" to "own + descendants summed by
account_name" — the group-total aggregation feature. The Phase 3
test (`test_pivot.py::test_pivot_data_matches_snapshot`) still
asserts the old leaf-only semantic and was not updated when the
group aggregation feature shipped.

The Phase 3.5 PR did add a new test
(`test_pivot_filter::test_get_pivot_data_group_totals_match_descendants_recursively`)
that asserts the recursive invariant `balance[node] == own + sum(balance[child])`
which **does pass** on main and is the correct gold-standard for
current API semantics. Production behaviour is correct; only the
old Phase 3 test assertion is stale.

**How this slipped through the Phase 3.5 PR:** the Phase 3.5 work
ran only its own new test module (`bench run-tests --module
dux_groupview.dux_groupview.tests.test_pivot_filter`) instead of
the full app suite. `test_pivot.py` was not re-run, so the
assertion drift went unnoticed.

**Resolution path** (per Aditya's call):

1. Fix the Phase 3 test on a separate branch
   (`fix/pivot-test-phase35-semantics`) off `main`. Rewrite the
   assertion to leaf-only behaviour: `balance[leaf] == direct
   snapshot sum, no aggregation expected`. This complements the
   Phase 3.5 recursive invariant test — leaves and groups now have
   distinct invariant tests, so a future regression that
   accidentally bubbles leaf values up gets caught by the leaf-only
   test.
2. Open small PR; merge once Aditya approves the assertion change.
3. Rebase `phase-4-drills` onto new main.
4. Re-run the full app test suite (`bench run-tests --app
   dux_groupview`) — both Phase 3 leaf test and Phase 3.5 recursive
   test must pass.
5. Then resume commit 1 review.

Going forward in Phase 4: every commit's verification step uses
`bench run-tests --app dux_groupview` (full suite) rather than
single-module runs, so this class of drift cannot recur silently.

---

## Finding 1 — Card scope assignments

The v3.2 spec §4.7 proposes mapping each spotlight card to a
`subtree` scope rooted on a COA parent name. Inspection of RGI-DEMO's
actual COA shows this mapping is **wrong for several cards**. The
existing card definitions in `spotlight/cards.py` use predicate
matching (`by_account_type` or `by_root_type_and_name_pattern`) for
good reason — RGI's COA does not have single subtree parents that
cleanly cover the data each card aggregates.

### Per-card inspection

#### Sundry creditors

- v3.2 proposed: `subtree "Sundry Creditors"`
- COA reality: `account_name='Sundry Creditors'` exists exactly once
  as a **leaf** (`is_group=0`, `account_type=Payable`, root=Liability).
  No parent group with that name exists.
- Existing card: `match: {"by_account_type": "Payable"}`
- **Recommended Phase 4 scope:** **NOT subtree.** The card aggregates
  *all* Payable accounts across the COA, which numbers 249 leaf
  accounts (per Finding 2). `subtree` cannot express "every account
  with `account_type=Payable`". Two ways to support this:
  - **Option A:** extend `ScopeSpec` with a 4th type
    `{"type": "by_account_type", "value": "Payable"}` (or a list).
    Most faithful to existing semantics.
  - **Option B:** use `name_pattern: "%Sundry%"` plus restrict by
    root_type at the resolver level. Loses precision (won't catch
    Payable accounts not named "Sundry").
  - **Option C:** keep the existing `match` schema for cards and
    only ADD a `scope` field for the *drill open* path; the
    spotlight-cache aggregation continues to use `match` predicates.
    The card click translates `match: {by_account_type: "Payable"}`
    into a runtime scope by calling `_resolve_account_type_to_leaves`
    rather than going through `ScopeSpec`.

  My read: **Option C is cheapest** and preserves backward
  compatibility with Phase 2's spotlight cache. The drill API gets
  a list of pre-resolved leaf account names, identical to what the
  spotlight cache already uses for aggregation.

#### Sundry debtors

- v3.2 proposed: `subtree "Sundry Debtors"`
- COA reality: same shape as Sundry creditors. Single leaf account
  named "Sundry Debtors" with `account_type=Receivable`. No parent
  group.
- Existing card: `match: {"by_account_type": "Receivable"}`
- **Recommended:** Same as Sundry creditors — Option C.

#### Cash & bank

- v3.2 proposed: `subtree "Cash & Bank"`
- COA reality: **no matches at all** for `account_name LIKE
  '%cash%bank%'` or `'%cash and bank%'`. There is no "Cash & Bank"
  parent in the RGI COA.
- Existing card: `match: {"by_account_type": ["Bank", "Cash"]}`
- **Recommended:** Option C (predicate is `by_account_type IN
  ('Bank', 'Cash')`). `subtree` is impossible — there's nothing to
  walk. Note Finding 2 shows 13 `Bank`-typed leaves and 136
  `Cash`-typed leaves on this seed.

#### Unsecured loans

- v3.2 proposed: `subtree "Unsecured Loans"`
- COA reality:
  - `account_name='Unsecured Loans'`: 125 occurrences. **1 as group**
    (in one company, with the inter-co child accounts hanging off it
    — the Yola Stays / Purple Squirrel / etc. seen during Phase 3.5
    debugging) and **124 as leaves** (in 124 other companies, with
    direct balances).
  - `account_name='Unsecured Loans Payable'`: 1 leaf.
- Existing card: `match: {"by_root_type_and_name_pattern":
  {"root_type": "Liability", "name_pattern": "%Unsecured Loan%"}}`
- **Recommended:** This is the only card where a `subtree` scope
  *might* work for one company, but the asymmetry across companies
  (group in one, leaf in 124 others) means the existing
  `name_pattern` predicate is more robust. Option C again — keep the
  existing match shape; the drill resolver translates it into a
  list of leaf accounts.

#### Inter-co receivable

- v3.2 proposed: `subtree "Branch & Division"` (or actual COA name)
- COA reality (real candidates exist):
  - `account_name='Branch & Division'`: 1 group, root=Asset
  - `account_name='Branch / Divisions'`: 2 groups, root=Asset
  - `account_name='Inter-Company Receivables'`: 1 group, root=Asset
  - `account_name='Inter-company Receivable'`: 2 leaves, root=Asset
- Existing card: `match: {"by_root_type_and_name_pattern":
  {"root_type": "Asset", "name_pattern": "%Inter%Compan%"}}`
- **Recommended:** Option C — same reasoning. The existing pattern
  catches both "Inter-Company" and "Inter-company" variants
  uniformly across companies. A `subtree` scope on any one of these
  parents would miss accounts in companies that use a different
  parent name.

  *Phase 2 migration note:* Aditya's spec mentions an upcoming
  Phase 2 inter-co migration to "Inter-Company JV". Once that lands
  the existing `name_pattern` will need re-checking; that's a
  Phase 5+ concern.

#### Fixed deposits

- v3.2 proposed: `subtree "Fixed Deposits"`
- COA reality:
  - `account_name='Fixed Deposit With [ DTE ]'`: 1 group, root=Asset
  - `account_name='Fixed Deposits with Bank'`: 1 group, root=Asset
  - `account_name='Interest on Fixed Deposits'`: 123 leaves,
    **root=Income** (not Asset — would be wrongly matched by a naive
    name pattern without root_type filter)
- Existing card: `match: {"by_root_type_and_name_pattern":
  {"root_type": "Asset", "name_pattern": "%Fixed Deposit%"}}`
- **Recommended:** Option C. The existing predicate's `root_type`
  filter is essential — without it, the 123 Income-side
  "Interest on Fixed Deposits" leaves would be wrongly aggregated
  into the card's value. A `subtree` scope on either of the two
  Asset-side groups would only cover one company each.

### Summary recommendation: Option C

Rather than retrofit `ScopeSpec` with `account_type`-based matching,
**preserve the existing `cards.py` `match` schema** (`by_account_type`
and `by_root_type_and_name_pattern`) and have the card-click handler
in commit 7 resolve the predicate to a list of leaf account names at
click time. The account drill API can either:

- **C-1:** accept the resolved leaf list directly via a new
  `accounts: list[str]` parameter (bypasses `ScopeSpec`), OR
- **C-2:** accept a `ScopeSpec` of a new fourth type
  `{"type": "match_predicate", "value": <existing match dict>}` that
  the resolver translates server-side.

C-1 keeps `ScopeSpec` clean (still 3 types) and is simpler.
C-2 is more uniform but adds a fourth scope type the original spec
explicitly avoided.

This decision should land in commit 2 design, not commit 1. Commit 7
(spotlight card scope wiring) consumes whichever shape commit 2
exposes.

### Net impact on the spec

- §4.6 ScopeSpec schema: **stays at 3 types** (account, subtree,
  name_pattern) per spec.
- §4.7 spotlight card scope assignments: needs revision. None of the
  six cards cleanly map to `subtree`. Either an extra parameter
  (C-1) or an extra scope type (C-2) is required.
- §4.1 account drill API input: may need `accounts: list[str]`
  alongside `scope` if Option C-1 is chosen.
- This finding does NOT block commit 1 (which only writes the helper
  and amends rules). It is a commit 2 design input.

---

## Finding 2 — Q17 party-trackable account_type allow-list

### Raw data (RGI-DEMO seed)

`account_type` distribution and party-entry presence:

| account_type | accounts | party_entries | total_entries | party% |
|---|---:|---:|---:|---:|
| Payable | 249 | 50 | 128,042 | 0.04% |
| Receivable | 125 | 1 | 41,104 | 0.00% |
| Cash | 136 | 0 | 42,054 | 0.00% |
| Temporary | 125 | 0 | 41,110 | 0.00% |
| Asset Received But Not Billed | 124 | 0 | 86,604 | 0.00% |
| Stock Adjustment | 125 | 0 | 40,798 | 0.00% |
| Fixed Asset | 905 | 0 | 288,524 | 0.00% |
| Equity | 615 | 0 | 433,585 | 0.00% |
| Chargeable | 372 | 0 | 122,162 | 0.00% |
| Bank | 13 | 0 | 998 | 0.00% |
| Stock Received But Not Billed | 125 | 0 | 87,295 | 0.00% |
| Round Off | 124 | 0 | 41,150 | 0.00% |
| Income Account | 3 | 0 | 240 | 0.00% |
| Expenses Included In Asset Valuation | 123 | 0 | 40,704 | 0.00% |
| Cost of Goods Sold | 125 | 0 | 40,844 | 0.00% |
| Capital Work in Progress | 131 | 0 | 41,775 | 0.00% |
| Tax | 2,357 | 0 | 1,280,174 | 0.00% |
| Accumulated Depreciation | 125 | 0 | 40,808 | 0.00% |
| Stock | 125 | 0 | 40,993 | 0.00% |
| Expenses Included In Valuation | 124 | 0 | 40,519 | 0.00% |
| Depreciation | 125 | 0 | 41,058 | 0.00% |

### Interpretation

**The dev seed has essentially no party data.** Every account_type
shows < 0.05% party-entry density. Even the two types that should
universally have parties on a real ERPNext install — `Payable` and
`Receivable` — have only 50 and 1 party-entries respectively across
hundreds of thousands of GL rows.

This is a **synthetic-seed artefact**: the Phase 0 / Option A seed
generators (`seed_light.py`, `seed_production.py`,
`seed_rgi_named_data`) populate `tabGL Entry` with synthetic
balanced-pair vouchers, but they do **not populate the `party` /
`party_type` fields**. The result is GL rows that look "real enough"
for trial-balance aggregation but are useless for testing the party
drill.

### Implications for Phase 4 commit 2

The Q17 default proposal `('Receivable', 'Payable', 'Loan')` cannot
be empirically validated on this seed, because the seed never tags
GL rows with parties under any account_type. The default is still
defensible based on ERPNext convention:

- **Receivable** — accounts where Customer parties are universally
  set (per ERPNext's Customer doctype linkage)
- **Payable** — same for Suppliers
- **Loan** — Loan account_type is used in Lending module; parties
  there are Borrowers / Loan Applicants

Three options for Phase 4 commit 2:

1. **Ship with the default allow-list `('Receivable', 'Payable', 'Loan')`**
   based on convention; document that party drill cannot be tested
   end-to-end on dev seed and defer real validation to commit 8
   (browser review on RGI-DEMO with a party-rich preview seed) and
   the eventual production rollout.
2. **Extend a seed generator** to populate `party` / `party_type` on
   the synthetic GL rows (would require a list of synthetic
   suppliers/customers; could randomly assign across the 50 created
   by seed). Adds Phase 0 / Option A scope.
3. **Construct a small fixture** in `tests/fixtures/` with a handful
   of accounts (Payable, Receivable, Loan, Bank, Equity) and 5-10
   parties with hand-rolled GL entries, used only by
   `test_party_drill_api.py` for unit-test correctness. Keeps the
   main seed generators unchanged.

**My recommendation: option 3.** The party drill's correctness can
be tested against a small deterministic fixture without bloating the
seed generator. End-to-end browser verification on a party-rich
seed (option 2 as a test-only addendum, optional) can be deferred to
commit 8.

### Final allow-list

Lock in `('Receivable', 'Payable', 'Loan')` for commit 2. Defer
empirical confirmation against real RGI production data to the
post-merge production-deployment audit (own ticket, post Phase 4
close).

---

## Finding 3 — Group company name match

### Spot-check results

| Sample name (from `tabCompany`) | Customer record? | Supplier record? |
|---|---|---|
| `GH Raisoni College Of Engineering` | no | no |
| `GH Raisoni College Of Engineering And Management Nagpur` | no | no |
| `GH Raisoni College Of Business Management` | no | no |
| `Ankush Shikshan Sanstha Society` | no | no |
| `GHRCE` (abbreviation) | no | no |
| `GHRCEMN` (abbreviation) | no | no |
| `GHRCEMA` (abbreviation) | no | no |

### Broader checks

- "Any party in `tabGL Entry` whose name matches a `tabCompany`
  name?" → **none**.
- "Customer / Supplier records whose name appears in `tabCompany`?"
  → **none**.

### Interpretation

**On the dev seed, no group company is mirrored as a Customer or
Supplier.** The `is_group_company` flag computation specified in
spec §4.2 (intersect `party` against the `tabCompany` name list)
will return False for every row on this seed, because there are no
matching parties.

This is a **second symptom of the same synthetic-seed-has-no-party-
data problem** flagged in Finding 2. The flag's logic is correct;
its testability on dev is non-existent.

### Implications for Phase 4 commit 2

- The `is_group_company` Python computation can be implemented and
  unit-tested via fixture (option 3 from Finding 2 covers this — the
  fixture would include a Customer record whose name matches a
  Company record, and a Supplier whose name does not, asserting the
  flag returns True / False correctly).
- The **exact-match assumption** in §4.2 ("Customer / Supplier
  record name matches the Company record name exactly") **cannot be
  confirmed against real data on dev**. It must be confirmed against
  RGI production data or a representative subset before the party
  drill ships.
- If on production a group company is mirrored as `GHRCE` (the
  abbreviation) while `tabCompany.name` is the full name, the flag
  silently misses every real group-co party.
- **Commit 2 must include the exact-match verification task** the
  spec already calls out (§4.2 "is_group_company verification" — a
  spot-check against 2-3 known group-company customer/supplier
  names). On dev, that spot-check returns "no match across the
  board" — the verification itself works; the data simply doesn't
  exercise it.

### Recommendation

Defer the production-data exact-match check to either:

- A dry-run on a Frappe Cloud staging clone of RGI prod (Q2 in
  OPEN_QUESTIONS.md is open about provisioning this), OR
- The first deployment to production, with a post-deploy validation
  step that reports any group company without a matching
  Customer/Supplier.

Implementation of the flag can proceed; testing is fixture-based
until production validation is possible.

---

## Summary checklist for commit 1

- [x] Spec saved at `specs/phase-4-drills.md` (autolink artefacts
      cleaned)
- [x] CLAUDE.md amendment added under rule 1 (new "Phase 4 amendment
      — drill reads" sub-section, verbatim from spec §3, no other
      rule touched)
- [x] `_walk_subtree_leaves` written fresh in
      `dux_groupview/dux_groupview/api/utils.py` (Note A — nothing
      to extract from `pivot.py`)
- [x] Phase 3 gold-standard pivot test status captured (Note B —
      pre-existing failure on main, fix path agreed: separate
      `fix/pivot-test-phase35-semantics` branch + PR before commit 1
      review)
- [x] Finding 1: card scope assignments inspected; recommendation
      Option C (preserve existing `match` schema, resolve to leaf
      list at click time)
- [x] Finding 2: Q17 allow-list defaults to
      `('Receivable', 'Payable', 'Loan')` on convention; empirical
      validation deferred (synthetic seed has no party data)
- [x] Finding 3: group-co name match cannot be tested on dev seed;
      exact-match verification deferred to production rollout
- [ ] `cards.py` not modified (commit 7's job)
- [ ] PHASE_LOG.md not modified (commit 10's job)
- [ ] No commits made on this branch yet (per prompt)

The pre-existing test failure on main is the one new blocker. Once
the `fix/pivot-test-phase35-semantics` PR merges and `phase-4-drills`
is rebased onto new main, commit 1 review can proceed.
