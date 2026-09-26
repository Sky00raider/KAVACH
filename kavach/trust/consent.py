"""Request pipeline (sig, ts, nonce, pairing, parse, decide) and the owner approval queue (BUILD_PLAN §4.6)."""

from __future__ import annotations

from kavach.db import new_id, utc_now
from kavach.models import AskAck, AskIn, Claim, Proposal, RequestView


def receive(req: AskIn, channel: str) -> AskAck:
    """Stub: accepts everything as waiting for pairing."""
    return AskAck(request_id=new_id("rq"), status="pending_pairing")


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
