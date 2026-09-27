"""Scripted landlord agent: its own keypair, talks to kavach-gate over MCP streamable HTTP, runs the same
verifier and appends results to requester/data/requests.json with via="agent" (CONTRACT §12).

Run on the requester laptop (OWNER_URL=http://<owner-ip>:8000):
    python -m requester.agent_client                      # the scripted questions, waits for the owner
    python -m requester.agent_client "Is the tenant over 21?" --wait 60
"""

from __future__ import annotations

import argparse
import time
from typing import Any
from urllib.parse import urlsplit

import anyio
from mcp import Client

from kavach import config
from kavach.models import AskResult, RRequest
from requester import common

SCRIPT = (
    "Does the tenant earn at least ₹50,000 a month?",
    "Any loan default in the last 12 months?",
    "What is the tenant's bank account number?",
)
POLL_EVERY_S = 2.0


def gate_url() -> str:
    """kavach-gate lives on the owner's host at GATE_PORT, path /mcp."""
    return f"http://{urlsplit(config.OWNER_URL).hostname}:{config.GATE_PORT}/mcp"


async def _run(questions: tuple[str, ...], target: Any, wait_s: float, log) -> list[RRequest]:
    ident = common.Identity("agent")
    open_ids: dict[str, str] = {}  # local_id -> request_id
    async with Client(target) as c:
        claims = await c.call_tool("list_disclosable_claims", {})
        if not claims.is_error:
            log("claims I may ask about: " + ", ".join(x["claim"] for x in claims.structured_content["claims"]))
        for q in questions:
            body = ident.ask_body(q)
            r = await c.call_tool("ask", body)
            if r.is_error:
                log(f"refused at the gate: {q!r}: {r.content[0].text if r.content else 'error'}")
                continue
            rec = common.record_request(ident, q, r.structured_content, body["nonce"])
            open_ids[rec.local_id] = rec.request_id
            log(f"asked {q!r} -> {rec.request_id} ({rec.status})")
        deadline = time.monotonic() + wait_s
        while open_ids and time.monotonic() < deadline:
            for local_id, request_id in list(open_ids.items()):
                r = await c.call_tool("get_answer", {"request_id": request_id, **ident.poll_auth(request_id)})
                if r.is_error:
                    continue
                rec = common.apply_answer(ident, local_id, AskResult.model_validate(r.structured_content))
                if rec is not None and rec.result is not None:
                    del open_ids[local_id]
                    res = rec.result
                    ticks = " ".join(f"{'✓' if ch.ok else '✗'} {ch.name}" for ch in res.checks)
                    log(f"{rec.request_id}: {res.answer_type} {res.claim} -> {res.result!r} "
                        f"{'VERIFIED' if res.all_ok else 'not verified'} {ticks}")
            if open_ids:
                await anyio.sleep(POLL_EVERY_S)
    mine = {r.local_id for r in common.load_requests() if r.via == "agent"}
    return [r for r in common.load_requests() if r.local_id in mine]


def run(questions: tuple[str, ...] = SCRIPT, target: Any = None, wait_s: float = 120.0, log=print) -> list[RRequest]:
    """Ask via kavach-gate, wait up to `wait_s` for the owner, verify. `target` defaults to gate_url()."""
    return anyio.run(_run, tuple(questions), target or gate_url(), wait_s, log)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions", nargs="*", help="questions to ask (default: the scripted three)")
    parser.add_argument("--wait", type=float, default=120.0, help="seconds to wait for the owner's decisions")
    args = parser.parse_args(argv)
    print(f"landlord agent -> {gate_url()}")
    run(tuple(args.questions) or SCRIPT, wait_s=args.wait)


if __name__ == "__main__":
    main()
