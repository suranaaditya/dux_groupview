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
    }

`match` keys (start with two; Phase 5 adds more):

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
]


def by_id():
	"""Return CARDS keyed by id for O(1) lookup."""
	return {c["id"]: c for c in CARDS}
