"""Claim -> Proposal by the pure-code rules in CONTRACT §5.4. No LLM here, ever."""

from __future__ import annotations

from kavach.models import DISCLOSABLE_FIELDS, Claim, ClaimInfo, ClaimsOut, IssuerClaim, Proposal

# CONTRACT §5.1: issuer-provable names and the favourable answer per disclosable claim.
_ISSUER_PROVABLE: dict[str, list[IssuerClaim]] = {
    "income": ["income_ge_25000", "income_ge_50000", "income_ge_75000", "income_ge_100000"],
    "loan_default_12m": ["loan_default_12m"],
    "age": ["age_over_18", "age_over_21"],
    "percentage": ["percentage_ge_60", "percentage_ge_75", "percentage_ge_90"],
    "result": ["result_pass"],
    "board": ["board"],
}
_FAVOURABLE = {"income": "YES", "loan_default_12m": "NO", "age": "YES", "percentage": "YES", "result": "YES",
               "board": None}


def claims() -> ClaimsOut:
    """GET /api/claims: disclosable claim names and issuer-provability. Never values (hard rule 5)."""
    return ClaimsOut(claims=[ClaimInfo(claim=c, issuer_provable=_ISSUER_PROVABLE[c], favourable=_FAVOURABLE[c])
                             for c in DISCLOSABLE_FIELDS])


def decide(claim: Claim, requester_fp: str) -> Proposal:
    """Stub: non-disclosable -> REFUSED, anything else -> favourable ISSUER_PROOF proposal."""
    if claim.claim not in DISCLOSABLE_FIELDS:
        return Proposal(answer_type="REFUSED", claim=claim, reason="Claim is not disclosable")
    return Proposal(answer_type="ISSUER_PROOF", claim=claim, result=True, favourable=True,
                    actions=["approve", "deny"], reason="Unused mock_bank income_proof copy covers this claim")
