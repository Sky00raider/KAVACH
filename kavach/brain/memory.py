"""Temporal facts, supersession, conversation-memory candidates, teaching."""

from __future__ import annotations

from kavach.db import new_id
from kavach.models import OWNER_ENTITY_ID, Entity, Fact, FactVersion, TeachResult


def teach(statement: str) -> TeachResult:
    """Stub: canned owner_stated fact."""
    fact = Fact(fact_id=new_id("f"), entity_id=OWNER_ENTITY_ID, field="monthly_income", value="70000",
                source_type="owner_stated", quote=statement, valid_from="2026-10-01", confidence="high")
    return TeachResult(fact=fact, superseded=[])


def decide_candidate(candidate_id: str, remember: bool) -> Fact | Entity | None:
    """Stub: nothing stored."""
    return None


def timeline(field: str | None) -> list[FactVersion]:
    """Stub: one superseded and one current version of monthly_income."""
    return [
        FactVersion(fact_id="f_1a2b3c4d5e", entity_id=OWNER_ENTITY_ID, field="monthly_income", value="62000",
                    source_type="issuer_doc", doc_id="d_03a1b2c3d4", quote="Salary Credit ... 62,000.00",
                    valid_from="2026-04-01", valid_to="2026-10-01", superseded_by="f_9f8e7d6c5b",
                    confidence="high", created_at="2026-09-26T10:00:00Z", current=False),
        FactVersion(fact_id="f_9f8e7d6c5b", entity_id=OWNER_ENTITY_ID, field="monthly_income", value="70000",
                    source_type="owner_stated", quote="My salary went up to ₹70k from October",
                    valid_from="2026-10-01", confidence="high", created_at="2026-09-26T11:00:00Z", current=True),
    ]
