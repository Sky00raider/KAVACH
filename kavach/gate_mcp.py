"""Inbound MCP server "kavach-gate" (CONTRACT §11.1): streamable HTTP on GATE_PORT, path /mcp.

A thin adapter: TRUST step 9 forwards every call to http://127.0.0.1:API_PORT/api/ask* with `X-Channel: mcp`,
so web and MCP requests share one consent path. It never touches the database or vault itself.
Run: python -m kavach.gate_mcp
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from kavach import config
from kavach.brain import decide
from kavach.db import new_id
from kavach.models import AskAck, AskResult, ClaimsOut

gate = MCPServer(
    "kavach-gate",
    instructions="Ask the owner's KAVACH yes/no questions about disclosable claims. Answers are signed proofs "
                 "or owner attestations, never documents. Requests are signed with your requester key.",
)


@gate.tool()
def list_disclosable_claims() -> ClaimsOut:
    """Claim names you can ask about and which ones an issuer can prove. Never values."""
    return decide.claims()  # stub: TRUST forwards to GET /api/claims


@gate.tool()
def ask(question: str, nonce: str, requester_pubkey: str, requester_name: str, requester_type: str, ts: int,
        sig: str) -> AskAck:
    """Submit a signed question. `ts` is Unix seconds; `sig` covers canonical JSON of the other fields."""
    return AskAck(request_id=new_id("rq"), status="pending_pairing")  # stub: TRUST forwards to POST /api/ask


@gate.tool()
def get_answer(request_id: str, requester_fp: str, ts: int, sig: str) -> AskResult:
    """Poll a request. `sig` signs "{request_id}|{ts}" with the requester key; `ts` is Unix seconds."""
    return AskResult(status="pending_pairing")  # stub: TRUST forwards to GET /api/ask/{id}


if __name__ == "__main__":
    # 0.0.0.0 so the requester laptop can reach it; the SDK only enables DNS-rebinding checks for loopback hosts.
    gate.run("streamable-http", host="0.0.0.0", port=config.GATE_PORT, streamable_http_path="/mcp")
