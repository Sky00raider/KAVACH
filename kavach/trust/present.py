"""Holder-bound presentations (CONTRACT §6.2) and owner attestations (§6.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from kavach import db
from kavach.models import Claim, CredentialRef
from kavach.trust import crypto, wallet

ATTESTATION_VALIDITY = timedelta(days=7)


class PresentError(Exception):
    pass


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def digest_list_hash(digests: list[str]) -> str:
    """What the binding commits to about the credential: base64url sha256 of canonical(digests)."""
    return crypto.b64e(crypto.sha256(crypto.canonical(digests)))


def binding_payload(credential: dict[str, Any], disclosures: list[dict[str, Any]], nonce: str, aud: str,
                    iat: str) -> dict[str, Any]:
    """The object `binding.sig` signs (§6.2). The verifier rebuilds it the same way."""
    return {"credential_digest_list_hash": digest_list_hash(credential["digests"]), "disclosures": disclosures,
            "nonce": nonce, "aud": aud, "iat": iat}


def build_presentation(ref: CredentialRef, issuer_claim: str, nonce: str, aud: str) -> dict:
    """Disclose one claim from one unused copy, bound to this request's nonce and audience. Uses the copy up."""
    copy = wallet.load(ref.cred_id)
    disclosures = [d for d in copy["disclosures"] if d["claim"] == issuer_claim]
    if not disclosures:
        raise PresentError(f"copy {ref.cred_id} has no {issuer_claim}")
    if not wallet.claim_copy(ref.cred_id):
        raise PresentError(f"copy {ref.cred_id} was already used")
    iat = _iso(datetime.now(timezone.utc))
    binding = binding_payload(copy["credential"], disclosures, nonce, aud, iat)
    return {"type": "kavach/presentation", "credential": copy["credential"], "disclosures": disclosures,
            "binding": {"nonce": nonce, "aud": aud, "iat": iat, "sig": crypto.sign(copy["holder_privkey"], binding)}}


def pairwise_key(requester_fp: str) -> bytes:
    row = db.fetch_one("SELECT status, owner_pairwise_privkey FROM requesters WHERE fingerprint = ?", (requester_fp,))
    if row is None or row["status"] != "paired" or not row["owner_pairwise_privkey"]:
        raise PresentError("requester is not paired")
    return row["owner_pairwise_privkey"]


def build_attestation(claim: Claim, answer: bool, requester_fp: str, nonce: str) -> dict:
    """The owner's signed word, with the pairwise key created for this requester at pairing."""
    priv = pairwise_key(requester_fp)
    now = datetime.now(timezone.utc)
    att = {"type": "kavach/attestation", "claim": claim.model_dump(exclude_none=True, exclude={"issuer_claim"}),
           "answer": answer, "nonce": nonce, "aud": requester_fp, "iat": _iso(now),
           "exp": _iso(now + ATTESTATION_VALIDITY), "owner_pairwise_pubkey": crypto.public_key(priv)}
    att["sig"] = crypto.sign(priv, att)
    return att
