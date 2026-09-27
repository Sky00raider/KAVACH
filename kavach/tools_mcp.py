"""Outbound MCP server "kavach-tools" (CONTRACT §11.2): stdio, spawned only by agent/executor.py.

Arguments are validated against the models.py arg models (and the §11.2 rules that need no graph) before anything
is written. Each call writes exactly one local file: an email or reminder or form in OUTBOX_DIR, or a note in
vault/notes (re-ingested by the watcher). Nothing is sent anywhere; there is no network code here.
Run: python -m kavach.tools_mcp
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pymupdf
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError

from kavach import config, db
from kavach.models import (
    Attachment,
    CreateReminderArgs,
    DraftEmailArgs,
    FillRentalFormArgs,
    SaveNoteArgs,
    ToolResult,
)

tools = MCPServer("kavach-tools", instructions="Owner-approved actions only. Each call writes one local file.")

EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PROOF_TYPES = ("ISSUER_PROOF", "OWNER_ATTESTED")
OWNER_FROM = "KAVACH owner <owner@kavach.local>"


class ToolError(McpToolError, ValueError):
    pass


def slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len].strip("-")
    return s or "untitled"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _unique(folder: Path, stem: str, suffix: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path, n = folder / f"{stem}{suffix}", 2
    while path.exists():
        path, n = folder / f"{stem}-{n}{suffix}", n + 1
    return path


def future_date(value: str) -> date:
    """§11.2: dates must be ISO (YYYY-MM-DD) and in the future."""
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"date {value!r} is not ISO YYYY-MM-DD") from None
    if len(value) != 10 or d <= datetime.now(timezone.utc).date():
        raise ToolError(f"date {value} is not in the future")
    return d


def proof_payload(request_id: str) -> dict:
    """§11.2: attachments must be `done` requests answered with a presentation or attestation."""
    row = db.fetch_one("SELECT status, answer_type, payload_json FROM requests WHERE request_id = ?", (request_id,))
    if row is None or row["status"] != "done" or row["answer_type"] not in PROOF_TYPES or not row["payload_json"]:
        raise ToolError(f"{request_id} is not an answered request with a proof")
    return json.loads(row["payload_json"])


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


@tools.tool()
def draft_email(to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> ToolResult:
    """Write outbox/<ts>_<slug>.eml. Attachments are presentations from answered requests. Nothing is sent."""
    args = DraftEmailArgs(to=to, subject=subject, body=body, attachments=attachments or [])
    if not EMAIL.match(args.to):
        raise ToolError(f"{args.to!r} is not an email address")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = OWNER_FROM, args.to, args.subject.replace("\n", " ")[:200]
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["X-Kavach"] = "draft; written locally, not sent"
    msg.set_content(args.body)
    for att in args.attachments:
        data = json.dumps(proof_payload(att.request_id), indent=2).encode()
        msg.add_attachment(data, maintype="application", subtype="json", filename=f"kavach-proof-{att.request_id}.json")
    path = _unique(config.OUTBOX_DIR, f"{_stamp()}_{slug(args.subject)}", ".eml")
    path.write_bytes(bytes(msg))
    return ToolResult(tool="draft_email", ok=True, output_path=f"outbox/{path.name}",
                      detail=f"draft to {args.to} with {len(args.attachments)} proof attachment(s)")


@tools.tool()
def create_reminder(title: str, date: str, notes: str = "") -> ToolResult:
    """Write outbox/<ts>_<slug>.ics for an ISO date in the future."""
    args = CreateReminderArgs(title=title, date=date, notes=notes)
    d = future_date(args.date)
    uid = f"{_stamp()}-{slug(args.title)}@kavach.local"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//KAVACH//kavach-tools//EN", "BEGIN:VEVENT", f"UID:{uid}",
             f"DTSTAMP:{_stamp()}", f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"SUMMARY:{_ics_text(args.title)}",
             f"DESCRIPTION:{_ics_text(args.notes)}", "BEGIN:VALARM", "ACTION:DISPLAY",
             f"DESCRIPTION:{_ics_text(args.title)}", "TRIGGER:PT9H", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    path = _unique(config.OUTBOX_DIR, f"{_stamp()}_{slug(args.title)}", ".ics")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    return ToolResult(tool="create_reminder", ok=True, output_path=f"outbox/{path.name}",
                      detail=f"reminder on {d.isoformat()}")


@tools.tool()
def fill_rental_form(fields: dict[str, str]) -> ToolResult:
    """Fill the rental application with the approved subset of fields (template text from demo_data/templates/)."""
    args = FillRentalFormArgs(fields=fields)
    if not args.fields:
        raise ToolError("no fields to fill")
    template = next((p for p in (config.DEMO_DATA_DIR / "templates" / n for n in
                                 ("rental_application.md", "rental_application.txt")) if p.is_file()), None)
    header = template.read_text(encoding="utf-8").splitlines()[:20] if template else []
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((56, 64), "Rental application", fontsize=16, fontname="helv")
    y = 96
    for line in header + [""] + [f"{k.replace('_', ' ').title()}: {v}" for k, v in args.fields.items()] + [
            "", "Filled by KAVACH with owner-approved fields only."]:
        page.insert_text((56, y), line[:95], fontsize=10, fontname="helv")
        y += 15
    config.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTBOX_DIR / "rental_application_filled.pdf"
    doc.save(path)
    doc.close()
    return ToolResult(tool="fill_rental_form", ok=True, output_path=f"outbox/{path.name}",
                      detail=f"{len(args.fields)} field(s)")


@tools.tool()
def save_note(title: str, markdown: str) -> ToolResult:
    """Write vault/notes/<slug>.md; the watcher re-ingests it. Never overwrites an existing note."""
    args = SaveNoteArgs(title=title, markdown=markdown)
    if not args.title.strip():
        raise ToolError("note needs a title")
    path = _unique(config.VAULT_DIR / "notes", slug(args.title), ".md")
    path.write_text(args.markdown if args.markdown.lstrip().startswith("#") else f"# {args.title}\n\n{args.markdown}",
                    encoding="utf-8")
    return ToolResult(tool="save_note", ok=True, output_path=f"vault/notes/{path.name}", detail=None)


if __name__ == "__main__":
    tools.run("stdio")
