"""Runs approved plans through the kavach-tools MCP server over stdio (BUILD_PLAN §4.8, CONTRACT §11.2).

Only `approved` tasks run. Every call is validated again here (the planner already did) before the tool server is
spawned: `to` must be an email attribute of a graph entity unless the owner typed it in the instruction, attachments
must be answered requests with a proof, dates must be ISO and in the future. A call that fails validation is
reported as not run; the others still run.
"""

from __future__ import annotations

import os
import re
import sys

import anyio
from mcp import Client, StdioServerParameters
from pydantic import ValidationError

from kavach import config, db
from kavach.models import DraftEmailCall, CreateReminderCall, ToolCall, ToolResult

EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class ExecutionError(Exception):
    pass


def known_emails() -> set[str]:
    return {v.strip().lower() for e in db.list_entities() for v in e.attrs.values() if EMAIL_IN_TEXT.fullmatch(v.strip())}


def validate_call(call: ToolCall, instruction: str) -> str | None:
    """None if the call may run, else the reason it may not."""
    from kavach import tools_mcp  # rules shared with the tool server

    try:
        if isinstance(call, DraftEmailCall):
            to = call.args.to.strip().lower()
            typed = {m.lower() for m in EMAIL_IN_TEXT.findall(instruction)}
            if to not in known_emails() and to not in typed:
                return f"{call.args.to} is not a known contact and was not typed by the owner"
            for att in call.args.attachments:
                tools_mcp.proof_payload(att.request_id)
        elif isinstance(call, CreateReminderCall):
            tools_mcp.future_date(call.args.date)
    except tools_mcp.ToolError as exc:
        return str(exc)
    return None


def _server() -> StdioServerParameters:
    env = dict(os.environ)
    env.update({"DB_PATH": str(config.DB_PATH), "VAULT_DIR": str(config.VAULT_DIR),
                "OUTBOX_DIR": str(config.OUTBOX_DIR), "DEMO_DATA_DIR": str(config.DEMO_DATA_DIR),
                "KEYS_DIR": str(config.KEYS_DIR), "PYTHONIOENCODING": "utf-8"})
    env.pop("OWNER_TOKEN", None)  # the tool server never needs owner credentials
    return StdioServerParameters(command=sys.executable, args=["-m", "kavach.tools_mcp"], env=env, cwd=config.ROOT)


async def _run(calls: list[ToolCall]) -> list[ToolResult]:
    results = []
    async with Client(_server()) as c:
        for call in calls:
            r = await c.call_tool(call.tool, call.args.model_dump(mode="json"))
            if r.is_error:
                text = r.content[0].text if r.content else "tool error"
                results.append(ToolResult(tool=call.tool, ok=False, detail=text[:300]))
                continue
            try:
                results.append(ToolResult.model_validate(r.structured_content))
            except ValidationError:
                results.append(ToolResult(tool=call.tool, ok=False, detail="tool returned an unexpected result"))
    return results


def execute(task_id: str) -> list[ToolResult]:
    task = db.get_task(task_id)
    if task is None:
        raise ExecutionError(f"unknown task {task_id}")
    if task.status != "approved":
        raise ExecutionError(f"task {task_id} is {task.status}, not approved")
    verdicts = [validate_call(c, task.instruction) for c in task.plan.calls]
    runnable = [c for c, v in zip(task.plan.calls, verdicts) if v is None]
    ran = iter(anyio.run(_run, runnable) if runnable else [])
    return [next(ran) if v is None else ToolResult(tool=c.tool, ok=False, detail=f"not run: {v}")
            for c, v in zip(task.plan.calls, verdicts)]
