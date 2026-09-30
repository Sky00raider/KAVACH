"""Every fixture in fixtures/api validates against its model, so fixtures cannot drift from the contract."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from kavach import models as m

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "api"

# CONTRACT §15 names -> response type (chunk is extra: citation popovers in fixture mode)
SCHEMAS = {
    "health": m.Health,
    "documents": list[m.Document],
    "identity": m.Identity,
    "entities": list[m.Entity],
    "facts": list[m.Fact],
    "graph": m.Graph,
    "chunk": m.Chunk,
    "chat": m.ChatResult,
    "memory_timeline": list[m.FactVersion],
    "queue": m.QueueOut,
    "wallet": m.WalletStatus,
    "tasks": list[m.Task],
    "task_planned": m.Task,
    "audit": m.AuditOut,
    "ingest_events": m.IngestEvents,
    "outbox": list[m.OutboxItem],
    "r_identity": m.RIdentity,
    "r_requests": list[m.RRequest],
    "r_storage": m.RStorage,
}


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_fixture_matches_model(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    TypeAdapter(SCHEMAS[name]).validate_python(data)


def test_chat_stream_follows_protocol():
    lines = (FIXTURES / "chat_stream.jsonl").read_text(encoding="utf-8").splitlines()
    events = [TypeAdapter(m.ChatEvent).validate_json(line) for line in lines]
    kinds = [e.event for e in events]
    assert kinds[0] == "meta" and kinds[-2:] == ["final", "done"]
    assert set(kinds[1:-2]) == {"token"}
    assert "".join(e.data.text for e in events if e.event == "token") == events[-2].data.answer


def test_no_unlisted_fixture_files():
    names = {p.name for p in FIXTURES.iterdir()}
    assert names == {f"{n}.json" for n in SCHEMAS} | {"chat_stream.jsonl"}


def test_audit_fixture_hash_chain_is_valid():
    data = json.loads((FIXTURES / "audit.json").read_text(encoding="utf-8"))
    prev = "0" * 64
    for e in sorted(data["entries"], key=lambda e: e["seq"]):
        detail_json = json.dumps(e["detail"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected = hashlib.sha256((prev + e["ts"] + e["event"] + (e["ref_id"] or "") + detail_json).encode()).hexdigest()
        assert e["prev_hash"] == prev and e["entry_hash"] == expected, e["seq"]
        prev = e["entry_hash"]
