"""Inbound MCP server "kavach-gate" (CONTRACT §11.1): streamable HTTP on GATE_PORT, path /mcp.

A thin adapter: every call is forwarded to http://127.0.0.1:API_PORT/api/ask* (and /api/claims) with
`X-Channel: mcp`, so web and MCP requests share one consent path (pairing, ledger, owner approval, audit).
It never touches the database or vault itself.
Run: python -m kavach.gate_mcp
"""

from __future__ import annotations

import re

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kavach import config
from kavach.models import AskAck, AskResult, ClaimsOut

gate = MCPServer(
    "kavach-gate",
    instructions="Ask the owner's KAVACH yes/no questions about disclosable claims. Answers are signed proofs "
                 "or owner attestations, never documents. Requests are signed with your requester key.",
)
_HEADERS = {"X-Channel": "mcp"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class GateError(ToolError):
    pass


def owner_api() -> httpx.Client:
    """The owner API on this machine (loopback, so the owner honours X-Channel: mcp). Tests replace it."""
    return httpx.Client(base_url=f"http://127.0.0.1:{config.API_PORT}", timeout=httpx.Timeout(60.0, connect=5.0))


def _call(method: str, path: str, **kw) -> dict:
    c = owner_api()
    try:
        r = c.request(method, path, headers=_HEADERS | kw.pop("headers", {}), **kw)
    except httpx.HTTPError as exc:
        raise GateError(f"owner KAVACH is not reachable ({type(exc).__name__})") from exc
    finally:
        c.close()
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise GateError(f"rejected ({r.status_code}): {detail if isinstance(detail, str) else 'invalid request'}")
    return r.json()


@gate.tool()
def list_disclosable_claims() -> ClaimsOut:
    """Claim names you can ask about and which ones an issuer can prove. Never values."""
    return ClaimsOut.model_validate(_call("GET", "/api/claims"))


@gate.tool()
def ask(question: str, nonce: str, requester_pubkey: str, requester_name: str, requester_type: str, ts: int,
        sig: str) -> AskAck:
    """Submit a signed question. `ts` is Unix seconds; `sig` covers canonical JSON of the other fields."""
    body = {"requester_pubkey": requester_pubkey, "requester_name": requester_name, "requester_type": requester_type,
            "question": question, "nonce": nonce, "ts": ts, "sig": sig}
    return AskAck.model_validate(_call("POST", "/api/ask", json=body))


@gate.tool()
def get_answer(request_id: str, requester_fp: str, ts: int, sig: str) -> AskResult:
    """Poll a request. `sig` signs "{request_id}|{ts}" with the requester key; `ts` is Unix seconds."""
    if not _REQUEST_ID.match(request_id):
        raise GateError("bad request_id")
    headers = {"X-Requester-Fp": requester_fp, "X-Ts": str(ts), "X-Sig": sig}
    return AskResult.model_validate(_call("GET", f"/api/ask/{request_id}", headers=headers))


if __name__ == "__main__":
    # 0.0.0.0 so the requester laptop can reach it; the SDK only enables DNS-rebinding checks for loopback hosts.
    gate.run("streamable-http", host="0.0.0.0", port=config.GATE_PORT, streamable_http_path="/mcp")
