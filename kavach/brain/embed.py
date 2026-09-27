"""Hybrid search over every chunk: 0.7 x cosine + 0.3 x BM25-lite, each min-max normalised (BUILD_PLAN §4.2).

The index (one L2-normalised float32 matrix plus keyword postings) is cached in memory. It is rebuilt when
`invalidate()` has been called (ingest does, after every write) or when the chunks table changed underneath
it (another process, e.g. reset_demo). Chunks stored without a vector (Ollama was down at ingest) are
backfilled in a background thread: embeddings are computed and written first, then the index is rebuilt,
and searches meanwhile use the current index. A failed backfill is not retried for BACKFILL_RETRY_S.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass

import numpy as np

from kavach import config, db
from kavach.brain import llm
from kavach.brain.amounts import normalize_amounts
from kavach.models import ScoredChunk

log = logging.getLogger(__name__)

COSINE_WEIGHT, KEYWORD_WEIGHT = 0.7, 0.3
BM25_K1, BM25_B = 1.2, 0.75
BACKFILL_RETRY_S = 60.0
_EMBED_BATCH = 64

_TOKEN = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset(
    "a an and are as at be by did do does for from has have how i in is it its me much my of on or our so "
    "that the this to was we what when where which who why will with you your".split())


def tokenize(text: str) -> list[str]:
    """NFKC, amounts -> integer strings (so `15k`, `₹15,000` and `15000` match), lowercase words, no stopwords."""
    text = normalize_amounts(unicodedata.normalize("NFKC", text)).lower()
    return [t for t in _TOKEN.findall(text) if t not in _STOPWORDS]


def embed_documents(texts: list[str]) -> list[bytes | None]:
    """float32 BLOBs in batches, each text embedded as `EMBED_DOC_PREFIX + text` (queries use
    `EMBED_QUERY_PREFIX`). All None if Ollama is unavailable; `backfill()` fills them in later."""
    docs = [config.EMBED_DOC_PREFIX + t for t in texts]
    try:
        parts = [llm.embed(docs[i:i + _EMBED_BATCH]) for i in range(0, len(docs), _EMBED_BATCH)]
    except llm.LLMError as exc:
        log.warning("embedding failed, %d chunks left without vectors: %s", len(texts), exc)
        return [None] * len(texts)
    if not parts:
        return []
    return [row.astype(np.float32).tobytes() for row in np.concatenate(parts)]


# --- index -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Index:
    generation: int
    signature: tuple
    chunks: list[dict]  # chunk_id, doc_id, locator, text
    vectors: np.ndarray  # (n, EMBED_DIM), L2-normalised; zero rows where has_vector is False
    has_vector: np.ndarray  # (n,) bool
    postings: dict[str, tuple[np.ndarray, np.ndarray]]  # token -> (chunk rows, term frequencies)
    lengths: np.ndarray  # (n,) tokens per chunk
    avg_length: float


def _build(rows: list[dict], generation: int, signature: tuple) -> _Index:
    n, dim = len(rows), config.EMBED_DIM
    vectors = np.zeros((n, dim), np.float32)
    has_vector = np.zeros(n, bool)
    lengths = np.zeros(n, np.float32)
    postings: dict[str, tuple[list[int], list[int]]] = {}
    for i, row in enumerate(rows):
        blob = row["embedding"]
        if blob is not None and len(blob) == dim * 4:  # a wrong-sized vector (model changed) counts as missing
            v = np.frombuffer(blob, np.float32)
            norm = float(np.linalg.norm(v))
            if norm > 0 and math.isfinite(norm):
                vectors[i], has_vector[i] = v / norm, True
        counts = Counter(tokenize(row["text"]))
        lengths[i] = sum(counts.values())
        for token, tf in counts.items():
            rows_tf = postings.setdefault(token, ([], []))
            rows_tf[0].append(i)
            rows_tf[1].append(tf)
    return _Index(
        generation=generation, signature=signature,
        chunks=[{c: row[c] for c in ("chunk_id", "doc_id", "locator", "text")} for row in rows],
        vectors=vectors, has_vector=has_vector,
        postings={t: (np.array(ix, np.intp), np.array(tf, np.float32)) for t, (ix, tf) in postings.items()},
        lengths=lengths, avg_length=float(lengths.mean()) if n and lengths.any() else 1.0)


_state_lock = threading.Lock()  # guards _index, _generation, _backfill_thread; held only for swaps and reads
_build_lock = threading.Lock()  # one rebuild at a time (fast: no model calls)
_backfill_lock = threading.Lock()  # one backfill at a time
_index: _Index | None = None
_generation = 0
_retry_at = 0.0
_backfill_thread: threading.Thread | None = None


def invalidate() -> None:
    """Mark the cached index stale; the next search rebuilds it. Ingest calls this after every write."""
    global _generation
    with _state_lock:
        _generation += 1


def _fresh(idx: _Index | None, generation: int, signature: tuple) -> bool:
    return idx is not None and idx.generation == generation and idx.signature == signature


def _current() -> _Index:
    """The cached index, rebuilt first if stale. The signature is read before the rows, so a write in between
    only causes one extra rebuild, never a stale index taken as fresh."""
    global _index
    with _state_lock:
        idx, generation = _index, _generation
    if _fresh(idx, generation, db.chunks_signature()):
        return idx
    with _build_lock:
        signature = db.chunks_signature()
        with _state_lock:
            idx, generation = _index, _generation
        if _fresh(idx, generation, signature):
            return idx  # another thread rebuilt it while we waited
        idx = _build(db.search_chunks(), generation, signature)
        with _state_lock:
            _index = idx
        return idx


def backfill() -> int:
    """Embed chunks stored without a (right-sized) vector, write them to the DB, then invalidate the index.
    No index lock is held while embedding. Returns the number filled; 0 if another backfill is running."""
    global _retry_at
    if not _backfill_lock.acquire(blocking=False):
        return 0
    try:
        missing = db.chunks_missing_embeddings(config.EMBED_DIM * 4)
        if not missing:
            return 0
        blobs = embed_documents([r["text"] for r in missing])
        pairs = [(r["chunk_id"], b) for r, b in zip(missing, blobs) if b is not None]
        if not pairs:
            _retry_at = time.monotonic() + BACKFILL_RETRY_S
            return 0
        db.set_chunk_embeddings(pairs)
        invalidate()
        log.info("backfilled %d chunk embeddings", len(pairs))
        return len(pairs)
    finally:
        _backfill_lock.release()


def _backfill_in_background() -> None:
    try:
        backfill()
    except Exception:
        log.exception("embedding backfill failed")


def _maybe_backfill(idx: _Index) -> None:
    global _backfill_thread
    if idx.has_vector.all() or time.monotonic() < _retry_at or _backfill_lock.locked():
        return
    with _state_lock:
        if _backfill_thread is not None and _backfill_thread.is_alive():
            return
        _backfill_thread = threading.Thread(target=_backfill_in_background, name="kavach-backfill", daemon=True)
        _backfill_thread.start()


def warm() -> None:
    """Backfill missing vectors, then build the index, so the first live search is fast (API startup)."""
    try:
        backfill()
        _current()
    except Exception:
        log.exception("search index warm-up failed")


# --- scoring -----------------------------------------------------------------------------------------------


def _bm25(idx: _Index, query_tokens: list[str]) -> np.ndarray:
    n = len(idx.chunks)
    scores = np.zeros(n, np.float32)
    for token in set(query_tokens):
        post = idx.postings.get(token)
        if post is None:
            continue
        rows, tf = post
        idf = math.log(1 + (n - len(rows) + 0.5) / (len(rows) + 0.5))
        norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * idx.lengths[rows] / idx.avg_length)
        scores[rows] += idf * tf * (BM25_K1 + 1) / norm
    return scores


def _minmax(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """values[mask] scaled to [0, 1]; entries outside the mask, or all of them if the masked values are equal, -> 0."""
    out = np.zeros(len(values), np.float32)
    if not mask.any():
        return out
    lo, hi = float(values[mask].min()), float(values[mask].max())
    if hi - lo > 1e-9:
        out[mask] = (values[mask] - lo) / (hi - lo)
    return out


def _query_vector(query: str) -> np.ndarray | None:
    try:
        v = llm.embed([config.EMBED_QUERY_PREFIX + query])[0].astype(np.float32)
    except llm.LLMError as exc:
        log.warning("query embedding failed, keyword-only search: %s", exc)
        return None
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else None


def search(query: str, k: int = 8) -> list[ScoredChunk]:
    """Top-k chunks for `query`, best first, ties in insertion order.

    score = 0.7 x cosine + 0.3 x BM25, each min-max normalised over the whole vault (cosine over chunks that
    have a vector); a component whose values are all equal counts as 0. Scores are relative to this query
    (the best chunk is near 1), not an absolute relevance measure. Without a query vector (Ollama down) the
    score is BM25 alone. Chunks with neither a vector nor a keyword match are never returned.
    """
    if k <= 0 or not query.strip():
        return []
    idx = _current()
    _maybe_backfill(idx)
    n = len(idx.chunks)
    if n == 0:
        return []
    keyword = _bm25(idx, tokenize(query))
    keyword_n = _minmax(keyword, np.ones(n, bool))
    qvec = _query_vector(query) if idx.has_vector.any() else None
    if qvec is None:
        score, signal = keyword_n, keyword > 0
    else:
        score = COSINE_WEIGHT * _minmax(idx.vectors @ qvec, idx.has_vector) + KEYWORD_WEIGHT * keyword_n
        signal = idx.has_vector | (keyword > 0)
    rows = np.flatnonzero(signal)
    rows = rows[np.lexsort((rows, -score[rows]))][:k]
    return [ScoredChunk(**idx.chunks[i], score=float(score[i])) for i in rows]
