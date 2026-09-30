"""brain/budget.py: which chunks get model extraction, and long WhatsApp chats end to end (BRAIN item 17)."""

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from kavach import config, db
from kavach.brain import budget, entities, extract, ingest, llm
from kavach.models import OWNER_ENTITY_ID


def _chunks(texts):
    return [{"chunk_id": f"c{i}", "text": t} for i, t in enumerate(texts)]


def test_documents_keep_their_first_chunks():
    chunks = _chunks([f"page {i} rent 14500" for i in range(20)])
    for source in ("pdf", "note"):
        assert [c["chunk_id"] for c in budget.model_chunks(chunks, source)] == [f"c{i}" for i in range(8)]


def test_a_chat_that_fits_is_sent_whole():
    chunks = _chunks(["[2026-08-01 10:00] Ravi: ok"] * budget.MAX_CHAT_WINDOWS)
    assert budget.model_chunks(chunks, "chat") == chunks


def test_long_chat_prefers_cued_windows_newest_first_in_order():
    n = 30
    texts = [f"[2026-08-{1 + i % 28:02d} 10:00] Ravi: ok see you" for i in range(n)]
    for i in (2, 25, 29):
        texts[i] = f"[2026-08-{1 + i % 28:02d} 10:00] Ravi: the rent goes up from January"
    picked = [int(c["chunk_id"][1:]) for c in budget.model_chunks(_chunks(texts), "chat")]
    assert len(picked) == budget.MAX_CHAT_WINDOWS and picked == sorted(picked)
    assert {2, 25, 29} <= set(picked)                                   # every cued window, even the oldest
    assert picked[3:] == list(range(n - 13, n))                          # then the newest uncued ones


@pytest.mark.parametrize("text, cue", [
    ("[2026-09-18 19:42] Ravi Kumar: ok see you", False),               # the stamp's digits do not count
    ("[2026-09-18 19:42] Ravi Kumar: rent is due", True),
    ("[2026-09-18 19:42] Ravi Kumar: I'll pay 5000 tomorrow", True),
    ("[2026-09-18 19:42] Ananya: we decided to renew", True),
    ("[2026-09-18 19:42] Ananya: thanks!!", False),
])
def test_cue(text, cue):
    assert budget.has_cue(text) is cue


# --- a long WhatsApp export, end to end ------------------------------------------------------------------------


@pytest.fixture
def vault(fresh_db, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    for sub in ("pdfs", "notes", "chats"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setattr(config, "VAULT_DIR", root)
    monkeypatch.setattr(llm, "embed", lambda texts: np.ones((len(texts), config.EMBED_DIM), dtype=np.float32))
    return root


def _export(messages):
    """Android export lines, each message 7 h after the last so every one is its own conversation window."""
    start = datetime(2026, 5, 1, 10, 0)
    return "\n".join(f"{(start + timedelta(hours=7 * i)):%d/%m/%y, %H:%M} - {who}: {text}"
                     for i, (who, text) in enumerate(messages))


def test_late_windows_of_a_long_chat_get_facts_and_entities(vault, monkeypatch):
    messages = [("Ravi Kumar", "ok see you")] * 40
    messages[1] = ("Ravi Kumar", "The rent is 14500 per month")
    messages[38] = ("Ravi Kumar", "Rent will be 16000 from January")
    (vault / "chats" / "landlord.txt").write_text(_export(messages), encoding="utf-8")

    seen = []

    def fake_facts(text):
        seen.append(text)
        facts = []
        for quote, value in (("The rent is 14500 per month", "14500"), ("Rent will be 16000 from January", "16000")):
            if quote in text:
                facts.append(extract.XFact(field="rent_amount", value=value, quote=quote))
        return extract.Extraction(facts=facts)

    entity_texts = []
    monkeypatch.setattr(extract, "extract", fake_facts)
    monkeypatch.setattr(entities, "extract", lambda text: entity_texts.append(text) or entities.Extraction())

    res = ingest.ingest_file(vault / "chats" / "landlord.txt")
    assert res.chunks_added == 40
    assert len(seen) == len(entity_texts) == budget.MAX_CHAT_WINDOWS
    assert any("16000 from January" in t for t in entity_texts)        # window 39 of 40: past the old cap of 8

    rows = db.fetch_all("SELECT value, valid_from, valid_to, superseded_by FROM facts WHERE field = 'rent_amount' "
                        "ORDER BY valid_from")
    assert [(r["value"], r["valid_from"]) for r in rows] == [("14500", "2026-05-01"), ("16000", "2027-01-01")]
    today = date(2026, 9, 30).isoformat()
    assert db.current_fact(OWNER_ENTITY_ID, "rent_amount", today)["value"] == "14500"
    assert [f["value"] for f in db.scheduled_facts(OWNER_ENTITY_ID, today)] == ["16000"]
