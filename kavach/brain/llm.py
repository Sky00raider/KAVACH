"""Ollama client: chat, structured output, embeddings, streaming (BRAIN step 1).

All calls go to `config.OLLAMA_URL` with `keep_alive=config.OLLAMA_KEEP_ALIVE` and `temperature: 0`.
Stub: not implemented yet; nothing in the scaffold calls these.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar

import numpy as np
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Typed error after a failed structured call and one retry."""


def chat(messages: list[dict], model: str | None = None) -> str:
    raise NotImplementedError("BRAIN step 1")


def chat_stream(messages: list[dict], model: str | None = None) -> Iterator[str]:
    raise NotImplementedError("BRAIN step 1")


def structured(messages: list[dict], schema: type[T], model: str | None = None) -> T:
    raise NotImplementedError("BRAIN step 1")


def embed(texts: list[str]) -> np.ndarray:
    raise NotImplementedError("BRAIN step 1")
