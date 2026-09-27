"""PDF / notes / WhatsApp -> documents + chunks (+ entities and facts, BRAIN steps 6-7).

Paths are stored vault-relative (CONTRACT §8). A changed file keeps its doc_id, gets new chunks and has its
old facts and edges closed, never deleted. An unchanged file (same text_hash) is not re-chunked or re-embedded.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from pathlib import Path

from kavach import config, db, textnorm
from kavach.brain import embed
from kavach.db import new_id, utc_now
from kavach.models import DocSource, IngestResult, SignatureResult
from kavach.trust import audit, issuer_check

log = logging.getLogger(__name__)

SOURCES: dict[str, DocSource] = {".pdf": "pdf", ".md": "note", ".txt": "chat"}

# [[Target]], [[Target|alias]], [[Target#heading]]; the target is what step 6 turns into MENTIONED_IN edges
_LINK = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")

# First match wins; checked against the lowercased file name + first page / opening text.
_DOC_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bank_statement", ("bank statement", "statement of account", "account statement", "bank_statement")),
    ("rent_agreement", ("rent agreement", "rental agreement", "lease agreement", "leave and licence",
                        "leave and license", "rent_agreement")),
    ("marksheet", ("marksheet", "mark sheet", "statement of marks", "grade card")),
    ("id_card", ("identity card", "id card", "id_card")),
)


# --- text helpers --------------------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """Notes and chats: NFKC, whitespace collapsed within each line, at most one blank line between paragraphs."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def note_links(text: str) -> list[str]:
    """`[[links]]` targets in order of first appearance, without aliases or headings."""
    seen: dict[str, None] = {}
    for m in _LINK.finditer(text):
        seen.setdefault(" ".join(m.group(1).split()), None)
    return [t for t in seen if t]


def _break_at(text: str, lo: int, hi: int) -> int:
    """End index for a chunk in text[lo:hi]: after a paragraph, line, sentence or word, preferred in that order."""
    window = text[lo:hi]
    for sep in ("\n\n", "\n", ". ", " "):
        i = window.rfind(sep)
        if i != -1:
            return lo + i + len(sep)
    return hi


def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """~`size`-char chunks that end on a boundary and overlap the previous chunk by ~`overlap` chars."""
    size = size or config.CHUNK_SIZE
    overlap = config.CHUNK_OVERLAP if overlap is None else overlap
    text = text.strip()
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = n if start + size >= n else _break_at(text, start + size // 2, start + size)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        nxt = max(end - overlap, start + 1)
        if not text[nxt - 1].isspace():  # begin the overlap on a word boundary
            ws = next((i for i in range(nxt, end) if text[i].isspace()), end - 1)
            nxt = ws + 1
        start = nxt
    return chunks


def _doc_type(source: DocSource, name: str, opening: str) -> str | None:
    if source == "note":
        return "note"
    if source == "chat":
        return "whatsapp"
    haystack = f"{name}\n{opening}".lower()
    return next((t for t, keys in _DOC_TYPES if any(k in haystack for k in keys)), None)


def vault_relative(path: Path) -> str:
    """Path relative to VAULT_DIR with forward slashes; ValueError outside the vault."""
    try:
        return Path(path).resolve().relative_to(Path(config.VAULT_DIR).resolve()).as_posix()
    except ValueError:
        raise ValueError(f"{path} is not under VAULT_DIR") from None


# --- reading -------------------------------------------------------------------------------------------


def _read(path: Path, source: DocSource) -> tuple[list[tuple[str, str]], str]:
    """([(locator, text)], text_hash). PermissionError (a Windows copy lock) propagates for the watcher to retry."""
    if source == "pdf":
        with open(path, "rb"):  # surface a copy lock as PermissionError before PyMuPDF sees the file
            pass
        pages = textnorm.pdf_pages(path)
        digest = textnorm.text_hash("\n".join(pages))  # == textnorm.pdf_text_hash(path), without a second parse
        return [(f"page {i}", textnorm.normalize_text(p)) for i, p in enumerate(pages, 1)], digest
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    label = "note" if source == "note" else "chat"  # WhatsApp windows + timestamp locators arrive in step 11
    return [(f"{label}: {path.name}", clean_text(raw))], textnorm.text_hash(raw)


# --- public (CONTRACT §7) ----------------------------------------------------------------------------


def ingest_file(path: Path) -> IngestResult:
    """Read, chunk, embed and store one vault file; audits `ingested` (and `document_signature_failed`)."""
    path = Path(path)
    source = SOURCES.get(path.suffix.lower())
    if source is None:
        raise ValueError(f"unsupported file type: {path.name}")
    rel = vault_relative(path)
    existing = db.get_document_by_path(rel)
    doc_id = existing["doc_id"] if existing else new_id("d")

    try:
        sections, digest = _read(path, source)
    except PermissionError:
        raise
    except (OSError, RuntimeError) as exc:  # half-written or corrupt PDF, file gone: the next event retries
        log.warning("could not read %s, left unchanged: %s", rel, exc)
        status = existing["signature_status"] if existing else "unsigned"
        return IngestResult(path=rel, doc_id=doc_id, source=source, signature_status=status)

    sig = issuer_check.verify_pdf(path) if source == "pdf" else SignatureResult(status="unsigned")

    if existing and existing["removed_at"] is None and existing["text_hash"] == digest:
        if (existing["signature_status"], existing["iss"]) != (sig.status, sig.iss):
            db.update("documents", "doc_id", doc_id, {"signature_status": sig.status, "iss": sig.iss})
        return IngestResult(path=rel, doc_id=doc_id, source=source, signature_status=sig.status)

    chunks = [{"chunk_id": new_id("c"), "locator": locator, "text": piece}
              for locator, text in sections for piece in chunk_text(text)]
    for chunk, blob in zip(chunks, embed.embed_documents([c["text"] for c in chunks])):
        chunk["embedding"] = blob

    opening = sections[0][1][: config.CHUNK_SIZE] if sections else ""
    db.store_document({"doc_id": doc_id, "path": rel, "source": source,
                       "doc_type": _doc_type(source, path.name, opening), "signature_status": sig.status,
                       "iss": sig.iss, "text_hash": digest, "ingested_at": utc_now(), "removed_at": None},
                      chunks, closed_on=date.today().isoformat())
    embed.invalidate()

    result = IngestResult(path=rel, doc_id=doc_id, source=source, signature_status=sig.status,
                          chunks_added=len(chunks))
    if sig.status == "invalid":
        audit.log("document_signature_failed", doc_id, {"path": rel, "doc_id": doc_id, "iss": sig.iss,
                                                        "reason": sig.detail})
    audit.log("ingested", doc_id, result.model_dump(include={"path", "doc_id", "signature_status", "chunks_added",
                                                             "entities_added", "facts_added"}))
    return result


def remove_file(path: Path) -> None:
    """Mark the document removed, drop its chunks, close its facts and edges, audit `document_removed`.
    Unknown or already-removed paths are ignored."""
    rel = vault_relative(Path(path))
    row = db.get_document_by_path(rel)
    if row is None or row["removed_at"] is not None:
        return None
    db.remove_document(row["doc_id"], utc_now(), closed_on=date.today().isoformat())
    embed.invalidate()
    audit.log("document_removed", row["doc_id"], {"path": rel, "doc_id": row["doc_id"]})
    return None
