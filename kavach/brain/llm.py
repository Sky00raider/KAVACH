"""Ollama client: chat, structured output, embeddings, streaming (BRAIN step 1).

All calls go to `config.OLLAMA_URL` with `keep_alive=config.OLLAMA_KEEP_ALIVE` and `temperature: 0`.
Rules every call must follow (built into the request builders below):
- structured calls send `think: false` (thinking models otherwise spend seconds on hidden reasoning;
  non-thinking models accept the flag), and `format` = the Pydantic model's JSON schema;
- embeddings go to `/api/embed` in one batched request with a list `input`, never one call per chunk.
The HTTP calls are stubs until BRAIN step 1; they must send the bodies from these builders.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypeVar

import numpy as np
from pydantic import BaseModel

from kavach import config

T = TypeVar("T", bound=BaseModel)

CHAT_PATH = "/api/chat"
EMBED_PATH = "/api/embed"


class LLMError(Exception):
    """Typed error after a failed structured call and one retry."""


def chat_request(messages: list[dict], model: str | None = None, stream: bool = False) -> dict[str, Any]:
    """Body for POST /api/chat (free-text answers)."""
    return {"model": model or config.LLM_MODEL, "messages": messages, "stream": stream,
            "options": {"temperature": 0}, "keep_alive": config.OLLAMA_KEEP_ALIVE}


def structured_request(messages: list[dict], schema: type[BaseModel], model: str | None = None) -> dict[str, Any]:
    """Body for POST /api/chat with structured output: JSON-schema `format`, `think: false`, no streaming."""
    body = chat_request(messages, model or config.FAST_MODEL, stream=False)
    body["format"] = schema.model_json_schema()
    body["think"] = False
    return body


def embed_request(texts: list[str], model: str | None = None) -> dict[str, Any]:
    """Body for one batched POST /api/embed call."""
    return {"model": model or config.EMBED_MODEL, "input": list(texts), "keep_alive": config.OLLAMA_KEEP_ALIVE}


def chat(messages: list[dict], model: str | None = None) -> str:
    raise NotImplementedError("BRAIN step 1")


def chat_stream(messages: list[dict], model: str | None = None) -> Iterator[str]:
    raise NotImplementedError("BRAIN step 1")


def structured(messages: list[dict], schema: type[T], model: str | None = None) -> T:
    raise NotImplementedError("BRAIN step 1")


def embed(texts: list[str]) -> np.ndarray:
    raise NotImplementedError("BRAIN step 1")
