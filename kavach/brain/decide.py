"""Claim -> Proposal by the pure-code rules in CONTRACT §5.4. No LLM here, ever.

In order:
1. unsupported, not disclosable or not well-formed                   -> REFUSED
2. issuer_claim set and the wallet has an unused copy                 -> ISSUER_PROOF, result = the signed value
3. a current, high-confidence `issuer_doc` fact on the owner whose document is still `issuer_signed`, and a
   yes/no claim                                                       -> ledger.check(); blocked -> REFUSED,
                                                                         else OWNER_ATTESTED, result = comparison
4. anything else                                                      -> CANNOT_CONFIRM
`board is` (which board?) discloses a value, so only a credential can answer it; `board eq X` is yes/no.
Favourable results offer approve/deny, unfavourable ones answer/decline. `board` has no favourable answer
(`favourable` null): the value or a match offers approve/deny, a mismatch answer/decline.
`reason` is owner-facing and never contains a fact value. `requester_fp` is unused on purpose: the ledger
assumes every requester colludes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from kavach import db
from kavach.brain import amounts
from kavach.models import (
    DISCLOSABLE_FIELDS,
    OWNER_ENTITY_ID,
    Claim,
    ClaimInfo,
    ClaimsOut,
    Fact,
    IssuerClaim,
    Proposal,
)
from kavach.trust import ledger, wallet

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
_NUMERIC = ("income", "percentage", "age")
_TRUE_WORDS = {"true", "yes", "y", "1"}
_FALSE_WORDS = {"false", "no", "n", "0", "none", "nil"}


def claims() -> ClaimsOut:
    """GET /api/claims: disclosable claim names and issuer-provability. Never values (hard rule 5)."""
    return ClaimsOut(claims=[ClaimInfo(claim=c, issuer_provable=_ISSUER_PROVABLE[c], favourable=_FAVOURABLE[c])
                             for c in DISCLOSABLE_FIELDS])


def issuer_claim_for(claim: Claim) -> IssuerClaim | None:
    """The §5.1 issuer claim that answers `claim` exactly, else None (a non-cutoff threshold, `board eq`)."""
    name = claim.claim
    if name in ("income", "percentage") and claim.op == "ge":
        wanted = f"{name}_ge_{claim.value}"
    elif name == "age" and claim.op == "ge":
        wanted = f"age_over_{claim.value}"
    elif name == "loan_default_12m" and claim.op == "is":
        wanted = "loan_default_12m"
    elif name == "result" and claim.op == "is":
        wanted = "result_pass"
    elif name == "board" and claim.op == "is":
        wanted = "board"
    else:
        return None
    return next((c for c in _ISSUER_PROVABLE[name] if c == wanted), None)


def _well_formed(claim: Claim) -> bool:
    name, op, value = claim.claim, claim.op, claim.value
    if name in _NUMERIC:
        return op == "ge" and isinstance(value, int) and not isinstance(value, bool) and value > 0
    if name == "loan_default_12m":
        return op == "is" and value is True
    if name == "result":
        return op == "is" and value == "pass"
    if name == "board":
        return (op == "is" and value is None) or (op == "eq" and isinstance(value, str) and bool(value.strip()))
    return False


def _describe(claim: Claim) -> str:
    """The claim in owner-facing words: the requester's threshold only, never a fact value."""
    name, value = claim.claim, claim.value
    if name == "income":
        return f"monthly income of at least ₹{value:,}"
    if name == "percentage":
        return f"a percentage of at least {value}"
    if name == "age":
        return f"age {value} or over"
    if name == "loan_default_12m":
        return "loan defaults in the last 12 months"
    if name == "result":
        return "the exam result"
    return "the exam board" if claim.op == "is" else f"the exam board being {value}"


def _today() -> date:
    return date.today()


def _grounded_fact(field: str) -> tuple[Fact, str] | None:
    """The newest current, high-confidence issuer_doc fact on the owner whose document still verifies."""
    facts = [f for f in db.list_facts(field=field, current=True)
             if f.entity_id == OWNER_ENTITY_ID and f.source_type == "issuer_doc" and f.confidence == "high"
             and f.doc_id]
    docs = db.document_status(f.doc_id for f in facts)
    signed = [f for f in facts if docs.get(f.doc_id, {}).get("signature_status") == "issuer_signed"]
    if not signed:
        return None
    fact = max(signed, key=lambda f: f.valid_from or "")  # list_facts is newest first; max keeps the first on ties
    return fact, docs[fact.doc_id]["path"]


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip().replace(",", ""))
    except InvalidOperation:
        amount = amounts.parse_amount(value)
        return Decimal(amount) if amount is not None else None


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _compare(claim: Claim, fact_value: str) -> bool | None:
    """The yes/no answer to `claim` from a fact value, in Python; None when the value can't be read."""
    name, value = claim.claim, claim.value
    if name in ("income", "percentage"):
        number = _number(fact_value)
        return None if number is None else number >= value
    if name == "age":
        try:
            return _age(date.fromisoformat(fact_value.strip()), _today()) >= value
        except ValueError:
            return None
    if name == "loan_default_12m":
        word = fact_value.strip().lower()
        return True if word in _TRUE_WORDS else False if word in _FALSE_WORDS else None
    if name == "result":
        word = fact_value.strip().lower()
        return True if word.startswith("pass") else False if word.startswith("fail") else None
    if name == "board" and claim.op == "eq":
        return " ".join(fact_value.split()).casefold() == " ".join(value.split()).casefold()
    return None


def _proposal(answer_type: str, claim: Claim, result: bool | str, reason: str) -> Proposal:
    favourable_word = _FAVOURABLE[claim.claim]
    if favourable_word is None:
        favourable = None
        agreeable = result is not False
    else:
        favourable = result is (favourable_word == "YES")
        agreeable = favourable
    return Proposal(answer_type=answer_type, claim=claim, result=result, favourable=favourable,
                    actions=["approve", "deny"] if agreeable else ["answer", "decline"], reason=reason)


def decide(claim: Claim, requester_fp: str) -> Proposal:
    """CONTRACT §5.4 (see module docstring). The returned claim carries the issuer_claim computed here."""
    if claim.claim not in DISCLOSABLE_FIELDS:
        return Proposal(answer_type="REFUSED", claim=claim, reason="The question does not map to a disclosable claim")
    if not _well_formed(claim):
        return Proposal(answer_type="REFUSED", claim=claim, reason=f"Malformed {claim.claim} claim")
    claim = claim.model_copy(update={"issuer_claim": issuer_claim_for(claim)})
    ic = claim.issuer_claim

    ref = wallet.find_copy(ic) if ic else None
    if ref is not None:
        try:
            signed = wallet.disclosed_value(ref, ic)
        except KeyError:
            signed = None
        if isinstance(signed, (bool, str)):
            return _proposal("ISSUER_PROOF", claim, signed,
                             f"Unused {ref.iss} {ref.credential_type} copy covers {ic}")

    grounded = _grounded_fact(DISCLOSABLE_FIELDS[claim.claim])
    if claim.claim == "board" and claim.op == "is":
        return Proposal(answer_type="CANNOT_CONFIRM", claim=claim,
                        reason="Only an issuer credential can disclose the board, and no unused copy is left"
                        if grounded else "No issuer credential covers the exam board")
    result = _compare(claim, grounded[0].value) if grounded else None
    if result is None:
        return Proposal(answer_type="CANNOT_CONFIRM", claim=claim,
                        reason=f"No issuer-signed document or credential covers {_describe(claim)}")
    check = ledger.check(claim, result)
    if not check.allowed:
        return Proposal(answer_type="REFUSED", claim=claim, reason=check.reason or "Blocked by the disclosure ledger")
    return _proposal("OWNER_ATTESTED", claim, result,
                     f"Checked in code against the issuer-signed {grounded[1]}; you attest the answer")
