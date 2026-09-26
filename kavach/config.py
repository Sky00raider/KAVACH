"""Ports, paths, model names and the owner token (CONTRACT §3).

Every value can be overridden by an environment variable of the same name.
Model names live here and nowhere else.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _path(name: str, default: Path) -> Path:
    return Path(os.environ[name]).resolve() if name in os.environ else default


# Ollama
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
LLM_MODEL = _env("LLM_MODEL", "qwen3:8b")
FAST_MODEL = _env("FAST_MODEL", "qwen3:4b")
EMBED_MODEL = _env("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(_env("EMBED_DIM", "768"))
OLLAMA_KEEP_ALIVE = _env("OLLAMA_KEEP_ALIVE", "24h")

# Paths (runtime state is gitignored)
DB_PATH = _path("DB_PATH", ROOT / "kavach.db")
VAULT_DIR = _path("VAULT_DIR", ROOT / "vault")
OUTBOX_DIR = _path("OUTBOX_DIR", ROOT / "outbox")
KEYS_DIR = _path("KEYS_DIR", ROOT / "keys")
FRONTEND_DIST = _path("FRONTEND_DIST", ROOT / "frontend" / "dist")
DEMO_DATA_DIR = _path("DEMO_DATA_DIR", ROOT / "demo_data")

# Ports
API_PORT = int(_env("API_PORT", "8000"))
GATE_PORT = int(_env("GATE_PORT", "8001"))
REQUESTER_PORT = int(_env("REQUESTER_PORT", "9000"))
OWNER_URL = _env("OWNER_URL", f"http://127.0.0.1:{API_PORT}")  # requester side

# Ingestion
CHUNK_SIZE = int(_env("CHUNK_SIZE", "600"))
CHUNK_OVERLAP = int(_env("CHUNK_OVERLAP", "100"))
WATCH_DEBOUNCE_S = float(_env("WATCH_DEBOUNCE_S", "2"))

# Disclosure ledger and wallet
LEDGER_MIN_WIDTH: dict[str, int] = json.loads(_env("LEDGER_MIN_WIDTH", '{"income": 25000, "percentage": 15}'))
LEDGER_MAX_ATTESTED_PER_30D = int(_env("LEDGER_MAX_ATTESTED_PER_30D", "3"))
WALLET_LOW_COPIES = int(_env("WALLET_LOW_COPIES", "3"))


def _owner_token() -> str:
    """Random per install, kept in keys/owner_token. Created on first import."""
    if "OWNER_TOKEN" in os.environ:
        return os.environ["OWNER_TOKEN"]
    path = KEYS_DIR / "owner_token"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


OWNER_TOKEN = _owner_token()
