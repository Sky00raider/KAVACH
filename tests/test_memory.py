"""memory.py (BRAIN step 7): teach, supersession ordering, chat candidates, decide_candidate, timeline."""

from __future__ import annotations

import pytest

from kavach import db
from kavach.brain import memory
from kavach.models import OWNER_ENTITY_ID, Entity, Fact, MemoryCandidate, TeachResult


# --- teach ---------------------------------------------------------------------------------------------------


def test_teach_stores_an_owner_stated_fact(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(
        field="monthly_income", value="70000", valid_from="October"))
    result = memory.teach("My salary went up to Rs 70000 from October.")
    assert isinstance(result, TeachResult)
    assert result.fact.field == "monthly_income" and result.fact.value == "70000"
    assert result.fact.source_type == "owner_stated" and result.fact.confidence == "high"
    assert result.fact.quote == "My salary went up to Rs 70000 from October."
    assert result.superseded == []
    stored = db.list_facts("monthly_income")[0]
    assert stored.fact_id == result.fact.fact_id


def test_teach_supersedes_the_current_fact(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="rent_amount", value="15000"))
    first = memory.teach("Rent is 15000.")
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="rent_amount", value="16000"))
    second = memory.teach("Rent is now 16000.")
    assert second.superseded == [Fact(**{**first.fact.model_dump(),
                                        "valid_to": first.fact.valid_from, "superseded_by": second.fact.fact_id})]


def test_teach_cleans_a_messy_field_name(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="Gym Membership Fee!!", value="2500"))
    result = memory.teach("My gym fee is 2500.")
    assert result.fact.field == "gym_membership_fee"


def test_teach_raises_when_nothing_usable_comes_back(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="!!!", value="x"))
    with pytest.raises(ValueError):
        memory.teach("mumble mumble")
    assert db.list_facts() == []


def test_teach_survives_a_model_failure(fresh_db, monkeypatch):
    from kavach.brain import llm

    def down(*a, **k):
        raise llm.LLMError("ollama down")

    monkeypatch.setattr(memory, "_parse_taught", down)
    with pytest.raises(ValueError):
        memory.teach("My salary is 70000")


# --- db.supersede_and_insert_fact temporal ordering (BRAIN step 7 owner instructions) -------------------------


def _fact(field, value, valid_from, **kw):
    return {"fact_id": db.new_id("f"), "entity_id": OWNER_ENTITY_ID, "field": field, "value": value,
           "source_type": "extracted", "doc_id": None, "quote": value, "valid_from": valid_from,
           "valid_to": None, "superseded_by": None, "confidence": "high", "created_at": db.utc_now(), **kw}


def test_out_of_order_ingest_never_disturbs_the_newer_fact(fresh_db):
    newer = _fact("rent_amount", "16000", "2026-06-01")
    db.supersede_and_insert_fact(newer, today="2026-09-20")
    older = _fact("rent_amount", "15000", "2026-01-01")
    closed = db.supersede_and_insert_fact(older, today="2026-09-20")
    assert closed == []  # the historical insert closes itself, not the newer fact
    older_row = db.fetch_one("SELECT * FROM facts WHERE fact_id = ?", (older["fact_id"],))
    assert (older_row["valid_to"], older_row["superseded_by"]) == ("2026-06-01", newer["fact_id"])
    newer_row = db.fetch_one("SELECT * FROM facts WHERE fact_id = ?", (newer["fact_id"],))
    assert newer_row["valid_to"] is None and newer_row["superseded_by"] is None
    assert db.current_fact(OWNER_ENTITY_ID, "rent_amount", "2026-09-20")["fact_id"] == newer["fact_id"]


def test_a_future_dated_fact_does_not_close_the_current_one_yet(fresh_db):
    current = _fact("rent_amount", "15000", "2026-06-01")
    db.supersede_and_insert_fact(current, today="2026-09-20")
    scheduled = _fact("rent_amount", "16000", "2027-01-01")
    closed = db.supersede_and_insert_fact(scheduled, today="2026-09-20")
    assert closed == []  # not yet in effect: nothing closes yet
    current_row = db.fetch_one("SELECT * FROM facts WHERE fact_id = ?", (current["fact_id"],))
    assert current_row["valid_to"] is None and current_row["superseded_by"] is None

    # before the scheduled date: the old value is still current, and shows up as scheduled
    assert db.current_fact(OWNER_ENTITY_ID, "rent_amount", "2026-09-20")["fact_id"] == current["fact_id"]
    assert [f["fact_id"] for f in db.scheduled_facts(OWNER_ENTITY_ID, "2026-09-20")] == [scheduled["fact_id"]]

    # once the scheduled date arrives, it becomes current on its own (no write needed)
    assert db.current_fact(OWNER_ENTITY_ID, "rent_amount", "2027-01-01")["fact_id"] == scheduled["fact_id"]
    assert db.scheduled_facts(OWNER_ENTITY_ID, "2027-01-01") == []


def test_an_already_effective_fact_closes_every_older_open_fact(fresh_db):
    a = _fact("rent_amount", "14000", "2026-01-01")
    db.supersede_and_insert_fact(a, today="2026-09-20")
    b = _fact("rent_amount", "15000", "2027-01-01")  # scheduled: does not close `a`
    db.supersede_and_insert_fact(b, today="2026-09-20")
    c = _fact("rent_amount", "16000", "2027-06-01")
    closed = db.supersede_and_insert_fact(c, today="2027-07-01")  # already in effect, later than both a and b
    assert {r["fact_id"] for r in closed} == {a["fact_id"], b["fact_id"]}
    for fid in (a["fact_id"], b["fact_id"]):
        row = db.fetch_one("SELECT * FROM facts WHERE fact_id = ?", (fid,))
        assert (row["valid_to"], row["superseded_by"]) == ("2027-06-01", c["fact_id"])
    assert db.current_fact(OWNER_ENTITY_ID, "rent_amount", "2027-07-01")["fact_id"] == c["fact_id"]


# --- chat memory candidates ------------------------------------------------------------------------------------


def test_candidates_from_statements_empty_input_never_calls_the_model(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_extract_candidates", lambda text: (_ for _ in ()).throw(AssertionError("called")))
    assert memory.candidates_from_statements([], "any message") == []


def test_candidates_from_statements_stores_a_grounded_fact_candidate(fresh_db, monkeypatch):
    message = "By the way my rent went up to 16000 from January."
    monkeypatch.setattr(memory, "_extract_candidates", lambda text: memory.CandidateExtraction(candidates=[
        memory.XCandidate(kind="fact", statement=message, field="rent_amount", value="16000", valid_from="January")]))
    out = memory.candidates_from_statements([message], message)
    assert len(out) == 1 and isinstance(out[0], MemoryCandidate)
    assert out[0].kind == "fact" and out[0].field == "rent_amount" and out[0].value == "16000"
    assert out[0].status == "pending" and out[0].statement == message
    assert db.fetch_one("SELECT * FROM memory_candidates WHERE candidate_id = ?", (out[0].candidate_id,)) is not None


def test_candidates_from_statements_drops_a_fabricated_statement(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_extract_candidates", lambda text: memory.CandidateExtraction(candidates=[
        memory.XCandidate(kind="fact", statement="Something never said.", field="rent_amount", value="16000")]))
    assert memory.candidates_from_statements(["My rent is fine."], "My rent is fine.") == []


def test_candidates_from_statements_decision_needs_decision_cues(fresh_db, monkeypatch):
    message = "I like my new flat a lot."
    monkeypatch.setattr(memory, "_extract_candidates", lambda text: memory.CandidateExtraction(candidates=[
        memory.XCandidate(kind="decision", statement=message)]))
    assert memory.candidates_from_statements([message], message) == []  # no decision cue: dropped


def test_candidates_from_statements_decision_resolves_an_existing_project(fresh_db, monkeypatch):
    db.insert("entities", {"entity_id": "e_proj", "type": "PROJECT", "name": "Flat move 2026",
                           "norm_name": "flat move 2026", "attrs_json": "{}"})
    message = "Decided to renew only if rent stays under 15000."
    monkeypatch.setattr(memory, "_extract_candidates", lambda text: memory.CandidateExtraction(candidates=[
        memory.XCandidate(kind="decision", statement=message, project="Flat move 2026")]))
    out = memory.candidates_from_statements([message], message)
    assert len(out) == 1 and out[0].kind == "decision" and out[0].project_entity_id == "e_proj"


def test_candidates_from_statements_survives_a_model_failure(fresh_db, monkeypatch):
    from kavach.brain import llm

    def down(text):
        raise llm.LLMError("ollama down")

    monkeypatch.setattr(memory, "_extract_candidates", down)
    assert memory.candidates_from_statements(["I decided to move out."], "I decided to move out.") == []


# --- decide_candidate ---------------------------------------------------------------------------------------


def _store_candidate(**kw):
    row = {"candidate_id": db.new_id("mc"), "statement": "x", "kind": "fact", "field": None, "value": None,
          "valid_from": None, "project_entity_id": None, "status": "pending", "created_at": db.utc_now(), **kw}
    db.insert("memory_candidates", row)
    return row


def test_decide_candidate_discard(fresh_db):
    row = _store_candidate(field="rent_amount", value="16000", valid_from="2026-09-20")
    assert memory.decide_candidate(row["candidate_id"], False) is None
    assert db.fetch_one("SELECT status FROM memory_candidates WHERE candidate_id = ?",
                        (row["candidate_id"],))["status"] == "discarded"
    assert db.list_facts() == []


def test_decide_candidate_accept_fact(fresh_db):
    row = _store_candidate(field="rent_amount", value="16000", valid_from="2026-09-20", statement="Rent is 16000.")
    stored = memory.decide_candidate(row["candidate_id"], True)
    assert isinstance(stored, Fact) and stored.field == "rent_amount" and stored.source_type == "owner_stated"
    assert db.fetch_one("SELECT status FROM memory_candidates WHERE candidate_id = ?",
                        (row["candidate_id"],))["status"] == "accepted"


def test_decide_candidate_accept_decision_creates_entity_and_edges(fresh_db):
    row = _store_candidate(kind="decision", statement="Decided to renew only if rent stays under 15000.",
                           valid_from="2026-09-20", project_entity_id=None)
    stored = memory.decide_candidate(row["candidate_id"], True)
    assert isinstance(stored, Entity) and stored.type == "DECISION"
    edge = db.fetch_one("SELECT * FROM edges WHERE dst = ? AND rel = 'DECIDED'", (stored.entity_id,))
    assert edge is not None and edge["src"] == OWNER_ENTITY_ID and edge["source_chunk_id"] is None
    assert edge["valid_from"] == "2026-09-20"


def test_decide_candidate_accept_decision_links_its_project(fresh_db):
    db.insert("entities", {"entity_id": "e_proj", "type": "PROJECT", "name": "Flat move 2026",
                           "norm_name": "flat move 2026", "attrs_json": "{}"})
    row = _store_candidate(kind="decision", statement="Decided to renew.", valid_from="2026-09-20",
                           project_entity_id="e_proj")
    stored = memory.decide_candidate(row["candidate_id"], True)
    edge = db.fetch_one("SELECT * FROM edges WHERE src = ? AND rel = 'PART_OF'", (stored.entity_id,))
    assert edge is not None and edge["dst"] == "e_proj"


def test_decide_candidate_unknown_or_already_decided_is_none(fresh_db):
    assert memory.decide_candidate("mc_missing", True) is None
    row = _store_candidate(field="rent_amount", value="16000", valid_from="2026-09-20")
    memory.decide_candidate(row["candidate_id"], False)
    assert memory.decide_candidate(row["candidate_id"], True) is None  # already decided, stays discarded


# --- timeline ---------------------------------------------------------------------------------------------


def test_timeline_marks_exactly_one_current_version(fresh_db, monkeypatch):
    # both dates safely in the past, regardless of when this test runs
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="monthly_income", value="62000",
                                                                                valid_from="2020-04-01"))
    memory.teach("Salary is 62000 from April.")
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="monthly_income", value="70000",
                                                                                valid_from="2020-10-01"))
    memory.teach("Salary went up to 70000 from October.")
    versions = memory.timeline("monthly_income")
    assert [v.value for v in versions] == ["62000", "70000"]
    assert [v.current for v in versions] == [False, True]


def test_timeline_filters_to_owner_entity(fresh_db):
    db.insert("facts", {"fact_id": "f_other", "entity_id": "e_someone_else", "field": "rent_amount", "value": "1",
                        "source_type": "extracted", "confidence": "high", "valid_from": "2026-01-01",
                        "created_at": db.utc_now()})
    assert memory.timeline("rent_amount") == []


def test_timeline_all_fields_when_none_given(fresh_db, monkeypatch):
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="rent_amount", value="15000"))
    memory.teach("Rent is 15000.")
    monkeypatch.setattr(memory, "_parse_taught", lambda text: memory.TaughtFact(field="monthly_income", value="62000"))
    memory.teach("Salary is 62000.")
    fields = {v.field for v in memory.timeline(None)}
    assert fields == {"rent_amount", "monthly_income"}
