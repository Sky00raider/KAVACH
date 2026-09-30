"""extract.py (BRAIN step 7): grounding, valid_from resolution, per-document fact extraction."""

from __future__ import annotations

from datetime import date

import pytest

from kavach.brain import amounts, extract, llm
from kavach.models import OWNER_ENTITY_ID


# --- quote_in_text / grounding -----------------------------------------------------------------------------


def test_quote_in_text_ignores_case_and_whitespace():
    assert extract.quote_in_text("Average  monthly\nsalary credit: INR 48000.", "average monthly salary credit: inr 48000.")
    assert not extract.quote_in_text("Average monthly salary credit: INR 48000.", "loan default")
    assert not extract.quote_in_text("some text", "")


@pytest.mark.parametrize("text, quote, value, confidence", [
    ("Average monthly salary credit: INR 48000.", "Average monthly salary credit: INR 48000.", "48000", "high"),
    ("Rent is due on the 5th of each month.", "Rent is due on the 5th of each month.", "5", "high"),
    ("Loan accounts: no default in the last 12 months.", "Loan accounts: no default in the last 12 months.", "no", "high"),
    ("Salary credited as usual this month.", "Salary credited as usual this month.", "48000", "low"),
    ("Board: Mock Board of Pre-University Education", "Board: Mock Board of Pre-University Education", "Mock Board", "high"),
    ("Board: Mock Board of Pre-University Education", "Board: Mock Board of Pre-University Education", "CBSE", "low"),
])
def test_grounding_high_low(text, quote, value, confidence):
    assert extract.grounding(text, quote, value) == confidence


def test_grounding_without_a_real_quote_is_low():
    assert extract.grounding("Some real text.", "A quote that was never in the text", "48000") == "low"
    assert extract.grounding("Some real text.", None, "48000") == "low"


# --- document_reference_date -------------------------------------------------------------------------------


@pytest.mark.parametrize("first_text, expected", [
    ("2026-09-05\n\nRent notes for the month.", date(2026, 9, 5)),
    ("12 March 2026\n\nDecided to renew.", date(2026, 3, 12)),
    ("March 12, 2026 - Decided to renew.", date(2026, 3, 12)),
    ("No date anywhere in this note.", None),
])
def test_document_reference_date_for_notes(first_text, expected):
    got = extract.document_reference_date("note", first_text, "2026-09-20T10:00:00Z")
    assert got == (expected or date(2026, 9, 20))


def test_document_reference_date_for_non_notes_is_always_ingested_at():
    text = "2026-01-01 this looks like a date but it is a PDF"
    assert extract.document_reference_date("pdf", text, "2026-09-20T10:00:00Z") == date(2026, 9, 20)


# --- resolve_valid_from -------------------------------------------------------------------------------------


def test_resolve_valid_from_defaults_to_reference_when_empty():
    ref = date(2026, 9, 20)
    assert extract.resolve_valid_from(None, ref) == "2026-09-20"
    assert extract.resolve_valid_from("  ", ref) == "2026-09-20"


def test_resolve_valid_from_iso_passthrough():
    assert extract.resolve_valid_from("2027-01-15", date(2026, 9, 20)) == "2027-01-15"
    assert extract.resolve_valid_from("2027-13-40", date(2026, 9, 20)) is None  # not a real date


def test_resolve_valid_from_bare_month_resolves_to_next_occurrence():
    # September 2026 note saying "from January" -> January has already passed this year -> next year
    assert extract.resolve_valid_from("January", date(2026, 9, 20)) == "2027-01-01"
    assert extract.resolve_valid_from("from October", date(2026, 9, 20)) == "2026-10-01"
    assert extract.resolve_valid_from("in Oct", date(2026, 9, 20)) == "2026-10-01"


def test_resolve_valid_from_bare_month_with_explicit_year():
    assert extract.resolve_valid_from("January 2027", date(2026, 9, 20)) == "2027-01-01"


def test_resolve_valid_from_unparseable_is_none():
    assert extract.resolve_valid_from("sometime soon", date(2026, 9, 20)) is None
    assert extract.resolve_valid_from("15 October 2026", date(2026, 9, 20)) is None


# --- clean_field_name ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, cleaned", [
    ("monthly_income", "monthly_income"),
    ("Gym Membership Fee", "gym_membership_fee"),
    ("  Rent!! ", "rent"),
    ("2fast", None),  # must start with a letter
    ("___", None),
    ("", None),
])
def test_clean_field_name(raw, cleaned):
    assert extract.clean_field_name(raw) == cleaned


# --- facts_for_document --------------------------------------------------------------------------------------


def _chunk(cid, text):
    return {"chunk_id": cid, "locator": "page 1", "text": text}


def test_facts_for_document_grounds_and_stores(fresh_db, monkeypatch):
    calls = []

    def fake_extract(text):
        # `text` is already amounts-normalised ("INR 48000" -> "48000"); a real model would quote from that
        calls.append(text)
        return extract.Extraction(facts=[
            extract.XFact(field="monthly_income", value="48000", quote="Average monthly salary credit: 48000."),
            extract.XFact(field="loan_default_12m", value="no",
                         quote="Loan accounts: no default in the last 12 months."),
            extract.XFact(field="board", value="Fabricated Board", quote="This sentence is not in the text at all"),
        ])

    monkeypatch.setattr(extract, "extract", fake_extract)
    chunks = [_chunk("c_1", "Average monthly salary credit: INR 48000. Loan accounts: no default in the last "
                          "12 months.")]
    stored = extract.facts_for_document("d_1", "pdf", "issuer_doc", chunks, "2026-09-20T10:00:00Z",
                                        doc_type="bank_statement")
    assert stored == 2  # the fabricated-quote fact is dropped entirely
    assert len(calls) == 1

    facts = {f.field: f for f in fresh_db.list_facts()}
    assert facts["monthly_income"].value == "48000" and facts["monthly_income"].confidence == "high"
    assert facts["monthly_income"].entity_id == OWNER_ENTITY_ID and facts["monthly_income"].doc_id == "d_1"
    assert facts["monthly_income"].source_type == "issuer_doc"
    assert facts["monthly_income"].valid_from == "2026-09-20"  # no valid_from stated -> the document's own date
    assert "board" not in facts


def test_facts_for_document_keeps_first_occurrence_per_field(fresh_db, monkeypatch):
    calls = iter([
        extract.Extraction(facts=[extract.XFact(field="employer", value="Acme Corp", quote="Works at Acme Corp.")]),
        extract.Extraction(facts=[extract.XFact(field="employer", value="Other Inc", quote="Mentions Other Inc.")]),
    ])
    monkeypatch.setattr(extract, "extract", lambda text: next(calls))
    chunks = [_chunk("c_1", "Works at Acme Corp."), _chunk("c_2", "Mentions Other Inc.")]
    stored = extract.facts_for_document("d_1", "note", "extracted", chunks, "2026-09-20T10:00:00Z")
    assert stored == 1
    assert fresh_db.list_facts()[0].value == "Acme Corp"


def test_facts_for_document_stops_on_llm_error_but_keeps_earlier_results(fresh_db, monkeypatch):
    calls = iter([
        extract.Extraction(facts=[extract.XFact(field="employer", value="Acme Corp", quote="Works at Acme Corp.")]),
    ])

    def fake_extract(text):
        try:
            return next(calls)
        except StopIteration:
            raise llm.LLMError("ollama died")

    monkeypatch.setattr(extract, "extract", fake_extract)
    chunks = [_chunk("c_1", "Works at Acme Corp."), _chunk("c_2", "more text"), _chunk("c_3", "more text")]
    stored = extract.facts_for_document("d_1", "note", "extracted", chunks, "2026-09-20T10:00:00Z")
    assert stored == 1


@pytest.mark.parametrize("quote, expected", [
    ("Landlord said rent goes to 16000 from January.", "2027-01-01"),  # the demo inbox note (27 Sep 2026)
    ("Rent is 15000 starting March 2027.", "2027-03-01"),
    ("New rent 16000 w.e.f. 01/02/2027.", "2027-02-01"),
    ("Salary 70000 effective from 1 October 2026.", "2026-10-01"),
    ("05/06/2026 UPI/RENT/RAVI KUMAR 14500", "2026-06-05"),  # a bank row: day first
    ("[2026-09-18 19:42] Ravi Kumar: Yes, the current rent is 14500.", "2026-09-18"),  # a chat line
    ("Date: 27 September 2026 Just got off the phone with Ravi Kumar.", "2026-09-27"),  # a full date, not "next September"
    ("Rent is 15000.", "2026-09-27"),  # nothing stated: the reference date
    ("The rent may go up.", "2026-09-27"),  # "may" is not a month without a start cue
    ("Agreement from 1 April 2026 to 31 March 2027.", "2026-04-01"),
])
def test_valid_from_is_read_from_the_quote(quote, expected):
    assert extract.valid_from_in_text(quote, date(2026, 9, 27)) == expected


def test_facts_for_document_dates_the_inbox_note_from_its_quote(fresh_db, monkeypatch):
    text = "# Inbox Note\n\nDate: 27 September 2026\n\nLandlord said rent goes to ₹16k from January."
    monkeypatch.setattr(extract, "extract", lambda t: extract.Extraction(facts=[
        extract.XFact(field="rent_amount", value="16000", quote="Landlord said rent goes to 16000 from January.")]))
    extract.facts_for_document("d_1", "note", "extracted", [_chunk("c_1", text)], "2026-09-30T10:00:00Z")
    assert fresh_db.list_facts()[0].valid_from == "2027-01-01"


def test_facts_for_document_dates_a_chat_window_from_its_locator(fresh_db, monkeypatch):
    monkeypatch.setattr(extract, "extract", lambda t: extract.Extraction(facts=[
        extract.XFact(field="rent_amount", value="14500", quote="Ravi Kumar: Yes, the current rent is 14500.")]))
    chunk = {"chunk_id": "c_1", "locator": "chat: 2026-09-18 19:42",
             "text": "Ravi Kumar: Yes, the current rent is 14500."}
    extract.facts_for_document("d_1", "chat", "extracted", [chunk], "2026-09-30T10:00:00Z")
    assert fresh_db.list_facts()[0].valid_from == "2026-09-18"


@pytest.mark.parametrize("name, canonical", [
    ("monthly_rent", "rent_amount"), ("rent", "rent_amount"), ("current_rent", "rent_amount"),
    ("salary", "monthly_income"), ("new_salary", "monthly_income"), ("monthly_income", "monthly_income"),
    ("landlord_name", "landlord"), ("company_name", "employer"), ("dob", "date_of_birth"),
    ("lease_end_date", "agreement_end_date"), ("gym_membership_fee", "gym_membership_fee"),
])
def test_canonical_field(name, canonical):
    assert extract.canonical_field(name) == canonical


def test_placeholder_and_contact_details_are_not_names():
    assert extract.clean_value("landlord", "unknown") is None
    assert extract.clean_value("landlord", "N/A") is None
    assert extract.clean_value("landlord", "ravi.landlord@example.com") is None
    assert extract.clean_value("landlord", "98450 123456") is None
    assert extract.clean_value("landlord", "Ravi Kumar") == "Ravi Kumar"


# --- what a grounded quote actually states (real-model run on the demo vault, 30 Sep) ----------------------


def test_a_chat_timestamp_does_not_ground_a_date():
    quote = "[2026-09-21 11:15] Ananya Iyer: Can you also send me the agreement details?"
    assert extract.grounding(quote, quote, "2026-09-21") == "low"
    assert extract.grounding("Ends on 31 December 2026.", "Ends on 31 December 2026.", "2026-12-31") == "high"
    x = extract.Extraction(facts=[extract.XFact(field="id_expiry", value="2026-09-21", quote=quote)])
    assert extract.ground(x, quote) == []  # a low-confidence date is dropped, not stored "unsure"


def test_a_date_value_is_not_its_own_valid_from(fresh_db, monkeypatch):
    text = "Date: 1 April 2026. The agreement ends on 31 December 2026."
    monkeypatch.setattr(extract, "extract", lambda t: extract.Extraction(facts=[
        extract.XFact(field="agreement_end_date", value="2026-12-31", quote="The agreement ends on 31 December 2026.")]))
    extract.facts_for_document("d_1", "note", "extracted", [_chunk("c_1", text)], "2026-09-30T10:00:00Z")
    fact = fresh_db.list_facts()[0]
    assert fact.value == "2026-12-31" and fact.valid_from == "2026-04-01"


@pytest.mark.parametrize("field, value, quote, ok", [
    ("rent_amount", "15000", "I will probably renew if the rent stays below 15000.", False),
    ("rent_amount", "15000", "Renew only if rent stays under Rs 15000", False),
    ("rent_amount", "14500", "[2026-09-18 19:48] Ravi Kumar: Yes, the current rent is 14500.", True),
    ("monthly_income", "62000", "Average monthly salary credit: 62000", True),
    ("employer", "Ravi Kumar", "Contacts: Ravi Kumar, Priya", False),
    ("employer", "Nimbus Analytics", "SALARY CREDIT Nimbus Analytics", True),
    ("landlord", "Ravi Kumar", "Landlord: Ravi Kumar", True),
    ("landlord", "Ravi Kumar", "Just got off the phone with Ravi Kumar.", False),
])
def test_states_value(field, value, quote, ok):
    assert extract.states_value(field, value, quote) is ok


BANK_TEXT = ("Date Description Debit Credit Balance 2026-06-01 SALARY CREDIT Nimbus Analytics Pvt L 62,000.00 "
             "80,250.00 2026-06-05 UPI/RENT/RAVI KUMAR 14,500.00 65,750.00 2026-07-05 UPI/RENT/RAVI KUMAR 14,500.00 "
             "1,04,129.50 2026-07-12 CARD/GROCERIES AND UTILITIES 9,120.50 95,009.00")


def test_bank_row_facts_come_from_the_latest_rows():
    facts = {f["field"]: f for f in extract.bank_row_facts([_chunk("c_1", BANK_TEXT)])}
    assert facts["rent_amount"]["value"] == "14500" and facts["rent_amount"]["valid_from"] == "2026-07-05"
    assert facts["landlord"]["value"] == "Ravi Kumar"
    quote = facts["rent_amount"]["quote"]
    assert quote == "2026-07-05 UPI/RENT/RAVI KUMAR 14500"
    assert extract.grounding(amounts.normalize_amounts(BANK_TEXT), quote, "14500") == "high"
    # the salary row gives income and employer (a truncated "Pvt L" suffix is cut, like the graph does)
    assert facts["monthly_income"]["value"] == "62000" and facts["monthly_income"]["valid_from"] == "2026-06-01"
    assert facts["employer"]["value"] == "Nimbus Analytics"
    assert extract.bank_row_facts([_chunk("c_1", "2026-07-12 CARD/GROCERIES 9,120.50 95,009.00")]) == []


def test_facts_for_document_takes_bank_rent_from_code_not_the_model(fresh_db, monkeypatch):
    monkeypatch.setattr(extract, "extract", lambda t: extract.Extraction(facts=[
        extract.XFact(field="rent_amount", value="65750", quote="14500 65750")]))  # a model misread: ignored
    extract.facts_for_document("d_1", "pdf", "issuer_doc", [_chunk("c_1", BANK_TEXT)], "2026-09-30T10:00:00Z",
                               doc_type="bank_statement")
    facts = {f.field: f for f in fresh_db.list_facts()}
    assert facts["rent_amount"].value == "14500" and facts["rent_amount"].source_type == "issuer_doc"
    assert facts["landlord"].value == "Ravi Kumar"


def test_facts_for_document_empty_chunks():
    assert extract.facts_for_document("d_1", "note", "extracted", [], "2026-09-20T10:00:00Z") == 0


# --- field shape and per-document-type allowlist (real-model run on the signed statement) --------------------


@pytest.mark.parametrize("field, raw, cleaned", [
    ("monthly_income", "62000", "62000"),
    ("monthly_income", "₹62,000", "62000"),
    ("monthly_income", "salary", None),
    ("percentage", "82.4", "82.4"),
    ("percentage", "82.4%", "82.4"),
    ("percentage", "824", None),
    ("result", "PASS", "pass"),
    ("result", "signed", None),
    ("loan_default_12m", "no", "no"),
    ("loan_default_12m", "maybe", None),
    ("date_of_birth", "2003-05-14", "2003-05-14"),
    ("date_of_birth", "14 May 2003", None),
    ("emi_date", "5", "5"),
    ("emi_date", "2026-06-12", "2026-06-12"),
    ("emi_date", "every month", None),
    ("board", "Mock Board of Pre-University Education", "Mock Board of Pre-University Education"),
    ("employer", "12345", None),
])
def test_clean_value(field, raw, cleaned):
    assert extract.clean_value(field, raw) == cleaned


def test_allowed_fields_per_document_type():
    assert "board" not in extract.allowed_fields("bank_statement", "issuer_doc")
    assert "monthly_income" in extract.allowed_fields("bank_statement", "issuer_doc")
    assert "board" in extract.allowed_fields("marksheet", "issuer_doc")
    # an issuer-signed PDF of unknown type never yields a disclosable field
    unknown = extract.allowed_fields(None, "issuer_doc")
    assert not {"monthly_income", "board", "result", "percentage", "date_of_birth", "loan_default_12m"} & unknown
    assert "board" in extract.allowed_fields("note", "extracted")


def test_bank_statement_never_yields_a_board_or_a_bad_result(fresh_db, monkeypatch):
    text = "SALARY CREDIT 62000 UPI/RENT/RAVI KUMAR This statement is digitally signed by the issuing bank."
    monkeypatch.setattr(extract, "extract", lambda t: extract.Extraction(facts=[
        extract.XFact(field="board", value="RAVI KUMAR", quote="UPI/RENT/RAVI KUMAR"),
        extract.XFact(field="result", value="signed", quote="This statement is digitally signed by the issuing bank."),
        extract.XFact(field="monthly_income", value="62000", quote="SALARY CREDIT 62000"),
    ]))
    stored = extract.facts_for_document("d_1", "pdf", "issuer_doc", [_chunk("c_1", text)], "2026-09-20T10:00:00Z",
                                        doc_type="bank_statement")
    assert stored == 1 and [f.field for f in fresh_db.list_facts()] == ["monthly_income"]
