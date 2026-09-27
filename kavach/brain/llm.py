"""Ollama client: chat, structured output, embeddings, streaming (BRAIN step 1).

All calls go to `config.OLLAMA_URL` with `keep_alive=config.OLLAMA_KEEP_ALIVE`; chat and structured calls
also send `temperature: 0` and `num_ctx=config.NUM_CTX`.
Rules every call must follow (built into the request builders below):
- structured calls send `think: false` (thinking models otherwise spend seconds on hidden reasoning;
  non-thinking models accept the flag), and `format` = the Pydantic model's JSON schema;
- embeddings go to `/api/embed` in one batched request with a list `input`, never one call per chunk.
Every failure surfaces as `LLMError`. Only `structured` retries (once); a stream that already yielded tokens
cannot be retried without duplicating them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, TypeVar

import httpx
import numpy as np
from pydantic import BaseModel, ValidationError

from kavach import config

T = TypeVar("T", bound=BaseModel)

CHAT_PATH = "/api/chat"
EMBED_PATH = "/api/embed"

TIMEOUT = httpx.Timeout(300.0, connect=5.0)          # whole-answer calls on a CPU-only laptop
STREAM_TIMEOUT = httpx.Timeout(120.0, connect=5.0)   # read timeout applies per streamed chunk
RETRY_NOTE = "Your previous reply was invalid: {error}. Return only JSON matching the schema."
_MAX_ERROR_CHARS = 500


class LLMError(Exception):
    """Typed error after a failed structured call and one retry."""


def chat_request(messages: list[dict], model: str | None = None, stream: bool = False) -> dict[str, Any]:
    """Body for POST /api/chat (free-text answers)."""
    return {"model": model or config.LLM_MODEL, "messages": messages, "stream": stream,
            "options": {"temperature": 0, "num_ctx": config.NUM_CTX}, "keep_alive": config.OLLAMA_KEEP_ALIVE}


def structured_request(messages: list[dict], schema: type[BaseModel], model: str | None = None) -> dict[str, Any]:
    """Body for POST /api/chat with structured output: JSON-schema `format`, `think: false`, no streaming."""
    body = chat_request(messages, model or config.FAST_MODEL, stream=False)
    body["format"] = schema.model_json_schema()
    body["think"] = False
    return body


def embed_request(texts: list[str], model: str | None = None) -> dict[str, Any]:
    """Body for one batched POST /api/embed call."""
    return {"model": model or config.EMBED_MODEL, "input": list(texts), "keep_alive": config.OLLAMA_KEEP_ALIVE}


_http: httpx.Client | None = None


def _client() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(base_url=config.OLLAMA_URL, timeout=TIMEOUT)
    return _http


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = _client().post(path, json=body)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"Ollama {path} returned {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise LLMError(f"Ollama {path} failed: {exc!r}") from exc
    if "error" in data:
        raise LLMError(f"Ollama {path} error: {data['error']}")
    return data


def _content(data: dict[str, Any]) -> str:
    content = (data.get("message") or {}).get("content")
    if not isinstance(content, str):
        raise LLMError("Ollama reply has no message.content")
    return content


def chat(messages: list[dict], model: str | None = None) -> str:
    return _content(_post(CHAT_PATH, chat_request(messages, model, stream=False)))


def chat_stream(messages: list[dict], model: str | None = None) -> Iterator[str]:
    body = chat_request(messages, model, stream=True)
    try:
        with _client().stream("POST", CHAT_PATH, json=body, timeout=STREAM_TIMEOUT) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise LLMError(f"Ollama {CHAT_PATH} returned {resp.status_code}: {resp.text[:200]}")
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise LLMError(f"Ollama stream error: {data['error']}")
                piece = (data.get("message") or {}).get("content") or ""
                if piece:
                    yield piece
                if data.get("done"):
                    return
    except (httpx.HTTPError, ValueError) as exc:
        raise LLMError(f"Ollama stream failed: {exc!r}") from exc
    raise LLMError("Ollama stream ended without done")


def structured(messages: list[dict], schema: type[T], model: str | None = None) -> T:
    """Validated structured output. On a bad reply, retry once with the validation error appended."""
    attempt_messages = list(messages)
    last: Exception | None = None
    for _ in range(2):
        try:
            raw = _content(_post(CHAT_PATH, structured_request(attempt_messages, schema, model)))
        except LLMError as exc:  # transport or server error: retry with the same messages
            last = exc
            continue
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            last = exc
            error = str(exc)[:_MAX_ERROR_CHARS]
            attempt_messages = [*messages, {"role": "assistant", "content": raw},
                                {"role": "user", "content": RETRY_NOTE.format(error=error)}]
    raise LLMError(f"structured {schema.__name__} failed after retry: {last}") from last


def embed(texts: list[str]) -> np.ndarray:
    """float32 matrix of shape (len(texts), EMBED_DIM), one batched call; vectors as Ollama returns them."""
    if not texts:
        return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
    vectors = _post(EMBED_PATH, embed_request(texts)).get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise LLMError(f"embed returned {len(vectors) if isinstance(vectors, list) else 'no'} vectors "
                       f"for {len(texts)} texts")
    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except ValueError as exc:
        raise LLMError(f"embed returned ragged vectors: {exc}") from exc
    if matrix.ndim != 2 or matrix.shape[1] != config.EMBED_DIM:
        raise LLMError(f"embed dim {matrix.shape[1:]} != EMBED_DIM {config.EMBED_DIM}")
    return matrix
