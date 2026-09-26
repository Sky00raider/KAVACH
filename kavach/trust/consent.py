"""Request pipeline (sig, ts, nonce, pairing, parse, decide) and the owner approval queue (BUILD_PLAN §4.6)."""

from __future__ import annotations

import json

from kavach import db
from kavach.db import new_id, utc_now
from kavach.models import AskAck, AskIn, AskResult, Claim, Proposal, RejectReason, RequestView
from kavach.trust import audit


class RequestRejected(Exception):
    """Raised by receive() and poll() after the rejection is audited; api.py maps `reason` to an HTTP status."""

    def __init__(self, reason: RejectReason):
        super().__init__(reason)
        self.reason = reason


def receive(req: AskIn, channel: str) -> AskAck:
    """Stub: accepts everything as waiting for pairing."""
    return AskAck(request_id=new_id("rq"), status="pending_pairing")


def poll(request_id: str, requester_fp: str, ts: int, sig: str) -> AskResult:
    """Stub: checks the request exists and belongs to `requester_fp`; TRUST step 5 adds the sig and ts checks."""
    row = db.fetch_one("SELECT requester_fp, status, answer_type, payload_json FROM requests WHERE request_id = ?",
                       (request_id,))
    reason: RejectReason | None = None
    if row is None:
        reason = "unknown_request"
    elif row["requester_fp"] != requester_fp:
        reason = "wrong_requester"
    if reason is not None:
        audit.log("request_rejected", requester_fp, {"reason": reason, "request_id": request_id})
        raise RequestRejected(reason)
    return AskResult(status=row["status"], answer_type=row["answer_type"],
                     payload=json.loads(row["payload_json"]) if row["payload_json"] else None)


def decide_request(request_id: str, action: str) -> RequestView:
    """Stub: canned answered request."""
    claim = Claim(claim="income", op="ge", value=50000, issuer_claim="income_ge_50000")
    return RequestView(
        request_id=request_id, requester_fp="a1b2c3d4e5f60718", requester_name="Ramesh Kumar", channel="web",
        question="Does the tenant earn at least ₹50,000 a month?", claim=claim,
        proposal=Proposal(answer_type="ISSUER_PROOF", claim=claim, result=True, favourable=True,
                          actions=["approve", "deny"], reason="Unused mock_bank income_proof copy covers this claim"),
        status="done", answer_type="ISSUER_PROOF", created_at="2026-09-26T12:00:00Z", decided_at=utc_now(),
    )
