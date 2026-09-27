"""Canonical JSON, Ed25519 sign/verify, sha256 (CONTRACT §6). Keys and signatures are base64url, no padding.

Private keys are the raw 32-byte Ed25519 seed; public keys are the raw 32 bytes, base64url.
`sign`/`verify` cover `canonical(obj)` for JSON values and the bytes themselves for `bytes` (e.g. the poll
string `"{id}|{ts}"` and the PDF text hash, which are signed as their UTF-8 bytes).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def canonical(obj: Any) -> bytes:
    """The exact byte encoding every signature covers (CONTRACT §6)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    """Strict base64url decode; accepts missing padding, rejects anything else."""
    if not isinstance(text, str) or not text or any(c in text for c in "+/ \n\r\t"):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def new_private_key() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def public_key(priv: bytes) -> str:
    raw = Ed25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return b64e(raw)


def fingerprint(pub: str) -> str:
    """First 16 hex chars of sha256(raw public key). Raises ValueError for a key that isn't 32 raw bytes."""
    raw = b64d(pub)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return hashlib.sha256(raw).hexdigest()[:16]


def _message(obj: Any) -> bytes:
    return obj if isinstance(obj, bytes) else canonical(obj)


def sign(priv: bytes, obj: Any) -> str:
    return b64e(Ed25519PrivateKey.from_private_bytes(priv).sign(_message(obj)))


def verify(pub: str, obj: Any, sig: str) -> bool:
    """Never raises: malformed keys, signatures or objects are just `False`."""
    try:
        Ed25519PublicKey.from_public_bytes(b64d(pub)).verify(b64d(sig), _message(obj))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
