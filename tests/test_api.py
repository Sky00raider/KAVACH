"""Owner API (CONTRACT §9, §10): auth, every route's shape, chat stream framing, uploads, frontend injection."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from kavach import api, config, db
from kavach.models import (
    AskAck,
    AskResult,
    AuditOut,
    CandidateDecisionOut,
    ChatDoneData,
    ChatFinal,
    ChatMetaData,
    ChatResult,
    Chunk,
    ClaimsOut,
    Document,
    Entity,
    Fact,
    FactVersion,
    Graph,
    Health,
    IngestEvents,
    IngestSyncOut,
    OutboxItem,
    QueueOut,
    Requester,
    RequestView,
    Task,
    TeachResult,
    WalletStatus,
)
from kavach.trust import crypto

TOKEN = {"X-Owner-Token": "test-owner-token"}
LAN = ("192.168.1.50", 50000)


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAULT_DIR", tmp_path / "vault")
    monkeypatch.setattr(config, "OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "dist")
    monkeypatch.setattr(api, "_ollama_status", lambda: (False, set()))
    return TestClient(api.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8000")


@pytest.fixture
def lan():
    return TestClient(api.app, client=LAN)


def _ask_body(**kw):
    body = {"requester_pubkey": "MCowBQYDK2VwAyEA" + "A" * 44, "requester_name": "Ramesh Kumar",
            "requester_type": "landlord", "question": "Earns 50k?", "nonce": "n1", "ts": 1790000000, "sig": "s"}
    return body | kw


def _signed_ask(priv, **kw):
    body = {"requester_pubkey": crypto.public_key(priv), "requester_name": "Ramesh Kumar",
            "requester_type": "landlord", "question": "Earns 50k?", "nonce": "n1", "ts": int(time.time())} | kw
    return body | {"sig": crypto.sign(priv, body)}


def _poll_headers(priv, request_id):
    ts = int(time.time())
    return {"X-Requester-Fp": crypto.fingerprint(crypto.public_key(priv)), "X-Ts": str(ts),
            "X-Sig": crypto.sign(priv, f"{request_id}|{ts}".encode())}


# --- auth ------------------------------------------------------------------------------------------------


def test_owner_routes_need_token(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers={"X-Owner-Token": "wrong"}).status_code == 401
    assert client.get("/api/documents", headers=TOKEN).status_code == 200


def test_owner_routes_are_loopback_only(client, lan):
    assert lan.get("/api/documents", headers=TOKEN).status_code == 403


def test_proxy_headers_do_not_make_a_lan_client_loopback(client, lan):
    spoof = TOKEN | {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1", "Forwarded": "for=127.0.0.1"}
    assert lan.get("/api/queue", headers=spoof).status_code == 403


GOOD_HOSTS = ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000", "[::1]", "[::1]:8000", "LOCALHOST:5173"]
BAD_HOSTS = ["evil.example", "evil.example:8000", "localhost.evil.example", "127.0.0.1.nip.io", "localhost:",
             "localhost:80@evil.example", "localhost:8000:1", "[::1]evil", "[::1", "::1", "0.0.0.0:8000",
             "192.168.1.20:8000", ""]


@pytest.mark.parametrize("host", GOOD_HOSTS)
def test_owner_routes_accept_loopback_host_headers(client, host):
    assert client.get("/api/documents", headers=TOKEN | {"Host": host}).status_code == 200


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_owner_routes_reject_non_loopback_host_headers(client, host):
    # DNS rebinding: a page on evil.example resolved to 127.0.0.1 arrives from loopback with Host: evil.example.
    assert client.get("/api/documents", headers=TOKEN | {"Host": host}).status_code == 403


def test_host_evil_gets_403_on_every_owner_route(client):
    for route in api.owner.routes:
        path = route.path.replace("{", "").replace("}", "")
        for method in route.methods:
            r = client.request(method, path, headers=TOKEN | {"Host": "evil.example:8000"})
            assert r.status_code == 403, (method, path)


def test_index_withholds_token_from_non_loopback_host(client):
    config.FRONTEND_DIST.mkdir()
    (config.FRONTEND_DIST / "index.html").write_text("<html><head><title>K</title></head><body></body></html>")
    for path in ("/", "/queue", "/index.html"):
        r = client.get(path, headers={"Host": "evil.example:8000"})
        assert r.status_code == 200 and _boot(r.text) == {"mode": "owner"}, path
        assert "test-owner-token" not in r.text
    assert _boot(client.get("/", headers={"Host": "localhost:8000"}).text)["token"] == "test-owner-token"


def test_public_routes_ignore_host(client):
    assert client.get("/api/claims", headers={"Host": "192.168.1.20:8000"}).status_code == 200


def test_every_owner_route_requires_auth(client):
    for route in api.owner.routes:
        path = route.path.replace("{chunk_id}", "c_x").replace("{candidate_id}", "mc_x") \
            .replace("{task_id}", "t_x").replace("{fp}", "fp").replace("{request_id}", "rq_x")
        method = next(iter(route.methods))
        assert client.request(method, path).status_code == 401, path


def test_requester_routes_need_no_token(client, lan):
    assert lan.get("/api/claims").status_code == 200
    assert lan.post("/api/ask", json=_signed_ask(crypto.new_private_key())).status_code == 200


# --- routes return their contract types ------------------------------------------------------------------


def test_read_routes_validate(client):
    for path, model in [("/api/health", Health), ("/api/ingest/events", IngestEvents), ("/api/graph", Graph),
                        ("/api/queue", QueueOut), ("/api/wallet", WalletStatus), ("/api/audit", AuditOut)]:
        r = client.get(path, headers=TOKEN)
        assert r.status_code == 200, path
        model.model_validate(r.json())
    for path, model in [("/api/documents", Document), ("/api/entities", Entity), ("/api/facts", Fact),
                        ("/api/memory/timeline?field=monthly_income", FactVersion), ("/api/tasks", Task),
                        ("/api/outbox", OutboxItem)]:
        r = client.get(path, headers=TOKEN)
        assert r.status_code == 200, path
        [model.model_validate(x) for x in r.json()]


def test_health_reports_config_models_and_unreachable_ollama(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    assert api._ollama_status() == (False, set())
    r = TestClient(api.app, client=("127.0.0.1", 1), base_url="http://localhost:8000").get("/api/health", headers=TOKEN)
    h = Health.model_validate(r.json())
    assert h.models == {"llm": config.LLM_MODEL, "fast": config.FAST_MODEL, "embed": config.EMBED_MODEL}
    assert h.db and not h.ollama and h.model_loaded == {"llm": False, "fast": False, "embed": False}


@pytest.mark.parametrize("url, local", [
    ("http://127.0.0.1:11434", True), ("http://localhost:11434", True), ("http://[::1]:11434", True),
    ("http://127.0.0.2:11434", True), ("http://192.168.1.20:11434", False), ("http://gpu-box.local:11434", False),
    ("http://localhost.evil.com:11434", False),
])
def test_health_local_inference_follows_ollama_host(client, monkeypatch, url, local):
    monkeypatch.setattr(config, "OLLAMA_URL", url)
    monkeypatch.setattr(api, "_ollama_status", lambda: (True, set()))
    assert Health.model_validate(client.get("/api/health", headers=TOKEN).json()).local_inference is local


def test_health_model_loaded_per_role(client, monkeypatch):
    monkeypatch.setattr(api, "_ollama_status", lambda: (True, {api._full_name(config.LLM_MODEL),
                                                               api._full_name(config.EMBED_MODEL)}))
    h = Health.model_validate(client.get("/api/health", headers=TOKEN).json())
    assert h.model_loaded == {"llm": True, "fast": False, "embed": True}


def test_db_backed_routes_read_rows(client):
    db.insert("documents", {"doc_id": "d_1", "path": "pdfs/a.pdf", "source": "pdf", "signature_status": "unsigned",
                            "ingested_at": "2026-09-26T10:00:00Z"})
    db.insert("chunks", {"chunk_id": "c_1", "doc_id": "d_1", "locator": "page 1", "text": "Rent 15,000"})
    db.insert("entities", {"entity_id": "e_1", "type": "PERSON", "name": "Ramesh", "norm_name": "ramesh",
                           "attrs_json": "{}"})
    assert [d["doc_id"] for d in client.get("/api/documents", headers=TOKEN).json()] == ["d_1"]
    assert Chunk.model_validate(client.get("/api/chunks/c_1", headers=TOKEN).json()).text == "Rent 15,000"
    assert client.get("/api/chunks/c_nope", headers=TOKEN).status_code == 404
    assert len(client.get("/api/entities?type=PERSON", headers=TOKEN).json()) == 1
    assert client.get("/api/entities?type=ORG", headers=TOKEN).json() == []


def test_ingest_events_come_from_ingested_audit_entries(client):
    detail = {"path": "pdfs/a.pdf", "doc_id": "d_1", "signature_status": "issuer_signed", "chunks_added": 3,
              "entities_added": 2, "facts_added": 1}
    for event, d in [("ingested", detail), ("task_planned", {}), ("ingested", detail | {"doc_id": "d_2"})]:
        db.insert("audit_log", {"ts": "2026-09-26T10:00:00Z", "event": event, "ref_id": d.get("doc_id"),
                                "detail_json": json.dumps(d), "prev_hash": "0" * 64, "entry_hash": "0" * 64})
    out = IngestEvents.model_validate(client.get("/api/ingest/events", headers=TOKEN).json())
    assert [(e.seq, e.doc_id) for e in out.events] == [(1, "d_1"), (3, "d_2")] and out.last_seq == 3
    later = IngestEvents.model_validate(client.get("/api/ingest/events?since=3", headers=TOKEN).json())
    assert later.events == [] and later.last_seq == 3


def test_chat_memory_and_decisions(client):
    r = client.post("/api/chat", headers=TOKEN, json={"question": "rent?", "history": []})
    out = ChatResult.model_validate(r.json())  # empty vault: fixed answer, no model call
    assert not out.citation_ok and out.flags == ["no_context", "not_in_vault"]
    r = client.post("/api/memory", headers=TOKEN, json={"statement": "My salary went up"})
    TeachResult.model_validate(r.json())
    r = client.post("/api/memory/candidates/mc_1/decision", headers=TOKEN, json={"remember": True})
    CandidateDecisionOut.model_validate(r.json())
    assert client.post("/api/requesters/fp1/decision", headers=TOKEN, json={"approve": False}).status_code == 404
    assert client.post("/api/requests/rq_1/decision", headers=TOKEN, json={"action": "approve"}).status_code == 404
    assert client.post("/api/requests/rq_1/decision", headers=TOKEN, json={"action": "leak"}).status_code == 422
    client.post("/api/ask", json=_signed_ask(crypto.new_private_key()))
    fp = client.get("/api/queue", headers=TOKEN).json()["requesters"][0]["fingerprint"]
    r = client.post(f"/api/requesters/{fp}/decision", headers=TOKEN, json={"approve": False})
    assert Requester.model_validate(r.json()).status == "blocked"
    req = db.list_requests()[0]
    assert req.status == "done" and req.answer_type == "REFUSED"
    assert client.post(f"/api/requests/{req.request_id}/decision", headers=TOKEN,
                       json={"action": "approve"}).status_code == 409


def test_task_plan_approve_execute(client):
    db.insert("entities", {"entity_id": "e_l", "type": "PERSON", "name": "Ramesh Kumar", "norm_name": "ramesh kumar",
                           "attrs_json": json.dumps({"email": "ramesh.kumar@example.com"})})
    task = Task.model_validate(client.post("/api/tasks", headers=TOKEN, json={"instruction": "Email Ramesh"}).json())
    assert task.status == "planned"
    assert [t["task_id"] for t in client.get("/api/queue", headers=TOKEN).json()["tasks"]] == [task.task_id]
    done = Task.model_validate(client.post(f"/api/tasks/{task.task_id}/decision", headers=TOKEN,
                                           json={"approve": True}).json())
    assert done.status == "done" and done.result and done.decided_at
    again = client.post(f"/api/tasks/{task.task_id}/decision", headers=TOKEN, json={"approve": True})
    assert again.status_code == 409
    assert client.post("/api/tasks/t_nope/decision", headers=TOKEN, json={"approve": True}).status_code == 404


def test_task_reject(client):
    task = client.post("/api/tasks", headers=TOKEN, json={"instruction": "Email Ramesh"}).json()
    r = client.post(f"/api/tasks/{task['task_id']}/decision", headers=TOKEN, json={"approve": False})
    assert r.json()["status"] == "rejected" and r.json()["result"] is None


def test_outbox_lists_known_kinds(client):
    config.OUTBOX_DIR.mkdir()
    (config.OUTBOX_DIR / "a.eml").write_text("x")
    (config.OUTBOX_DIR / "b.ics").write_text("xy")
    (config.OUTBOX_DIR / "notes.tmp").write_text("z")
    items = [OutboxItem.model_validate(x) for x in client.get("/api/outbox", headers=TOKEN).json()]
    assert sorted((i.name, i.kind, i.size) for i in items) == [("a.eml", "eml", 1), ("b.ics", "ics", 2)]


# --- chat stream (§10) -----------------------------------------------------------------------------------


def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_chat_stream_emits_contract_events(client):
    with client.stream("POST", "/api/chat/stream", headers=TOKEN, json={"question": "rent?"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.read().decode("utf-8"))
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-2:] == ["final", "done"]
    assert set(names[1:-2]) == {"token"}
    ChatMetaData.model_validate(events[0][1])
    final = ChatFinal.model_validate(events[-2][1])
    ChatDoneData.model_validate(events[-1][1])
    assert "".join(d["text"] for e, d in events if e == "token") == final.answer


def test_local_model_failure_is_503(client, monkeypatch):
    from kavach.brain.llm import LLMError

    def down(question, history):
        raise LLMError("Ollama /api/chat failed: ConnectError")

    monkeypatch.setattr(api.chat, "answer", down)
    r = client.post("/api/chat", headers=TOKEN, json={"question": "rent?"})
    assert r.status_code == 503 and r.json()["detail"].startswith("local model unavailable")


def test_chat_stream_events_are_in_openapi(client):
    spec = client.get("/openapi.json").json()
    content = spec["paths"]["/api/chat/stream"]["post"]["responses"]["200"]["content"]
    assert list(content) == ["text/event-stream"]
    refs = {v["$ref"].rsplit("/", 1)[1] for v in content["text/event-stream"]["schema"]["oneOf"]}
    assert refs == {"ChatMetaEvent", "ChatTokenEvent", "ChatFinalEvent", "ChatDoneEvent", "ChatErrorEvent"}


def test_chat_stream_turns_exceptions_into_error_event(client, monkeypatch):
    def boom(question, history):
        yield from ()
        raise RuntimeError("ollama down")

    monkeypatch.setattr(api.chat, "answer_stream", boom)
    r = client.post("/api/chat/stream", headers=TOKEN, json={"question": "rent?"})
    assert _parse_sse(r.text) == [("error", {"message": "RuntimeError: ollama down"})]


# --- uploads ---------------------------------------------------------------------------------------------


def _upload(client, name, content=b"%PDF-1.7 test"):
    return client.post("/api/ingest", headers=TOKEN, files={"file": (name, content)})


def test_upload_routes_by_extension(client):
    for name, rel in [("stmt.pdf", "pdfs/stmt.pdf"), ("idea.md", "notes/idea.md"), ("landlord.txt", "chats/landlord.txt")]:
        r = _upload(client, name)
        assert r.status_code == 200 and r.json() == {"path": rel}
        assert (config.VAULT_DIR / rel).read_bytes() == b"%PDF-1.7 test"
    assert _upload(client, "run.exe").status_code == 415
    assert not list((config.VAULT_DIR / ".uploads").iterdir())


def test_upload_keeps_basename_only(client):
    r = _upload(client, "some/dir/stmt.pdf")
    assert r.json() == {"path": "pdfs/stmt.pdf"}


UNSAFE = ["../evil.pdf", "pdfs/../../evil.pdf", "..\\evil.pdf", "/etc/evil.pdf", "\\\\server\\share\\evil.pdf",
          "C:\\evil.pdf", "C:evil.pdf", "C:/evil.pdf", "..", ".", "", "evil.pdf:ads", "a\x00.pdf", "dir/", None]


@pytest.mark.parametrize("name", UNSAFE)
def test_safe_upload_name_rejects(name):
    with pytest.raises(api.HTTPException) as exc:
        api.safe_upload_name(name)
    assert exc.value.status_code == 400


def test_safe_upload_name_keeps_basename():
    assert api.safe_upload_name("some/dir/stmt.pdf") == "stmt.pdf"
    assert api.safe_upload_name("sub\\stmt.pdf") == "stmt.pdf"


@pytest.mark.parametrize("name", [n for n in UNSAFE if n])
def test_upload_never_escapes_vault(client, name):
    # Starlette's multipart parser already reduces Windows full paths to the basename and httpx
    # percent-encodes NUL, so some names never reach safe_upload_name; either way nothing lands outside.
    r = _upload(client, name)
    assert r.status_code == 400 or (r.status_code == 200 and "/" not in r.json()["path"].removeprefix("pdfs/"))
    assert not (config.VAULT_DIR.parent / "evil.pdf").exists()
    assert not (config.VAULT_DIR / "evil.pdf").exists()


def test_upload_never_overwrites(client):
    assert _upload(client, "stmt.pdf", b"one").status_code == 200
    assert _upload(client, "stmt.pdf", b"one").status_code == 200
    assert _upload(client, "stmt.pdf", b"two").status_code == 409
    assert (config.VAULT_DIR / "pdfs/stmt.pdf").read_bytes() == b"one"


def test_ingest_sync_scans_vault_subdirs(client):
    _upload(client, "a.pdf")
    _upload(client, "b.md")
    (config.VAULT_DIR / "notes" / "ignore.bin").write_bytes(b"x")
    out = IngestSyncOut.model_validate(client.post("/api/ingest/sync", headers=TOKEN).json())
    assert sorted(r.source for r in out.ingested) == ["note", "pdf"]


# --- requester-facing ------------------------------------------------------------------------------------


def test_claims_are_names_only(client):
    out = ClaimsOut.model_validate(client.get("/api/claims").json())
    assert [c.claim for c in out.claims] == ["income", "loan_default_12m", "age", "percentage", "result", "board"]
    assert set(out.model_dump()["claims"][0]) == {"claim", "issuer_provable", "favourable"}


def test_mcp_channel_only_from_loopback(client, lan, monkeypatch):
    seen = []
    monkeypatch.setattr(api.consent, "receive", lambda req, channel: seen.append(channel) or
                        AskAck(request_id="rq_1", status="pending_pairing"))
    client.post("/api/ask", json=_ask_body(), headers={"X-Channel": "mcp"})
    lan.post("/api/ask", json=_ask_body(), headers={"X-Channel": "mcp"})
    lan.post("/api/ask", json=_ask_body(), headers={"X-Channel": "mcp", "X-Forwarded-For": "127.0.0.1"})
    client.post("/api/ask", json=_ask_body())
    assert seen == ["mcp", "web", "web", "web"]


@pytest.mark.parametrize("reason,status", [("bad_sig", 401), ("stale_ts", 401), ("nonce_reuse", 409),
                                           ("unknown_requester_blocked", 403)])
def test_ask_rejections_map_to_status(client, monkeypatch, reason, status):
    def reject(req, channel):
        raise api.consent.RequestRejected(reason)

    monkeypatch.setattr(api.consent, "receive", reject)
    assert client.post("/api/ask", json=_ask_body()).status_code == status


def test_poll_only_by_the_requesting_fp(client, lan):
    a, b = crypto.new_private_key(), crypto.new_private_key()
    rid = AskAck.model_validate(lan.post("/api/ask", json=_signed_ask(a)).json()).request_id
    lan.post("/api/ask", json=_signed_ask(b))
    r = lan.get(f"/api/ask/{rid}", headers=_poll_headers(a, rid))
    assert r.status_code == 200 and AskResult.model_validate(r.json()).status == "pending_pairing"
    other = lan.get(f"/api/ask/{rid}", headers=_poll_headers(b, rid))
    missing = lan.get("/api/ask/rq_nope", headers=_poll_headers(a, "rq_nope"))
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    forged = _poll_headers(b, rid) | {"X-Requester-Fp": crypto.fingerprint(crypto.public_key(a))}
    assert lan.get(f"/api/ask/{rid}", headers=forged).status_code == 401


# --- frontend --------------------------------------------------------------------------------------------


def _boot(html):
    start = html.index("window.__KAVACH__=") + len("window.__KAVACH__=")
    return json.loads(html[start:html.index("</script>", start)])


def test_index_injects_token_for_loopback_only(client, lan):
    config.FRONTEND_DIST.mkdir()
    (config.FRONTEND_DIST / "index.html").write_text("<html><head><title>K</title></head><body></body></html>")
    local = client.get("/queue")
    assert local.status_code == 200 and local.headers["cache-control"] == "no-store"
    assert _boot(local.text) == {"mode": "owner", "token": "test-owner-token"}
    assert _boot(lan.get("/").text) == {"mode": "owner"}
    assert "test-owner-token" not in lan.get("/index.html").text


def test_placeholder_when_frontend_not_built(client):
    r = client.get("/")
    assert r.status_code == 200 and "npm run build" in r.text and _boot(r.text)["token"] == "test-owner-token"


def test_static_files_and_unknown_api_paths(client):
    (config.FRONTEND_DIST / "assets").mkdir(parents=True)
    (config.FRONTEND_DIST / "favicon.svg").write_text("<svg/>")
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.get("/api/nope").status_code == 404
    assert "npm run build" in client.get("/%2e%2e/%2e%2e/etc/passwd").text


def test_facts_default_to_current(client):
    base = {"entity_id": "e_owner", "field": "monthly_income", "source_type": "extracted", "confidence": "high",
            "created_at": "2026-09-26T10:00:00Z"}
    db.insert("facts", base | {"fact_id": "f_old", "value": "62000", "superseded_by": "f_new"})
    db.insert("facts", base | {"fact_id": "f_new", "value": "70000"})
    assert [f["fact_id"] for f in client.get("/api/facts", headers=TOKEN).json()] == ["f_new"]
    every = client.get("/api/facts?current=false", headers=TOKEN).json()
    assert sorted(f["fact_id"] for f in every) == ["f_new", "f_old"]
