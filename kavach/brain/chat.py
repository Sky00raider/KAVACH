"""Ask my vault: cited answers, sync and streamed (CONTRACT §10)."""

from __future__ import annotations

import time
from collections.abc import Iterator

from kavach.models import (
    ChatDoneData,
    ChatDoneEvent,
    ChatEvent,
    ChatFinal,
    ChatFinalEvent,
    ChatMetaData,
    ChatMetaEvent,
    ChatResult,
    ChatTokenData,
    ChatTokenEvent,
    ChatTurn,
    ChunkRef,
    Citation,
)

_CANNED = "Your rent is ₹15,000 a month [1], and the agreement ends on 31 March 2027 [2]."
_CHUNKS = [
    ChunkRef(n=1, chunk_id="c_3f9a1b2c4d", doc_id="d_7e21a0c9b4", locator="page 1"),
    ChunkRef(n=2, chunk_id="c_8b7c6d5e4f", doc_id="d_7e21a0c9b4", locator="page 2"),
]
_CITATIONS = [
    Citation(**_CHUNKS[0].model_dump(), quote="Monthly rent: Rs. 15,000"),
    Citation(**_CHUNKS[1].model_dump(), quote="valid until 31/03/2027"),
]
_ENTITIES = ["e_owner", "e_5c4b3a2d1e"]


def answer(question: str, history: list[ChatTurn]) -> ChatResult:
    """Stub: canned cited answer."""
    return ChatResult(answer=_CANNED, citations=_CITATIONS, citation_ok=True, flags=[], memory_candidates=[],
                      entities_used=_ENTITIES)


def answer_stream(question: str, history: list[ChatTurn]) -> Iterator[ChatEvent]:
    """Stub: streams the canned answer word by word as §10 events."""
    start = time.perf_counter()
    yield ChatMetaEvent(data=ChatMetaData(entities_used=_ENTITIES, chunks=_CHUNKS))
    first_token_ms = 0
    for i, word in enumerate(_CANNED.split(" ")):
        if i == 0:
            first_token_ms = int((time.perf_counter() - start) * 1000)
        yield ChatTokenEvent(data=ChatTokenData(text=word if i == 0 else " " + word))
        time.sleep(0.03)
    yield ChatFinalEvent(data=ChatFinal(answer=_CANNED, citations=_CITATIONS, citation_ok=True))
    yield ChatDoneEvent(data=ChatDoneData(latency_ms=int((time.perf_counter() - start) * 1000),
                                          first_token_ms=first_token_ms))
