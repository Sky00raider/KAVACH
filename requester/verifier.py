"""The requester's own checks (CONTRACT §6.4), against its own issuer trust list, never the owner's keys.

Trust list: $REQUESTER_TRUST_LIST, else requester/data/trusted_issuers.json (copied from the issuers'
`make_keys --export`), else the committed requester/trusted_issuers.json.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from kavach.models import AnswerType, Attestation, Presentation, VerifierCheck, VerifierOutput
from kavach.trust import crypto

_HERE = Path(__file__).resolve().parent
TRUST_LIST = _HERE / "trusted_issuers.json"

PRESENTATION_CHECKS = ("Issuer signature", "Disclosure digest", "Holder binding", "Nonce and audience", "Not expired")
ATTESTATION_CHECKS = ("Owner signature", "Nonce and audience", "Not expired")


def trust_list_path() -> Path:
    if "REQUESTER_TRUST_LIST" in os.environ:
        return Path(os.environ["REQUESTER_TRUST_LIST"])
    from requester import common  # common imports this module

    local = common.data_dir() / "trusted_issuers.json"
    return local if local.is_file() else TRUST_LIST


def trusted_issuers() -> dict[str, str]:
    """iss -> Ed25519 public key, from the requester's own file."""
    path = trust_list_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _digest(salt: str, claim: str, value: Any) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(f"{salt}|{claim}|{json.dumps(value)}".encode()).digest()
                                    ).rstrip(b"=").decode()


def _parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _failed(answer_type: AnswerType, claim: str, names: tuple[str, ...], detail: str) -> VerifierOutput:
    return VerifierOutput(answer_type=answer_type, claim=claim, result=None, all_ok=False,
                          checks=[VerifierCheck(name=n, ok=False, detail=detail) for n in names])


def _verify_presentation(payload: dict[str, Any], nonce: str, aud: str, now: datetime) -> VerifierOutput:
    claim = ((payload.get("disclosures") or [{}])[0] or {}).get("claim", "unsupported")
    try:
        Presentation.model_validate(payload)
        cred = dict(payload["credential"])
        disclosures = payload["disclosures"]
        binding = payload["binding"]
    except (ValidationError, KeyError, TypeError):
        return _failed("ISSUER_PROOF", str(claim), PRESENTATION_CHECKS, "malformed presentation")
    if len(disclosures) != 1:
        return _failed("ISSUER_PROOF", str(claim), PRESENTATION_CHECKS, "expected exactly one disclosed claim")
    disc = disclosures[0]
    checks = []

    issuer_pub = trusted_issuers().get(cred["iss"])
    body = {k: v for k, v in cred.items() if k != "issuer_sig"}
    ok = issuer_pub is not None and crypto.verify(issuer_pub, body, cred["issuer_sig"])
    checks.append(VerifierCheck(name="Issuer signature", ok=ok, detail=(
        f"signed by {cred['iss']}" if ok else f"{cred['iss']} is not in my trust list" if issuer_pub is None
        else f"{cred['iss']} signature does not verify")))

    ok = _digest(disc["salt"], disc["claim"], disc["value"]) in set(cred["digests"])
    checks.append(VerifierCheck(name="Disclosure digest", ok=ok, detail=(
        f"{disc['claim']} = {json.dumps(disc['value'])} matches a signed digest" if ok
        else "disclosed value does not match any signed digest")))

    signed = {"credential_digest_list_hash": crypto.b64e(crypto.sha256(crypto.canonical(cred["digests"]))),
              "disclosures": disclosures, "nonce": binding["nonce"], "aud": binding["aud"], "iat": binding["iat"]}
    ok = crypto.verify(cred["holder_pubkey"], signed, binding["sig"])
    checks.append(VerifierCheck(name="Holder binding", ok=ok, detail=(
        "presenter holds the credential's one-time key" if ok else "binding signature does not verify")))

    ok = binding["nonce"] == nonce and binding["aud"] == aud
    checks.append(VerifierCheck(name="Nonce and audience", ok=ok, detail=(
        "made for this request, to me" if ok else "replayed or addressed to someone else")))

    try:
        ok = _parse_time(cred["exp"]) > now
        detail = f"valid until {cred['exp']}" if ok else f"expired {cred['exp']}"
    except ValueError:
        ok, detail = False, "bad expiry"
    checks.append(VerifierCheck(name="Not expired", ok=ok, detail=detail))

    all_ok = all(c.ok for c in checks)
    return VerifierOutput(answer_type="ISSUER_PROOF", claim=disc["claim"], result=disc["value"] if all_ok else None,
                          checks=checks, all_ok=all_ok)


def _verify_attestation(payload: dict[str, Any], nonce: str, aud: str, pinned: str | None,
                        now: datetime) -> VerifierOutput:
    c = payload.get("claim") if isinstance(payload.get("claim"), dict) else {}
    claim = f"{c.get('claim')} {c.get('op')} {c.get('value')}" if c.get("claim") else "unsupported"
    try:
        Attestation.model_validate(payload)
    except ValidationError:
        return _failed("OWNER_ATTESTED", claim, ATTESTATION_CHECKS, "malformed attestation")
    checks = []
    body = {k: v for k, v in payload.items() if k != "sig"}
    key = payload["owner_pairwise_pubkey"]
    ok = pinned is not None and key == pinned and crypto.verify(key, body, payload["sig"])
    checks.append(VerifierCheck(name="Owner signature", ok=ok, detail=(
        "signed with the owner's key for me (owner-attested, not the issuer's word)" if ok
        else "no owner key received at pairing" if pinned is None
        else "not the owner key received at pairing" if key != pinned else "signature does not verify")))
    ok = payload["nonce"] == nonce and payload["aud"] == aud
    checks.append(VerifierCheck(name="Nonce and audience", ok=ok, detail=(
        "made for this request, to me" if ok else "replayed or addressed to someone else")))
    try:
        ok = _parse_time(payload["exp"]) > now
        detail = f"valid until {payload['exp']}" if ok else f"expired {payload['exp']}"
    except ValueError:
        ok, detail = False, "bad expiry"
    checks.append(VerifierCheck(name="Not expired", ok=ok, detail=detail))
    all_ok = all(c.ok for c in checks)
    return VerifierOutput(answer_type="OWNER_ATTESTED", claim=claim, result=payload["answer"] if all_ok else None,
                          checks=checks, all_ok=all_ok)


def verify(answer_type: AnswerType, payload: dict[str, Any] | None, nonce: str, aud: str,
           owner_pairwise_pubkey: str | None = None, now: datetime | None = None) -> VerifierOutput:
    """Checks a presentation or attestation for the request that carried `nonce` to `aud` (this requester's fp).

    `owner_pairwise_pubkey` is the key pinned when this requester was paired; attestations must match it.
    """
    now = now or datetime.now(timezone.utc)
    if answer_type == "ISSUER_PROOF":
        return _verify_presentation(payload or {}, nonce, aud, now)
    if answer_type == "OWNER_ATTESTED":
        return _verify_attestation(payload or {}, nonce, aud, owner_pairwise_pubkey, now)
    return VerifierOutput(answer_type=answer_type, claim="unsupported", result=None, checks=[], all_ok=False)
