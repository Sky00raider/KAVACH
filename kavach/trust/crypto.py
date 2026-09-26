"""Canonical JSON, Ed25519 sign/verify, sha256 (CONTRACT §6). Keys and signatures are base64url."""

from __future__ import annotations

import json
from typing import Any


def canonical(obj: Any) -> bytes:
    """The exact byte encoding every signature covers (CONTRACT §6)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(priv: bytes, obj: Any) -> str:
    """Stub: placeholder signature. TRUST step 1 signs canonical(obj) with Ed25519."""
    return "c3R1Yi1zaWduYXR1cmU"


def verify(pub: str, obj: Any, sig: str) -> bool:
    """Stub: fails closed until TRUST step 1 lands."""
    return False
