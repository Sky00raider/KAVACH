import importlib.util
import json
import sys
from pathlib import Path

import pytest

from kavach import config, db

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for attr, sub in {"DB_PATH": "kavach.db", "VAULT_DIR": "vault", "OUTBOX_DIR": "outbox",
                      "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    demo = tmp_path / "demo_data"
    (demo / "notes").mkdir(parents=True)
    (demo / "notes" / "flat-move-2026.md").write_text("# Flat move", encoding="utf-8")
    (demo / "notes" / "inbox_note.md").write_text("held back", encoding="utf-8")
    return tmp_path


def test_reset_then_restore(runtime):
    reset_demo = _load("reset_demo")
    (runtime / "outbox").mkdir()
    (runtime / "outbox" / "old.eml").write_text("x", encoding="utf-8")

    reset_demo.main([])
    assert (runtime / "vault" / "notes" / "flat-move-2026.md").exists()
    assert not (runtime / "vault" / "notes" / "inbox_note.md").exists()
    assert not (runtime / "outbox" / "old.eml").exists()
    assert (runtime / "kavach.db.bak").exists()
    pdfs = {p.name for p in (runtime / "vault" / "pdfs").iterdir()}
    assert {"bank_statement_signed.pdf", "marksheet_signed.pdf", "id_card_signed.pdf"} <= pdfs
    assert "bank_statement_TAMPERED.pdf" not in pdfs  # held back for the live moment
    assert (runtime / "demo_data" / "generated" / "bank_statement_TAMPERED.pdf").exists()
    from kavach.trust import wallet
    assert {(t.credential_type, t.unused) for t in wallet.status().by_type} == {
        ("income_proof", 20), ("marksheet", 20), ("id_card", 20)}
    assert set(json.loads((reset_demo.requester_data_dir() / "trusted_issuers.json").read_text())) == {
        "mock_bank", "mock_board", "mock_govt"}
    signed = [d for d in db.list_documents() if d.signature_status == "issuer_signed"]
    assert len(signed) == 3

    config.DB_PATH.unlink()
    reset_demo.main(["--restore"])
    assert config.DB_PATH.exists()


def test_run_eval_attacks_and_disclosure_set(runtime, monkeypatch):
    run_eval = _load("run_eval")
    monkeypatch.setattr(run_eval, "EVAL_DIR", runtime / "eval")
    (runtime / "eval").mkdir()
    rows = [{"id": "b1", "question": "Earns 50k?", "expect": "ISSUER_PROOF", "expect_result": True, "real": False},
            {"id": "b2", "question": "Account number?", "expect": "REFUSED", "real": True}]
    (runtime / "eval" / "set_b.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    db.init_db()
    out = runtime / "results.json"
    assert run_eval.main(["--sets", "B,C,D", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    c = report["sets"]["C"]
    assert c["n"] == c["blocked"] == 6 and c["audit_chain_intact"]
    assert report["sets"]["B"]["n"] == 2 and "wrong_disclosures" in report["sets"]["B"]
    assert "D" not in report["sets"]  # no input file
    assert config.DB_PATH == runtime / "kavach.db"  # runtime restored after the throwaway runs


def test_run_eval_attacks_do_not_need_the_model(runtime, monkeypatch):
    from kavach.brain import llm, parse_question

    def down(text):
        raise llm.LLMError("no model")

    monkeypatch.setattr(parse_question, "_map", down)
    run_eval = _load("run_eval")
    real_parse = parse_question.parse
    c = run_eval.run_attacks()
    assert c["n"] == c["blocked"] == 6 and c["audit_chain_intact"]
    assert parse_question.parse is real_parse  # the pinned claim is removed again


@pytest.fixture
def requester_laptop(tmp_path, monkeypatch):
    """A requester data dir, this machine's own issuer keys and a built frontend, all under tmp_path."""
    from requester import common

    monkeypatch.setattr(config, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "dist")
    monkeypatch.delenv("REQUESTER_TRUST_LIST", raising=False)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path / "requester")
    (tmp_path / "requester").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    return tmp_path


def _status(check):
    return check[0]


def test_preflight_trust_list(requester_laptop):
    from kavach.mock_issuers import make_keys
    from kavach.trust import crypto

    pre = _load("requester_preflight")
    data = requester_laptop / "requester"
    (data / "trusted_issuers.json").write_text(json.dumps({"mock_bank": "x"}), encoding="utf-8")
    assert _status(pre.check_trust_list("http://192.168.1.5:8000")) == pre.FAIL  # two issuers missing

    own = make_keys.export_trust_list(data / "trusted_issuers.json")
    assert _status(pre.check_trust_list("http://127.0.0.1:8000")) == pre.OK      # single laptop: own keys are right
    assert _status(pre.check_trust_list("http://192.168.1.5:8000")) == pre.WARN  # two laptops: probably not copied

    theirs = {iss: crypto.public_key(crypto.new_private_key()) for iss in make_keys.ISSUERS}
    own.write_text(json.dumps(theirs), encoding="utf-8")
    status, _, detail = pre.check_trust_list("http://192.168.1.5:8000")
    assert status == pre.OK and crypto.fingerprint(theirs["mock_bank"]) in detail
    assert pre.fingerprints({"mock_bank": "not a key"}) == {"mock_bank": "?"}


def test_preflight_owner_clock_and_frontend(requester_laptop, monkeypatch):
    from datetime import datetime, timezone

    import httpx

    pre = _load("requester_preflight")
    assert _status(pre.check_frontend()) == pre.OK
    (requester_laptop / "dist" / "index.html").unlink()
    assert _status(pre.check_frontend()) == pre.FAIL

    def refused(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(pre.httpx, "get", refused)
    check, response = pre.check_owner("http://192.168.1.5:8000")
    assert _status(check) == pre.FAIL and response is None
    assert _status(pre.check_clock(None)) == pre.WARN

    r = httpx.Response(200, headers={"Date": "Wed, 30 Sep 2026 10:00:00 GMT"}, json={"claims": []})
    monkeypatch.setattr(pre.httpx, "get", lambda url, **kw: r)
    check, response = pre.check_owner("http://192.168.1.5:8000")
    assert _status(check) == pre.OK
    at = lambda s: datetime(2026, 9, 30, 10, 0, s % 60, tzinfo=timezone.utc).replace(minute=s // 60)  # noqa: E731
    assert [_status(pre.check_clock(response, now=at(s))) for s in (5, 45, 100)] == [pre.OK, pre.WARN, pre.FAIL]


def test_preflight_reset_clears_requests_and_pins_but_keeps_keys(requester_laptop):
    from requester import common

    pre = _load("requester_preflight")
    data = requester_laptop / "requester"
    ident = common.Identity("web")
    common.record_request(ident, "Earns 50k?", {"request_id": "rq_1", "status": "pending"}, "nonce-1")
    (data / "owner_keys.json").write_text(json.dumps({ident.fingerprint: "old-owner-key"}), encoding="utf-8")
    (data / "trusted_issuers.json").write_text("{}", encoding="utf-8")
    assert _status(pre.check_state(reset=False)) == pre.WARN
    assert (data / "requests.json").exists()  # a check alone removes nothing

    assert _status(pre.check_state(reset=True)) == pre.OK
    assert not any((data / n).exists() for n in pre.STALE_FILES)
    assert (data / "web.key").exists() and (data / "identity.json").exists() and (data / "trusted_issuers.json").exists()
    assert _status(pre.check_state(reset=False)) == pre.OK


def test_bench_script_imports():
    bench = _load("bench_models")
    assert bench.TARGETS["first_token_s"] == 5.0


@pytest.mark.llm
def test_bench_runs_against_ollama(monkeypatch):
    bench = _load("bench_models")
    monkeypatch.setattr(sys, "argv", ["bench_models.py", "--runs", "1"])
    assert bench.main() == 0


def test_run_eval_scores_a_scheduled_value_as_correct():
    """Set E e04 ("rent goes up to 16,000 from January"): today's value stays, the new one is scheduled."""
    from types import SimpleNamespace as V

    run_eval = _load("run_eval")
    now = V(value="14500", current=True, superseded_by=None, valid_to=None, valid_from="2026-08-05")
    later = V(value="16000", current=False, superseded_by=None, valid_to=None, valid_from="2027-01-01")
    old = V(value="14000", current=False, superseded_by="f_now", valid_to="2026-08-05", valid_from="2026-01-01")
    stray = V(value="9999", current=False, superseded_by=None, valid_to=None, valid_from="2026-02-01")  # open, past
    assert run_eval.score_e([now, later, old], "14500", "16000", today="2026-10-01")
    assert run_eval.score_e([now, later], "14500", today="2026-10-01")
    assert not run_eval.score_e([now, later], "14500", "17000", today="2026-10-01")
    assert not run_eval.score_e([now, stray], "14500", today="2026-10-01")
    assert not run_eval.score_e([later], "16000", today="2026-10-01")
