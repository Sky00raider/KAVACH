"""Claim -> Proposal by the pure-code rules in CONTRACT §5.4. No LLM here, ever."""

from __future__ import annotations

from kavach.models import DISCLOSABLE_FIELDS, Claim, Proposal


def decide(claim: Claim, requester_fp: str) -> Proposal:
    """Stub: non-disclosable -> REFUSED, anything else -> favourable ISSUER_PROOF proposal."""
    if claim.claim not in DISCLOSABLE_FIELDS:
        return Proposal(answer_type="REFUSED", claim=claim, reason="Claim is not disclosable")
    return Proposal(answer_type="ISSUER_PROOF", claim=claim, result=True, favourable=True,
                    actions=["approve", "deny"], reason="Unused mock_bank income_proof copy covers this claim")
