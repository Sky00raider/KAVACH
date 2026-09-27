"""Rupee amounts in free text -> plain integer strings (BUILD_PLAN §4.10).

`₹`, `Rs`, `Rs.`, `INR` and a trailing `/-` are dropped; grouping commas are dropped (Western `120,000` and
Indian `1,20,000`); `k` = x1,000, `lakh`/`lac`/`L` = x1,00,000, `crore`/`cr` = x1,00,00,000. Ranges such as
`50-60k` are ambiguous and left unchanged, as is any amount that is not a whole number of rupees.
Search tokenising (`embed.py`) and question parsing (`parse_question.py`) both use this module;
per-period words (`/month`, `pm`) and percentages are parse_question's job, not this module's.
"""

from __future__ import annotations

import re
from decimal import Decimal

_MULTIPLIERS = {"k": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
                "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000}

_CURRENCY = r"(?:₹|(?i:\brs\.?|\binr))\s*"
# Indian grouping (1,20,000 / 12,34,567) before Western (120,000), then plain digits; optional decimals
_NUMBER = r"(?:\d{1,2}(?:,\d{2})+,\d{3}|\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d,]\d|\.\d|\d)"
# `L` is upper case only, so a lone `l` (litre) is never read as lakh
_MULTIPLIER = r"\s*(?:(?i:k|lakhs?|lacs?|crores?|cr)|L)\b"

_AMOUNT = re.compile(rf"(?<![\w.,])(?P<cur>{_CURRENCY})?(?P<num>{_NUMBER})(?P<mult>{_MULTIPLIER})?(?P<tail>/-)?")
_ATOM = rf"(?:{_CURRENCY})?{_NUMBER}(?:{_MULTIPLIER})?"
_RANGE = re.compile(rf"(?<![\w.,]){_ATOM}\s*(?:-|–|—|(?i:\bto\b))\s*{_ATOM}")


def _value(m: re.Match) -> int | None:
    number = Decimal(m["num"].replace(",", ""))
    if m["mult"]:
        number *= _MULTIPLIERS[m["mult"].strip().lower()]
    return int(number) if number == number.to_integral_value() else None


def parse_amount(text: str) -> int | None:
    """The integer rupee value of a string that is exactly one amount (`"₹15,000"`, `"1.2 lakh"`, `"50000"`),
    else None."""
    m = _AMOUNT.fullmatch(text.strip())
    return _value(m) if m else None


def normalize_amounts(text: str) -> str:
    """Replace every marked amount (currency, grouping commas or a multiplier) with its integer string.
    Unmarked numbers, ranges and non-whole amounts are left as they are."""
    ranges = [m.span() for m in _RANGE.finditer(text)]

    def replace(m: re.Match) -> str:
        if any(lo <= m.start() < hi for lo, hi in ranges):
            return m[0]
        if not (m["cur"] or m["mult"] or "," in m["num"]):
            return m[0]
        value = _value(m)
        return m[0] if value is None else str(value)

    return _AMOUNT.sub(replace, text)
