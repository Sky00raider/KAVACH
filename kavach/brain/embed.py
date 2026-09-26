"""Embeddings + hybrid search: 0.7 cosine + 0.3 BM25-lite over one cached NumPy matrix."""

from __future__ import annotations

from kavach.models import ScoredChunk


def search(query: str, k: int = 8) -> list[ScoredChunk]:
    """Stub: one canned hit."""
    return [ScoredChunk(chunk_id="c_3f9a1b2c4d", doc_id="d_7e21a0c9b4", locator="page 1",
                        text="Monthly rent: Rs. 15,000 payable on or before the 5th of each month.", score=0.82)][:k]
