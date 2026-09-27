"""TRUST steps 5, 6 and 8: request pipeline rejections, owner decisions, ledger, malformed-request audit, audit chain."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from kavach import api, config, db
from kavach.models import Claim, Proposal
from kavach.trust import audit, consent, crypto, ledger

TOKEN = {"X-Owner-Token": "test-owner-token"}


@pytest.fixture
def owner(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "dist")
    return TestClient(api.app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8000")


@pytest.fixture
def lan():
    return TestClient(api.app, client=("192.168.1.60", 2))


def _ask(priv, **kw):
    body = {"requester_pubkey": crypto.public_key(priv), "requester_name": "Ramesh Kumar",
            "requester_type": "landlord", "question": "Earns 60k?", "nonce": crypto.b64e(crypto.sha256(
                str(time.time_ns()).encode()))[:16], "ts": int(time.time())} | kw
    return body | {"sig": crypto.sign(priv, body)}


def _reasons():
    return [json.loads(r["detail_json"])["reason"] for r in
            db.fetch_all("SELECT detail_json FROM audit_log WHERE event = 'request_rejected' ORDER BY seq")]


def _pair(owner, priv):
    fp = crypto.fingerprint(crypto.public_key(priv))
    assert owner.post(f"/api/requesters/{fp}/decision", headers=TOKEN, json={"approve": True}).status_code == 200
    return fp


# --- rejections (attacks) ----------------------------------------------------------------------------------


def test_bad_sig_stale_ts_nonce_reuse_and_blocked(owner, lan):
    priv = crypto.new_private_key()
    body = _ask(priv)
    assert lan.post("/api/ask", json=body | {"question": "Earns 10k?"}).status_code == 401  # altered after signing
    assert lan.post("/api/ask", json=_ask(priv, ts=int(time.time()) - 600)).status_code == 401
    assert lan.post("/api/ask", json=body).status_code == 200
    assert lan.post("/api/ask", json=body).status_code == 409  # same nonce again
    assert lan.post("/api/ask", json=_ask(priv, requester_pubkey="not a key")).status_code == 401
    fp = crypto.fingerprint(crypto.public_key(priv))
    owner.post(f"/api/requesters/{fp}/decision", headers=TOKEN, json={"approve": False})
    assert lan.post("/api/ask", json=_ask(priv)).status_code == 403
    assert _reasons() == ["bad_sig", "stale_ts", "nonce_reuse", "bad_sig", "unknown_requester_blocked"]


def test_unpaired_requester_never_gets_an_answer(owner, lan):
    priv = crypto.new_private_key()
    rid = lan.post("/api/ask", json=_ask(priv)).json()["request_id"]
    view = db.list_requests()[0]
    assert view.status == "pending_pairing" and view.claim is None and view.proposal is None
    ts = int(time.time())
    hdrs = {"X-Requester-Fp": crypto.fingerprint(crypto.public_key(priv)), "X-Ts": str(ts),
            "X-Sig": crypto.sign(priv, f"{rid}|{ts}".encode())}
    res = lan.get(f"/api/ask/{rid}", headers=hdrs).json()
    assert res["status"] == "pending_pairing" and res["payload"] is None and res["owner_pairwise_pubkey"] is None


def test_malformed_ask_is_audited_without_body(owner, lan):
    assert lan.post("/api/ask", json={"question": "SECRET-BODY", "ts": "soon"}).status_code == 422
    assert lan.get("/api/ask/rq_1", headers={"X-Requester-Fp": "abcdef0123456789", "X-Ts": "x"}).status_code == 422
    rows = db.fetch_all("SELECT ref_id, detail_json FROM audit_log WHERE event = 'request_rejected' ORDER BY seq")
    details = [json.loads(r["detail_json"]) for r in rows]
    assert [set(d) for d in details] == [{"reason", "route", "client_ip", "error_type"}] * 2
    assert details[0]["route"] == "/api/ask" and details[1]["route"] == "/api/ask/{id}"
    assert [r["ref_id"] for r in rows] == [None, "abcdef0123456789"]
    assert "SECRET-BODY" not in json.dumps(details)
    # validation errors elsewhere are not audited as requests
    owner.post("/api/requests/rq_1/decision", headers=TOKEN, json={"action": "leak"})
    assert len(_reasons()) == 2


# --- owner decisions ---------------------------------------------------------------------------------------


def _propose(monkeypatch, claim: Claim, answer_type="OWNER_ATTESTED", result=True):
    monkeypatch.setattr(consent.parse_question, "parse", lambda q: claim)
    actions = ["approve", "deny"] if result else ["answer", "decline"]
    monkeypatch.setattr(consent.decide, "decide", lambda c, fp: Proposal(
        answer_type=answer_type, claim=c, result=result, favourable=result, actions=actions, reason="test"))


def test_owner_attested_answer_and_actions(owner, lan, monkeypatch):
    _propose(monkeypatch, Claim(claim="income", op="ge", value=60000))
    priv = crypto.new_private_key()
    lan.post("/api/ask", json=_ask(priv))
    _pair(owner, priv)
    req = owner.get("/api/queue", headers=TOKEN).json()["requests"][0]
    assert req["status"] == "pending" and req["proposal"]["actions"] == ["approve", "deny"]
    bad = owner.post(f"/api/requests/{req['request_id']}/decision", headers=TOKEN, json={"action": "answer"})
    assert bad.status_code == 409
    done = owner.post(f"/api/requests/{req['request_id']}/decision", headers=TOKEN, json={"action": "approve"})
    assert done.json()["status"] == "done" and done.json()["answer_type"] == "OWNER_ATTESTED"
    payload = json.loads(db.fetch_one("SELECT payload_json FROM requests")["payload_json"])
    assert payload["type"] == "kavach/attestation" and payload["aud"] == crypto.fingerprint(crypto.public_key(priv))
    assert owner.post(f"/api/requests/{req['request_id']}/decision", headers=TOKEN,
                      json={"action": "approve"}).status_code == 409


def test_auto_refused_and_cannot_confirm_finish_without_owner(owner, lan, monkeypatch):
    priv = crypto.new_private_key()
    lan.post("/api/ask", json=_ask(priv))
    _pair(owner, priv)
    for answer_type, event in (("REFUSED", "request_auto_refused"), ("CANNOT_CONFIRM", "request_cannot_confirm")):
        monkeypatch.setattr(consent.decide, "decide", lambda c, fp, at=answer_type: Proposal(
            answer_type=at, claim=c, reason="auto"))
        rid = lan.post("/api/ask", json=_ask(priv)).json()["request_id"]
        row = db.fetch_one("SELECT status, answer_type FROM requests WHERE request_id = ?", (rid,))
        assert (row["status"], row["answer_type"]) == ("done", answer_type)
        assert db.fetch_one("SELECT 1 FROM audit_log WHERE event = ? AND ref_id = ?", (event, rid))


def test_parse_failure_is_refused_not_guessed(owner, lan, monkeypatch):
    def boom(q):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(consent.parse_question, "parse", boom)
    priv = crypto.new_private_key()
    lan.post("/api/ask", json=_ask(priv))
    _pair(owner, priv)
    row = db.fetch_one("SELECT status, answer_type, claim_json FROM requests")
    assert row["answer_type"] == "REFUSED" and json.loads(row["claim_json"])["claim"] == "unsupported"


# --- ledger ------------------------------------------------------------------------------------------------


def test_ledger_narrowing_is_blocked_for_colluding_keys(owner, lan, monkeypatch):
    """Three different paired landlord keys probe 60k / 70k / 65k: the global ledger refuses the narrowing."""
    salary = 62000
    results = []
    for t in (60000, 70000, 65000):
        _propose(monkeypatch, Claim(claim="income", op="ge", value=t), result=salary >= t)
        priv = crypto.new_private_key()
        rid = lan.post("/api/ask", json=_ask(priv)).json()["request_id"]
        _pair(owner, priv)
        action = "approve" if salary >= t else "answer"
        results.append(owner.post(f"/api/requests/{rid}/decision", headers=TOKEN, json={"action": action}
                                  ).json()["answer_type"])
    assert results == ["OWNER_ATTESTED", "REFUSED", "REFUSED"]
    assert db.fetch_one("SELECT COUNT(*) AS n FROM audit_log WHERE event = 'request_refused_ledger'")["n"] == 2


def test_ledger_rules(fresh_db):
    yes = Claim(claim="income", op="ge", value=50000, issuer_claim="income_ge_50000")
    no = Claim(claim="income", op="ge", value=75000, issuer_claim="income_ge_75000")
    assert ledger.check(yes, True).allowed
    ledger.record(yes, True)
    assert ledger.check(no, False).allowed  # width exactly 25000 is allowed
    ledger.record(no, False)
    assert not ledger.check(Claim(claim="income", op="ge", value=60000), True).allowed
    assert ledger.check(Claim(claim="age", op="ge", value=18, issuer_claim="age_over_18"), True).allowed
    assert ledger.check(Claim(claim="board", op="is", value="x"), True).allowed


def test_ledger_caps_distinct_attested_thresholds(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_MIN_WIDTH", {"income": 1, "percentage": 1})
    for t in (10000, 20000, 30000):
        c = Claim(claim="percentage", op="ge", value=t // 1000)
        assert ledger.check(c, True).allowed
        ledger.record(c, True)
    assert ledger.check(Claim(claim="percentage", op="ge", value=20), True).allowed  # repeat threshold is fine
    assert not ledger.check(Claim(claim="percentage", op="ge", value=25), True).allowed


# --- audit chain -------------------------------------------------------------------------------------------


def test_audit_chain_detects_tampering(fresh_db):
    for i in range(4):
        audit.log("request_received", f"rq_{i}", {"i": i})
    st = audit.verify_chain()
    assert st.intact and st.entries == 4
    first = db.fetch_one("SELECT prev_hash, entry_hash FROM audit_log WHERE seq = 1")
    assert first["prev_hash"] == "0" * 64
    db.update("audit_log", "seq", 3, {"detail_json": '{"i":99}'})
    st = audit.verify_chain()
    assert not st.intact and st.broken_at == 3
    with pytest.raises(ValueError):
        audit.log("made_up_event", None, {})


def test_audit_endpoint_reports_break(owner):
    audit.log("request_received", "rq_1", {})
    audit.log("request_received", "rq_2", {})
    assert owner.get("/api/audit", headers=TOKEN).json()["chain_intact"]
    db.update("audit_log", "seq", 1, {"event": "disclosure_denied"})
    out = owner.get("/api/audit", headers=TOKEN).json()
    assert not out["chain_intact"] and out["broken_at"] == 1
