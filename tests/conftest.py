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
