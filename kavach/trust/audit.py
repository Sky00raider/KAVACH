"""Hash-chained audit log (CONTRACT §8). detail never holds document text, chunk text or raw fact values."""

from __future__ import annotations

from kavach.models import ChainStatus


def log(event: str, ref_id: str | None, detail: dict) -> int:
    """Stub: returns a placeholder seq without writing. TRUST step 5 appends to audit_log."""
    return 0


def verify_chain() -> ChainStatus:
    """Stub: reports an intact, empty chain."""
    return ChainStatus(intact=True, broken_at=None, entries=0)
