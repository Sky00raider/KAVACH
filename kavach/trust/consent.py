"""Request pipeline and the owner approval queue (BUILD_PLAN §4.6, CONTRACT §9).

receive: signature, 120 s ts window, blocked check, nonce reuse -> pairing check -> parse_question.parse ->
decide.decide -> auto-resolve REFUSED / CANNOT_CONFIRM, else wait for the owner.
decide_request (owner): ledger re-check -> present.build_* -> ledger.record -> copy used -> audit.
Nothing here returns document text, chunks or raw fact values to a requester (hard rule 5).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from kavach import config, db
from kavach.brain import decide, parse_question
from kavach.db import new_id, utc_now
from kavach.models import AskAck, AskIn, AskResult, Claim, Proposal, RejectReason, RequestView
from kavach.trust import audit, crypto, ledger, pairing, present, wallet

TS_WINDOW_S = 120
MAX_NONCE = 128
MAX_QUESTION = 2000
_decide_lock = threading.Lock()


class RequestRejected(Exception):
    """Raised by receive() and poll() after the rejection is audited; api.py maps `reason` to an HTTP status."""

    def __init__(self, reason: RejectReason):
        super().__init__(reason)
        self.reason = reason


class RequestNotFound(KeyError):
    pass


class RequestConflict(Exception):
    """The owner's action does not apply (already decided, action not offered, no credential copy left)."""


def _reject(reason: RejectReason, fp: str | None, **extra: Any) -> RequestRejected:
    audit.log("request_rejected", fp, {"reason": reason, **extra})
    return RequestRejected(reason)


def _fresh(ts: int) -> bool:
    return abs(time.time() - ts) <= TS_WINDOW_S


def _row(request_id: str) -> dict[str, Any] | None:
    return db.fetch_one("SELECT * FROM requests WHERE request_id = ?", (request_id,))


def _view(request_id: str) -> RequestView:
    for v in db.list_requests():
        if v.request_id == request_id:
            return v
    raise RequestNotFound(request_id)


def _finish(request_id: str, answer_type: str, payload: dict | None = None) -> None:
    db.update("requests", "request_id", request_id, {
        "status": "done", "answer_type": answer_type, "payload_json": json.dumps(payload) if payload else None,
        "decided_at": utc_now()})


# --- intake ------------------------------------------------------------------------------------------------


def receive(req: AskIn, channel: str) -> AskAck:
    if not 1 <= len(req.nonce) <= MAX_NONCE or len(req.question) > MAX_QUESTION or len(req.sig) > 200:
        raise _reject("malformed", None, channel=channel, error_type="field_length")
    try:
        fp = crypto.fingerprint(req.requester_pubkey)
    except ValueError:
        raise _reject("bad_sig", None, channel=channel) from None
    if not crypto.verify(req.requester_pubkey, req.model_dump(exclude={"sig"}), req.sig):
        raise _reject("bad_sig", fp, channel=channel)
    if not _fresh(req.ts):
        raise _reject("stale_ts", fp, channel=channel)
    requester = pairing.get(fp)
    if requester is not None and requester["status"] == "blocked":
        raise _reject("unknown_requester_blocked", fp, channel=channel)
    if requester is None:
        pairing.register_pending(req.requester_pubkey, req.requester_name, req.requester_type)
        requester = pairing.get(fp)

    request_id = new_id("rq")
    try:
        db.insert("requests", {"request_id": request_id, "requester_fp": fp, "channel": channel,
                               "question": req.question[:500], "nonce": req.nonce, "status": "pending_pairing",
                               "created_at": utc_now()})
    except sqlite3.IntegrityError:
        raise _reject("nonce_reuse", fp, channel=channel) from None
    audit.log("request_received", request_id, {"requester_fp": fp, "channel": channel,
                                                "question": req.question[:500]})
    if requester["status"] == "paired":
        _process(request_id)
    return AskAck(request_id=request_id, status=_row(request_id)["status"])


def _process(request_id: str) -> None:
    """Parse and decide one request whose requester is paired; auto outcomes finish it, the rest wait."""
    row = _row(request_id)
    try:
        claim = parse_question.parse(row["question"])
    except Exception as exc:  # noqa: BLE001 - an unparseable question is refused, never guessed
        claim = Claim(claim="unsupported")
        parse_error: str | None = type(exc).__name__
    else:
        parse_error = None
    try:
        proposal = decide.decide(claim, row["requester_fp"])
    except Exception as exc:  # noqa: BLE001
        proposal = Proposal(answer_type="REFUSED", claim=claim, reason=f"Could not evaluate ({type(exc).__name__})")
    db.update("requests", "request_id", request_id, {"claim_json": claim.model_dump_json(),
                                                     "proposal_json": proposal.model_dump_json(),
                                                     "status": "pending"})
    if proposal.answer_type in ("REFUSED", "CANNOT_CONFIRM"):
        _finish(request_id, proposal.answer_type)
        detail = {"claim": claim.claim, "reason": proposal.reason}
        if parse_error:
            detail["parse_error"] = parse_error
        audit.log("request_auto_refused" if proposal.answer_type == "REFUSED" else "request_cannot_confirm",
                  request_id, detail)


def release_waiting(fp: str) -> None:
    """After a pairing decision: run (paired) or refuse (blocked) this requester's waiting requests."""
    status = (pairing.get(fp) or {}).get("status")
    for r in db.fetch_all("SELECT request_id FROM requests WHERE requester_fp = ? AND status = 'pending_pairing' "
                          "ORDER BY created_at", (fp,)):
        if status == "paired":
            _process(r["request_id"])
        elif status == "blocked":
            _finish(r["request_id"], "REFUSED")
            audit.log("request_auto_refused", r["request_id"], {"claim": None, "reason": "requester blocked"})


# --- requester poll ----------------------------------------------------------------------------------------


def poll(request_id: str, requester_fp: str, ts: int, sig: str) -> AskResult:
    """Signed poll: X-Sig over "{id}|{ts}" by the requester's key, 120 s window, and only the asker may read."""
    requester = pairing.get(requester_fp)
    if requester is None:
        raise _reject("unknown_request", requester_fp, request_id=request_id[:40])
    if not crypto.verify(requester["pubkey"], f"{request_id}|{ts}".encode(), sig):
        raise _reject("bad_sig", requester_fp, request_id=request_id[:40])
    if not _fresh(ts):
        raise _reject("stale_ts", requester_fp, request_id=request_id[:40])
    row = _row(request_id)
    if row is None:
        raise _reject("unknown_request", requester_fp, request_id=request_id[:40])
    if row["requester_fp"] != requester_fp:
        raise _reject("wrong_requester", requester_fp, request_id=request_id[:40])
    done = row["status"] == "done"
    return AskResult(status=row["status"], answer_type=row["answer_type"] if done else None,
                     payload=json.loads(row["payload_json"]) if done and row["payload_json"] else None,
                     owner_pairwise_pubkey=pairing.pairwise_pubkey(requester_fp))


# --- owner decision ----------------------------------------------------------------------------------------


def _refuse_by_ledger(request_id: str, claim: Claim, reason: str | None) -> None:
    _finish(request_id, "REFUSED")
    audit.log("request_refused_ledger", request_id, {"claim": claim.claim, "reason": reason})


def _answer(request_id: str, row: dict[str, Any], proposal: Proposal) -> None:
    claim, fp, nonce = proposal.claim, row["requester_fp"], row["nonce"]
    if proposal.answer_type == "ISSUER_PROOF":
        ic = claim.issuer_claim
        ref = wallet.find_copy(ic) if ic else None
        if ref is None:
            raise RequestConflict(f"No unused credential copy covers {ic or claim.claim}")
        value = wallet.disclosed_value(ref, ic)
        if isinstance(value, bool):
            lc = ledger.check(claim, value)
            if not lc.allowed:
                return _refuse_by_ledger(request_id, claim, lc.reason)
        payload = present.build_presentation(ref, ic, nonce, fp)
        if isinstance(value, bool):
            ledger.record(claim, value)
        low = next((t for t in wallet.status().by_type if t.credential_type == ref.credential_type), None)
        if low is not None and low.unused < config.WALLET_LOW_COPIES:
            audit.log("wallet_low", ref.credential_type, {"credential_type": ref.credential_type,
                                                          "unused": low.unused})
        shown = ic
    elif proposal.answer_type == "OWNER_ATTESTED":
        if not isinstance(proposal.result, bool):
            raise RequestConflict("Owner attestations answer yes/no claims only")
        lc = ledger.check(claim, proposal.result)
        if not lc.allowed:
            return _refuse_by_ledger(request_id, claim, lc.reason)
        payload = present.build_attestation(claim, proposal.result, fp, nonce)
        ledger.record(claim, proposal.result)
        shown = claim.claim
    else:
        raise RequestConflict(f"{proposal.answer_type} has nothing to disclose")
    _finish(request_id, proposal.answer_type, payload)
    audit.log("disclosure_answered", request_id, {"answer_type": proposal.answer_type, "claim": shown,
                                                   "requester_fp": fp, "channel": row["channel"]})


def decide_request(request_id: str, action: str) -> RequestView:
    """Owner action on a pending request: approve/answer discloses, decline/deny answers DECLINED."""
    with _decide_lock:
        row = _row(request_id)
        if row is None:
            raise RequestNotFound(request_id)
        if row["status"] != "pending" or not row["proposal_json"]:
            raise RequestConflict(f"request is {row['status']}, not waiting for a decision")
        proposal = Proposal.model_validate_json(row["proposal_json"])
        if action not in proposal.actions:
            raise RequestConflict(f"'{action}' is not offered; choose one of {', '.join(proposal.actions) or 'none'}")
        if action in ("approve", "answer"):
            _answer(request_id, row, proposal)
        else:
            _finish(request_id, "DECLINED")
            audit.log("disclosure_declined" if action == "decline" else "disclosure_denied", request_id,
                      {"claim": proposal.claim.claim, "requester_fp": row["requester_fp"]})
    return _view(request_id)
