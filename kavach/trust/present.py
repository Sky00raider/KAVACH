"""Holder-bound presentations (§6.2) and owner attestations (§6.3)."""

from __future__ import annotations

from kavach.models import Claim, CredentialRef


def build_presentation(ref: CredentialRef, issuer_claim: str, nonce: str, aud: str) -> dict:
    """Stub: correctly shaped presentation with placeholder crypto."""
    return {
        "type": "kavach/presentation",
        "credential": {"iss": ref.iss, "credential_type": ref.credential_type, "copy": ref.copy_no,
                       "holder_pubkey": "stub", "iat": "2026-09-01T00:00:00Z", "exp": "2027-03-01T00:00:00Z",
                       "digests": ["stub"], "issuer_sig": "stub"},
        "disclosures": [{"salt": "stub", "claim": issuer_claim, "value": True}],
        "binding": {"nonce": nonce, "aud": aud, "iat": "2026-09-26T12:00:00Z", "sig": "stub"},
    }


def build_attestation(claim: Claim, answer: bool, requester_fp: str, nonce: str) -> dict:
    """Stub: correctly shaped attestation with placeholder crypto."""
    return {
        "type": "kavach/attestation", "claim": claim.model_dump(exclude_none=True), "answer": answer,
        "nonce": nonce, "aud": requester_fp, "iat": "2026-09-26T12:00:00Z", "exp": "2026-09-27T12:00:00Z",
        "owner_pairwise_pubkey": "stub", "sig": "stub",
    }
