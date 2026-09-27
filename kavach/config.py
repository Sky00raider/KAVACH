"""Ports, paths, model names and the owner token (CONTRACT §3).

Every value can be overridden by an environment variable of the same name.
Model names live here and nowhere else.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _path(name: str, default: Path) -> Path:
    return Path(os.environ[name]).resolve() if name in os.environ else default


# Ollama
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
LLM_MODEL = _env("LLM_MODEL", "qwen2.5:3b")  # laptop default; LLM_MODEL=qwen2.5:7b when OLLAMA_URL is a GPU host
FAST_MODEL = _env("FAST_MODEL", "qwen2.5:3b")
EMBED_MODEL = _env("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(_env("EMBED_DIM", "768"))
# nomic-embed-text task prefixes: chunks are embedded as documents, questions as queries
EMBED_DOC_PREFIX = _env("EMBED_DOC_PREFIX", "search_document: ")
EMBED_QUERY_PREFIX = _env("EMBED_QUERY_PREFIX", "search_query: ")
OLLAMA_KEEP_ALIVE = _env("OLLAMA_KEEP_ALIVE", "24h")
NUM_CTX = int(_env("NUM_CTX", "8192"))  # options.num_ctx on chat and structured calls
CHAT_CONTEXT_TOKENS = int(_env("CHAT_CONTEXT_TOKENS", "800"))  # estimated history + chunk tokens per chat prompt

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

# The owner (entity e_owner); matches the mock issuers' profile so documents about them resolve to one entity
OWNER_NAME = _env("OWNER_NAME", "Ananya Iyer")

# Ingestion
CHUNK_SIZE = int(_env("CHUNK_SIZE", "600"))
CHUNK_OVERLAP = int(_env("CHUNK_OVERLAP", "100"))
WATCH_DEBOUNCE_S = float(_env("WATCH_DEBOUNCE_S", "2"))
# "0" turns off the owner API's vault watcher and search-index warm-up (tests)
KAVACH_WATCH = _env("KAVACH_WATCH", "1") != "0"

# Disclosure ledger and wallet
LEDGER_MIN_WIDTH: dict[str, int] = json.loads(_env("LEDGER_MIN_WIDTH", '{"income": 25000, "percentage": 15}'))
LEDGER_MAX_ATTESTED_PER_30D = int(_env("LEDGER_MAX_ATTESTED_PER_30D", "3"))
WALLET_LOW_COPIES = int(_env("WALLET_LOW_COPIES", "3"))


_owner_token_lock = threading.Lock()
_owner_token_value: str | None = None


def owner_token() -> str:
    """Random per install, kept in keys/owner_token.

    Created on first use by the owner API, never on import, so processes that only import config
    (the requester laptop, scripts, MCP servers) never create one. An existing file is never replaced.
    """
    global _owner_token_value
    if "OWNER_TOKEN" in os.environ:
        return os.environ["OWNER_TOKEN"]
    with _owner_token_lock:
        if _owner_token_value is None:
            path = KEYS_DIR / "owner_token"
            KEYS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(path, "x", encoding="utf-8") as f:  # exclusive create: a racing process keeps its token
                    token = secrets.token_urlsafe(32)
                    f.write(token)
            except FileExistsError:
                token = path.read_text(encoding="utf-8").strip()
            if not token:
                raise RuntimeError(f"{path} is empty; delete it to generate a new owner token")
            _owner_token_value = token
        return _owner_token_value


def __getattr__(name: str) -> str:
    """`config.OWNER_TOKEN` (CONTRACT §3) resolves lazily through owner_token()."""
    if name == "OWNER_TOKEN":
        return owner_token()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
