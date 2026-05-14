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
    }

`match` keys (three supported as of the cash & bank card split, spec
`specs/cash-bank-card-split.md`):

* `by_account_type`:
    - value can be a string or a list of strings.
    - Single string: `WHERE account_type = X`.
    - List: `WHERE account_type IN (...)`.
  Example: `{"by_account_type": "Payable"}` or
  `{"by_account_type": ["Bank", "Cash"]}`.

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
  Example: `{"by_parent_account_stem_in":
              {"stems": ["Bank Accounts", "Cash in Hand"],
               "root_type": "Asset"}}`.
"""

CARDS = [
	{
		"id": "sundry_creditors",
		"label": "Sundry creditors",
		"match": {"by_account_type": "Payable"},
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
			"by_root_type_and_name_pattern": {
				"root_type": "Liability",
				"name_pattern": "%Unsecured Loan%",
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
]


def by_id():
	"""Return CARDS keyed by id for O(1) lookup."""
	return {c["id"]: c for c in CARDS}
