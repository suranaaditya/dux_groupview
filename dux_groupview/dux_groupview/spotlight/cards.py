"""Hardcoded card definitions for Phase 2.

Phase 5 will move these into a `DGV Spotlight Card` doctype with a UI
editor. For now they live in code so the cockpit can ship without an
editor and so the refresh function has a deterministic source of truth.

Each card definition is a dict with the following shape:

    {
        "id":           str          stable identifier
        "label":        str          shown on the card
        "match":        dict         account matching strategy
        "polarity":    "good_up" | "bad_up" | "neutral"
        "format":      "crore" | "lakh" | "auto"
        "color":        str (hex)
        "disabled":     bool         optional, default False. When True,
                                     the card is HIDDEN from the cockpit
                                     grid + the headline composer, but
                                     its cache rows are still written
                                     by `refresh_spotlight_cache` so the
                                     sparkline history is preserved if
                                     the card is later re-enabled.
                                     `cards_v1.resolve_match_to_accounts`
                                     still resolves disabled cards'
                                     predicates so bookmarked deep-links
                                     to the drill panel continue to work.
        "display_sign": str          optional, default "natural". Final
                                     sign transform applied to the
                                     aggregated card value before it is
                                     written to `tabDGV Spotlight Cache`
                                     AND before it is returned by the
                                     cockpit's filtered-spotlight path
                                     (i.e. applied inside `_aggregate`
                                     itself, so every read path sees the
                                     transformed number). Three values:
                                       - "natural" (default): no change.
                                       - "absolute": abs(value). Use
                                         when the underlying sign is
                                         semantically wrong for the
                                         card's surface direction
                                         (e.g. `supplier_advances` --
                                         a debit balance on a Payable
                                         account flips negative through
                                         the natural-side CASE, but
                                         "advances paid" is conceptually
                                         a positive concept).
                                       - "negated": -value. Included
                                         for completeness; no card uses
                                         it today.
                                     Caveat on delta math: the prior-
                                     month value and every sparkline
                                     point are run through the same
                                     transform, so deltas remain
                                     internally consistent. A card that
                                     flips sign month-over-month under
                                     "absolute" will show a delta on
                                     the absolute axis (e.g. -2 -> -3
                                     becomes 2 -> 3, delta +1) which
                                     may not match the underlying
                                     directional change. None of the
                                     cards that use "absolute" today
                                     are expected to cross zero in
                                     practice.
                                     Sparkline implication: changing
                                     `display_sign` between deploys
                                     produces a discontinuity at the
                                     deploy date in the cached
                                     sparkline -- new cache rows write
                                     transformed values while older
                                     rows persist with the old
                                     transform. This is intended;
                                     refresh re-computes the sparkline
                                     window on every run so the
                                     discontinuity rolls off naturally.
                                     Invalid value -> log warning and
                                     treat as "natural" (refresh does
                                     not crash).
    }

`match` keys (three supported; both `by_account_type` and
`by_parent_account_stem_in` also accept optional `balance_sign` and
`exclude_parent_stems` sub-keys as of the supplier-advances split and
supplier-advances display-and-exclude fix respectively):

* `by_account_type`:
    - Three input shapes; first two are shortcuts for the third:
      - `"Payable"` (string)                        -> single account_type
      - `["Bank", "Cash"]` (list)                   -> multiple account_types
      - `{"account_type": <str|list>,
         "balance_sign": <"positive"|"negative"|"any">,
         "exclude_parent_stems": <list[str]>}` (dict, canonical)
    - `balance_sign` is OPTIONAL and defaults to "any" (no sign filter,
      same as the legacy string/list shapes). When set to "positive"
      or "negative", the predicate filters leaves to those whose
      LATEST-snapshot raw `balance` column has the matching sign.
      Raw balance is debit-credit, pre-natural-side-flip:
        - Payable / Liability accounts:
            balance < 0 = credit balance = company owes (default)
            balance > 0 = debit balance  = advance paid to supplier
        - Receivable / Asset accounts:
            balance > 0 = debit balance  = customer owes (default)
            balance < 0 = credit balance = advance received from customer
      Sign semantics route through the snapshot, NOT the COA
      structure. A leaf whose balance flips sign between snapshots
      will appear/disappear from the leaf set across refreshes -- this
      is intended (the card surface flips the same way).
    - `exclude_parent_stems` is OPTIONAL (dict shape only -- shortcut
      string/list shapes do NOT accept this key). When set to a
      non-empty list of stem names, excludes leaves whose immediate
      parent group's account_name stem (the part BEFORE the first
      ` - ` separator in `parent_account`) is in the list. Used to
      carve out incidentally-included parents that another card
      already owns -- e.g. `sundry_creditors` excludes the
      `Unsecured Loans` parent stem because Payable-tagged loan
      leaves living under that parent are correctly attributed to the
      `unsecured_loans` card via `by_parent_account_stem_in`.
      Defensive: empty list / missing key / non-list value / list with
      non-string elements -> no-op (predicate behaves as today, no
      exclusion clause emitted, no error).
  Examples:
    `{"by_account_type": "Payable"}`                   -- legacy shortcut, no sign
    `{"by_account_type": ["Bank", "Cash"]}`            -- legacy shortcut, no sign
    `{"by_account_type": {"account_type": "Payable",   -- canonical, with sign
                          "balance_sign": "negative",  --   and parent-stem
                          "exclude_parent_stems":      --   exclusion
                              ["Unsecured Loans"]}}`

* `by_root_type_and_name_pattern`:
    - filters on `root_type`, then matches `account` name with LIKE.
  Example: `{"by_root_type_and_name_pattern": {"root_type": "Asset",
            "name_pattern": "%Inter%Compan%"}}`.

* `by_parent_account_stem_in`:
    - matches leaves whose immediate parent group's account_name stem
      (the part BEFORE the first ` - ` separator in `parent_account`)
      is in a supplied list AND `root_type` matches a supplied value
      AND `is_group = 0`. Used by the Liquid cash + Secured loans
      cards to express predicate-disjoint matching against an
      ERPNext-standard COA structure.
    - Also accepts the same optional `balance_sign` sub-key as
      `by_account_type` (same semantics, same snapshot consultation).
      Not currently consumed by any card in this PR; added for
      forward consistency / OD-positive future work.
    - Also accepts the same optional `exclude_parent_stems` sub-key as
      `by_account_type`. Layered on top of the inclusion list: a leaf
      whose parent stem appears in BOTH `stems` and
      `exclude_parent_stems` is excluded (defensive against
      misconfigured overlap; the exclusion wins). Defensive shape
      identical to `by_account_type` -- empty / missing / non-list /
      non-string elements -> no-op, no error.
  Example: `{"by_parent_account_stem_in":
              {"stems": ["Bank Accounts", "Cash in Hand"],
               "root_type": "Asset"}}`.
"""

CARDS = [
	{
		"id": "sundry_creditors",
		"label": "Sundry creditors",
		"match": {
			# Predicate split (spec/supplier-advances-split): the old
			# `by_account_type: "Payable"` summed BOTH credit balances
			# (money owed) AND debit balances (advances paid to
			# suppliers) into a single net figure. Production diagnostic
			# showed a net of ~Rs 37.79 Cr hiding ~Rs 2.52 Cr of paid-
			# ahead advances. Splitting into gross-owed (this card) and
			# gross-advances (new `supplier_advances` card).
			#
			# `balance_sign: negative` => raw balance < 0 = credit
			# balance = actually owed to the supplier (the natural
			# "creditors" semantic). The CASE flip in _aggregate then
			# turns this into a positive natural-side total for the
			# card display.
			#
			# Sparkline discontinuity at deploy: pre-PR points were
			# net values; post-PR points are gross-owed. The
			# transition will look like a small jump on most companies
			# (~7% on prod baseline, plus the variance from the
			# previously-netted advances).
			"by_account_type": {
				"account_type": "Payable",
				"balance_sign": "negative",
				# Excludes Payable-tagged leaves living under the
				# `Unsecured Loans` parent group. 3 such leaves on
				# production (~Rs 0.25 Cr) were previously double-
				# counted between this card and `unsecured_loans`;
				# the dedicated card catches them via
				# `by_parent_account_stem_in` so removing them here
				# eliminates the overlap. Net effect at deploy:
				# `sundry_creditors` drops by the previously-double-
				# counted total; the sparkline shows a small step
				# down at deploy date. Pre-warned in the PR.
				"exclude_parent_stems": ["Unsecured Loans"],
			},
		},
		"polarity": "neutral",
		"format": "crore",
		"color": "#BA7517",
	},
	{
		"id": "sundry_debtors",
		"label": "Sundry debtors",
		"match": {"by_account_type": "Receivable"},
		"polarity": "bad_up",
		"format": "crore",
		"color": "#3B6D11",
		# Disabled pending a future predicate fix; cache rows still
		# refreshed for history continuity (spec §5.2).
		"disabled": True,
	},
	{
		"id": "unsecured_loans",
		"label": "Unsecured loans",
		"match": {
			# Switched from `by_root_type_and_name_pattern` (the old
			# `%Unsecured Loan%` LIKE) to `by_parent_account_stem_in`
			# because the name-pattern only matched leaves whose own
			# `account` name happened to contain "Unsecured Loan" --
			# 5 incidentally-named accounts on production, totalling
			# ~-Rs 0.52 Cr. The actual unsecured loan leaves live
			# under a parent group named `Unsecured Loans` across 68
			# production companies (1,649 leaves total, 142 with
			# non-zero balance, ~-Rs 202.78 Cr).
			#
			# Card id `unsecured_loans` is retained so existing
			# DGV Spotlight Cache history rows stay attached; the
			# sparkline will show a discontinuity at deploy as the
			# new (correct) value lands on top of historical (wrong)
			# values. Pre-warned in the PR description.
			#
			# Known gap: 31 suppliers in the "Unsecured Loan Lenders"
			# supplier_group currently post to leaves under other
			# parents (mostly "Current Liabilities") and are NOT
			# captured here. To be resolved via supplier-side
			# `default_payable_account` configuration on prod by
			# Aditya, not code. Once that data converges, no further
			# code change is needed -- the parent-stem predicate
			# catches everything by construction.
			"by_parent_account_stem_in": {
				"stems": ["Unsecured Loans"],
				"root_type": "Liability",
			},
		},
		"polarity": "neutral",
		"format": "crore",
		"color": "#5F5E5A",
	},
	{
		"id": "cash_and_bank",
		"label": "Cash & bank",
		"match": {"by_account_type": ["Bank", "Cash"]},
		"polarity": "good_up",
		"format": "crore",
		"color": "#185FA5",
		# Replaced by `liquid_cash` (Bank Accounts + Cash in Hand,
		# parent-stem-filtered to exclude bank-loan accounts) and
		# `secured_loans` (Secured Loans + Bank OD A/c). Predicate kept
		# unchanged + cache continues to refresh so the historical
		# sparkline survives the split (spec §5.2 + §6).
		"disabled": True,
	},
	{
		"id": "inter_co_receivable",
		"label": "Inter-co receivable",
		"match": {
			"by_root_type_and_name_pattern": {
				"root_type": "Asset",
				"name_pattern": "%Inter%Compan%",
			},
		},
		"polarity": "neutral",
		"format": "crore",
		"color": "#534AB7",
		# Disabled pending a future predicate fix; cache rows still
		# refreshed for history continuity (spec §5.2).
		"disabled": True,
	},
	{
		"id": "fixed_deposits",
		"label": "Fixed deposits",
		"match": {
			"by_root_type_and_name_pattern": {
				"root_type": "Asset",
				"name_pattern": "%Fixed Deposit%",
			},
		},
		"polarity": "good_up",
		"format": "crore",
		"color": "#534AB7",
	},
	{
		"id": "financial_exp_to_bank",
		"label": "Financial Exp — Bank",
		"match": {
			"by_root_type_and_name_pattern": {
				"root_type": "Expense",
				"name_pattern": "%Financial Exp To Bank%",
			},
		},
		"polarity": "bad_up",
		"format": "crore",
		"color": "#A33B3B",
	},
	{
		"id": "financial_exp_to_other",
		"label": "Financial Exp — Other",
		"match": {
			"by_root_type_and_name_pattern": {
				"root_type": "Expense",
				"name_pattern": "%Financial Exp To Other%",
			},
		},
		"polarity": "bad_up",
		"format": "crore",
		"color": "#C46A1F",
	},
	{
		"id": "liquid_cash",
		"label": "Liquid cash",
		"match": {
			"by_parent_account_stem_in": {
				# Both case variants are enumerated explicitly so the
				# predicate matches without relying on the database's
				# default collation. Dev seeds use "Cash in Hand"
				# (lowercase 'in'); production COA uses "Cash In Hand"
				# (capital 'I'). DO NOT collapse to one entry on a
				# "dedup obvious duplicates" cleanup -- the second
				# entry is load-bearing for production. A Phase 5
				# cards-editor "fuzzy stem matching" feature
				# (case-insensitive + trailing-s tolerant) would
				# eliminate the need for manual enumeration. See
				# spec/cash-bank-card-split.md §1 "Known limitations".
				"stems": ["Bank Accounts", "Cash in Hand", "Cash In Hand"],
				"root_type": "Asset",
			},
		},
		"polarity": "good_up",
		"format": "crore",
		# Reuses the old `cash_and_bank` blue (`#185FA5`) so users who
		# learned that color carry the visual continuity onto the
		# replacement card.
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
	{
		"id": "supplier_advances",
		"label": "Supplier advances",
		"match": {
			# Companion to `sundry_creditors`. Same predicate (Payable
			# account_type) but filtered to leaves with debit balances
			# = advances paid to suppliers ahead of invoices arriving.
			# Sign convention: raw balance > 0 on a Payable account is
			# a debit balance (atypical state), representing money the
			# supplier holds in advance against future invoices.
			#
			# Polarity: `bad_up`. Advances rising means more working
			# capital is parked with suppliers; from an owner's
			# perspective that's a deterioration of cash position,
			# even though individual advances may be legitimate.
			"by_account_type": {
				"account_type": "Payable",
				"balance_sign": "positive",
				# Same exclusion rationale as `sundry_creditors`:
				# Payable-tagged leaves under the `Unsecured Loans`
				# parent group are owned by the `unsecured_loans`
				# card. Without this filter, a debit-balance leaf
				# named e.g. `Unsecured Loans Payable - <abbr>`
				# would surface in BOTH cards.
				"exclude_parent_stems": ["Unsecured Loans"],
			},
		},
		"polarity": "bad_up",
		"format": "crore",
		# Rose-mauve. Distinct from the existing palette (creditors
		# brown #BA7517, debtors green #3B6D11, financial-exp red
		# #A33B3B, etc.). Sits adjacent to creditors visually (warm
		# tones for AP-adjacent concepts) without competing with it.
		"color": "#A85B8C",
		# The per-row natural-side CASE flip in `_aggregate` produces
		# a negative number for a debit-balance Payable leaf (Payable
		# is a Liability root_type and so flips). Semantically,
		# "advances paid ahead" is a positive concept -- an owner
		# wants to see "we have Rs 2.5 Cr parked with suppliers", not
		# "minus 2.5 Cr". `display_sign: "absolute"` applies a final
		# magnitude transform on the aggregated value before it lands
		# in the cache (and before the cockpit's filtered-scope path
		# returns it). Doesn't touch the underlying refresh-path sign
		# convention -- that's a separate architectural issue
		# deferred to Phase 5.
		"display_sign": "absolute",
	},
]


def by_id():
	"""Return CARDS keyed by id for O(1) lookup."""
	return {c["id"]: c for c in CARDS}
