"""Requester backend (CONTRACT §12), verifier stub (§6.4) and agent client stub."""

import json

import pytest
from fastapi.testclient import TestClient

from kavach import config
from kavach.models import RAskOut, RIdentity, RRequest, RStorage
from requester import agent_client, app as rapp, verifier

FIXTURES = config.ROOT / "fixtures" / "api"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(rapp, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "dist")
    return TestClient(rapp.app)


def test_routes_return_contract_types(client):
    RIdentity.model_validate(client.get("/r/identity").json())
    out = RAskOut.model_validate(client.post("/r/ask", json={"question": "Earns 50k?"}).json())
    assert out.local_id.startswith("l_") and out.request_id.startswith("rq_")
    assert client.get("/r/requests").json() == []
    assert client.get("/r/storage").json() == {"files": []}


def test_requests_and_storage_read_data_dir(client):
    rapp.DATA_DIR.mkdir()
    (rapp.DATA_DIR / "requests.json").write_bytes((FIXTURES / "r_requests.json").read_bytes())
    (rapp.DATA_DIR / "identity.json").write_text('{\n  "name": "Ramesh Kumar"\n}')
    (rapp.DATA_DIR / "requester.key").write_bytes(b"secret")
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


def test_verifier_stub_never_claims_success():
    for answer_type, payload, n in [
        ("ISSUER_PROOF", {"disclosures": [{"claim": "income_ge_50000", "value": True}]}, 5),
        ("OWNER_ATTESTED", {"claim": {"claim": "income", "op": "ge", "value": 60000}}, 3),
    ]:
        out = verifier.verify(answer_type, payload, "n1", "fp")
        assert len(out.checks) == n and not out.all_ok and not any(c.ok for c in out.checks)
    assert verifier.verify("ISSUER_PROOF", {"disclosures": [{"claim": "income_ge_50000"}]}, "n", "a").claim \
        == "income_ge_50000"
    assert verifier.verify("OWNER_ATTESTED", {"claim": {"claim": "income", "op": "ge", "value": 60000}}, "n",
                           "a").claim == "income ge 60000"
    refused = verifier.verify("REFUSED", None, "n1", "fp")
    assert refused.checks == [] and not refused.all_ok
    assert verifier.trusted_issuers() == {} or all(isinstance(v, str) for v in verifier.trusted_issuers().values())


def test_agent_client_stub(monkeypatch):
    monkeypatch.setattr(config, "OWNER_URL", "http://192.168.1.20:8000")
    assert agent_client.gate_url() == f"http://192.168.1.20:{config.GATE_PORT}/mcp"
    assert agent_client.run() == []
