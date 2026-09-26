"""Global cumulative disclosure ledger: per numeric field, the [lo, hi) range implied by every answer given."""

from __future__ import annotations

from kavach.models import Claim, LedgerCheck


def check(claim: Claim, answer: bool) -> LedgerCheck:
    """Stub: always allowed."""
    return LedgerCheck(allowed=True, reason=None)


def record(claim: Claim, answer: bool) -> None:
    """Stub: records nothing."""
    return None
