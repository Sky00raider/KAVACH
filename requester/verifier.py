"""The requester's own checks (CONTRACT §6.4), against requester/trusted_issuers.json, never the owner's keys.

Stub: reports every check as not done, so nothing is shown as verified until TRUST step 3 lands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kavach.models import AnswerType, VerifierCheck, VerifierOutput

TRUST_LIST = Path(__file__).resolve().parent / "trusted_issuers.json"

PRESENTATION_CHECKS = ("Issuer signature", "Disclosure digest", "Holder binding", "Nonce and audience", "Not expired")
ATTESTATION_CHECKS = ("Owner signature", "Nonce and audience", "Not expired")


def trusted_issuers() -> dict[str, str]:
    """iss -> Ed25519 public key, from the requester's own file."""
    return json.loads(TRUST_LIST.read_text(encoding="utf-8"))


def verify(answer_type: AnswerType, payload: dict[str, Any] | None, nonce: str, aud: str,
           owner_pairwise_pubkey: str | None = None) -> VerifierOutput:
    """Checks a presentation or attestation for the request that carried `nonce` to `aud`."""
    if answer_type == "ISSUER_PROOF":
        names = PRESENTATION_CHECKS
        claim = ((payload or {}).get("disclosures") or [{}])[0].get("claim", "unsupported")
    elif answer_type == "OWNER_ATTESTED":
        names = ATTESTATION_CHECKS
        c = (payload or {}).get("claim") or {}
        claim = f"{c.get('claim')} {c.get('op')} {c.get('value')}" if c.get("claim") else "unsupported"
    else:
        return VerifierOutput(answer_type=answer_type, claim="unsupported", result=None, checks=[], all_ok=False)
    checks = [VerifierCheck(name=n, ok=False, detail="not implemented (stub)") for n in names]
    return VerifierOutput(answer_type=answer_type, claim=claim, result=None, checks=checks, all_ok=False)
