import os

import pytest

from kavach import config


def test_contract_config_names_exist():
    for name in ("OLLAMA_URL", "LLM_MODEL", "FAST_MODEL", "EMBED_MODEL", "EMBED_DIM", "OLLAMA_KEEP_ALIVE", "DB_PATH",
                 "VAULT_DIR", "OUTBOX_DIR", "KEYS_DIR", "OWNER_TOKEN", "API_PORT", "GATE_PORT", "REQUESTER_PORT",
                 "OWNER_URL", "CHUNK_SIZE", "CHUNK_OVERLAP", "WATCH_DEBOUNCE_S", "LEDGER_MIN_WIDTH",
                 "LEDGER_MAX_ATTESTED_PER_30D", "WALLET_LOW_COPIES"):
        assert hasattr(config, name), name
    assert config.CHUNK_SIZE == 600 and config.LEDGER_MIN_WIDTH == {"income": 25000, "percentage": 15}


def test_env_overrides_are_applied():
    # conftest sets these before import
    assert config.OWNER_TOKEN == "test-owner-token"
    assert config.DB_PATH.name == "kavach.db" and "kavach-test-" in str(config.DB_PATH)


def test_import_never_creates_owner_token(tmp_path):
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "OWNER_TOKEN"} | {"KEYS_DIR": str(tmp_path / "keys")}
    code = "import kavach.config, kavach.gate_mcp, kavach.tools_mcp, requester.app, requester.agent_client, kavach.api"
    subprocess.run([sys.executable, "-c", code], env=env, cwd=config.ROOT, check=True)
    assert not (tmp_path / "keys" / "owner_token").exists()


@pytest.fixture
def no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_TOKEN")
    monkeypatch.setattr(config, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(config, "_owner_token_value", None)
    return tmp_path / "keys" / "owner_token"


def test_owner_token_created_on_first_use_then_stable(no_token):
    assert not no_token.exists()
    token = config.OWNER_TOKEN
    assert no_token.read_text(encoding="utf-8") == token and len(token) >= 40
    config._owner_token_value = None
    assert config.owner_token() == token


def test_existing_owner_token_is_reused(no_token):
    no_token.parent.mkdir(parents=True)
    no_token.write_text("kept-token\n", encoding="utf-8")
    assert config.OWNER_TOKEN == "kept-token"


def test_empty_owner_token_file_is_an_error(no_token):
    no_token.parent.mkdir(parents=True)
    no_token.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError):
        config.owner_token()


def test_owner_api_creates_token_on_first_request(no_token, fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from kavach import api

    monkeypatch.setattr(config, "FRONTEND_DIST", no_token.parent.parent / "dist")
    client = TestClient(api.app, client=("127.0.0.1", 1))
    assert not no_token.exists()
    html = client.get("/").text
    token = no_token.read_text(encoding="utf-8")
    assert token in html
    assert client.get("/api/wallet", headers={"X-Owner-Token": token}).status_code == 200
