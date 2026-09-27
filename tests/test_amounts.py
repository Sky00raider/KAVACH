import pytest

from kavach.brain.amounts import normalize_amounts, parse_amount


@pytest.mark.parametrize("text, value", [
    ("₹15,000", 15000),
    ("Rs. 15,000/-", 15000),
    ("Rs 15000", 15000),
    ("Rs15000", 15000),
    ("INR 50000", 50000),
    ("15k", 15000),
    ("₹50K", 50000),
    ("1.5k", 1500),
    ("1,20,000", 120000),
    ("12,34,567", 1234567),
    ("120,000", 120000),
    ("62,000.00", 62000),
    ("1.2 lakh", 120000),
    ("2 lakhs", 200000),
    ("3 lac", 300000),
    ("5 L", 500000),
    ("2 cr", 20000000),
    ("1.5 crore", 15000000),
    ("50000", 50000),
])
def test_parse_amount(text, value):
    assert parse_amount(text) == value
    assert normalize_amounts(text) == str(value)


@pytest.mark.parametrize("text", ["50-60k", "₹50k to ₹60k", "50k – 60k", "₹99.50", "12.5", "5 l", "abc", "", "15kg"])
def test_not_one_whole_amount(text):
    assert parse_amount(text) is None


@pytest.mark.parametrize("text, expected", [
    ("Rent: Rs. 15,000 payable on the 5th.", "Rent: 15000 payable on the 5th."),
    ("salary 62,000.00 and bonus 1.2 lakh", "salary 62000 and bonus 120000"),
    ("between 50-60k a month", "between 50-60k a month"),  # ranges are ambiguous: unchanged
    ("from ₹50,000 to ₹60,000", "from ₹50,000 to ₹60,000"),
    ("on 2026-08-14 at 21:03", "on 2026-08-14 at 21:03"),  # dates, times, phone numbers, percentages untouched
    ("call 98450 12345", "call 98450 12345"),
    ("scored 91.3% in 2026", "scored 91.3% in 2026"),
    ("items 1,2,3", "items 1,2,3"),  # not a grouping
    ("5 l of milk, 15kg rice", "5 l of milk, 15kg rice"),
    ("12 hours 5 min", "12 hours 5 min"),  # "rs" inside a word is not a currency
    ("no amounts here", "no amounts here"),
])
def test_normalize_amounts_in_text(text, expected):
    assert normalize_amounts(text) == expected
