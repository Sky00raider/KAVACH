"""Runs approved plans through the kavach-tools MCP server over stdio (TRUST step 9)."""

from __future__ import annotations

from kavach.models import ToolResult


def execute(task_id: str) -> list[ToolResult]:
    """Stub: reports success without spawning kavach-tools."""
    return [ToolResult(tool="draft_email", ok=True, output_path="outbox/20260926T120000Z_rent-agreement-renewal.eml")]
