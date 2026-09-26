"""Outsider question -> Claim (LLM maps wording; code validates against CONTRACT §5.1)."""

from __future__ import annotations

from kavach.models import DISCLOSABLE_FIELDS, Claim


def parse(question: str) -> Claim:
    """Stub: always the income >= 50,000 claim. Anything outside DISCLOSABLE_FIELDS becomes `unsupported`."""
    claim = Claim(claim="income", op="ge", value=50000, issuer_claim="income_ge_50000")
    return claim if claim.claim in DISCLOSABLE_FIELDS else Claim(claim="unsupported")
