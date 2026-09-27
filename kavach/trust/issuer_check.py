"""PDF signature verification (CONTRACT §6.5): issuer sig over `textnorm.pdf_text_hash`, in metadata `keywords`.

The owner side trusts the mock issuers' public keys from keys/issuers/trusted_issuers.json (make_keys).
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from kavach.models import SignatureResult
from kavach.textnorm import pdf_text_hash
from kavach.trust import crypto


def _trust() -> dict[str, str]:
    from kavach.mock_issuers.make_keys import trust_list

    return trust_list()


def verify_pdf(path: Path) -> SignatureResult:
    try:
        with pymupdf.open(path) as doc:
            keywords = (doc.metadata or {}).get("keywords") or ""
    except Exception as exc:  # noqa: BLE001 - unreadable PDF: nothing to verify
        return SignatureResult(status="unsigned", iss=None, detail=f"unreadable PDF ({type(exc).__name__})")
    try:
        meta = json.loads(keywords)
    except ValueError:
        meta = None
    if not isinstance(meta, dict) or "sig" not in meta:
        return SignatureResult(status="unsigned", iss=None, detail=None)
    iss = meta.get("iss") if isinstance(meta.get("iss"), str) else None
    pub = _trust().get(iss or "")
    if pub is None:
        return SignatureResult(status="invalid", iss=iss, detail="issuer not trusted")
    if not crypto.verify(pub, pdf_text_hash(path).encode(), str(meta["sig"])):
        return SignatureResult(status="invalid", iss=iss, detail="signature does not match document text")
    return SignatureResult(status="issuer_signed", iss=iss, detail=None)
