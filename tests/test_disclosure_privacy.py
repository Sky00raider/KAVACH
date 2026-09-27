"""Proposal.reason is owner-facing only: it must never reach a requester (hard rule 5).

Every outcome (ISSUER_PROOF, OWNER_ATTESTED, CANNOT_CONFIRM, REFUSED) runs through the real consent pipeline
with `decide` wrapped so each reason is a unique marker; then every requester-facing surface is searched for it:
the /api/ask ack, /api/ask/{id}, MCP ask + get_answer, the presentation / attestation payloads, and the
requester app's own records (/r/requests, /r/storage).
"""

from __future__ import annotations

import json

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from kavach import api, config, db, gate_mcp
from kavach.gate_mcp import gate
from kavach.mock_issuers import issue, make_keys
from kavach.trust import consent, wallet
from requester import app as rapp, common

TOKEN = {"X-Owner-Token": "test-owner-token"}
MARKER = "REASON-MARKER"
QUESTIONS = {
    "Does the tenant earn at least 50k?": "ISSUER_PROOF",
    "Does the tenant earn at least 60k?": "OWNER_ATTESTED",
    "Is the tenant over 21 years old?": "CANNOT_CONFIRM",
    "What is the tenant's exact salary?": "REFUSED",
}


@pytest.fixture
def owner_and_landlord(fresh_db, tmp_path, monkeypatch):
    for attr, sub in {"KEYS_DIR": "keys", "VAULT_DIR": "vault", "FRONTEND_DIST": "dist",
                      "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path / "rdata")
    make_keys.export_trust_list(tmp_path / "rdata" / "trusted_issuers.json")
    pubkeys = json.loads(wallet.export_holder_pubkeys(4).read_text())
    (tmp_path / "b.json").write_text(json.dumps(issue.issue_batch("income_proof", pubkeys)))
    wallet.import_batch(tmp_path / "b.json")
    # a grounded bank-statement fact, so the 60k question becomes an owner attestation
    db.insert("documents", {"doc_id": "d_stmt", "path": "pdfs/bank_statement.pdf", "source": "pdf",
                            "signature_status": "issuer_signed", "text_hash": "h", "ingested_at": db.utc_now()})
    db.insert("facts", {"fact_id": "f_income", "entity_id": "e_owner", "field": "monthly_income", "value": "62000",
                        "source_type": "issuer_doc", "doc_id": "d_stmt", "quote": "62,000", "confidence": "high",
                        "valid_from": "2026-04-01", "created_at": db.utc_now()})

    real = consent.decide.decide
    seen: list[str] = []

    def marked(claim, fp):
        p = real(claim, fp)
        seen.append(p.answer_type)
        return p.model_copy(update={"reason": f"{MARKER}-{len(seen)} {p.reason}"})

    monkeypatch.setattr(consent.decide, "decide", marked)
    lan = lambda: TestClient(api.app, client=("192.168.1.60", 40000), base_url="http://owner:8000")  # noqa: E731
    monkeypatch.setattr(common, "owner_client", lan)
    monkeypatch.setattr(gate_mcp, "owner_api", lambda: TestClient(api.app, client=("127.0.0.1", 3)))
    owner = TestClient(api.app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8000")
    return owner, TestClient(rapp.app), seen


def _mcp(fn):
    async def main():
        async with Client(gate) as c:
            return await fn(c)
    return anyio.run(main)


def test_proposal_reason_never_reaches_a_requester(owner_and_landlord):
    owner, landlord, seen = owner_and_landlord
    web = common.Identity("web")
    agent = common.Identity("agent")
    surfaces: list[object] = []
    asked: list[tuple[common.Identity, str]] = []

    for question in QUESTIONS:
        ack, _ = common.http_ask(web, question)  # the same POST /api/ask the requester app makes
        surfaces.append(ack)
        asked.append((web, ack["request_id"]))
        surfaces.append(landlord.post("/r/ask", json={"question": question}).json())
    mcp_ack = _mcp(lambda c: c.call_tool("ask", agent.ask_body("Does the tenant earn at least 50k?")))
    surfaces.append(mcp_ack.structured_content)
    asked.append((agent, mcp_ack.structured_content["request_id"]))

    # owner pairs both requesters and approves every proposal that waits for a decision
    for r in owner.get("/api/queue", headers=TOKEN).json()["requesters"]:
        owner.post(f"/api/requesters/{r['fingerprint']}/decision", headers=TOKEN, json={"approve": True})
    queue = owner.get("/api/queue", headers=TOKEN).json()["requests"]
    assert all(MARKER in r["proposal"]["reason"] for r in queue if r["proposal"])  # the owner does see it
    for r in queue:
        if r["status"] == "pending":
            assert owner.post(f"/api/requests/{r['request_id']}/decision", headers=TOKEN,
                              json={"action": r["proposal"]["actions"][0]}).status_code == 200
    assert set(seen) == set(QUESTIONS.values())

    answers = []
    for ident, rid in asked:
        answers.append(common.http_poll(ident, rid))  # GET /api/ask/{id}
        auth = ident.poll_auth(rid)
        res = _mcp(lambda c, rid=rid, auth=auth: c.call_tool("get_answer", {"request_id": rid, **auth}))
        assert not res.is_error
        surfaces.append(res.structured_content)
        surfaces.extend(c.text for c in res.content if hasattr(c, "text"))
    surfaces.extend(a.model_dump(mode="json") for a in answers)
    assert {a.answer_type for a in answers} == set(QUESTIONS.values())
    assert {a.payload["type"] for a in answers if a.payload} == {"kavach/presentation", "kavach/attestation"}
    surfaces.append(landlord.get("/r/requests").json())
    surfaces.append(landlord.get("/r/storage").json())
    for f in sorted(common.DATA_DIR.glob("*.json")):
        surfaces.append(f.read_text(encoding="utf-8"))

    for s in surfaces:
        text = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
        assert MARKER not in text
        assert "pdfs/bank_statement.pdf" not in text and "62000" not in text and "62,000" not in text
