"""Point every runtime path at a throwaway directory before kavach.config is imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="kavach-test-"))
_DIRS = {"DB_PATH": "kavach.db", "VAULT_DIR": "vault", "OUTBOX_DIR": "outbox", "KEYS_DIR": "keys",
         "REQUESTER_DATA_DIR": "requester_data"}
for _name, _sub in _DIRS.items():
    os.environ[_name] = str(_TMP / _sub)
os.environ["OWNER_TOKEN"] = "test-owner-token"
os.environ["KAVACH_WATCH"] = "0"  # no vault watcher or index warm-up in the API lifespan

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """An initialised, empty database for one test."""
    from kavach import config, db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kavach.db")
    db.init_db()
    return db


def fake_taught_map(text: str):
    """Keyword stand-in for memory.teach's one model call, in the same spirit as fake_parse_map."""
    import re

    from kavach.brain.memory import TaughtFact

    low = text.lower()
    n = re.search(r"\d+", text)
    value = n.group() if n else "unspecified"
    if re.search(r"salary|income|earn", low):
        field = "monthly_income"
    elif re.search(r"rent", low):
        field = "rent_amount"
    elif re.search(r"employ|job", low):
        field = "employer"
    elif re.search(r"landlord", low):
        field = "landlord"
    else:
        field = "note"
    return TaughtFact(field=field, value=value, valid_from=None)


def fake_plan_map(instruction: str, today: str):
    """Keyword stand-in for planner._propose's one model call: which tools the instruction names, the recipient
    words after "email"/"mail"/"write to", the instruction as the date words. Resolution and validation still run."""
    import re

    from kavach.agent.planner import XPlan, XStep

    low = instruction.lower()
    steps = []
    m = re.search(r"\b(?:email|mail|write to|message)\s+(.+?)(?:\s+(?:about|that|and|with|to say)\b|[,.]|$)", low)
    if m:
        steps.append(XStep(tool="draft_email", recipient=m.group(1), subject="Hello", body="Hi,\n\nJust checking in.",
                           attach_proof="proof" in low))
    if "remind" in low:
        rel = "agreement_end_date" if "agreement ends" in low else ""
        steps.append(XStep(tool="create_reminder", title="Reminder", date_text=instruction, relative_to=rel,
                           days_before=7 if rel and "before" in low else 0))
    if "form" in low:
        steps.append(XStep(tool="fill_rental_form", form_fields=["full_name", "employer"]))
    if "note" in low:
        steps.append(XStep(tool="save_note", title="Note", markdown=instruction))
    return XPlan(steps=steps)


def fake_parse_map(text: str):
    """Keyword stand-in for parse_question's one model call. Normalisation and validation still run."""
    import re

    from kavach.brain.parse_question import _Mapped

    low = text.lower()
    n = re.search(r"\d+", text)
    t = int(n.group()) if n else None
    if re.search(r"earn|income|salary", low):
        return _Mapped(claim="income", threshold=t)
    if re.search(r"percent|marks|score", low):
        return _Mapped(claim="percentage", threshold=t)
    if re.search(r"\bage\b|adult|\bold\b|\d\+", low):
        return _Mapped(claim="age", threshold=t or 18)
    if "default" in low:
        return _Mapped(claim="loan_default_12m")
    if "pass" in low:
        return _Mapped(claim="result")
    if "board" in low:
        return _Mapped(claim="board")
    return _Mapped(claim="unsupported")


@pytest.fixture(autouse=True)
def _no_ollama_outside_llm_tests(request, monkeypatch):
    """Tests not marked `llm` never call Ollama: questions reaching parse_question (consent, MCP, eval) are
    mapped by `fake_parse_map` instead of the model, and ingest's entity extraction finds nothing (tests that
    need entities patch `entities.extract` themselves). Query and entity-name vectors are never reused across
    tests, since tests fake `llm.embed` differently."""
    from kavach.brain import embed, entities

    embed._query_cache.clear()
    entities._name_vectors.clear()
    if request.node.get_closest_marker("llm") is None:
        from kavach.brain import extract, memory, parse_question

        monkeypatch.setattr(parse_question, "_map", fake_parse_map)
        monkeypatch.setattr(entities, "extract", lambda text: entities.Extraction())
        monkeypatch.setattr(extract, "extract", lambda text: extract.Extraction())
        monkeypatch.setattr(memory, "_extract_candidates", lambda text: memory.CandidateExtraction())
        monkeypatch.setattr(memory, "_parse_taught", fake_taught_map)
        from kavach.agent import planner

        monkeypatch.setattr(planner, "_propose", fake_plan_map)
