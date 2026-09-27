import math
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from kavach import api, config
from kavach.brain import embed, ingest, llm, watcher
from kavach.db import new_id
from kavach.trust import audit

DIM = config.EMBED_DIM


def _axis(i: int) -> np.ndarray:
    v = np.zeros(DIM, np.float32)
    v[i] = 1.0
    return v


def _at_cosine(c: float, axis: int) -> np.ndarray:
    """A unit vector whose cosine with the query axis (0) is exactly `c`."""
    return (c * _axis(0) + math.sqrt(1 - c * c) * _axis(axis)).astype(np.float32)


@pytest.fixture
def space(fresh_db, monkeypatch):
    """A fake embedding model: texts (without their nomic prefix) map to fixed vectors; unknown texts get
    their own orthogonal axis. `down` makes every call fail; `gate` blocks document embeddings."""
    monkeypatch.setattr(embed, "_index", None)
    monkeypatch.setattr(embed, "_retry_at", 0.0)
    monkeypatch.setattr(embed, "_backfill_thread", None)
    state = SimpleNamespace(vectors={}, calls=[], down=False, gate=None, next_axis=[DIM - 1])

    def fake_embed(texts):
        state.calls.append(list(texts))
        if state.down:
            raise llm.LLMError("ollama down")
        if state.gate is not None and texts[0].startswith(config.EMBED_DOC_PREFIX):
            state.gate.wait(5)
        out = []
        for t in texts:
            key = t.removeprefix(config.EMBED_DOC_PREFIX).removeprefix(config.EMBED_QUERY_PREFIX)
            if key not in state.vectors:
                state.vectors[key] = _axis(state.next_axis[0])
                state.next_axis[0] -= 1
            out.append(state.vectors[key])
        return np.stack(out).astype(np.float32)

    monkeypatch.setattr(llm, "embed", fake_embed)
    yield state
    if embed._backfill_thread is not None:
        embed._backfill_thread.join(5)


def _add(db, text, vector=None, blob=None, doc_id="d_test00001"):
    chunk_id = new_id("c")
    if vector is not None:
        blob = np.asarray(vector, np.float32).tobytes()
    db.insert("chunks", {"chunk_id": chunk_id, "doc_id": doc_id, "locator": "page 1", "text": text, "embedding": blob})
    return chunk_id


def _join_backfill():
    t = embed._backfill_thread
    if t is not None:
        t.join(5)
        assert not t.is_alive()


# --- tokeniser ---------------------------------------------------------------------------------------------


def test_tokenize_normalises_amounts_and_drops_stopwords():
    assert embed.tokenize("Is my rent ₹15,000?") == ["rent", "15000"]
    assert embed.tokenize("15k") == embed.tokenize("Rs. 15,000/-") == embed.tokenize("15000") == ["15000"]
    assert embed.tokenize("1.2 lakh deposit") == ["120000", "deposit"]
    assert embed.tokenize("Flat_4B, Indiranagar") == ["flat", "4b", "indiranagar"]


# --- search ------------------------------------------------------------------------------------------------


def test_empty_vault_blank_query_and_k(space, fresh_db):
    assert embed.search("rent") == []
    _add(fresh_db, "rent is due", _axis(0))
    assert embed.search("   ") == [] and embed.search("rent", k=0) == []
    assert len(embed.search("rent", k=5)) == 1


def test_semantic_match_wins_when_raw_cosine_is_compressed(space, fresh_db):
    # Real nomic cosines sit in a narrow band. The unnormalised blend 0.7*cos + 0.3*bm25/max would rank the
    # keyword-only chunk first (0.7*0.45 + 0.3 = 0.615 > 0.7*0.60 = 0.42); min-max on both components
    # lets the best semantic match win (0.7 > 0.3).
    space.vectors["how do I contact the property owner"] = _axis(0)
    semantic = _add(fresh_db, "Mr. Ramesh Kumar, phone 98450 12345, for repairs", _at_cosine(0.60, 1))
    keyword = _add(fresh_db, "the property tax receipt for 2025", _at_cosine(0.45, 2))
    _add(fresh_db, "gym routine and cardio", _at_cosine(0.46, 3))
    _add(fresh_db, "marks in physics and chemistry", _at_cosine(0.47, 4))
    hits = embed.search("how do I contact the property owner")
    assert [h.chunk_id for h in hits[:2]] == [semantic, keyword]
    assert hits[0].score == pytest.approx(0.7) and hits[1].score == pytest.approx(0.3)


def test_amount_keyword_breaks_a_cosine_tie(space, fresh_db):
    space.vectors["is my rent 15k"] = _axis(0)
    other = _add(fresh_db, "Rent is paid by bank transfer every month.", _at_cosine(0.5, 1))
    amount = _add(fresh_db, "Monthly rent: Rs. 15,000 payable on the 5th.", _at_cosine(0.5, 2))
    hits = embed.search("is my rent 15k")
    assert [h.chunk_id for h in hits] == [amount, other]


def test_query_is_embedded_with_the_query_prefix(space, fresh_db):
    _add(fresh_db, "rent", _axis(0))
    embed.search("how much is my rent")
    assert space.calls[-1] == [config.EMBED_QUERY_PREFIX + "how much is my rent"]


def test_results_are_sorted_and_limited_to_k(space, fresh_db):
    space.vectors["q"] = _axis(0)
    ids = [_add(fresh_db, f"chunk {i}", _at_cosine(c, i + 1)) for i, c in enumerate([0.2, 0.9, 0.5, 0.7, 0.1])]
    hits = embed.search("q", k=3)
    assert [h.chunk_id for h in hits] == [ids[1], ids[3], ids[2]]
    assert hits[0].score == pytest.approx(0.7) and hits[0].score > hits[1].score > hits[2].score
    assert (hits[0].doc_id, hits[0].locator, hits[0].text) == ("d_test00001", "page 1", "chunk 1")


def test_a_lone_chunk_is_returned_even_though_its_normalised_score_is_zero(space, fresh_db):
    only = _add(fresh_db, "the only note", _axis(5))
    hits = embed.search("anything")
    assert [h.chunk_id for h in hits] == [only] and hits[0].score == 0.0


def test_ollama_down_falls_back_to_keywords_only(space, fresh_db):
    rent = _add(fresh_db, "Monthly rent: Rs. 15,000", _axis(1))
    _add(fresh_db, "gym routine", _axis(2))
    space.down = True
    hits = embed.search("rent 15k")
    assert [h.chunk_id for h in hits] == [rent] and hits[0].score == pytest.approx(1.0)


def test_chunks_with_no_vector_need_a_keyword_match(space, fresh_db):
    space.down = True  # also keeps the backfill from filling them
    with_word = _add(fresh_db, "landlord phone number")
    _add(fresh_db, "gym routine")
    assert [h.chunk_id for h in embed.search("landlord")] == [with_word]


# --- cache -------------------------------------------------------------------------------------------------


def test_index_is_cached_until_invalidated_or_the_db_changes(space, fresh_db, monkeypatch):
    loads = []
    real = fresh_db.search_chunks
    monkeypatch.setattr(fresh_db, "search_chunks", lambda: loads.append(1) or real())
    _add(fresh_db, "rent", _axis(0))
    embed.search("rent")
    embed.search("rent")
    assert len(loads) == 1
    embed.invalidate()
    embed.search("rent")
    assert len(loads) == 2
    _add(fresh_db, "a chunk written by another process", _axis(1))
    assert len(embed.search("rent")) == 2 and len(loads) == 3


def test_ingest_invalidates_the_index(space, fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAULT_DIR", tmp_path / "vault")
    monkeypatch.setattr(audit, "log", lambda *a: 1)
    note = tmp_path / "vault" / "notes" / "rent.md"
    note.parent.mkdir(parents=True)
    note.write_text("Monthly rent is 15,000 rupees.", encoding="utf-8")
    assert embed.search("rent") == []  # index built on the empty vault
    ingest.ingest_file(note)
    assert [h.locator for h in embed.search("rent")] == ["note: rent.md"]
    ingest.remove_file(note)
    assert embed.search("rent") == []


# --- backfill ----------------------------------------------------------------------------------------------


def test_backfill_fills_missing_vectors_in_the_background(space, fresh_db):
    space.vectors["q"] = _axis(0)
    space.vectors["semantic match"] = _axis(0)
    chunk = _add(fresh_db, "semantic match")  # stored while Ollama was down
    assert embed.search("q") == []  # no vector, no keyword: nothing yet; backfill starts
    _join_backfill()
    assert config.EMBED_DOC_PREFIX + "semantic match" in [t for call in space.calls for t in call]
    row = fresh_db.fetch_one("SELECT embedding FROM chunks WHERE chunk_id = ?", (chunk,))
    assert np.frombuffer(row["embedding"], np.float32)[0] == 1.0
    assert [h.chunk_id for h in embed.search("q")] == [chunk]


def test_searches_during_a_backfill_use_the_current_index(space, fresh_db):
    space.gate = threading.Event()
    rent = _add(fresh_db, "rent agreement")
    first = embed.search("rent")  # starts the backfill, which blocks on the gate
    assert [h.chunk_id for h in first] == [rent]
    deadline = time.monotonic() + 5
    while not space.calls and time.monotonic() < deadline:  # wait until the backfill is inside the model call
        time.sleep(0.01)
    assert embed._backfill_lock.locked()
    assert [h.chunk_id for h in embed.search("rent")] == [rent]  # not blocked by the running backfill
    space.gate.set()
    _join_backfill()
    assert fresh_db.fetch_one("SELECT embedding FROM chunks")["embedding"] is not None


def test_failed_backfill_is_not_retried_immediately(space, fresh_db):
    _add(fresh_db, "rent agreement")
    space.down = True
    embed.search("rent")
    _join_backfill()
    calls = len(space.calls)
    embed.search("rent")
    _join_backfill()
    assert len(space.calls) == calls and embed._retry_at > 0


def test_wrong_sized_vectors_are_re_embedded(space, fresh_db):
    chunk = _add(fresh_db, "old model vector", blob=np.ones(10, np.float32).tobytes())
    assert embed.backfill() == 1
    row = fresh_db.fetch_one("SELECT embedding FROM chunks WHERE chunk_id = ?", (chunk,))
    assert len(row["embedding"]) == DIM * 4


def test_warm_backfills_then_builds(space, fresh_db):
    _add(fresh_db, "rent agreement")
    embed.warm()
    assert embed._index is not None and embed._index.has_vector.all()


# --- API lifespan ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("enabled", [False, True])
def test_lifespan_watcher_and_warm_up_follow_kavach_watch(fresh_db, tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(config, "VAULT_DIR", tmp_path / "vault")
    monkeypatch.setattr(config, "KAVACH_WATCH", enabled)
    started, warmed = [], threading.Event()
    monkeypatch.setattr(watcher, "start", lambda vault: started.append(vault) or SimpleNamespace(
        stop=lambda: None, join=lambda timeout=None: None))
    monkeypatch.setattr(embed, "warm", warmed.set)
    with TestClient(api.app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8000"):
        assert warmed.wait(2) if enabled else not warmed.is_set()
    assert started == ([config.VAULT_DIR] if enabled else [])


# --- real model ----------------------------------------------------------------------------------------------

VAULT = {
    "rent": "The monthly rent for Flat 4B is Rs. 15,000, payable on or before the 5th of every month by bank transfer.",
    "salary": "Salary credit from Acme Technologies Pvt Ltd: Rs. 62,000.00 credited to account ending 4321 on 01 Apr 2026.",
    "marks": "Class XII board examination results: Physics 88, Chemistry 91, Mathematics 95. Aggregate 91.3%. Result: PASS.",
    "landlord": "Mr. Ramesh Kumar owns the apartment. Phone +91 98450 12345, email ramesh.kumar@example.com. "
                "Call him for repairs and maintenance.",
    "decision": "Decided on 12 Aug 2026: we move out of the Indiranagar flat in December and will not renew the "
                "lease unless the new terms are reasonable.",
    "gym": "Gym routine: 30 minutes of cardio, then squats and deadlifts three times a week. Drink more water.",
}


@pytest.mark.llm
@pytest.mark.parametrize("question, expected", [
    ("How much rent do I pay each month?", "rent"),
    ("How much do I earn?", "salary"),  # no keyword overlap with the salary chunk
    ("What were my board exam marks?", "marks"),
    ("How do I contact the property owner?", "landlord"),  # paraphrase: no shared keyword
])
def test_real_embeddings_rank_the_right_chunk_first(fresh_db, monkeypatch, question, expected):
    monkeypatch.setattr(embed, "_index", None)
    blobs = embed.embed_documents(list(VAULT.values()))
    assert all(b is not None for b in blobs)
    ids = {name: _add(fresh_db, text, blob=blob) for (name, text), blob in zip(VAULT.items(), blobs)}
    hits = embed.search(question)
    assert hits[0].chunk_id == ids[expected], [(h.text[:30], round(h.score, 3)) for h in hits]
