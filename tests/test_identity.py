"""Identity anchor and holder check (CONTRACT §6.6): a signed document only counts as the owner's in their name."""

import shutil
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from kavach import api, config
from kavach.brain import decide, extract, identity, ingest, llm
from kavach.mock_issuers import issue
from kavach.models import Claim
from kavach.trust import audit

REAL_AUDIT_LOG = audit.log  # the vault fixture replaces it

FRIEND = {"name": "Rohan Mehta", "date_of_birth": "2001-11-02", "monthly_income": 95000, "landlord": "Suresh Rao",
          "employer": "Orbit Labs Pvt Ltd", "account": "XXXXXXXX9034"}


# --- the check itself (no files) -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Account holder: Ananya Iyer\nAccount number: XXXX4821",
    "Account holder: IYER ANANYA\n",                       # surname first, capitals
    "Account holder: Ananya R. Iyer\n",                     # middle initial
    "Account holder:\nAnanya Iyer\n",                       # value wrapped below the label
    "Statement of Account Account holder: Ananya Iyer Account number: XXXX4821",  # normalised, no newlines
])
def test_owner_name_after_holder_label_verifies(text):
    assert identity.check(text, "bank_statement", "Ananya Iyer", None) == "verified"


@pytest.mark.parametrize("text", [
    "Account holder: Rohan Mehta\n",
    "Account holder: Ananya Mehta\n",                                    # one word is not the name
    "Account holder: A. Iyer\n",                                         # initials do not count
    # a friend's statement that pays the owner: the name is in a row, not the holder line
    "Account holder: Rohan Mehta\n2026-08-03  UPI/ANANYA IYER  5,000.00\n",
    "Account holder: Rohan Kumar Mehta Ananya Iyer\n",                   # beyond the n + 2 word window
])
def test_other_holder_is_mismatch(text):
    assert identity.check(text, "bank_statement", "Ananya Iyer", None) == "mismatch"


def test_name_label_of_someone_else_is_skipped():
    text = "Father's Name: Ananya Iyer\nName: Rohan Mehta\n"
    assert identity.check(text, "id_card", "Ananya Iyer", None) == "mismatch"
    assert identity.check(text, "id_card", "Rohan Mehta", None) == "verified"


def test_without_a_holder_label_the_name_may_be_anywhere():
    assert identity.check("Certificate issued to Ananya Iyer for 2026", None, "Ananya Iyer", None) == "verified"
    assert identity.check("Certificate issued to Rohan Mehta for 2026", None, "Ananya Iyer", None) == "mismatch"


@pytest.mark.parametrize("dob_line, status", [
    ("Date of birth: 2003-05-14", "verified"),
    ("DOB: 14/05/2003", "verified"),
    ("D.O.B. 14 May 2003", "verified"),
    ("Date of Birth: May 14, 2003", "verified"),
    ("Date of birth: 2001-01-01", "mismatch"),
    ("DOB: 14/05/2001", "mismatch"),
    ("Date of birth: fourteenth of May", "unknown"),
])
def test_date_of_birth_must_match_the_anchor(dob_line, status):
    text = f"Candidate: Ananya Iyer\n{dob_line}\n"
    assert identity.check(text, "marksheet", "Ananya Iyer", "2003-05-14") == status


def test_date_of_birth_is_not_checked_without_one_in_the_anchor():
    text = "Candidate: Ananya Iyer\nDate of birth: 2001-01-01\n"
    assert identity.check(text, "marksheet", "Ananya Iyer", None) == "verified"


def test_read_id_card():
    assert identity.read_id_card("Identity Card\nName: Ananya Iyer\nDate of birth: 2003-05-14\n") == \
        ("Ananya Iyer", "2003-05-14")
    assert identity.read_id_card("Identity Card Name: Ananya Iyer Date of birth: 2003-05-14 ID number: X") == \
        ("Ananya Iyer", "2003-05-14")
    assert identity.read_id_card("Identity Card\nNo holder here\n") is None


def test_config_fallback_is_not_verified(fresh_db):
    out = identity.current()
    assert (out.status, out.source, out.name_initials, out.birth_year) == ("not_verified", "config", "A. I.", None)


# --- real signed PDFs from the mock issuer --------------------------------------------------------------------


@pytest.fixture
def vault(fresh_db, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    for sub in ("pdfs", "notes", "chats"):
        (root / sub).mkdir(parents=True)
    for attr, sub in {"VAULT_DIR": "vault", "KEYS_DIR": "keys", "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    monkeypatch.setattr(llm, "embed", lambda texts: np.ones((len(texts), config.EMBED_DIM), dtype=np.float32))
    events = []
    monkeypatch.setattr(audit, "log", lambda event, ref_id, detail: events.append((event, ref_id, detail)) or 1)

    def signed(kind: str, file: str, **profile):
        """The mock issuer's `kind` PDF for `profile` (PROFILE with overrides), copied to vault/pdfs/<file>.pdf."""
        out = tmp_path / "issued" / file
        issue.make_pdfs(out, {**issue.PROFILE, **profile})
        dest = root / "pdfs" / f"{file}.pdf"
        shutil.copy(out / f"{kind}.pdf", dest)
        return dest

    return SimpleNamespace(root=root, signed=signed, events=events, db=fresh_db)


def _doc(db, path):
    return db.get_document_by_path(f"pdfs/{path.name}")


def _facts(db, doc_id):
    return db.fetch_all("SELECT * FROM facts WHERE doc_id = ?", (doc_id,))


def test_signed_id_card_becomes_the_anchor(vault):
    idc = ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    assert idc.holder_status == "verified"
    out = identity.current()
    assert (out.status, out.source, out.issuer, out.doc_id) == ("verified", "signed_id", "mock_govt", idc.doc_id)
    assert (out.name_initials, out.birth_year) == ("A. I.", 2003)
    assert identity.anchor().dob == "2003-05-14"


def test_friends_signed_statement_is_mismatch_and_backs_nothing(vault):
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    own = ingest.ingest_file(vault.signed("bank_statement_signed", "bank_statement_signed"))
    friend = ingest.ingest_file(vault.signed("bank_statement_signed", "friend_bank_statement", **FRIEND))

    assert own.signature_status == friend.signature_status == "issuer_signed"  # the bank really signed both
    assert (own.holder_status, friend.holder_status) == ("verified", "mismatch")
    assert _doc(vault.db, vault.root / "pdfs" / "friend_bank_statement.pdf")["holder_status"] == "mismatch"
    assert vault.events[-1][2]["holder_status"] == "mismatch"  # the ingest strip's event carries it

    friend_facts = _facts(vault.db, friend.doc_id)
    assert friend_facts and {(f["source_type"], f["confidence"]) for f in friend_facts} == {("extracted", "low")}
    income = vault.db.current_fact("e_owner", "monthly_income")
    assert (income["value"], income["source_type"], income["doc_id"]) == ("62000", "issuer_doc", own.doc_id)
    # no PAID edges from someone else's rent row
    assert not vault.db.fetch_all("SELECT 1 FROM edges e JOIN chunks c ON c.chunk_id = e.source_chunk_id "
                                  "WHERE c.doc_id = ? AND e.rel = 'PAID'", (friend.doc_id,))

    # the owner's own 62,000 still answers (what their statement says); 75k is not the friend's 95k
    p = decide.decide(Claim(claim="income", op="ge", value=75000), "fp")
    assert (p.answer_type, p.result) == ("OWNER_ATTESTED", False)


def test_ingest_events_carry_holder_status(vault, monkeypatch):
    monkeypatch.setattr(audit, "log", REAL_AUDIT_LOG)
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    ingest.ingest_file(vault.signed("bank_statement_signed", "friend_bank_statement", **FRIEND))
    events = {e.path: e.holder_status for e in vault.db.ingest_events(0).events}
    assert events == {"pdfs/id_card.pdf": "verified", "pdfs/friend_bank_statement.pdf": "mismatch"}


def test_friends_statement_alone_cannot_confirm(vault):
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    ingest.ingest_file(vault.signed("bank_statement_signed", "friend_bank_statement", **FRIEND))
    for value in (45000, 75000):
        assert decide.decide(Claim(claim="income", op="ge", value=value), "fp").answer_type == "CANNOT_CONFIRM"


def test_same_name_different_date_of_birth_is_mismatch(vault):
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    marks = ingest.ingest_file(vault.signed("marksheet_signed", "marksheet_other", date_of_birth="2001-01-01"))
    assert marks.holder_status == "mismatch"
    same = ingest.ingest_file(vault.signed("marksheet_signed", "marksheet_signed"))
    assert same.holder_status == "verified"


def test_second_id_card_in_another_name_does_not_reanchor(vault):
    first = ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    other = ingest.ingest_file(vault.signed("id_card_signed", "id_card_friend", **FRIEND))
    assert other.holder_status == "mismatch"
    assert identity.current().doc_id == first.doc_id and identity.anchor().name == "Ananya Iyer"


def test_anchor_arriving_later_rechecks_and_reextracts(vault, monkeypatch):
    monkeypatch.setattr(extract, "extract", lambda text: extract.Extraction(facts=[
        extract.XFact(field="percentage", value="82.4", quote="Percentage: 82.4%")] if "Percentage" in text else []))
    # before any signed ID the anchor is config.OWNER_NAME without a date of birth: the name matches
    marks = ingest.ingest_file(vault.signed("marksheet_signed", "marksheet_other", date_of_birth="2001-01-01"))
    assert marks.holder_status == "verified"
    assert decide.decide(Claim(claim="percentage", op="ge", value=70), "fp").answer_type == "OWNER_ATTESTED"

    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))  # pins the DOB: the marksheet is rechecked
    assert _doc(vault.db, vault.root / "pdfs" / "marksheet_other.pdf")["holder_status"] == "mismatch"
    live = [f for f in _facts(vault.db, marks.doc_id) if f["valid_to"] is None and f["superseded_by"] is None]
    assert live and {(f["source_type"], f["confidence"]) for f in live} == {("extracted", "low")}
    assert decide.decide(Claim(claim="percentage", op="ge", value=70), "fp").answer_type == "CANNOT_CONFIRM"


def test_removing_the_anchor_falls_back_to_the_next_id_then_config(vault):
    first = vault.signed("id_card_signed", "id_card")
    ingest.ingest_file(first)
    second = ingest.ingest_file(vault.signed("id_card_signed", "id_card_reissued"))  # same person
    first.unlink()
    ingest.remove_file(first)
    assert identity.current().doc_id == second.doc_id

    path = vault.root / "pdfs" / "id_card_reissued.pdf"
    path.unlink()
    ingest.remove_file(path)
    assert identity.current().status == "not_verified"


def test_unsigned_documents_have_no_holder_status(vault):
    note = vault.root / "notes" / "rent.md"
    note.write_text("Rent is 14,500.", encoding="utf-8")
    assert ingest.ingest_file(note).holder_status is None


def test_identity_route_is_masked(vault, monkeypatch):
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    monkeypatch.setattr(api, "_ollama_status", lambda: (False, set()))
    client = TestClient(api.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8000")
    res = client.get("/api/identity", headers={"X-Owner-Token": "test-owner-token"})
    assert res.status_code == 200
    body = res.json()
    assert body["name_initials"] == "A. I." and body["birth_year"] == 2003
    assert "Ananya" not in res.text and "2003-05-14" not in res.text


def test_chat_marks_chunks_and_facts_from_someone_elses_document(vault):
    from kavach.brain import chat
    from kavach.models import ScoredChunk

    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    own = ingest.ingest_file(vault.signed("bank_statement_signed", "bank_statement_signed"))
    friend = ingest.ingest_file(vault.signed("bank_statement_signed", "friend_bank_statement", **FRIEND))
    chunks = [ScoredChunk(chunk_id=c["chunk_id"], doc_id=d, locator=c["locator"], text=c["text"], score=1.0)
              for d in (own.doc_id, friend.doc_id) for c in vault.db.chunks_for_document(d)[:1]]
    context = chat._context(chunks)
    assert context.count('holder="someone else"') == 1
    assert '<chunk n="2" source="page 1" holder="someone else">' in context

    fact = _facts(vault.db, friend.doc_id)[0]
    doc = vault.db.get_document_by_path("pdfs/friend_bank_statement.pdf")
    assert chat.source_label(fact, doc) == "signed document in someone else's name"


def test_make_friend_statement_script(tmp_path, monkeypatch):
    import importlib.util

    from kavach.trust import issuer_check

    for attr, sub in {"KEYS_DIR": "keys", "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    spec = importlib.util.spec_from_file_location("make_friend_statement",
                                                  config.ROOT / "scripts" / "make_friend_statement.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    pdf = script.make(tmp_path / "drop")
    assert pdf.name == "friend_bank_statement.pdf"
    assert issuer_check.verify_pdf(pdf).status == "issuer_signed"
    from kavach.textnorm import pdf_pages
    assert identity.check("\n".join(pdf_pages(pdf)), "bank_statement", "Ananya Iyer", "2003-05-14") == "mismatch"
