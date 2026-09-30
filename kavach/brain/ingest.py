"""PDF / notes / WhatsApp -> documents + chunks + entities, edges and facts.

A `.txt` in `chats/` that parses as a WhatsApp export (`parse_whatsapp`) is chunked by conversation window
(`whatsapp_windows`), each window's locator its first message's time; any other `.txt` is plain text.

Paths are stored vault-relative (CONTRACT §8). A changed file keeps its doc_id, gets new chunks and has its
old facts and edges closed, never deleted. An unchanged file (same text_hash) is not re-chunked or re-embedded.
Entities, edges (`entities.index_document`) and facts (`extract.facts_for_document`) are extracted after the
chunks are stored, except for documents whose signature check failed: those get none, and a document that turns
`invalid` without a text change has its edges and facts closed. A document whose status leaves `invalid` is
re-ingested so it gets them. A bank statement's rows also get code-parsed `PAID` edges (`entities.bank_edges`),
never a model guess (entities.py's docstring); `source_type` for extracted facts is `issuer_doc` when the
document verifies, else `extracted` (never called for a document whose signature check failed).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from kavach import config, db, textnorm
from kavach.brain import embed, entities, extract
from kavach.db import new_id, utc_now
from kavach.models import DocSource, IngestResult, SignatureResult
from kavach.trust import audit, issuer_check

log = logging.getLogger(__name__)

SOURCES: dict[str, DocSource] = {".pdf": "pdf", ".md": "note", ".txt": "chat"}

# [[Target]], [[Target|alias]], [[Target#heading]]; the target is what step 6 turns into MENTIONED_IN edges
_LINK = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
# Note layout the owner writes: "Project: [[X]]", and "Related: [[A]], [[B]]" or a "Related:" line / heading
# followed by a list of links
_PROJECT_LINE = re.compile(r"^\s*(?:[-*+]\s+)?project\s*:\s*(.*)$", re.IGNORECASE)
_RELATED_LINE = re.compile(r"^\s*(?:#+\s*related\s*:?|(?:[-*+]\s+)?related\s*:)\s*(.*)$", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

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


def note_structure(text: str) -> tuple[str | None, list[str]]:
    """(project, related) from a note's layout: the first link on a "Project: [[X]]" line, and every link on a
    "Related:" line or in the list right under it (the list ends at a blank line, a heading or any other line)."""
    project: str | None = None
    related: list[str] = []
    in_related = False
    for line in text.splitlines():
        m = _PROJECT_LINE.match(line)
        if m and project is None and note_links(m.group(1)):
            project = note_links(m.group(1))[0]
        r = _RELATED_LINE.match(line)
        if r:
            in_related = True
            related += note_links(r.group(1))
        elif in_related and (_LIST_ITEM.match(line) or line.lstrip().startswith("[[")) and note_links(line):
            related += note_links(line)
        else:
            in_related = False
    return project, list(dict.fromkeys(related))


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


# --- WhatsApp exports (BUILD_PLAN §4.1) ------------------------------------------------------------------

# Android "18/09/26, 19:42 - Name: text", iOS "[18/09/26, 19:42:10] Name: text"; 24 h or "7:42 pm"
_WA_HEADER = re.compile(r"^\[?(\d{1,2})[/.](\d{1,2})[/.](\d{2}|\d{4}),?\s+(\d{1,2}):(\d{2})(?::\d{2})?"
                        r"\s*([ap])?\.?\s?(m)?\.?\]?\s*(?:-\s+)?(.*)$", re.IGNORECASE)
_WA_SENDER = re.compile(r"^([^:\n]{1,60}?):\s(.*)$", re.DOTALL)
_WA_SKIP = re.compile(r"^(?:<media omitted>|this message was deleted|you deleted this message|null)$", re.IGNORECASE)
_BIDI = re.compile("[‎‏‪-‮]")
WA_WINDOW_GAP = timedelta(hours=6)  # a longer silence starts a new conversation window


@dataclass
class WaMessage:
    ts: datetime
    sender: str
    text: str

    def line(self) -> str:
        return f"[{self.ts:%Y-%m-%d %H:%M}] {self.sender}: {self.text}"


def parse_whatsapp(text: str) -> list[WaMessage]:
    """Messages from a WhatsApp chat export, oldest first. A line that does not start a message continues the
    previous one (multi-line messages); system lines (no "Name:"), media placeholders and deleted messages are
    left out. Dates are day-first (the Indian export) unless a second field above 12 shows the file is month-first."""
    raw: list[tuple[tuple[str, ...], list[str]]] = []  # (header groups, text lines)
    for line in _BIDI.sub("", unicodedata.normalize("NFKC", text)).replace("\r\n", "\n").split("\n"):
        m = _WA_HEADER.match(line.strip())
        if m:
            raw.append((m.groups()[:7], [m.group(8)]))
        elif raw:
            raw[-1][1].append(line.strip())
    month_first = any(int(g[1]) > 12 for g, _ in raw)
    out: list[WaMessage] = []
    for (d1, d2, year, hh, mm, ampm, _m), lines in raw:
        body = "\n".join(lines).strip()
        s = _WA_SENDER.match(body)
        if not s:
            continue  # "Messages and calls are end-to-end encrypted", "X added Y"
        msg = "\n".join(ln for ln in s.group(2).split("\n") if ln).strip()
        if not msg or _WA_SKIP.match(msg):
            continue
        day, month = (int(d2), int(d1)) if month_first else (int(d1), int(d2))
        hour = int(hh)
        if ampm:
            hour = hour % 12 + (12 if ampm.lower() == "p" else 0)
        try:
            ts = datetime(int(year) + (2000 if len(year) == 2 else 0), month, day, hour, int(mm))
        except ValueError:
            continue
        out.append(WaMessage(ts=ts, sender=" ".join(s.group(1).split()), text=msg))
    return out


def whatsapp_windows(messages: list[WaMessage], size: int | None = None) -> list[tuple[str, str]]:
    """[(locator, text)]: consecutive messages grouped into conversation windows, a new window after a silence
    of WA_WINDOW_GAP or when the next line would pass `size` chars (then the last message is repeated at the
    start of the next window, the chat equivalent of chunk overlap). Locator `chat: YYYY-MM-DD HH:MM` is the
    window's first message; each line is `[YYYY-MM-DD HH:MM] Name: text`, so a quote carries its own date."""
    size = size or config.CHUNK_SIZE
    windows: list[list[WaMessage]] = []
    for msg in messages:
        cur = windows[-1] if windows else None
        if cur is None or msg.ts - cur[-1].ts > WA_WINDOW_GAP:
            windows.append([msg])
        elif len("\n".join(m.line() for m in cur)) + 1 + len(msg.line()) > size:
            windows.append([cur[-1], msg] if len(cur) > 1 and len(cur[-1].line()) + len(msg.line()) < size else [msg])
        else:
            cur.append(msg)
    return [(f"chat: {w[0].ts:%Y-%m-%d %H:%M}", "\n".join(m.line() for m in w)) for w in windows]


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
    if source == "chat":
        messages = parse_whatsapp(raw)
        if len(messages) >= 2:  # a WhatsApp export; any other .txt stays plain text
            return whatsapp_windows(messages), textnorm.text_hash(raw)
    label = "note" if source == "note" else "chat"
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

    leaves_invalid = existing is not None and existing["signature_status"] == "invalid" and sig.status != "invalid"
    if existing and existing["removed_at"] is None and existing["text_hash"] == digest and not leaves_invalid:
        if (existing["signature_status"], existing["iss"]) != (sig.status, sig.iss):
            db.update("documents", "doc_id", doc_id, {"signature_status": sig.status, "iss": sig.iss})
            if sig.status == "invalid":
                db.close_document_knowledge(doc_id, closed_on=date.today().isoformat())
        return IngestResult(path=rel, doc_id=doc_id, source=source, signature_status=sig.status)

    chunks = [{"chunk_id": new_id("c"), "locator": locator, "text": piece}
              for locator, text in sections for piece in chunk_text(text)]
    for chunk, blob in zip(chunks, embed.embed_documents([c["text"] for c in chunks])):
        chunk["embedding"] = blob

    opening = sections[0][1][: config.CHUNK_SIZE] if sections else ""
    doc_type = _doc_type(source, path.name, opening)
    ingested_at = utc_now()
    db.store_document({"doc_id": doc_id, "path": rel, "source": source,
                       "doc_type": doc_type, "signature_status": sig.status,
                       "iss": sig.iss, "text_hash": digest, "ingested_at": ingested_at, "removed_at": None},
                      chunks, closed_on=date.today().isoformat())
    embed.invalidate()

    entities_added = facts_added = 0
    if sig.status != "invalid":  # a tampered document contributes nothing to the graph or the facts table
        links = [(target, c["chunk_id"]) for c in chunks for target in note_links(c["text"])] if source == "note" else []
        first_link: dict[str, tuple[str, str]] = {}
        for target, chunk_id in links:
            first_link.setdefault(entities.normalise_name(target), (target, chunk_id))
        project, related = note_structure("\n".join(t for _, t in sections)) if source == "note" else (None, [])
        entities_added = entities.index_document(rel, source, chunks, list(first_link.values()), doc_type=doc_type,
                                                 project=project, related=related)
        if doc_type == "bank_statement":
            bank_edges = entities.bank_edges(chunks)
            if bank_edges:
                db.insert_edges(bank_edges)
        source_type = "issuer_doc" if sig.status == "issuer_signed" else "extracted"
        facts_added = extract.facts_for_document(doc_id, source, source_type, chunks, ingested_at,
                                                    doc_type=doc_type)

    result = IngestResult(path=rel, doc_id=doc_id, source=source, signature_status=sig.status,
                          chunks_added=len(chunks), entities_added=entities_added, facts_added=facts_added)
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
