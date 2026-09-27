"""Text normalisation shared by ingestion (BRAIN) and PDF signing/verification (TRUST), CONTRACT §6.5.

Both sides must hash exactly the same bytes, so this is the only place the rule lives.
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pymupdf


def normalize_text(text: str) -> str:
    """NFKC, then every whitespace run (str.isspace) -> one space, then strip."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def text_hash(text: str) -> str:
    """Lowercase hex sha256 of the UTF-8 bytes of normalize_text(text)."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def pdf_pages(path: Path) -> list[str]:
    """Raw `page.get_text()` for every page, in order."""
    with pymupdf.open(path) as doc:
        return [page.get_text() for page in doc]


def pdf_text_hash(path: Path) -> str:
    """text_hash of the pages joined with "\\n". Issuers sign this; issuer_check and ingest recompute it."""
    return text_hash("\n".join(pdf_pages(path)))
