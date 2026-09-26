"""Scripted landlord agent: its own keypair, talks to kavach-gate over MCP streamable HTTP, runs the same
verifier and appends results to requester/data/requests.json with via="agent" (CONTRACT §12).

Stub until TRUST step 9. Run: python -m requester.agent_client
"""

from __future__ import annotations

from urllib.parse import urlsplit

from kavach import config
from kavach.models import RRequest

SCRIPT = (
    "Does the tenant earn at least ₹50,000 a month?",
    "Any loan default in the last 12 months?",
    "What is the tenant's bank account number?",
)


def gate_url() -> str:
    """kavach-gate lives on the owner's host at GATE_PORT, path /mcp."""
    return f"http://{urlsplit(config.OWNER_URL).hostname}:{config.GATE_PORT}/mcp"


def run(questions: tuple[str, ...] = SCRIPT) -> list[RRequest]:
    """Stub: asks nothing, returns no requests."""
    return []


if __name__ == "__main__":
    print(f"agent_client stub: would ask {len(SCRIPT)} questions via {gate_url()}")
