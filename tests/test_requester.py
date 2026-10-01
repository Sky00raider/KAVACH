"""Requester backend (CONTRACT §12), the verifier (§6.4) and the full two-laptop disclosure flow (M1)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from kavach import api, config, db
from kavach.mock_issuers import issue, make_keys
from kavach.models import RAskOut, RIdentity, RRequest, RStorage
from kavach.trust import crypto, present, wallet
from requester import agent_client, app as rapp, common, verifier

FIXTURES = config.ROOT / "fixtures" / "api"
TOKEN = {"X-Owner-Token": "test-owner-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "dist")
    monkeypatch.setattr(config, "OWNER_URL", "http://127.0.0.1:9")
    return TestClient(rapp.app)


@pytest.fixture
def laptops(fresh_db, tmp_path, monkeypatch):
    """Owner API (reached from a LAN address) + requester app, wallet stocked, trust list copied over."""
    monkeypatch.setattr(config, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(config, "VAULT_DIR", tmp_path / "vault")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "dist")
    monkeypatch.setattr(common, "DATA_DIR", tmp_path / "rdata")
    make_keys.export_trust_list(tmp_path / "rdata" / "trusted_issuers.json")
    for ctype in ("income_proof", "id_card"):
        pubkeys = json.loads(wallet.export_holder_pubkeys(4).read_text())
        path = tmp_path / f"{ctype}.json"
        path.write_text(json.dumps(issue.issue_batch(ctype, pubkeys)))
        wallet.import_batch(path)
    monkeypatch.setattr(common, "owner_client", lambda: TestClient(api.app, client=("192.168.1.60", 40000),
                                                                  base_url="http://owner:8000"))
    owner = TestClient(api.app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8000")
    return owner, TestClient(rapp.app)


def _pair_and_approve(owner, action="approve"):
    queue = owner.get("/api/queue", headers=TOKEN).json()
    for r in queue["requesters"]:
        if r["status"] == "pending":
            assert owner.post(f"/api/requesters/{r['fingerprint']}/decision", headers=TOKEN,
                              json={"approve": True}).status_code == 200
    pending = [r for r in owner.get("/api/queue", headers=TOKEN).json()["requests"] if r["status"] == "pending"]
    for r in pending:
        assert owner.post(f"/api/requests/{r['request_id']}/decision", headers=TOKEN,
                          json={"action": action}).status_code == 200
    return pending


# --- routes ------------------------------------------------------------------------------------------------


def test_identity_is_stable_and_key_never_served(client):
    a = RIdentity.model_validate(client.get("/r/identity").json())
    b = RIdentity.model_validate(client.get("/r/identity").json())
    assert a.fingerprint == b.fingerprint and len(a.fingerprint) == 16 and a.owner_url == config.OWNER_URL
    storage = RStorage.model_validate(client.get("/r/storage").json())
    assert [f.name for f in storage.files] == ["identity.json"]
    assert crypto.b64e((common.DATA_DIR / "web.key").read_bytes()) not in json.dumps(storage.model_dump())


def test_identity_says_the_owner_is_unreachable(client):
    assert client.get("/r/identity").json()["owner_reachable"] is False   # OWNER_URL points at a closed port


def test_identity_says_the_owner_is_reachable(laptops):
    _, landlord = laptops
    assert landlord.get("/r/identity").json()["owner_reachable"] is True   # the in-process owner API answers


def test_ask_without_owner_is_502(client):
    r = client.post("/r/ask", json={"question": "Earns 50k?"})
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]
    assert client.post("/r/ask", json={"question": "  "}).status_code == 422
    assert client.get("/r/requests").json() == []


def test_requests_and_storage_read_data_dir(client):
    common.DATA_DIR.mkdir()
    (common.DATA_DIR / "requests.json").write_bytes((FIXTURES / "r_requests.json").read_bytes())
    (common.DATA_DIR / "identity.json").write_text('{\n  "name": "Ramesh Kumar"\n}')
    (common.DATA_DIR / "requester.key").write_bytes(b"secret")
    reqs = [RRequest.model_validate(r) for r in client.get("/r/requests").json()]
    assert {r.via for r in reqs} == {"web", "agent"}
    storage = RStorage.model_validate(client.get("/r/storage").json())
    assert [f.name for f in storage.files] == ["identity.json", "requests.json"]
    assert storage.files[0].preview_json == '{"name":"Ramesh Kumar"}'
    assert len(storage.files[1].preview_json) <= rapp.PREVIEW_MAX + 1
    assert "secret" not in json.dumps(storage.model_dump())


def test_frontend_is_requester_mode_without_token(client):
    html = client.get("/verify").text
    assert 'window.__KAVACH__={"mode": "requester"}' in html
    assert config.OWNER_TOKEN not in html
    assert client.get("/r/nope").status_code == 404


def test_openapi_has_every_r_route(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {"/r/identity", "/r/ask", "/r/requests", "/r/storage"}


# --- the M1 flow: a bank-signed proof verified on laptop 2 -------------------------------------------------


def test_bank_signed_proof_verifies_with_all_five_checks(laptops):
    owner, landlord = laptops
    out = RAskOut.model_validate(landlord.post("/r/ask", json={"question": "Earns at least 50k?"}).json())
    assert out.status == "pending_pairing"
    assert landlord.get("/r/requests").json()[0]["result"] is None  # nothing before the owner approves
    _pair_and_approve(owner)
    req = RRequest.model_validate(landlord.get("/r/requests").json()[0])
    assert req.status == "done" and req.result is not None
    assert req.result.answer_type == "ISSUER_PROOF" and req.result.claim == "income_ge_50000"
    assert [c.name for c in req.result.checks] == list(verifier.PRESENTATION_CHECKS)
    assert req.result.all_ok and req.result.result is True
    # The landlord holds small JSON only: the one presentation, no document text.
    files = {f["name"]: f for f in landlord.get("/r/storage").json()["files"]}
    assert "proofs.json" in files and "Salary" not in json.dumps(files)
    audit = owner.get("/api/audit", headers=TOKEN).json()
    assert audit["chain_intact"]
    events = [e["event"] for e in audit["entries"]]
    for ev in ("requester_pending", "request_received", "requester_paired", "disclosure_answered"):
        assert ev in events
    assert {t.credential_type: t.unused for t in wallet.status().by_type} == {"id_card": 4, "income_proof": 3}


def _one_presentation(laptops):
    owner, landlord = laptops
    landlord.post("/r/ask", json={"question": "Earns 50k?"})
    _pair_and_approve(owner)
    rec = landlord.get("/r/requests").json()[0]
    proofs = json.loads((common.DATA_DIR / "proofs.json").read_text())
    nonce = json.loads((common.DATA_DIR / "nonces.json").read_text())[rec["local_id"]]["nonce"]
    return proofs[rec["request_id"]]["payload"], nonce, common.Identity("web").fingerprint


def test_replayed_forwarded_and_altered_presentations_fail(laptops):
    pres, nonce, fp = _one_presentation(laptops)
    assert verifier.verify("ISSUER_PROOF", pres, nonce, fp).all_ok
    replay = verifier.verify("ISSUER_PROOF", pres, "another-nonce", fp)
    forwarded = verifier.verify("ISSUER_PROOF", pres, nonce, "0123456789abcdef")
    assert not replay.all_ok and not forwarded.all_ok
    assert not {c.name: c.ok for c in replay.checks}["Nonce and audience"]
    altered = json.loads(json.dumps(pres))
    altered["disclosures"][0]["value"] = False
    out = verifier.verify("ISSUER_PROOF", altered, nonce, fp)
    assert not {c.name: c.ok for c in out.checks}["Disclosure digest"]
    assert not {c.name: c.ok for c in out.checks}["Holder binding"]
    swapped = json.loads(json.dumps(pres))
    swapped["disclosures"][0]["claim"] = "income_ge_100000"
    assert not verifier.verify("ISSUER_PROOF", swapped, nonce, fp).all_ok


def test_untrusted_issuer_and_expiry_fail(laptops, monkeypatch):
    pres, nonce, fp = _one_presentation(laptops)
    later = datetime.now(timezone.utc) + timedelta(days=issue.VALIDITY_DAYS + 1)
    assert not {c.name: c.ok for c in verifier.verify("ISSUER_PROOF", pres, nonce, fp, now=later).checks}[
        "Not expired"]
    (common.DATA_DIR / "trusted_issuers.json").write_text("{}")
    out = verifier.verify("ISSUER_PROOF", pres, nonce, fp)
    assert not out.checks[0].ok and "trust list" in out.checks[0].detail


def test_malformed_payloads_never_pass():
    for payload in (None, {}, {"credential": 1}, {"type": "kavach/presentation"}):
        out = verifier.verify("ISSUER_PROOF", payload, "n", "a")
        assert not out.all_ok and len(out.checks) == 5 and not any(c.ok for c in out.checks)
    refused = verifier.verify("REFUSED", None, "n1", "fp")
    assert refused.checks == [] and not refused.all_ok
    assert verifier.verify("OWNER_ATTESTED", {"claim": {"claim": "income", "op": "ge", "value": 60000}}, "n",
                           "a").claim == "income ge 60000"


def test_owner_attestation_verifies_against_pinned_key(fresh_db):
    priv = crypto.new_private_key()
    db.insert("requesters", {"fingerprint": "fpfpfpfpfpfpfpfp", "pubkey": "x", "name": "R", "type": "t",
                             "status": "paired", "owner_pairwise_privkey": priv, "paired_at": "2026-09-27T00:00:00Z"})
    from kavach.models import Claim
    att = present.build_attestation(Claim(claim="income", op="ge", value=60000), True, "fpfpfpfpfpfpfpfp", "n1")
    pinned = crypto.public_key(priv)
    ok = verifier.verify("OWNER_ATTESTED", att, "n1", "fpfpfpfpfpfpfpfp", owner_pairwise_pubkey=pinned)
    assert ok.all_ok and ok.result is True and ok.claim == "income ge 60000"
    assert "owner-attested" in ok.checks[0].detail
    assert not verifier.verify("OWNER_ATTESTED", att, "n1", "fpfpfpfpfpfpfpfp").all_ok  # nothing pinned
    other = crypto.public_key(crypto.new_private_key())
    assert not verifier.verify("OWNER_ATTESTED", att, "n1", "fpfpfpfpfpfpfpfp", owner_pairwise_pubkey=other).all_ok


def test_declined_request_shows_as_declined(laptops):
    owner, landlord = laptops
    landlord.post("/r/ask", json={"question": "Earns 50k?"})
    _pair_and_approve(owner, action="deny")
    req = RRequest.model_validate(landlord.get("/r/requests").json()[0])
    assert req.status == "done" and req.result.answer_type == "DECLINED" and not req.result.all_ok


def test_agent_client_gate_url(monkeypatch):
    monkeypatch.setattr(config, "OWNER_URL", "http://192.168.1.20:8000")
    assert agent_client.gate_url() == f"http://192.168.1.20:{config.GATE_PORT}/mcp"
