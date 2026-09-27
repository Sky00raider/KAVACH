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


def test_bench_script_imports():
    bench = _load("bench_models")
    assert bench.TARGETS["first_token_s"] == 5.0


@pytest.mark.llm
def test_bench_runs_against_ollama(monkeypatch):
    bench = _load("bench_models")
    monkeypatch.setattr(sys, "argv", ["bench_models.py", "--runs", "1"])
    assert bench.main() == 0
