"""Indian-format number helper.

Executable spec for the JS `formatIndian` in
`dux_groupview/public/js/pivot_grid.js`. Lives here so the unit test
can verify the algorithm without spinning up a JS test runner. The JS
implementation is a hand translation -- if either side changes, both
must be updated and `test_indian_format_function` must still pass.

Format rules (Indian comma grouping):
    1234.56              -> "1,234.56"
    1234567.89           -> "12,34,567.89"
    12345678901.23       -> "12,34,56,78,901.23"
    0                    -> "0.00"
    -100.5               -> "(100.50)"     (parens, no minus sign)
    1e9 + 0.005          -> "1,00,00,00,000.01"  (rounded to 2 dp)
"""


def format_indian(value):
	if value == 0:
		return "0.00"

	abs_val = abs(value)
	# 2-decimal fixed; matches JS `Number.prototype.toFixed(2)` semantics
	# closely enough for the values we care about.
	s = f"{abs_val:.2f}"
	whole, decimal = s.split(".")

	last_three = whole[-3:]
	rest = whole[:-3]

	if rest:
		# Indian grouping: walk from the right of `rest` in 2-digit
		# chunks. Mirrors the JS regex
		# `\B(?=(\d{2})+(?!\d))` -> "12345678" -> "12,34,56,78".
		chunks = []
		i = len(rest)
		while i > 0:
			step = min(2, i)
			chunks.append(rest[i - step:i])
			i -= step
		chunks.reverse()
		rest_with_commas = ",".join(chunks)
		formatted = f"{rest_with_commas},{last_three}.{decimal}"
	else:
		formatted = f"{last_three}.{decimal}"

	return f"({formatted})" if value < 0 else formatted
