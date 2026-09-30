from datetime import date
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

from kavach import config, textnorm
from kavach.brain import embed, ingest, llm
from kavach.models import SignatureResult
from kavach.trust import audit, issuer_check

WORDS = ("rent deposit landlord agreement salary credit renewal notice flat Indiranagar payment month "
         "tenant clause signed witness").split()


def _prose(n_words: int, seed: int = 0) -> str:
    return " ".join(WORDS[(i * 7 + seed) % len(WORDS)] + str(i) for i in range(n_words))


def _make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        words, lines, line = text.split(), [], ""
        for w in words:  # wrap so every word stays on the page
            if len(line) + len(w) > 80:
                lines.append(line)
                line = ""
            line = f"{line} {w}".strip()
        lines.append(line)
        doc.new_page().insert_text((40, 40), "\n".join(lines), fontsize=8)
    doc.save(path)
    doc.close()


@pytest.fixture
def vault(fresh_db, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    for sub in ("pdfs", "notes", "chats"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setattr(config, "VAULT_DIR", root)

    batches, embedded = [], []

    def fake_embed(texts):
        batches.append(len(texts))
        embedded.extend(texts)
        return np.tile(np.arange(config.EMBED_DIM, dtype=np.float32), (len(texts), 1))

    events = []
    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(audit, "log", lambda event, ref_id, detail: events.append((event, ref_id, detail)) or 1)
    return SimpleNamespace(root=root, batches=batches, events=events, embedded=embedded)


def _chunks(db, doc_id):
    return db.fetch_all("SELECT * FROM chunks WHERE doc_id = ? ORDER BY rowid", (doc_id,))


# --- text helpers --------------------------------------------------------------------------------------


def test_chunk_text_short_text_is_one_chunk():
    assert ingest.chunk_text("  just a line  ") == ["just a line"]
    assert ingest.chunk_text("") == []


def test_chunk_text_sizes_overlap_and_word_boundaries():
    text = _prose(400)
    chunks = ingest.chunk_text(text, size=600, overlap=100)
    assert len(chunks) > 3
    assert all(len(c) <= 600 for c in chunks)
    words = set(text.split())
    for a, b in zip(chunks, chunks[1:]):
        assert b.split()[0] in words and a.split()[-1] in words  # never cut mid-word
        assert b.split()[0] in a.split()  # the next chunk starts inside the previous one's tail
        shared = a[a.index(" " + b.split()[0] + " ") + 1:]
        assert 50 <= len(shared) <= 150 and b.startswith(shared)
    assert set(" ".join(chunks).split()) == words


def test_chunk_text_prefers_paragraph_breaks():
    para = _prose(40)
    chunks = ingest.chunk_text(f"{para}\n\n{_prose(60, 3)}", size=len(para) + 100, overlap=20)
    assert chunks[0] == para


def test_chunk_text_handles_text_without_spaces():
    chunks = ingest.chunk_text("x" * 1500, size=600, overlap=100)
    assert all(len(c) <= 600 for c in chunks) and len("".join(chunks)) >= 1500


def test_clean_text_keeps_lines_and_collapses_whitespace():
    raw = "# Flat move\r\n\r\n\r\n\r\n- rent   ₹15k\t now\n１２ Sep  "
    assert ingest.clean_text(raw) == "# Flat move\n\n- rent ₹15k now\n12 Sep"


def test_note_links():
    text = "See [[Flat move 2026]] and [[rent renewal|renewal]], [[Flat move 2026#Budget]], [[ Ramesh  Kumar ]]."
    assert ingest.note_links(text) == ["Flat move 2026", "rent renewal", "Ramesh Kumar"]


# --- ingest_file -----------------------------------------------------------------------------------------


def test_pdf_ingest_stores_pages_chunks_and_embeddings(vault, fresh_db):
    pdf = vault.root / "pdfs" / "bank_statement_signed.pdf"
    _make_pdf(pdf, [_prose(250), "Salary Credit 62,000.00"])
    res = ingest.ingest_file(pdf)

    assert res.path == "pdfs/bank_statement_signed.pdf" and res.source == "pdf"
    assert res.signature_status == "unsigned" and res.chunks_added > 2
    doc = fresh_db.list_documents()[0]
    assert doc.doc_id == res.doc_id and doc.path == "pdfs/bank_statement_signed.pdf"
    assert doc.text_hash == textnorm.pdf_text_hash(pdf) and doc.doc_type == "bank_statement"

    rows = _chunks(fresh_db, res.doc_id)
    assert len(rows) == res.chunks_added
    assert {r["locator"] for r in rows} == {"page 1", "page 2"}
    assert rows[-1]["locator"] == "page 2" and rows[-1]["text"] == "Salary Credit 62,000.00"
    assert all(r["chunk_id"].startswith("c_") for r in rows)
    vec = np.frombuffer(rows[0]["embedding"], dtype=np.float32)
    assert vec.shape == (config.EMBED_DIM,) and vec[5] == 5.0


def test_ingested_audit_detail_is_exact(vault):
    note = vault.root / "notes" / "rent.md"
    note.write_text("# Rent\nRent is ₹15,000, due on the 5th. See [[Flat move 2026]].", encoding="utf-8")
    res = ingest.ingest_file(note)
    assert vault.events == [("ingested", res.doc_id, {
        "path": "notes/rent.md", "doc_id": res.doc_id, "signature_status": "unsigned",
        "chunks_added": 1, "entities_added": 2, "facts_added": 0})]  # note DOCUMENT + link placeholder


def test_ingest_extracts_grounded_facts(vault, fresh_db, monkeypatch):
    from kavach.brain import extract

    # ingest normalises amounts before the model sees the text, so "15,000" already reads as "15000" here
    monkeypatch.setattr(extract, "extract", lambda text: extract.Extraction(facts=[
        extract.XFact(field="rent_amount", value="15000", quote="Rent is 15000, due on the 5th.")]))
    note = vault.root / "notes" / "rent.md"
    note.write_text("# Rent\nRent is 15,000, due on the 5th.", encoding="utf-8")
    res = ingest.ingest_file(note)
    assert res.facts_added == 1
    fact = fresh_db.list_facts()[0]
    assert (fact.field, fact.value, fact.source_type) == ("rent_amount", "15000", "extracted")
    assert fact.doc_id == res.doc_id and fact.entity_id == "e_owner"


def test_ingest_marks_facts_issuer_doc_only_when_signed(vault, fresh_db, monkeypatch):
    from kavach.brain import extract

    monkeypatch.setattr(extract, "extract", lambda text: extract.Extraction(
        facts=[extract.XFact(field="monthly_income", value="62000", quote="Salary Credit 62000")]))
    monkeypatch.setattr(issuer_check, "verify_pdf",
                        lambda path: SignatureResult(status="issuer_signed", iss="mock_bank"))
    pdf = vault.root / "pdfs" / "bank_statement_signed.pdf"
    _make_pdf(pdf, ["Salary Credit 62,000.00"])
    ingest.ingest_file(pdf)
    assert fresh_db.list_facts()[0].source_type == "issuer_doc"


def test_ingest_never_extracts_facts_from_an_invalid_document(vault, fresh_db, monkeypatch):
    from kavach.brain import extract

    calls = []
    monkeypatch.setattr(extract, "extract", lambda text: calls.append(text) or extract.Extraction())
    monkeypatch.setattr(issuer_check, "verify_pdf",
                        lambda path: SignatureResult(status="invalid", iss="mock_bank", detail="tampered"))
    pdf = vault.root / "pdfs" / "bank_statement_TAMPERED.pdf"
    _make_pdf(pdf, ["Salary Credit 92,000.00"])
    res = ingest.ingest_file(pdf)
    assert res.facts_added == 0 and calls == []


def test_note_ingest(vault, fresh_db):
    note = vault.root / "notes" / "rent.md"
    note.write_text("﻿# Rent\n\nRent is ₹15,000.\n", encoding="utf-8")
    res = ingest.ingest_file(note)
    doc = fresh_db.list_documents()[0]
    assert (res.source, doc.doc_type, doc.path) == ("note", "note", "notes/rent.md")
    assert doc.text_hash == textnorm.text_hash("# Rent\n\nRent is ₹15,000.\n")
    [chunk] = _chunks(fresh_db, res.doc_id)
    assert chunk["locator"] == "note: rent.md" and chunk["text"] == "# Rent\n\nRent is ₹15,000."


def test_non_whatsapp_txt_ingests_as_plain_text(vault, fresh_db):
    chat = vault.root / "chats" / "todo.txt"
    chat.write_text("Call the landlord about the deposit.\nBuy boxes.", encoding="utf-8")
    res = ingest.ingest_file(chat)
    assert res.source == "chat" and fresh_db.list_documents()[0].doc_type == "whatsapp"
    assert _chunks(fresh_db, res.doc_id)[0]["locator"] == "chat: todo.txt"


# --- WhatsApp exports (BUILD_PLAN §4.1) -------------------------------------------------------------------

ANDROID = """18/09/26, 19:40 - Messages and calls are end-to-end encrypted. Tap to learn more.
18/09/26, 19:42 - Ananya Iyer: Hi Ravi, I wanted to ask about renewing the flat agreement.

18/09/26, 19:48 - Ravi Kumar: Yes, the current rent is 14500.
It includes maintenance.
18/09/26, 19:49 - Ravi Kumar: <Media omitted>
20/09/26, 18:21 - Ananya Iyer: I will probably renew if the rent stays below 15000.
"""


def test_parse_whatsapp_android_24h_multiline_and_system_lines():
    msgs = ingest.parse_whatsapp(ANDROID)
    assert [(m.ts.isoformat(), m.sender) for m in msgs] == [
        ("2026-09-18T19:42:00", "Ananya Iyer"), ("2026-09-18T19:48:00", "Ravi Kumar"),
        ("2026-09-20T18:21:00", "Ananya Iyer")]
    assert msgs[1].text == "Yes, the current rent is 14500.\nIt includes maintenance."


@pytest.mark.parametrize("line, iso", [
    ("18/09/26, 7:42 pm - Ravi Kumar: hi", "2026-09-18T19:42:00"),
    ("18/09/2026, 12:05 am - Ravi Kumar: hi", "2026-09-18T00:05:00"),
    ("18/09/26, 12:05 PM - Ravi Kumar: hi", "2026-09-18T12:05:00"),  # newer exports: narrow no-break space
    ("[18/09/26, 19:42:10] Ravi Kumar: hi", "2026-09-18T19:42:00"),  # iOS
    ("‎[18/09/26, 7:42:10 PM] Ravi Kumar: hi", "2026-09-18T19:42:00"),
])
def test_parse_whatsapp_time_formats(line, iso):
    [msg] = ingest.parse_whatsapp(line)
    assert msg.ts.isoformat() == iso and msg.sender == "Ravi Kumar" and msg.text == "hi"


def test_parse_whatsapp_month_first_file():
    msgs = ingest.parse_whatsapp("9/18/26, 19:42 - A: one\n9/19/26, 08:00 - B: two")
    assert [m.ts.date().isoformat() for m in msgs] == ["2026-09-18", "2026-09-19"]


def test_whatsapp_windows_split_on_silence_and_size():
    msgs = ingest.parse_whatsapp(ANDROID)
    windows = ingest.whatsapp_windows(msgs)
    assert [loc for loc, _ in windows] == ["chat: 2026-09-18 19:42", "chat: 2026-09-20 18:21"]
    assert windows[0][1].splitlines()[0] == ("[2026-09-18 19:42] Ananya Iyer: Hi Ravi, I wanted to ask about "
                                             "renewing the flat agreement.")
    many = ingest.parse_whatsapp("\n".join(f"18/09/26, 10:{i:02d} - Ravi Kumar: {'word ' * 20}{i}"
                                           for i in range(30)))
    windows = ingest.whatsapp_windows(many, size=600)
    assert len(windows) > 1 and all(len(text) <= 600 for _, text in windows)
    first_lines = windows[0][1].splitlines()
    assert windows[1][1].splitlines()[0] == first_lines[-1]  # the last message overlaps into the next window


def test_whatsapp_ingest_uses_windows_and_dated_locators(vault, fresh_db):
    chat = vault.root / "chats" / "landlord.txt"
    chat.write_text(ANDROID, encoding="utf-8")
    res = ingest.ingest_file(chat)
    assert [c["locator"] for c in _chunks(fresh_db, res.doc_id)] == ["chat: 2026-09-18 19:42",
                                                                     "chat: 2026-09-20 18:21"]


def test_unchanged_file_is_not_reingested(vault, fresh_db):
    note = vault.root / "notes" / "a.md"
    note.write_text("Rent is ₹15,000.", encoding="utf-8")
    first = ingest.ingest_file(note)
    ids = [r["chunk_id"] for r in _chunks(fresh_db, first.doc_id)]
    note.write_text("Rent  is ₹15,000.\n", encoding="utf-8")  # same normalised text
    again = ingest.ingest_file(note)
    assert again.doc_id == first.doc_id and again.chunks_added == 0
    assert [r["chunk_id"] for r in _chunks(fresh_db, first.doc_id)] == ids
    assert len(vault.events) == 1 and vault.batches == [1]


def test_changed_file_replaces_chunks_and_closes_old_knowledge(vault, fresh_db):
    note = vault.root / "notes" / "a.md"
    note.write_text("Rent is ₹15,000.", encoding="utf-8")
    first = ingest.ingest_file(note)
    [old_chunk] = _chunks(fresh_db, first.doc_id)
    fresh_db.insert("facts", {"fact_id": "f_1", "entity_id": "e_owner", "field": "rent_amount", "value": "15000",
                              "source_type": "extracted", "doc_id": first.doc_id, "quote": "Rent is ₹15,000.",
                              "valid_from": "2026-01-01", "confidence": "high", "created_at": "2026-09-01T00:00:00Z"})
    fresh_db.insert("edges", {"edge_id": "x_1", "src": "e_1", "rel": "LANDLORD_OF", "dst": "e_owner",
                              "valid_from": "2026-01-01", "source_chunk_id": old_chunk["chunk_id"]})

    note.write_text("Rent is ₹16,000 from January.", encoding="utf-8")
    second = ingest.ingest_file(note)
    assert second.doc_id == first.doc_id and second.chunks_added == 1
    [new_chunk] = _chunks(fresh_db, first.doc_id)
    assert new_chunk["chunk_id"] != old_chunk["chunk_id"] and "16,000" in new_chunk["text"]
    today = date.today().isoformat()
    assert fresh_db.fetch_one("SELECT valid_to FROM facts WHERE fact_id = 'f_1'")["valid_to"] == today
    assert fresh_db.fetch_one("SELECT valid_to FROM edges WHERE edge_id = 'x_1'")["valid_to"] == today
    assert len(fresh_db.list_documents()) == 1 and [e[0] for e in vault.events] == ["ingested", "ingested"]


def test_remove_then_readd(vault, fresh_db):
    note = vault.root / "notes" / "a.md"
    note.write_text("Rent is ₹15,000.", encoding="utf-8")
    res = ingest.ingest_file(note)
    fresh_db.insert("facts", {"fact_id": "f_1", "entity_id": "e_owner", "field": "rent_amount", "value": "15000",
                              "source_type": "extracted", "doc_id": res.doc_id, "valid_from": "2026-01-01",
                              "confidence": "high", "created_at": "2026-09-01T00:00:00Z"})
    note.unlink()
    ingest.remove_file(note)

    assert vault.events[-1] == ("document_removed", res.doc_id, {"path": "notes/a.md", "doc_id": res.doc_id})
    assert fresh_db.list_documents() == []
    assert fresh_db.list_documents(include_removed=True)[0].removed_at is not None
    assert _chunks(fresh_db, res.doc_id) == []
    assert fresh_db.fetch_one("SELECT * FROM facts WHERE fact_id = 'f_1'")["valid_to"] == date.today().isoformat()
    ingest.remove_file(note)  # idempotent
    ingest.remove_file(vault.root / "notes" / "never-seen.md")
    assert [e[0] for e in vault.events].count("document_removed") == 1

    note.write_text("Rent is ₹15,000.", encoding="utf-8")  # same text: still re-ingested, chunks were dropped
    back = ingest.ingest_file(note)
    assert back.doc_id == res.doc_id and back.chunks_added == 1
    assert fresh_db.list_documents()[0].removed_at is None


def test_chunks_are_embedded_with_the_document_prefix(vault, fresh_db):
    note = vault.root / "notes" / "a.md"
    note.write_text("Rent is ₹15,000.", encoding="utf-8")
    res = ingest.ingest_file(note)
    assert vault.embedded == ["search_document: Rent is ₹15,000."]
    assert _chunks(fresh_db, res.doc_id)[0]["text"] == "Rent is ₹15,000."  # stored without the prefix


def test_ollama_down_stores_chunks_without_vectors(vault, fresh_db, monkeypatch):
    def down(texts):
        raise llm.LLMError("connection refused")

    monkeypatch.setattr(llm, "embed", down)
    note = vault.root / "notes" / "a.md"
    note.write_text(_prose(200), encoding="utf-8")
    res = ingest.ingest_file(note)
    rows = _chunks(fresh_db, res.doc_id)
    assert res.chunks_added == len(rows) > 1 and all(r["embedding"] is None for r in rows)


def test_embedding_is_batched(vault, monkeypatch):
    monkeypatch.setattr(embed, "_EMBED_BATCH", 3)
    note = vault.root / "notes" / "a.md"
    note.write_text(_prose(400), encoding="utf-8")
    res = ingest.ingest_file(note)
    assert sum(vault.batches) == res.chunks_added and max(vault.batches) == 3


def test_invalid_signature_is_audited(vault, fresh_db, monkeypatch):
    monkeypatch.setattr(issuer_check, "verify_pdf",
                        lambda path: SignatureResult(status="invalid", iss="mock_bank", detail="signature mismatch"))
    pdf = vault.root / "pdfs" / "bank_statement_TAMPERED.pdf"
    _make_pdf(pdf, ["Salary Credit 92,000.00"])
    res = ingest.ingest_file(pdf)
    doc = fresh_db.list_documents()[0]
    assert (doc.signature_status, doc.iss) == ("invalid", "mock_bank")
    assert vault.events[0] == ("document_signature_failed", res.doc_id, {
        "path": "pdfs/bank_statement_TAMPERED.pdf", "doc_id": res.doc_id, "iss": "mock_bank",
        "reason": "signature mismatch"})
    assert vault.events[1][0] == "ingested" and vault.events[1][2]["signature_status"] == "invalid"


def test_signature_change_without_text_change_updates_the_row(vault, fresh_db, monkeypatch):
    pdf = vault.root / "pdfs" / "stmt.pdf"
    _make_pdf(pdf, ["Salary Credit 62,000.00"])
    ingest.ingest_file(pdf)
    monkeypatch.setattr(issuer_check, "verify_pdf", lambda path: SignatureResult(status="issuer_signed", iss="mock_bank"))
    res = ingest.ingest_file(pdf)
    assert res.chunks_added == 0 and res.signature_status == "issuer_signed"
    assert fresh_db.list_documents()[0].iss == "mock_bank"


def test_unreadable_pdf_is_left_unchanged(vault, fresh_db):
    pdf = vault.root / "pdfs" / "half.pdf"
    pdf.write_bytes(b"%PDF-1.7 truncated")
    res = ingest.ingest_file(pdf)
    assert res.chunks_added == 0 and res.path == "pdfs/half.pdf"
    assert fresh_db.list_documents() == [] and vault.events == []


def test_permission_error_propagates(vault, monkeypatch):
    note = vault.root / "notes" / "locked.md"
    note.write_text("x", encoding="utf-8")

    def locked(*a, **kw):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(type(note), "read_text", locked)
    with pytest.raises(PermissionError):
        ingest.ingest_file(note)


def test_rejects_unsupported_and_outside_paths(vault, tmp_path):
    with pytest.raises(ValueError):
        ingest.ingest_file(vault.root / "notes" / "x.docx")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        ingest.ingest_file(outside)


@pytest.mark.llm
def test_real_embeddings_are_stored(vault, fresh_db, monkeypatch):
    db_file = config.DB_PATH
    monkeypatch.undo()  # real llm.embed; keep the vault + db paths
    monkeypatch.setattr(config, "VAULT_DIR", vault.root)
    monkeypatch.setattr(config, "DB_PATH", db_file)
    monkeypatch.setattr(audit, "log", lambda *a: 1)
    note = vault.root / "notes" / "rent.md"
    note.write_text("# Rent\nMonthly rent is ₹15,000, payable by the 5th.\n" + _prose(150), encoding="utf-8")
    res = ingest.ingest_file(note)
    rows = _chunks(fresh_db, res.doc_id)
    assert rows and all(r["embedding"] is not None for r in rows)
    vec = np.frombuffer(rows[0]["embedding"], dtype=np.float32)
    assert vec.shape == (config.EMBED_DIM,) and np.linalg.norm(vec) > 0
