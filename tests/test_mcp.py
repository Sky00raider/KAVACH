"""kavach-gate (CONTRACT §11.1) and kavach-tools (§11.2): tools registered with contract args and result shapes."""

import anyio
import pytest
from mcp import Client

from kavach.gate_mcp import gate
from kavach.models import AskAck, AskResult, ClaimsOut, ToolResult
from kavach.tools_mcp import tools


def _call(server, fn):
    async def main():
        async with Client(server) as c:
            return await fn(c)
    return anyio.run(main)


def _args(server):
    async def fn(c):
        return {t.name: set(t.input_schema.get("properties", {})) for t in (await c.list_tools()).tools}
    return _call(server, fn)


def test_gate_tools_and_args():
    assert _args(gate) == {
        "list_disclosable_claims": set(),
        "ask": {"question", "nonce", "requester_pubkey", "requester_name", "requester_type", "ts", "sig"},
        "get_answer": {"request_id", "requester_fp", "ts", "sig"},
    }


def test_gate_results_match_api_shapes():
    ask = {"question": "q", "nonce": "n", "requester_pubkey": "p", "requester_name": "n", "requester_type": "t",
           "ts": 1790000000, "sig": "s"}

    async def fn(c):
        return [await c.call_tool("list_disclosable_claims", {}), await c.call_tool("ask", ask),
                await c.call_tool("get_answer", {"request_id": "rq_1", "requester_fp": "fp", "ts": 1, "sig": "s"})]

    claims, ack, answer = _call(gate, fn)
    assert not (claims.is_error or ack.is_error or answer.is_error)
    ClaimsOut.model_validate(claims.structured_content)
    AskAck.model_validate(ack.structured_content)
    AskResult.model_validate(answer.structured_content)


def test_tools_registered_with_contract_args():
    assert _args(tools) == {
        "draft_email": {"to", "subject", "body", "attachments"},
        "create_reminder": {"title", "date", "notes"},
        "fill_rental_form": {"fields"},
        "save_note": {"title", "markdown"},
    }


@pytest.mark.parametrize("name,args", [
    ("draft_email", {"to": "a@b.in", "subject": "s", "body": "b",
                     "attachments": [{"type": "presentation", "request_id": "rq_1"}]}),
    ("create_reminder", {"title": "Rent", "date": "2027-03-01"}),
    ("fill_rental_form", {"fields": {"name": "A"}}),
    ("save_note", {"title": "t", "markdown": "# t"}),
])
def test_tools_return_tool_result(name, args):
    r = _call(tools, lambda c: c.call_tool(name, args))
    assert not r.is_error and ToolResult.model_validate(r.structured_content).tool == name


def test_tools_reject_bad_args():
    r = _call(tools, lambda c: c.call_tool("draft_email", {"to": "a@b.in", "subject": "s", "body": "b",
                                                          "attachments": [{"type": "document", "request_id": "x"}]}))
    assert r.is_error
    assert _call(tools, lambda c: c.call_tool("create_reminder", {"title": "x"})).is_error
