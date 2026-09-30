"""A decision's amount condition, checked in code against the owner's facts (hard rule 2).

"I decided to renew only if rent stays under ₹15,000" is a DECISION entity (entities.py). When a chat question is
about such a decision, `checks()` parses its condition in plain code - subject (rent / salary / income), comparison
(under, below, at most, above, at least...) and amount, all from the decision's own quote - and compares it with
the field's current fact and every scheduled one. chat.py turns each result into one citable line, so the model
reports "met today, not met from 1 Jan 2027" instead of comparing numbers itself. On a real run without this, the
model answered "Should I still renew the flat?" with "the condition still holds" 15/15 times after the inbox note
had scheduled ₹16,000 from January: the rent facts never reached the prompt (the question shares no word with them).
"""

from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass, field as dc_field
from datetime import date

from kavach import db
from kavach.brain import amounts, embed
from kavach.models import OWNER_ENTITY_ID

SUBJECT_FIELD = {"rent": "rent_amount", "salary": "monthly_income", "income": "monthly_income"}
_OPS = {"under": "<", "below": "<", "less than": "<", "lower than": "<", "at most": "<=", "up to": "<=",
        "upto": "<=", "within": "<=", "above": ">", "over": ">", "more than": ">", "at least": ">="}
_COMPARE = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_CONDITION = re.compile(
    rf"\b(?:if|as long as|provided|unless)\s+(?:the\s+|my\s+)?(?P<subject>{'|'.join(SUBJECT_FIELD)})\b[^.?!;]*?"
    rf"\b(?P<op>{'|'.join(sorted(_OPS, key=len, reverse=True))})\s+(?:rs\.?\s*|inr\s*|₹\s*)?(?P<amount>\d+)\b",
    re.IGNORECASE)
_DECISION_WORDS = re.compile(r"\b(?:decision|decided|decide|condition|plan|planned)\b", re.IGNORECASE)
PREFIX = 5  # "renewal", "renewing" and "renew" match on their first five letters
MAX_CHECKS = 2


@dataclass
class Condition:
    field: str
    op: str
    amount: int

    def holds(self, value: str) -> bool | None:
        n = amounts.parse_amount(value) if not value.isdigit() else int(value)
        return None if n is None else _COMPARE[self.op](n, self.amount)


@dataclass
class Check:
    decision_id: str
    quote: str
    condition: Condition
    chunk_id: str | None
    today: dict | None                      # the field's current fact
    later: list[dict] = dc_field(default_factory=list)  # its scheduled facts, soonest first


def parse(quote: str) -> Condition | None:
    """The first "if <subject> ... <comparison> <amount>" in the quote, amounts normalised first; "unless" flips
    the comparison ("unless rent goes above 15000" = rent <= 15000)."""
    m = _CONDITION.search(amounts.normalize_amounts(quote))
    if not m:
        return None
    op = _OPS[m.group("op").lower()]
    if m.group(0).lower().startswith("unless"):
        op = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}[op]
    return Condition(field=SUBJECT_FIELD[m.group("subject").lower()], op=op, amount=int(m.group("amount")))


def _stems(text: str) -> set[str]:
    return {t[:PREFIX] for t in embed.tokenize(text) if len(t) >= 3 and not t.isdigit()}


def _decision_chunk(decision_id: str) -> str | None:
    """The chunk the decision was grounded in (its DECIDED edge), if that chunk still exists."""
    row = db.fetch_one("SELECT e.source_chunk_id FROM edges e JOIN chunks c ON c.chunk_id = e.source_chunk_id "
                       "WHERE e.dst = ? AND e.rel = 'DECIDED' ORDER BY e.rowid LIMIT 1", (decision_id,))
    return row["source_chunk_id"] if row else None


def checks(question: str, used: list[str] = (), today: str | None = None) -> list[Check]:
    """Condition checks for the decisions this question is about: a DECISION the chat already linked (`used`), or
    one sharing a word stem with the question (beyond the condition's own subject word, unless the question says
    "decision"/"condition"/"plan"). At most MAX_CHECKS, one per distinct condition, oldest decision first."""
    today = today or date.today().isoformat()
    q_stems = _stems(question)
    generic = bool(_DECISION_WORDS.search(question))
    out: list[Check] = []
    seen: set[tuple[str, str, int]] = set()
    for row in db.fetch_all("SELECT entity_id, attrs_json, name FROM entities WHERE type = 'DECISION' ORDER BY rowid"):
        attrs = json.loads(row["attrs_json"] or "{}")
        quote = attrs.get("quote") or row["name"]
        cond = parse(quote)
        if cond is None or (cond.field, cond.op, cond.amount) in seen:
            continue
        subject = {s[:PREFIX] for s, f in SUBJECT_FIELD.items() if f == cond.field}
        about = row["entity_id"] in used or generic or bool((q_stems & _stems(quote)) - subject)
        if not about:
            continue
        seen.add((cond.field, cond.op, cond.amount))
        later = [f for f in db.scheduled_facts(OWNER_ENTITY_ID, today) if f["field"] == cond.field]
        out.append(Check(decision_id=row["entity_id"], quote=quote, condition=cond,
                         chunk_id=_decision_chunk(row["entity_id"]),
                         today=db.current_fact(OWNER_ENTITY_ID, cond.field, today), later=later))
        if len(out) >= MAX_CHECKS:
            break
    return out
