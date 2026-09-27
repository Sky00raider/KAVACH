import pytest
from pydantic import ValidationError

from kavach.models import DISCLOSABLE_FIELDS, EXTRACTED_FIELDS, AskIn, AuditEntry, Fact


def _fact(**kw):
    base = {"fact_id": "f_1", "entity_id": "e_owner", "field": "monthly_income", "value": "62000",
            "source_type": "issuer_doc", "confidence": "high"}
    return Fact(**{**base, **kw})


def test_extracted_and_owner_stated_facts_accept_any_snake_case_field():
    assert _fact(field="gym_membership_fee", source_type="owner_stated").field == "gym_membership_fee"
    assert _fact(field="wifi_provider", source_type="extracted").field == "wifi_provider"


@pytest.mark.parametrize("field", ["MonthlyIncome", "monthly-income", "1st_field", "trailing_", ""])
def test_field_must_be_snake_case(field):
    with pytest.raises(ValidationError):
        _fact(field=field, source_type="owner_stated")


def test_issuer_doc_facts_limited_to_extracted_fields():
    assert _fact().field in EXTRACTED_FIELDS
    with pytest.raises(ValidationError):
        _fact(field="gym_membership_fee")


def test_disclosable_fields_are_extracted_fields():
    assert set(DISCLOSABLE_FIELDS.values()) <= set(EXTRACTED_FIELDS)
    assert "unsupported" not in DISCLOSABLE_FIELDS


def test_ask_ts_is_unix_seconds():
    ask = AskIn(requester_pubkey="p", requester_name="R", requester_type="person", question="q", nonce="n",
                ts=1790000000, sig="s")
    assert ask.ts == 1790000000
    with pytest.raises(ValidationError):
        AskIn(**{**ask.model_dump(), "ts": "2026-09-26T12:00:00Z"})


def test_request_rejected_is_an_audit_event():
    AuditEntry(seq=1, ts="2026-09-26T12:00:00Z", event="request_rejected", ref_id=None,
               detail={"reason": "nonce_reuse"}, prev_hash="0" * 64, entry_hash="a" * 64)


def test_document_removed_is_an_audit_event():
    AuditEntry(seq=1, ts="2026-09-26T12:00:00Z", event="document_removed", ref_id="d_1",
               detail={"path": "notes/a.md", "doc_id": "d_1"}, prev_hash="0" * 64, entry_hash="a" * 64)
