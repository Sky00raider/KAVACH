"""Outbound MCP server "kavach-tools" (CONTRACT §11.2): stdio, spawned only by agent/executor.py.

Arguments are validated against the models.py arg models before anything is written. Outputs go to
OUTBOX_DIR (email, reminder, form) or vault/notes (note, re-ingested by the watcher).
Run: python -m kavach.tools_mcp
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from kavach.models import (
    Attachment,
    CreateReminderArgs,
    DraftEmailArgs,
    FillRentalFormArgs,
    SaveNoteArgs,
    ToolResult,
)

tools = MCPServer("kavach-tools", instructions="Owner-approved actions only. Each call writes one local file.")


@tools.tool()
def draft_email(to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> ToolResult:
    """Write outbox/<ts>_<slug>.eml. Attachments are presentations from answered requests."""
    DraftEmailArgs(to=to, subject=subject, body=body, attachments=attachments or [])
    return ToolResult(tool="draft_email", ok=True, output_path="outbox/20260926T120000Z_stub.eml",
                      detail="stub: nothing written")  # TRUST step 10


@tools.tool()
def create_reminder(title: str, date: str, notes: str = "") -> ToolResult:
    """Write outbox/<ts>_<slug>.ics for an ISO date in the future."""
    CreateReminderArgs(title=title, date=date, notes=notes)
    return ToolResult(tool="create_reminder", ok=True, output_path="outbox/20260926T120000Z_stub.ics",
                      detail="stub: nothing written")  # TRUST step 10


@tools.tool()
def fill_rental_form(fields: dict[str, str]) -> ToolResult:
    """Fill demo_data/templates/ rental form with the approved subset of fields."""
    FillRentalFormArgs(fields=fields)
    return ToolResult(tool="fill_rental_form", ok=True, output_path="outbox/rental_application_filled.pdf",
                      detail="stub: nothing written")  # TRUST step 10


@tools.tool()
def save_note(title: str, markdown: str) -> ToolResult:
    """Write vault/notes/<slug>.md; the watcher re-ingests it."""
    SaveNoteArgs(title=title, markdown=markdown)
    return ToolResult(tool="save_note", ok=True, output_path="vault/notes/stub.md",
                      detail="stub: nothing written")  # TRUST step 10


if __name__ == "__main__":
    tools.run("stdio")
