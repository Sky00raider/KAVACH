"""PDF signature verification: issuer sig over sha256 of normalised text, stored in metadata `keywords`."""

from __future__ import annotations

from pathlib import Path

from kavach.models import SignatureResult


def verify_pdf(path: Path) -> SignatureResult:
    """Stub: every PDF is `unsigned` until TRUST step 2."""
    return SignatureResult(status="unsigned", iss=None, detail="issuer check not implemented")
