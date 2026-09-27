"""Point every runtime path at a throwaway directory before kavach.config is imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="kavach-test-"))
_DIRS = {"DB_PATH": "kavach.db", "VAULT_DIR": "vault", "OUTBOX_DIR": "outbox", "KEYS_DIR": "keys",
         "REQUESTER_DATA_DIR": "requester_data"}
for _name, _sub in _DIRS.items():
    os.environ[_name] = str(_TMP / _sub)
os.environ["OWNER_TOKEN"] = "test-owner-token"
os.environ["KAVACH_WATCH"] = "0"  # no vault watcher or index warm-up in the API lifespan

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """An initialised, empty database for one test."""
    from kavach import config, db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kavach.db")
    db.init_db()
    return db


def fake_parse_map(text: str):
    """Keyword stand-in for parse_question's one model call. Normalisation and validation still run."""
    import re

    from kavach.brain.parse_question import _Mapped

    low = text.lower()
    n = re.search(r"\d+", text)
    t = int(n.group()) if n else None
    if re.search(r"earn|income|salary", low):
        return _Mapped(claim="income", threshold=t)
    if re.search(r"percent|marks|score", low):
        return _Mapped(claim="percentage", threshold=t)
    if re.search(r"\bage\b|adult|\bold\b|\d\+", low):
        return _Mapped(claim="age", threshold=t or 18)
    if "default" in low:
        return _Mapped(claim="loan_default_12m")
    if "pass" in low:
        return _Mapped(claim="result")
    if "board" in low:
        return _Mapped(claim="board")
    return _Mapped(claim="unsupported")


@pytest.fixture(autouse=True)
def _no_ollama_outside_llm_tests(request, monkeypatch):
    """Tests not marked `llm` never call Ollama: questions reaching parse_question (consent, MCP, eval) are
    mapped by `fake_parse_map` instead of the model, and ingest's entity extraction finds nothing (tests that
    need entities patch `entities.extract` themselves). Query and entity-name vectors are never reused across
    tests, since tests fake `llm.embed` differently."""
    from kavach.brain import embed, entities

    embed._query_cache.clear()
    entities._name_vectors.clear()
    if request.node.get_closest_marker("llm") is None:
        from kavach.brain import parse_question

        monkeypatch.setattr(parse_question, "_map", fake_parse_map)
        monkeypatch.setattr(entities, "extract", lambda text: entities.Extraction())
