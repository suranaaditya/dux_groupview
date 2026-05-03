"""Hardcoded trust group definitions for Phase 3.

10 RGI trusts plus a synthetic `default` trust that captures any
company not explicitly listed. Phase 5 will lift these into a
`DGV Cockpit Settings` doctype with a UI editor. For now the data
lives in code so the pivot can ship without an editor.

Each trust definition has:

    {
        "id":        str          stable identifier (lowercase)
        "name":      str          full human-readable name
        "abbr":      str          short tag for UI badges / column headers
        "color":     str          hex string, used for column tinting + heatmap
        "companies": list[str]    exact tabCompany names belonging to this trust
    }

Company names are matched case-sensitively against `tabCompany.name`.
Real RGI company names won't match anything on the dev site (the seed
uses "Test Company A"-"Test Company E"). On production they should
match exactly; if not, the company falls to the `default` trust and
Aditya should add it explicitly to the relevant trust.
"""

TRUSTS = [
	{
		"id": "ass",
		"name": "Ankush Shikshan Sanstha",
		"abbr": "ASS",
		"color": "#1D9E75",
		"companies": [
			"Ankush Shikshan Sanstha Society",
			"GH Raisoni College Of Engineering",
			"GHRCE — MBA",
			"Ankush Shikshan Sanstha COE",
			"Ankush Shikshan Sanstha Hostel",
			"Ankush Shikshan Sanstha Admission",
			"GH Raisoni College Of Engineering And Management Nagpur",
			"GHRCEMN — MBA",
			"GHRCEMN — MCA",
			"GHRCEMN — Diploma",
			"GHRCEMN — Phase II",
			"ASS Boys Hostel Shraddha Park",
			"ASS Girls Hostel Shraddha Park",
			"GH Raisoni Institute Of Life Science",
			"ASS For GHRCEM",
			"GH Raisoni Law College",
		],
	},
	{
		"id": "ghremf",
		"name": "GH Raisoni Educational And Medical Foundation",
		"abbr": "GHREMF",
		"color": "#534AB7",
		"companies": [
			"GH Raisoni College Of Engineering And Management Pune",
			"GHRCEM Pune — MBA",
			"GHRCEM Pune — MCA",
			"GHR CACS Pune",
			"GH Raisoni Public School Pune",
			"GH Raisoni Junior College Pune",
			"GHREMF Society Pune",
			"GHREMF Society Nagpur",
		],
	},
	{
		"id": "ghref",
		"name": "GH Raisoni Education Foundation Jalgaon",
		"abbr": "GHREF",
		"color": "#BA7517",
		"companies": [
			"GH Raisoni College Of Engineering And Management Jalgaon",
			"GHRCEMJ — Phase II",
			"GH Raisoni Public School Jalgaon",
			"GH Raisoni Junior College Jalgaon",
			"GHREF Society Jalgaon",
			"GHREF Society Nagpur",
			"Sadabai Raisoni Womens College",
		],
	},
	{
		"id": "ghrf",
		"name": "GH Raisoni Foundation",
		"abbr": "GHRF",
		"color": "#D85A30",
		"companies": [
			"GH Raisoni College Of Business Management",
			"GHR CACS Foundation",
			"GH Raisoni Public School Foundation",
			"GH Raisoni Public School And Junior College",
			"GH Raisoni Foundation Society",
		],
	},
	{
		"id": "ghrus",
		"name": "GH Raisoni University Saikheda",
		"abbr": "GHRUS",
		"color": "#185FA5",
		"companies": [
			"GH Raisoni University Saikheda",
			"GH Raisoni Hospital And Research Centre",
			"GH Raisoni University Construction Account",
			"GH Raisoni University Hostel",
			"GH Raisoni University TBIF",
			"School of Nursing Saikheda",
			"School of Paramedical Sciences Saikheda",
			"School of Pharmacy Saikheda",
			"School of Agriculture Saikheda",
			"School of Commerce And Management Saikheda",
			"School of Engineering And Technology Saikheda",
			"School of Law Saikheda",
			"School of Science Saikheda",
		],
	},
	{
		"id": "cbs",
		"name": "Chaitanya Bahuudeshiya Sanstha",
		"abbr": "CBS",
		"color": "#D4537E",
		"companies": [
			"Chaitanya Bahuudeshiya Sanstha Society",
			"GH Raisoni College Of Business Management Khaparkheda",
			"GH Raisoni College Of Engineering And Management Amravati",
		],
	},
	{
		"id": "ghrua",
		"name": "GH Raisoni University Amravati",
		"abbr": "GHRUA",
		"color": "#378ADD",
		"companies": [
			"GH Raisoni University Amravati",
			"GHRUA — Nagpur Office",
			"GHRUA — Pune Office",
		],
	},
	{
		"id": "ghrstu",
		"name": "GH Raisoni Skill Tech University Nagpur",
		"abbr": "GHRSTU",
		"color": "#639922",
		"companies": [
			"GH Raisoni Skill Tech University Nagpur",
		],
	},
	{
		"id": "ghristu",
		"name": "GH Raisoni International Skill Tech University Pune",
		"abbr": "GHRISTU",
		"color": "#7F77DD",
		"companies": [
			"GH Raisoni International Skill Tech University Pune",
		],
	},
	{
		"id": "sgr",
		"name": "SGR Foundation Group",
		"abbr": "SGR",
		"color": "#5F5E5A",
		"companies": [
			"SGR Foundation",
			"SGR Education Foundation",
		],
	},
]

DEFAULT_TRUST = {
	"id": "default",
	"name": "Other",
	"abbr": "OTH",
	"color": "#888780",
	"companies": [],  # populated implicitly by exclusion
}


def get_company_trust_assignments():
	"""Return {company_name: trust_id} for every named company in TRUSTS.

	Companies not present in any trust are mapped to "default" lazily by
	get_trust_for_company().
	"""
	assignments = {}
	for trust in TRUSTS:
		for company in trust["companies"]:
			assignments[company] = trust["id"]
	return assignments


def get_trust_for_company(company_name):
	"""Return the trust id for one company. Falls back to "default"."""
	return get_company_trust_assignments().get(company_name, "default")


def get_trust_by_id(trust_id):
	"""Return the trust dict for a given id, or DEFAULT_TRUST if unknown."""
	for trust in TRUSTS:
		if trust["id"] == trust_id:
			return trust
	return DEFAULT_TRUST


def get_all_trusts_with_default():
	"""TRUSTS + DEFAULT_TRUST as a single list, default appended last."""
	return TRUSTS + [DEFAULT_TRUST]
