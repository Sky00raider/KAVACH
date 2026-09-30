"""decide.decide: every CONTRACT §5.4 branch against a real wallet, real facts and the real ledger."""

from __future__ import annotations

import json
from datetime import date

import pytest

from kavach import config, db
from kavach.brain import decide
from kavach.mock_issuers import issue
from kavach.models import Claim
from kavach.trust import ledger, wallet

INCOME_50K = Claim(claim="income", op="ge", value=50000, issuer_claim="income_ge_50000")


@pytest.fixture
def runtime(fresh_db, tmp_path, monkeypatch):
    for attr, sub in {"KEYS_DIR": "keys", "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    return tmp_path


def _batch(runtime, credential_type: str, n: int = 2, **profile) -> None:
    pubkeys = json.loads(wallet.export_holder_pubkeys(n).read_text())
    path = runtime / f"{credential_type}.json"
    path.write_text(json.dumps(issue.issue_batch(credential_type, pubkeys, dict(issue.PROFILE, **profile))))
    wallet.import_batch(path)


def _use_all_copies() -> None:
    for r in db.fetch_all("SELECT cred_id FROM credentials"):
        wallet.claim_copy(r["cred_id"])


def _fact(field: str, value: str, *, status: str = "issuer_signed", source_type: str = "issuer_doc",
          confidence: str = "high", valid_from: str = "2026-04-01", superseded_by: str | None = None,
          doc: str = "d_stmt", holder: str | None = "verified") -> None:
    if db.fetch_one("SELECT 1 FROM documents WHERE doc_id = ?", (doc,)) is None:
        path = "pdfs/bank_statement_signed.pdf" if doc == "d_stmt" else f"pdfs/{doc}.pdf"
        db.insert("documents", {"doc_id": doc, "path": path, "source": "pdf", "signature_status": status,
                                "holder_status": holder if status == "issuer_signed" else None, "text_hash": "h",
                                "ingested_at": "2026-09-01T00:00:00Z"})
    db.insert("facts", {"fact_id": db.new_id("f"), "entity_id": "e_owner", "field": field, "value": value,
                        "source_type": source_type, "doc_id": doc, "quote": value, "valid_from": valid_from,
                        "superseded_by": superseded_by, "confidence": confidence,
                        "created_at": db.utc_now()})


# --- automatic outcomes --------------------------------------------------------------------------------------


@pytest.mark.parametrize("claim", [
    Claim(claim="unsupported"),
    Claim(claim="income", op="ge", value="50k"),
    Claim(claim="income", op="is", value=50000),
    Claim(claim="income", op="ge", value=True),
    Claim(claim="age", op="ge", value=0),
    Claim(claim="loan_default_12m", op="is", value=False),
    Claim(claim="result", op="is", value="first class"),
    Claim(claim="board", op="eq", value="  "),
])
def test_unsupported_or_malformed_is_refused(runtime, claim):
    p = decide.decide(claim, "fp")
    assert p.answer_type == "REFUSED" and p.actions == [] and p.result is None and p.favourable is None


def test_nothing_to_go_on_cannot_confirm(runtime):
    p = decide.decide(INCOME_50K, "fp")
    assert p.answer_type == "CANNOT_CONFIRM" and p.actions == [] and p.result is None
    assert "₹50,000" in p.reason


# --- issuer proofs -------------------------------------------------------------------------------------------


def test_issuer_proof_favourable(runtime):
    _batch(runtime, "income_proof")
    p = decide.decide(INCOME_50K, "fp")
    assert p.answer_type == "ISSUER_PROOF" and p.result is True and p.favourable is True
    assert p.actions == ["approve", "deny"]
    assert p.reason == "Your signed Mock Bank income proof covers monthly income ≥ ₹50,000"


def test_issuer_proof_unfavourable_offers_answer_or_decline(runtime):
    _batch(runtime, "income_proof", monthly_income=40000)
    p = decide.decide(INCOME_50K, "fp")
    assert p.answer_type == "ISSUER_PROOF" and p.result is False and p.favourable is False
    assert p.actions == ["answer", "decline"]


def test_loan_default_favourable_answer_is_no(runtime):
    claim = Claim(claim="loan_default_12m", op="is", value=True)
    _batch(runtime, "income_proof", n=1)
    p = decide.decide(claim, "fp")
    assert (p.result, p.favourable, p.actions) == (False, True, ["approve", "deny"])
    assert p.claim.issuer_claim == "loan_default_12m"
    _use_all_copies()
    _batch(runtime, "income_proof", n=1, loan_default_12m=True)
    p = decide.decide(claim, "fp")
    assert (p.answer_type, p.result, p.favourable, p.actions) == ("ISSUER_PROOF", True, False, ["answer", "decline"])


def test_board_value_from_the_credential(runtime):
    _batch(runtime, "marksheet", n=1)
    p = decide.decide(Claim(claim="board", op="is"), "fp")
    assert p.answer_type == "ISSUER_PROOF" and p.result == issue.PROFILE["board"]
    assert p.favourable is None and p.actions == ["approve", "deny"]
    assert p.reason == "Your signed Mock Board marksheet covers your exam board"  # not the board's name


def test_board_value_needs_a_credential(runtime):
    _fact("board", "Mock Board of Pre-University Education")
    p = decide.decide(Claim(claim="board", op="is"), "fp")
    assert p.answer_type == "CANNOT_CONFIRM" and "Mock Board" not in p.reason


def test_issuer_claim_is_recomputed_in_code(runtime):
    _batch(runtime, "income_proof", n=1)
    p = decide.decide(Claim(claim="income", op="ge", value=60000, issuer_claim="income_ge_50000"), "fp")
    assert p.claim.issuer_claim is None and p.answer_type == "CANNOT_CONFIRM"
    p = decide.decide(Claim(claim="age", op="ge", value=21), "fp")
    assert p.claim.issuer_claim == "age_over_21"


def test_used_copies_fall_back_to_the_fact(runtime):
    _batch(runtime, "income_proof", n=1)
    _use_all_copies()
    assert decide.decide(INCOME_50K, "fp").answer_type == "CANNOT_CONFIRM"
    _fact("monthly_income", "62000")
    p = decide.decide(INCOME_50K, "fp")
    assert p.answer_type == "OWNER_ATTESTED" and p.result is True


# --- owner attestations from grounded facts ------------------------------------------------------------------


@pytest.mark.parametrize("threshold, result, actions", [
    (60000, True, ["approve", "deny"]),
    (62000, True, ["approve", "deny"]),
    (70000, False, ["answer", "decline"]),
])
def test_owner_attested_income_is_a_python_comparison(runtime, threshold, result, actions):
    _fact("monthly_income", "62,000.00")
    p = decide.decide(Claim(claim="income", op="ge", value=threshold), "fp")
    assert (p.answer_type, p.result, p.favourable, p.actions) == ("OWNER_ATTESTED", result, result, actions)
    assert p.reason == "Signed bank statement shows this; you'd attest it yourself"  # the document, never the value


def test_percentage_and_result(runtime):
    _fact("percentage", "82.4")
    _fact("result", "PASS")
    assert decide.decide(Claim(claim="percentage", op="ge", value=80), "fp").result is True
    assert decide.decide(Claim(claim="percentage", op="ge", value=83), "fp").result is False
    p = decide.decide(Claim(claim="result", op="is", value="pass"), "fp")
    assert p.answer_type == "OWNER_ATTESTED" and p.result is True


@pytest.mark.parametrize("today, result", [(date(2024, 5, 13), False), (date(2024, 5, 14), True)])
def test_age_from_date_of_birth_on_the_birthday(runtime, monkeypatch, today, result):
    monkeypatch.setattr(decide, "_today", lambda: today)
    _fact("date_of_birth", "2003-05-14")
    p = decide.decide(Claim(claim="age", op="ge", value=21), "fp")
    assert p.answer_type == "OWNER_ATTESTED" and p.result is result


@pytest.mark.parametrize("asked, result, actions", [
    ("mock board of pre-university  education", True, ["approve", "deny"]),
    ("CBSE", False, ["answer", "decline"]),
])
def test_board_match_is_yes_no(runtime, asked, result, actions):
    _fact("board", "Mock Board of Pre-University Education")
    p = decide.decide(Claim(claim="board", op="eq", value=asked), "fp")
    assert (p.answer_type, p.result, p.favourable, p.actions) == ("OWNER_ATTESTED", result, None, actions)


@pytest.mark.parametrize("kwargs", [
    {"status": "invalid"},
    {"status": "unsigned"},
    {"source_type": "extracted"},
    {"source_type": "owner_stated"},
    {"confidence": "low"},
    {"superseded_by": "f_newer"},
])
def test_only_grounded_issuer_facts_count(runtime, kwargs):
    _fact("monthly_income", "62000", **kwargs)
    assert decide.decide(Claim(claim="income", op="ge", value=60000), "fp").answer_type == "CANNOT_CONFIRM"


def test_newest_valid_fact_wins(runtime):
    _fact("monthly_income", "45000", valid_from="2026-01-01", doc="d_old")
    _fact("monthly_income", "62000", valid_from="2026-04-01", doc="d_new")
    assert decide.decide(Claim(claim="income", op="ge", value=60000), "fp").result is True


def test_unreadable_fact_value_cannot_confirm(runtime):
    _fact("monthly_income", "see attached")
    assert decide.decide(Claim(claim="income", op="ge", value=60000), "fp").answer_type == "CANNOT_CONFIRM"


def test_ledger_block_is_refused(runtime):
    _fact("monthly_income", "62000")
    ledger.record(Claim(claim="income", op="ge", value=50000, issuer_claim="income_ge_50000"), True)
    p = decide.decide(Claim(claim="income", op="ge", value=70000), "fp")  # NO would leave [50000, 70000)
    assert p.answer_type == "REFUSED" and p.actions == [] and "narrower" in p.reason
    assert decide.decide(Claim(claim="income", op="ge", value=80000), "fp").answer_type == "OWNER_ATTESTED"
