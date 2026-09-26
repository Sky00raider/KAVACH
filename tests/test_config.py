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
