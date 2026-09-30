"""conditions.py + chat.condition_chunks: a decision's amount condition compared with the facts in code."""

from __future__ import annotations

import json
from datetime import date

import pytest

from kavach import config, db
from kavach.brain import chat, conditions, embed, ingest
from kavach.brain.conditions import Condition
from kavach.models import OWNER_ENTITY_ID

TODAY = "2026-09-30"


@pytest.mark.parametrize("quote, cond", [
    ("I decided to renew only if rent stays under ₹15,000.", Condition("rent_amount", "<", 15000)),
    ("I will probably renew if the rent stays below 15000.", Condition("rent_amount", "<", 15000)),
    ("Renew as long as the rent is at most 15k", Condition("rent_amount", "<=", 15000)),
    ("Take the loan only if my salary is at least ₹50,000", Condition("monthly_income", ">=", 50000)),
    ("Stay unless rent goes above 16000", Condition("rent_amount", "<=", 16000)),
    ("Decided to move out in January.", None),
    ("Rent is 15000 below market.", None),  # no "if": not a condition
])
def test_parse(quote, cond):
    assert conditions.parse(quote) == cond


def test_holds():
    c = Condition("rent_amount", "<", 15000)
    assert c.holds("14500") is True and c.holds("16000") is False and c.holds("15000") is False
    assert c.holds("₹14,500") is True and c.holds("n/a") is None


@pytest.fixture
def demo(fresh_db):
    """The demo after inbox_note.md: a rent decision (grounded in a note chunk), rent 14500 today from the signed
    statement, 16000 scheduled for January from the inbox note."""
    for doc_id, path, source, doc_type, status in [
            ("d_dec", "notes/rent_decision.md", "note", "note", "unsigned"),
            ("d_bank", "pdfs/bank_statement_signed.pdf", "pdf", "bank_statement", "issuer_signed"),
            ("d_inbox", "notes/inbox_note.md", "note", "note", "unsigned")]:
        db.insert("documents", {"doc_id": doc_id, "path": path, "source": source, "doc_type": doc_type,
                                "signature_status": status, "ingested_at": "2026-09-30T00:00:00Z"})
    for cid, doc_id, loc, text in [
            ("c_dec", "d_dec", "note: rent_decision.md", "I decided to renew only if rent stays under ₹15,000."),
            ("c_bank", "d_bank", "page 1", "2026-08-05 UPI/RENT/RAVI KUMAR 14,500.00 65,750.00"),
            ("c_inbox", "d_inbox", "note: inbox_note.md", "Landlord said rent goes to ₹16k from January.")]:
        db.insert("chunks", {"chunk_id": cid, "doc_id": doc_id, "locator": loc, "text": text})
    db.insert("entities", {"entity_id": "e_dec", "type": "DECISION", "name": "I decided to renew only if rent stays "
                           "under ₹15,000", "norm_name": "x", "attrs_json": json.dumps(
                               {"quote": "I decided to renew only if rent stays under ₹15,000.", "date": "2026-09-18"})})
    db.insert_edges([{"edge_id": "x_d", "src": OWNER_ENTITY_ID, "rel": "DECIDED", "dst": "e_dec",
                      "valid_from": "2026-09-18", "valid_to": None, "source_chunk_id": "c_dec"}])
    for fid, value, source, doc_id, quote, vf in [
            ("f_now", "14500", "issuer_doc", "d_bank", "2026-08-05 UPI/RENT/RAVI KUMAR 14500", "2026-08-05"),
            ("f_jan", "16000", "extracted", "d_inbox", "Landlord said rent goes to 16000 from January.",
             "2027-01-01")]:
        db.insert("facts", {"fact_id": fid, "entity_id": OWNER_ENTITY_ID, "field": "rent_amount", "value": value,
                            "source_type": source, "doc_id": doc_id, "quote": quote, "valid_from": vf,
                            "confidence": "high", "created_at": db.utc_now()})
    return db


@pytest.mark.parametrize("question", ["Should I still renew the flat?", "Does my renewal condition still hold?",
                                      "What was my decision about renewing the flat?"])
def test_the_demo_questions_get_the_check(demo, question):
    [chunk] = chat.condition_chunks(question, [], TODAY)
    assert chunk.chunk_id == "c_dec" and chunk.locator == "note: rent_decision.md"  # cited as the decision
    assert chunk.text == (
        'condition check (computed) for "I decided to renew only if rent stays under ₹15,000." (rent_amount < '
        "15000): outcome: the condition stops being met on 2027-01-01. rent_amount today = 14500 (bank-signed "
        "statement, bank_statement_signed.pdf) -> met; from 2027-01-01 = 16000 (from your notes, inbox_note.md) "
        "-> NOT met")


@pytest.mark.parametrize("question", ["What's my rent?", "What is my salary?", "Who is my landlord?"])
def test_questions_not_about_the_decision_get_none(demo, question):
    # sharing only the condition's subject word ("rent") is not enough; an unrelated line confused the model before
    assert chat.condition_chunks(question, [], TODAY) == []


def test_a_linked_decision_counts_even_without_a_shared_word(demo):
    assert len(chat.condition_chunks("What about the flat?", ["e_dec"], TODAY)) == 1


def test_outcomes(demo):
    # once January has come, the condition is simply not met
    [chunk] = chat.condition_chunks("Does my renewal condition still hold?", [], "2027-01-02")
    assert "outcome: the condition is NOT met today" in chunk.text
    with db.connect() as conn:
        conn.execute("DELETE FROM facts WHERE fact_id = 'f_jan'")
    [chunk] = chat.condition_chunks("Does my renewal condition still hold?", [], TODAY)
    assert "outcome: the condition is met." in chunk.text


def test_same_condition_twice_is_checked_once(demo):
    db.insert("entities", {"entity_id": "e_dec2", "type": "DECISION", "name": "I will probably renew if the rent "
                           "stays below 15000", "norm_name": "y", "attrs_json": json.dumps(
                               {"quote": "I will probably renew if the rent stays below 15000."})})
    assert len(chat.condition_chunks("Should I still renew?", [], TODAY)) == 1


def test_the_check_reaches_the_prompt_first(demo, monkeypatch):
    monkeypatch.setattr(chat, "retrieve", lambda q: ([], [], [], {}))  # no search, no entity-name embeddings
    captured = {}

    def fake_stream(messages, stats=None):
        captured["prompt"] = messages[-1]["content"]
        yield "Your condition stops being met on 2027-01-01 [1]."

    monkeypatch.setattr(chat.llm, "chat_stream", fake_stream)
    final = chat.answer("Does my renewal condition still hold?", [])
    first = captured["prompt"].split('<chunk n="1"', 1)[1].split("</chunk>", 1)[0]
    assert "condition check (computed)" in first
    assert final.citation_ok and final.citations[0].chunk_id == "c_dec"


# --- real model ----------------------------------------------------------------------------------------------

NOTES = {
    "rent_decision.md": "# Rent Renewal Decision\n\nDate: 18 September 2026\n\nI decided to renew only if rent stays "
                        "under ₹15,000.",
    "rent.md": "Rent\n\nDate: 18 September 2026\n\nRavi Kumar confirmed the current rent is ₹14,500 a month.",
    "inbox_note.md": "# Inbox Note\n\nDate: 27 September 2026\n\nJust got off the phone with Ravi Kumar.\n"
                     "Landlord said rent goes to ₹16k from January.\nGuess we are moving.\n",
}


@pytest.mark.llm
def test_real_model_reports_the_condition_breaking(fresh_db, tmp_path, monkeypatch):
    if date.today() >= date(2027, 1, 1):
        pytest.skip("the January change is in effect from 2027-01-01")
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    monkeypatch.setattr(config, "VAULT_DIR", vault)
    monkeypatch.setattr(embed, "_index", None)
    for name, text in NOTES.items():
        (vault / "notes" / name).write_text(text, encoding="utf-8")
        ingest.ingest_file(vault / "notes" / name)
    final = chat.answer("Does my renewal condition still hold?", [])
    print(f"\n[conditions llm] {ascii(final.answer)}")
    answer = final.answer.replace(",", "")
    assert final.citation_ok and "16000" in answer, final.answer
    assert "2027-01-01" in answer or "January" in answer, final.answer
