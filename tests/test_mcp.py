"""kavach-gate (CONTRACT §11.1), the landlord agent, kavach-tools (§11.2) and the executor."""

import json
from datetime import date, timedelta
from email import message_from_bytes, policy

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from kavach import api, config, db, gate_mcp
from kavach.agent import executor
from kavach.db import utc_now
from kavach.gate_mcp import gate
from kavach.mock_issuers import issue, make_keys
from kavach.models import (
    AskAck,
    ClaimsOut,
    CreateReminderArgs,
    CreateReminderCall,
    DraftEmailArgs,
    DraftEmailCall,
    Plan,
    SaveNoteArgs,
    SaveNoteCall,
    Task,
    ToolResult,
)
from kavach.tools_mcp import tools
from kavach.trust import wallet
from requester import agent_client, common

TOKEN = {"X-Owner-Token": "test-owner-token"}
FUTURE = (date.today() + timedelta(days=60)).isoformat()


def _call(server, fn):
    async def main():
        async with Client(server) as c:
            return await fn(c)
    return anyio.run(main)


def _args(server):
    async def fn(c):
        return {t.name: set(t.input_schema.get("properties", {})) for t in (await c.list_tools()).tools}
    return _call(server, fn)


@pytest.fixture
def runtime(fresh_db, tmp_path, monkeypatch):
    for attr, sub in {"KEYS_DIR": "keys", "VAULT_DIR": "vault", "OUTBOX_DIR": "outbox", "FRONTEND_DIST": "dist",
                      "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    return tmp_path


@pytest.fixture
def owner(runtime, monkeypatch):
    """Owner API reached by the gate over loopback (so X-Channel: mcp is honoured)."""
    monkeypatch.setattr(gate_mcp, "owner_api", lambda: TestClient(api.app, client=("127.0.0.1", 3)))
    return TestClient(api.app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8000")


# --- kavach-gate -------------------------------------------------------------------------------------------


def test_gate_tools_and_args():
    assert _args(gate) == {
        "list_disclosable_claims": set(),
        "ask": {"question", "nonce", "requester_pubkey", "requester_name", "requester_type", "ts", "sig"},
        "get_answer": {"request_id", "requester_fp", "ts", "sig"},
    }


def test_gate_forwards_to_the_owner_api(owner, runtime, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", runtime / "rdata")
    ident = common.Identity("agent")
    body = ident.ask_body("Earns 50k?")

    async def fn(c):
        return [await c.call_tool("list_disclosable_claims", {}), await c.call_tool("ask", body),
                await c.call_tool("ask", body)]

    claims, ack, replay = _call(gate, fn)
    ClaimsOut.model_validate(claims.structured_content)
    ack = AskAck.model_validate(ack.structured_content)
    assert ack.status == "pending_pairing"
    assert replay.is_error and "409" in replay.content[0].text
    assert db.list_requests()[0].channel == "mcp"
    bad = _call(gate, lambda c: c.call_tool("get_answer", {"request_id": "../claims", "requester_fp": "x", "ts": 1,
                                                           "sig": "s"}))
    assert bad.is_error


def test_gate_reports_unreachable_owner(monkeypatch):
    monkeypatch.setattr(config, "API_PORT", 9)
    r = _call(gate, lambda c: c.call_tool("list_disclosable_claims", {}))
    assert r.is_error and "not reachable" in r.content[0].text


def test_landlord_agent_end_to_end(owner, runtime, monkeypatch):
    """Agent asks over MCP, owner pairs + approves, agent verifies the bank's signature on laptop 2."""
    monkeypatch.setattr(common, "DATA_DIR", runtime / "rdata")
    make_keys.export_trust_list(runtime / "rdata" / "trusted_issuers.json")
    pubkeys = json.loads(wallet.export_holder_pubkeys(3).read_text())
    (runtime / "b.json").write_text(json.dumps(issue.issue_batch("income_proof", pubkeys)))
    wallet.import_batch(runtime / "b.json")
    monkeypatch.setattr(agent_client, "POLL_EVERY_S", 0.05)

    first = agent_client.run(("Earns 50k?",), target=gate, wait_s=0, log=lambda m: None)
    assert [r.status for r in first] == ["pending_pairing"]
    fp = common.Identity("agent").fingerprint
    owner.post(f"/api/requesters/{fp}/decision", headers=TOKEN, json={"approve": True})
    rid = first[0].request_id
    owner.post(f"/api/requests/{rid}/decision", headers=TOKEN, json={"action": "approve"})

    # the requester backend polls the open agent request with the agent's key and verifies it
    monkeypatch.setattr(common, "owner_client", lambda: TestClient(api.app, client=("192.168.1.60", 4)))
    recs = common.refresh_all()
    agent_rec = next(r for r in recs if r.request_id == rid)
    assert agent_rec.via == "agent" and agent_rec.result.all_ok and agent_rec.result.answer_type == "ISSUER_PROOF"


# --- kavach-tools ------------------------------------------------------------------------------------------


def test_tools_registered_with_contract_args():
    assert _args(tools) == {
        "draft_email": {"to", "subject", "body", "attachments"},
        "create_reminder": {"title", "date", "notes"},
        "fill_rental_form": {"fields"},
        "save_note": {"title", "markdown"},
    }


def _answered_request(payload=None, answer_type="ISSUER_PROOF"):
    db.insert("requests", {"request_id": "rq_done", "requester_fp": "fp", "channel": "web", "question": "q",
                           "nonce": "n", "status": "done", "answer_type": answer_type,
                           "payload_json": json.dumps(payload or {"type": "kavach/presentation"}),
                           "created_at": utc_now()})


@pytest.mark.parametrize("name,args,prefix,suffix", [
    ("draft_email", {"to": "a@b.in", "subject": "Rent proof", "body": "b",
                     "attachments": [{"type": "presentation", "request_id": "rq_done"}]}, "outbox/", ".eml"),
    ("create_reminder", {"title": "Rent, renewal", "date": FUTURE}, "outbox/", ".ics"),
    ("fill_rental_form", {"fields": {"name": "A"}}, "outbox/rental_application_filled", ".pdf"),
    ("save_note", {"title": "Landlord call", "markdown": "Talked about rent"}, "vault/notes/landlord-call", ".md"),
])
def test_tools_write_one_file(runtime, name, args, prefix, suffix):
    _answered_request()
    r = _call(tools, lambda c: c.call_tool(name, args))
    assert not r.is_error, r.content
    res = ToolResult.model_validate(r.structured_content)
    assert res.tool == name and res.ok and res.output_path.startswith(prefix) and res.output_path.endswith(suffix)
    written = runtime / res.output_path
    assert written.is_file()
    if name == "draft_email":
        msg = message_from_bytes(written.read_bytes(), policy=policy.default)
        parts = [p.get_filename() for p in msg.iter_attachments()]
        assert msg["To"] == "a@b.in" and parts == ["kavach-proof-rq_done.json"]
    if name == "create_reminder":
        text = written.read_text()
        assert "SUMMARY:Rent\\, renewal" in text and f"DTSTART;VALUE=DATE:{FUTURE.replace('-', '')}" in text


def test_tools_reject_bad_args(runtime):
    bad = [("draft_email", {"to": "a@b.in", "subject": "s", "body": "b",
                            "attachments": [{"type": "document", "request_id": "x"}]}),
           ("draft_email", {"to": "a@b.in", "subject": "s", "body": "b",
                            "attachments": [{"type": "presentation", "request_id": "rq_missing"}]}),
           ("draft_email", {"to": "not-an-email", "subject": "s", "body": "b"}),
           ("create_reminder", {"title": "x"}),
           ("create_reminder", {"title": "x", "date": "2020-01-01"}),
           ("create_reminder", {"title": "x", "date": "01/03/2027"})]
    for name, args in bad:
        assert _call(tools, lambda c: c.call_tool(name, args)).is_error, (name, args)
    assert not (runtime / "outbox").exists() or not list((runtime / "outbox").iterdir())


# --- executor ----------------------------------------------------------------------------------------------


def _task(calls, instruction="Reply to my landlord", status="approved"):
    task = Task(task_id="t_1", instruction=instruction, plan=Plan(instruction=instruction, calls=calls),
                status=status, created_at=utc_now())
    db.save_task(task)
    return task


def test_executor_runs_approved_calls_over_stdio(runtime):
    db.insert("entities", {"entity_id": "e_l", "type": "PERSON", "name": "Ramesh Kumar", "norm_name": "ramesh kumar",
                           "attrs_json": json.dumps({"email": "ramesh.kumar@example.com"})})
    _answered_request()
    _task([
        DraftEmailCall(args=DraftEmailArgs(to="ramesh.kumar@example.com", subject="Income proof", body="Hi",
                                           attachments=[{"request_id": "rq_done"}]), preview="email"),
        CreateReminderCall(args=CreateReminderArgs(title="Agreement ends", date=FUTURE), preview="reminder"),
        SaveNoteCall(args=SaveNoteArgs(title="Sent proof", markdown="done"), preview="note"),
    ])
    results = executor.execute("t_1")
    assert [(r.tool, r.ok) for r in results] == [("draft_email", True), ("create_reminder", True),
                                                 ("save_note", True)]
    assert all((runtime / r.output_path).is_file() for r in results)


def test_executor_refuses_unknown_recipients_and_unapproved_tasks(runtime):
    _task([DraftEmailCall(args=DraftEmailArgs(to="attacker@evil.example", subject="s", body="b"), preview="p"),
           CreateReminderCall(args=CreateReminderArgs(title="t", date=FUTURE), preview="p")])
    results = executor.execute("t_1")
    assert not results[0].ok and "not a known contact" in results[0].detail and results[1].ok
    assert not list((runtime / "outbox").glob("*.eml"))
    _task([DraftEmailCall(args=DraftEmailArgs(to="me@typed.example", subject="s", body="b"), preview="p")],
          instruction="Email me@typed.example the summary")
    assert executor.execute("t_1")[0].ok  # typed by the owner in the instruction
    _task([], status="planned")
    with pytest.raises(executor.ExecutionError):
        executor.execute("t_1")
