"""PDF / notes / WhatsApp -> documents + chunks (+ entities and facts)."""

from __future__ import annotations

from pathlib import Path

from kavach.db import new_id
from kavach.models import DocSource, IngestResult

_SOURCES: dict[str, DocSource] = {".pdf": "pdf", ".md": "note", ".txt": "chat"}


def ingest_file(path: Path) -> IngestResult:
    """Stub: reports a document without reading it."""
    return IngestResult(path=str(path), doc_id=new_id("d"), source=_SOURCES.get(path.suffix.lower(), "note"),
                        signature_status="unsigned", chunks_added=0, entities_added=0, facts_added=0)


def remove_file(path: Path) -> None:
    """Stub: will supersede the file's facts and drop its chunks."""
    return None
