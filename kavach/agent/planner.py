"""Instruction -> validated Plan of kavach-tools calls. Never executes anything."""

from __future__ import annotations

from kavach.models import CreateReminderArgs, CreateReminderCall, DraftEmailArgs, DraftEmailCall, Plan


def plan(instruction: str) -> Plan:
    """Stub: canned two-step plan."""
    return Plan(instruction=instruction, calls=[
        DraftEmailCall(args=DraftEmailArgs(to="ramesh.kumar@example.com", subject="Rent agreement renewal",
                                           body="Dear Mr. Kumar,\n\nI would like to discuss renewing the agreement."),
                       preview='Email to Ramesh Kumar <ramesh.kumar@example.com>: "Rent agreement renewal"'),
        CreateReminderCall(args=CreateReminderArgs(title="Rent agreement ends", date="2027-03-01",
                                                   notes="Agreement ends 31 Mar 2027"),
                           preview="Reminder on 2027-03-01: Rent agreement ends"),
    ])
