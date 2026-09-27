import json
import re

from kavach.models import CreateReminderArgs, CreateReminderCall, Plan, Task


def test_schema_creates_every_table(fresh_db):
    names = {r["name"] for r in fresh_db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(fresh_db.TABLES) <= names
    fresh_db.init_db()  # idempotent


def test_new_id_and_timestamp_format(fresh_db):
    assert re.fullmatch(r"d_[0-9a-f]{10}", fresh_db.new_id("d"))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", fresh_db.utc_now())


def test_insert_update_and_typed_readers(fresh_db):
    fresh_db.insert("documents", {"doc_id": "d_1", "path": "vault/pdfs/a.pdf", "source": "pdf",
                                  "signature_status": "unsigned", "ingested_at": "2026-09-26T10:00:00Z"})
    fresh_db.insert("chunks", {"chunk_id": "c_1", "doc_id": "d_1", "locator": "page 1", "text": "hello"})
    fresh_db.insert("entities", {"entity_id": "e_owner", "type": "PERSON", "name": "Owner", "norm_name": "owner",
                                 "attrs_json": "{}"})
    fresh_db.insert("entities", {"entity_id": "e_2", "type": "PERSON", "name": "Ramesh", "norm_name": "ramesh",
                                 "attrs_json": json.dumps({"email": "r@example.com"})})
    fresh_db.insert("edges", {"edge_id": "x_1", "src": "e_2", "rel": "LANDLORD_OF", "dst": "e_owner",
                              "source_chunk_id": "c_1"})
    fresh_db.insert("facts", {"fact_id": "f_1", "entity_id": "e_owner", "field": "rent_amount", "value": "15000",
                              "source_type": "extracted", "confidence": "high", "created_at": "2026-09-26T10:00:00Z"})
    fresh_db.insert("facts", {"fact_id": "f_0", "entity_id": "e_owner", "field": "rent_amount", "value": "14000",
                              "source_type": "extracted", "confidence": "high", "superseded_by": "f_1",
                              "valid_to": "2026-04-01", "created_at": "2026-09-25T10:00:00Z"})

    assert [d.doc_id for d in fresh_db.list_documents()] == ["d_1"]
    assert fresh_db.get_chunk("c_1").text == "hello"
    assert fresh_db.get_chunk("missing") is None
    assert fresh_db.list_entities("PERSON")[1].attrs == {"email": "r@example.com"}
    assert [f.fact_id for f in fresh_db.list_facts("rent_amount", current=True)] == ["f_1"]
    assert len(fresh_db.list_facts()) == 2

    g = fresh_db.graph("e_2", hops=1)
    assert {n.id for n in g.nodes} == {"e_2", "e_owner"} and g.edges[0].rel == "LANDLORD_OF"
    assert len(fresh_db.graph().nodes) == 2
    # doc_id comes from the source chunk; null once that chunk is gone (replaced or removed version)
    assert g.edges[0].doc_id == "d_1"
    fresh_db.insert("edges", {"edge_id": "x_2", "src": "e_owner", "rel": "RELATES_TO", "dst": "e_2",
                              "source_chunk_id": "c_gone", "valid_to": "2026-09-26"})
    assert {e.id: e.doc_id for e in fresh_db.graph().edges} == {"x_1": "d_1", "x_2": None}

    assert fresh_db.update("documents", "doc_id", "d_1", {"removed_at": "2026-09-26T11:00:00Z"}) == 1
    assert fresh_db.list_documents() == []


def test_task_round_trip(fresh_db):
    plan = Plan(instruction="remind me", calls=[CreateReminderCall(
        args=CreateReminderArgs(title="Renew", date="2027-03-01"), preview="Reminder")])
    fresh_db.save_task(Task(task_id="t_1", instruction="remind me", plan=plan, status="planned",
                            created_at="2026-09-26T10:00:00Z"))
    task = fresh_db.get_task("t_1")
    assert task.plan.calls[0].tool == "create_reminder"
    assert [t.task_id for t in fresh_db.list_tasks("planned")] == ["t_1"]


def test_audit_readers_on_empty_log(fresh_db):
    assert fresh_db.audit_entries() == []
    assert fresh_db.last_audit_hash() == "0" * 64
