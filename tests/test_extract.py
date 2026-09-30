"""extract.py (BRAIN step 7): grounding, valid_from resolution, per-document fact extraction."""

from __future__ import annotations

from datetime import date

import pytest

from kavach.brain import extract, llm
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


def test_facts_for_document_drops_a_fact_with_unparseable_valid_from(fresh_db, monkeypatch):
    monkeypatch.setattr(extract, "extract", lambda text: extract.Extraction(facts=[
        extract.XFact(field="rent_amount", value="15000", quote="Rent is 15000.", valid_from="sometime soon")]))
    chunks = [_chunk("c_1", "Rent is 15000.")]
    assert extract.facts_for_document("d_1", "note", "extracted", chunks, "2026-09-20T10:00:00Z") == 0
    assert fresh_db.list_facts() == []


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
