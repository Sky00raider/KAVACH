"""Structured facts with exact quotes + grounding check (BRAIN step 7). Stub module."""

from __future__ import annotations

from kavach.models import Confidence


def grounding(text: str, quote: str | None, value: str) -> Confidence:
    """Stub: always `low` until the real check lands (quote in text and contains the value's digits)."""
    return "low"
