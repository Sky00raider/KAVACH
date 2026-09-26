"""Outsider question -> Claim (LLM maps wording; code validates against CONTRACT §5.1)."""

from __future__ import annotations

from kavach.models import Claim


def parse(question: str) -> Claim:
    """Stub: always the income >= 50,000 claim."""
    return Claim(claim="income", op="ge", value=50000, issuer_claim="income_ge_50000")
