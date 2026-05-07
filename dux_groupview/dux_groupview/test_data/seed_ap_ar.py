"""Pure data templates for the AP/AR seed augmentation.

This module is data-only (no logic, no Frappe imports). It's
imported by both the generator (for the actual seed) and the unit
tests (for distribution-shape assertions). Keeping the data flat lets
tests verify properties like the Pareto split without spinning up a
DB.

Naming conventions:
- SUPPLIER_TEMPLATES / CUSTOMER_TEMPLATES — list-of-dict, one row
  per party. Each row has its tier and (display-only) category. The
  generator picks the actual balance from BALANCE_TIERS using `tier`.
- BALANCE_TIERS — per-tier (min, max) pair in rupees. Top suppliers
  have outlier-tier ranges (₹50L-₹1Cr); see Pareto distribution note
  in seed_ap_ar_generator.py.
- TRANSACTION_VOLUMES — per-tier (min, max) transaction count range.

Reordering the lists changes which parties land in which tier — keep
top-tier suppliers/customers at the top of the list so the test
assertions on rank-order remain stable.
"""

# ---------------------------------------------------------------------------
# Tier definitions (balance ranges in rupees, tx count ranges)
# ---------------------------------------------------------------------------

BALANCE_TIERS = {
	# Suppliers
	"sup_outlier": (5_000_000,  10_000_000),  # ₹50L - ₹1Cr  (4 parties)
	"sup_top":     (1_500_000,   5_000_000),  # ₹15L - ₹50L  (6 parties)
	"sup_mid":       (200_000,   1_000_000),  # ₹2L  - ₹10L  (20 parties)
	"sup_bottom":     (20_000,     200_000),  # ₹20K - ₹2L   (20 parties)
	# Customers
	"cust_top":    (2_000_000,   8_000_000),  # ₹20L - ₹80L  (5 parties)
	"cust_mid":      (200_000,   1_500_000),  # ₹2L  - ₹15L  (15 parties)
	"cust_bottom":    (50_000,     200_000),  # ₹50K - ₹2L   (10 parties)
}

TRANSACTION_VOLUMES = {
	# Suppliers
	"sup_outlier": (40, 80),   # higher tx count for outliers (active accounts)
	"sup_top":     (30, 60),
	"sup_mid":     (10, 30),
	"sup_bottom":   (3, 10),
	# Customers
	"cust_top":    (20, 50),
	"cust_mid":     (5, 20),
	"cust_bottom":  (2,  8),
}

# Per-supplier company-affiliation count weights. Most suppliers serve
# 1-2 companies; a few serve up to 5 (the "shared across the trust"
# vendors mentioned in the spec).
SUPPLIER_COMPANY_COUNT_WEIGHTS = [
	(1, 35),  # 35% serve only one company (campus-specific vendor)
	(2, 30),  # 30% serve two
	(3, 20),
	(4, 10),
	(5,  5),  # rare — region-wide vendor
]

CUSTOMER_COMPANY_COUNT_WEIGHTS = [
	(1, 40),
	(2, 30),
	(3, 20),
	(4,  7),
	(5,  3),
]


# ---------------------------------------------------------------------------
# Suppliers — 50 entries, 4 outliers + 6 top + 20 mid + 20 bottom
# ---------------------------------------------------------------------------

SUPPLIER_TEMPLATES = [
	# --- Outliers (4) — ₹50L-₹1Cr each ---------------------------------
	{"name": "Bhandari Hardware",          "tier": "sup_outlier", "category": "hardware"},
	{"name": "Sun Infotech Solutions",     "tier": "sup_outlier", "category": "it"},
	{"name": "Pune Chemicals Ltd",         "tier": "sup_outlier", "category": "chemicals"},
	{"name": "Mahesh Caterers",            "tier": "sup_outlier", "category": "catering"},

	# --- Top (6) — ₹15L-₹50L each ---------------------------------------
	{"name": "Asha Stationers Pvt Ltd",    "tier": "sup_top",     "category": "stationers"},
	{"name": "Vidarbha Office Supplies",   "tier": "sup_top",     "category": "stationers"},
	{"name": "Bhandari Printers",          "tier": "sup_top",     "category": "press"},
	{"name": "Ishan Computers",            "tier": "sup_top",     "category": "it"},
	{"name": "Nagpur Electricals",         "tier": "sup_top",     "category": "electrical"},
	{"name": "Ramdas Maintenance Services","tier": "sup_top",     "category": "maintenance"},

	# --- Mid (20) — ₹2L-₹10L each ---------------------------------------
	{"name": "Maharashtra Books and Co",   "tier": "sup_mid",     "category": "stationers"},
	{"name": "Nagpur Stationers Pvt Ltd",  "tier": "sup_mid",     "category": "stationers"},
	{"name": "Saraswati Stationery",       "tier": "sup_mid",     "category": "stationers"},
	{"name": "Gulab Hardware",             "tier": "sup_mid",     "category": "hardware"},
	{"name": "Pune Hardware Mart",         "tier": "sup_mid",     "category": "hardware"},
	{"name": "Marathwada Hardware",        "tier": "sup_mid",     "category": "hardware"},
	{"name": "Vidya Bookbinding",          "tier": "sup_mid",     "category": "press"},
	{"name": "Saraswati Press",            "tier": "sup_mid",     "category": "press"},
	{"name": "Annapurna Press",            "tier": "sup_mid",     "category": "press"},
	{"name": "DataLink IT Services",       "tier": "sup_mid",     "category": "it"},
	{"name": "TechVidya Solutions",        "tier": "sup_mid",     "category": "it"},
	{"name": "Annapurna Mess Services",    "tier": "sup_mid",     "category": "catering"},
	{"name": "Quick Fix Plumbing",         "tier": "sup_mid",     "category": "maintenance"},
	{"name": "Pune Maintenance Co",        "tier": "sup_mid",     "category": "maintenance"},
	{"name": "BrightLite Distributors",    "tier": "sup_mid",     "category": "electrical"},
	{"name": "Vidarbha Electric Co",       "tier": "sup_mid",     "category": "electrical"},
	{"name": "Vidarbha Lab Supplies",      "tier": "sup_mid",     "category": "chemicals"},
	{"name": "Maharashtra Chemicals",      "tier": "sup_mid",     "category": "chemicals"},
	{"name": "Royal Decorators",           "tier": "sup_mid",     "category": "misc"},
	{"name": "Krishna Transport",          "tier": "sup_mid",     "category": "misc"},

	# --- Bottom (20) — ₹20K-₹2L each ------------------------------------
	{"name": "Vidya Stationers",           "tier": "sup_bottom",  "category": "stationers"},
	{"name": "Pune Paper Mart",            "tier": "sup_bottom",  "category": "stationers"},
	{"name": "Vidarbha Hardware Stores",   "tier": "sup_bottom",  "category": "hardware"},
	{"name": "Shree Hardware",             "tier": "sup_bottom",  "category": "hardware"},
	{"name": "Shri Sai Printers",          "tier": "sup_bottom",  "category": "press"},
	{"name": "Pune Computer Services",     "tier": "sup_bottom",  "category": "it"},
	{"name": "Maharashtra IT Hub",         "tier": "sup_bottom",  "category": "it"},
	{"name": "Shri Sai Catering",          "tier": "sup_bottom",  "category": "catering"},
	{"name": "Vidarbha Mess Services",     "tier": "sup_bottom",  "category": "catering"},
	{"name": "Reliable Repairs",           "tier": "sup_bottom",  "category": "maintenance"},
	{"name": "Shri Krishna Maintenance",   "tier": "sup_bottom",  "category": "maintenance"},
	{"name": "Maharashtra Electric Supplies","tier":"sup_bottom", "category": "electrical"},
	{"name": "Pune Electric Trading",      "tier": "sup_bottom",  "category": "electrical"},
	{"name": "Shree Lab Equipment",        "tier": "sup_bottom",  "category": "chemicals"},
	{"name": "Nagpur Chemicals Pvt Ltd",   "tier": "sup_bottom",  "category": "chemicals"},
	{"name": "Shri Sai Decorators",        "tier": "sup_bottom",  "category": "misc"},
	{"name": "Vidarbha Transport",         "tier": "sup_bottom",  "category": "misc"},
	{"name": "Pune Decorators",            "tier": "sup_bottom",  "category": "misc"},
	{"name": "Nagpur Couriers",            "tier": "sup_bottom",  "category": "misc"},
	{"name": "Marathwada Logistics",       "tier": "sup_bottom",  "category": "misc"},
]


# ---------------------------------------------------------------------------
# Customers — 30 entries, 5 top + 15 mid + 10 bottom
# ---------------------------------------------------------------------------

CUSTOMER_TEMPLATES = [
	# --- Top (5) — ₹20L-₹80L each ---------------------------------------
	{"name": "Maharashtra State Council of Education", "tier": "cust_top", "category": "institutional"},
	{"name": "Tata Steel Foundation",                  "tier": "cust_top", "category": "corporate"},
	{"name": "Reliance Foundation",                    "tier": "cust_top", "category": "corporate"},
	{"name": "Government of Maharashtra Education Dept", "tier": "cust_top", "category": "government"},
	{"name": "AICTE Regional Office",                  "tier": "cust_top", "category": "institutional"},

	# --- Mid (15) — ₹2L-₹15L each ---------------------------------------
	{"name": "Mahindra Education Trust",               "tier": "cust_mid", "category": "corporate"},
	{"name": "Cipla Skill Initiatives",                "tier": "cust_mid", "category": "corporate"},
	{"name": "Wipro Cares Trust",                      "tier": "cust_mid", "category": "corporate"},
	{"name": "Infosys Foundation",                     "tier": "cust_mid", "category": "corporate"},
	{"name": "Bajaj Skills Initiative",                "tier": "cust_mid", "category": "corporate"},
	{"name": "L and T Educational Foundation",         "tier": "cust_mid", "category": "corporate"},
	{"name": "DTE Maharashtra",                        "tier": "cust_mid", "category": "government"},
	{"name": "UGC Western Regional Office",            "tier": "cust_mid", "category": "institutional"},
	{"name": "Council of Architecture",                "tier": "cust_mid", "category": "institutional"},
	{"name": "MSBTE Regional Cell",                    "tier": "cust_mid", "category": "institutional"},
	{"name": "All India Council for Technical Education","tier":"cust_mid", "category": "institutional"},
	{"name": "Bharat Conference Solutions",            "tier": "cust_mid", "category": "events"},
	{"name": "EduSummit Mumbai",                       "tier": "cust_mid", "category": "events"},
	{"name": "Pune Convention Bureau",                 "tier": "cust_mid", "category": "events"},
	{"name": "Maharashtra Higher Education Dept",      "tier": "cust_mid", "category": "government"},

	# --- Bottom (10) — ₹50K-₹2L each ------------------------------------
	{"name": "GoI Skill Development Mission",          "tier": "cust_bottom", "category": "government"},
	{"name": "Department of School Education GoM",     "tier": "cust_bottom", "category": "government"},
	{"name": "Pune District Education Office",         "tier": "cust_bottom", "category": "government"},
	{"name": "NAAC Regional Office",                   "tier": "cust_bottom", "category": "institutional"},
	{"name": "NCTE Western Office",                    "tier": "cust_bottom", "category": "institutional"},
	{"name": "Mumbai Tech Summit Pvt Ltd",             "tier": "cust_bottom", "category": "events"},
	{"name": "Vidarbha Education Expo",                "tier": "cust_bottom", "category": "events"},
	{"name": "Ramesh and Co Consultants",              "tier": "cust_bottom", "category": "misc"},
	{"name": "Vidya Knowledge Services",               "tier": "cust_bottom", "category": "misc"},
	{"name": "Bharat Education Consultants",           "tier": "cust_bottom", "category": "misc"},
]
