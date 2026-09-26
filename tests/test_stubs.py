"""Every CONTRACT §7 function exists with its signature and returns its declared type."""

from pathlib import Path

from kavach.agent import executor, planner
from kavach.brain import chat, decide, embed, ingest, memory, parse_question, watcher
from kavach.models import (
    AskAck,
    AskIn,
    ChainStatus,
    ChatFinalEvent,
    ChatMetaEvent,
    ChatResult,
    Claim,
    CredentialRef,
    IngestResult,
    LedgerCheck,
    Plan,
    Presentation,
    Proposal,
    RequestView,
    SignatureResult,
    TeachResult,
    ToolResult,
    WalletStatus,
)
from kavach.trust import audit, consent, crypto, issuer_check, ledger, present, wallet


def test_brain_stubs_return_contract_types():
    assert isinstance(ingest.ingest_file(Path("vault/pdfs/x.pdf")), IngestResult)
    assert ingest.remove_file(Path("vault/pdfs/x.pdf")) is None
    assert embed.search("rent", k=3)[0].score > 0
    assert isinstance(chat.answer("rent?", []), ChatResult)
    assert isinstance(memory.teach("My salary went up"), TeachResult)
    assert memory.timeline("monthly_income")[-1].current
    claim = parse_question.parse("Earns 50k?")
    assert isinstance(claim, Claim)
    assert isinstance(decide.decide(claim, "fp"), Proposal)
    assert decide.decide(Claim(claim="unsupported"), "fp").answer_type == "REFUSED"
    assert isinstance(planner.plan("email my landlord"), Plan)


def test_chat_stream_event_order():
    events = [e.event for e in chat.answer_stream("rent?", [])]
    assert events[0] == "meta" and events[-2:] == ["final", "done"] and "token" in events
    first, final = next(chat.answer_stream("q", [])), list(chat.answer_stream("q", []))[-2]
    assert isinstance(first, ChatMetaEvent) and isinstance(final, ChatFinalEvent)


def test_watcher_start_returns_running_observer(tmp_path):
    obs = watcher.start(tmp_path)
    try:
        assert obs.is_alive()
    finally:
        obs.stop()
        obs.join(timeout=5)


def test_trust_stubs_return_contract_types():
    assert crypto.canonical({"b": 1, "a": "₹"}) == '{"a":"₹","b":1}'.encode()
    assert crypto.verify("pub", {}, crypto.sign(b"k", {})) is False  # fails closed
    assert isinstance(issuer_check.verify_pdf(Path("x.pdf")), SignatureResult)
    assert wallet.find_copy("income_ge_50000") is None
    assert isinstance(wallet.status(), WalletStatus)
    ref = CredentialRef(cred_id="cr_1", iss="mock_bank", credential_type="income_proof", copy=1)
    Presentation.model_validate(present.build_presentation(ref, "income_ge_50000", "n", "aud"))
    assert present.build_attestation(Claim(claim="income", op="ge", value=60000), True, "fp", "n")["answer"] is True
    assert isinstance(ledger.check(Claim(claim="income", op="ge", value=60000), True), LedgerCheck)
    ask = AskIn(requester_pubkey="p", requester_name="R", requester_type="person", question="q", nonce="n",
                ts="2026-09-26T12:00:00Z", sig="s")
    assert isinstance(consent.receive(ask, "web"), AskAck)
    assert isinstance(consent.decide_request("rq_1", "approve"), RequestView)
    assert isinstance(audit.log("request_received", "rq_1", {}), int)
    assert isinstance(audit.verify_chain(), ChainStatus)
    assert all(isinstance(r, ToolResult) for r in executor.execute("t_1"))
