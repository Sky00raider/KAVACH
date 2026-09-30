"""agent/planner.py (BRAIN step 9): the model proposes steps, code resolves recipients, dates, proofs and form
values, validates (CONTRACT §11.2) and previews. Non-llm tests replace `_propose` with fixed steps."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from kavach import config, db
from kavach.agent import executor, planner
from kavach.agent.planner import XPlan, XStep
from kavach.models import OWNER_ENTITY_ID

TODAY = date.today()
END = (TODAY + timedelta(days=92)).isoformat()  # the agreement ends in about three months, whenever this runs


@pytest.fixture
def graph(fresh_db):
    """Owner, landlord Ravi Kumar (LANDLORD_OF, email), employer Nimbus (no email), friend Priya (email)."""
    ents = [(OWNER_ENTITY_ID, "PERSON", config.OWNER_NAME, {}),
            ("e_ravi", "PERSON", "Ravi Kumar", {"email": "ravi.landlord@example.com", "phone": "98450 12345"}),
            ("e_nimbus", "ORG", "Nimbus Analytics", {}),
            ("e_priya", "PERSON", "Priya Shah", {"email": "priya@example.com"})]
    for eid, typ, name, attrs in ents:
        db.insert("entities", {"entity_id": eid, "type": typ, "name": name, "norm_name": name.lower(),
                               "attrs_json": json.dumps(attrs)})
    db.insert_edges([{"edge_id": "x_1", "src": "e_ravi", "rel": "LANDLORD_OF", "dst": OWNER_ENTITY_ID,
                      "valid_from": None, "valid_to": None, "source_chunk_id": "c_1"},
                     {"edge_id": "x_2", "src": OWNER_ENTITY_ID, "rel": "EMPLOYED_BY", "dst": "e_nimbus",
                      "valid_from": None, "valid_to": None, "source_chunk_id": "c_1"}])
    for fid, field, value, source in [("f_end", "agreement_end_date", END, "extracted"),
                                      ("f_inc", "monthly_income", "62000", "issuer_doc"),
                                      ("f_emp", "employer", "Nimbus Analytics", "issuer_doc"),
                                      ("f_gym", "gym_fee", "1500", "owner_stated")]:
        db.insert("facts", {"fact_id": fid, "entity_id": OWNER_ENTITY_ID, "field": field, "value": value,
                            "source_type": source, "doc_id": None, "quote": value, "valid_from": "2026-01-01",
                            "confidence": "high", "created_at": db.utc_now()})
    return db


def _plan(monkeypatch, instruction, *steps):
    monkeypatch.setattr(planner, "_propose", lambda text, today: XPlan(steps=list(steps)))
    return planner.plan(instruction)


def _proof(request_id="rq_proof", status="done", answer_type="ISSUER_PROOF", decided_at="2026-09-30T10:00:00Z"):
    db.insert("requests", {"request_id": request_id, "requester_fp": "fp", "channel": "web", "question": "q",
                           "nonce": request_id, "status": status, "answer_type": answer_type,
                           "payload_json": json.dumps({"kind": "presentation"}), "created_at": decided_at,
                           "decided_at": decided_at})


# --- recipients ----------------------------------------------------------------------------------------------


def test_my_landlord_resolves_through_the_graph(graph, monkeypatch):
    p = _plan(monkeypatch, "Email my landlord that I'll renew",
              XStep(tool="draft_email", recipient="my landlord", subject="Renewal", body="Hi Ravi,\n\nI'll renew."))
    [call] = p.calls
    assert call.args.to == "ravi.landlord@example.com"
    assert call.preview.startswith("Email to Ravi Kumar <ravi.landlord@example.com>: “Renewal”")
    assert call.args.body.endswith("Thanks,\n" + config.OWNER_NAME.split()[0])  # sign-off added in code
    assert p.warnings == []


def test_a_first_name_resolves_and_a_typed_address_is_allowed(graph, monkeypatch):
    p = _plan(monkeypatch, "Mail Priya, and cc bob@newco.in",
              XStep(tool="draft_email", recipient="Priya", subject="Hi", body="Hi"),
              XStep(tool="draft_email", recipient="bob@newco.in", subject="Hi", body="Hi"))
    assert [c.args.to for c in p.calls] == ["priya@example.com", "bob@newco.in"]


def test_an_address_the_model_invents_is_never_used(graph, monkeypatch):
    p = _plan(monkeypatch, "Email my accountant",
              XStep(tool="draft_email", recipient="accountant@firm.com", subject="Hi", body="Hi"))
    assert p.calls == []
    assert "No email address for “accountant@firm.com”" in p.warnings[0]


def test_a_contact_without_an_email_is_not_guessed(graph, monkeypatch):
    p = _plan(monkeypatch, "Email my employer", XStep(tool="draft_email", recipient="my employer", body="Hi"))
    assert p.calls == [] and "No email address" in p.warnings[0]


# --- dates ---------------------------------------------------------------------------------------------------


def test_reminder_counts_back_from_the_agreement_end(graph, monkeypatch):
    p = _plan(monkeypatch, "Remind me a week before the agreement ends",
              XStep(tool="create_reminder", title="Agreement ends", date_text="a week before the agreement ends",
                    relative_to="agreement_end_date", days_before=7))
    [call] = p.calls
    assert call.args.date == (date.fromisoformat(END) - timedelta(days=7)).isoformat()
    assert "(7 days before agreement end date" in call.preview


def test_before_without_a_number_means_a_week(graph, monkeypatch):
    p = _plan(monkeypatch, "Remind me before the agreement ends",
              XStep(tool="create_reminder", title="x", relative_to="agreement_end_date"))
    assert p.calls[0].args.date == (date.fromisoformat(END) - timedelta(days=7)).isoformat()


@pytest.mark.parametrize("words, expected", [
    ("tomorrow", TODAY + timedelta(days=1)),
    ("next week", TODAY + timedelta(days=7)),
    ("in 3 days", TODAY + timedelta(days=3)),
    ("in 2 weeks", TODAY + timedelta(days=14)),
    (f"on {(TODAY + timedelta(days=40)).day} {(TODAY + timedelta(days=40)):%B %Y}", TODAY + timedelta(days=40)),
])
def test_reminder_dates_from_the_owners_words(graph, monkeypatch, words, expected):
    p = _plan(monkeypatch, f"Remind me to pay the deposit {words}",
              XStep(tool="create_reminder", title="Pay deposit", date_text=words))
    assert p.calls[0].args.date == expected.isoformat()


def test_bare_day_month_date_text(graph, monkeypatch):
    """qwen2.5:3b sends the owner's "5 November" as date_text without "on"; it resolved to nothing and the plan
    warned "Couldn't tell when" next to a reminder it had dated from a junk duplicate step (live gate check)."""
    d = TODAY + timedelta(days=40)
    for date_text in (f"{d.day} {d:%B}", f"{d.day}th of {d:%b}"):
        instruction = f"Remind me to pay rent on {date_text}"
        p = _plan(monkeypatch, instruction, XStep(tool="create_reminder", title="Pay rent", date_text=date_text))
        assert [c.args.date for c in p.calls] == [d.isoformat()] and p.warnings == [], date_text
    # the model repeating the step (2-3 times on real runs) still plans one reminder
    twice = XStep(tool="create_reminder", title="Pay rent", date_text=f"{d.day} {d:%B}")
    assert len(_plan(monkeypatch, f"Remind me to pay rent on {d.day} {d:%B}", twice, twice, twice).calls) == 1
    step = XStep(tool="create_reminder", title="Pay rent", date_text=f"3 days after {d.day} {d:%B}")
    assert planner.resolve_date(step, f"Remind me 3 days after {d.day} {d:%B}", TODAY, {}) == d.isoformat()


def test_a_date_fact_is_used_only_when_the_instruction_talks_about_it(graph, monkeypatch):
    # a real run counted "next week" back from the agreement's end
    p = _plan(monkeypatch, "Remind me to pay the security deposit next week",
              XStep(tool="create_reminder", title="Pay deposit", date_text="next week",
                    relative_to="agreement_end_date", days_before=7))
    assert p.calls[0].args.date == (TODAY + timedelta(days=7)).isoformat()
    assert "days before" not in p.calls[0].preview


def test_steps_for_tools_the_instruction_does_not_ask_for_are_dropped(graph, monkeypatch):
    # a real run added a save_note after an email, and an email after a form
    p = _plan(monkeypatch, "Email my landlord asking to keep the rent at 14,500",
              XStep(tool="draft_email", recipient="my landlord", subject="Rent", body="Could we keep it at 14500?"),
              XStep(tool="save_note", title="Rent inquiry", markdown="Asked about the rent."))
    assert [c.tool for c in p.calls] == ["draft_email"]
    p = _plan(monkeypatch, "Fill the rental form", XStep(tool="fill_rental_form", form_fields=["full_name"]),
              XStep(tool="draft_email", recipient="my landlord", subject="Form", body="Here it is."))
    assert [c.tool for c in p.calls] == ["fill_rental_form"]


def test_the_models_own_date_is_ignored(graph, monkeypatch):
    p = _plan(monkeypatch, "Remind me to call the bank",
              XStep(tool="create_reminder", title="Call bank", date_text="2027-02-03"))  # not in the instruction
    assert p.calls == [] and "Couldn't tell when" in p.warnings[0]


def test_a_past_date_is_left_out(graph, monkeypatch):
    past = TODAY - timedelta(days=3)
    p = _plan(monkeypatch, f"Remind me on {past.day} {past:%B %Y}",
              XStep(tool="create_reminder", title="x", date_text=f"on {past.day} {past:%B %Y}"))
    assert p.calls == [] and "is not in the future" in p.warnings[0]


# --- proofs, forms, notes ------------------------------------------------------------------------------------


def test_the_newest_answered_proof_is_attached_when_asked(graph, monkeypatch):
    _proof("rq_old", decided_at="2026-09-01T10:00:00Z")
    _proof("rq_new")
    _proof("rq_declined", answer_type="DECLINED", decided_at="2026-09-30T11:00:00Z")
    p = _plan(monkeypatch, "Send my landlord the income proof",
              XStep(tool="draft_email", recipient="my landlord", subject="Proof", body="Attached."))
    assert [a.request_id for a in p.calls[0].args.attachments] == ["rq_new"]
    assert "+ 1 proof attachment" in p.calls[0].preview


def test_no_proof_yet_is_a_warning_not_a_failure(graph, monkeypatch):
    p = _plan(monkeypatch, "Email my landlord the proof",
              XStep(tool="draft_email", recipient="my landlord", subject="Proof", body="Hi", attach_proof=True))
    assert p.calls[0].args.attachments == [] and "No answered proof" in p.warnings[0]


def test_email_stating_a_disclosable_value_is_flagged(graph, monkeypatch):
    p = _plan(monkeypatch, "Email my landlord my salary",
              XStep(tool="draft_email", recipient="my landlord", subject="Salary", body="I earn Rs 62,000 a month."))
    assert len(p.calls) == 1 and "states your monthly income" in p.warnings[0]


def test_rental_form_values_come_from_document_facts_only(graph, monkeypatch):
    p = _plan(monkeypatch, "Fill the rental form",
              XStep(tool="fill_rental_form", form_fields=["full_name", "employer", "monthly_income",
                                                          "date_of_birth", "made_up_field"]))
    [call] = p.calls
    assert call.args.fields == {"full_name": config.OWNER_NAME, "employer": "Nimbus Analytics",
                                "monthly_income": "₹62,000"}
    assert "No date of birth in your documents" in p.warnings[0]


def test_save_note_and_empty_plans(graph, monkeypatch):
    p = _plan(monkeypatch, "Note that I called the landlord",
              XStep(tool="save_note", title="Called landlord", markdown="Called Ravi about the renewal."))
    assert p.calls[0].args.title == "Called landlord" and p.calls[0].preview.startswith("Save note “Called landlord”")
    assert "Couldn't turn that" in _plan(monkeypatch, "What's the weather?").warnings[0]


def test_every_kept_call_passes_the_executors_checks(graph, monkeypatch):
    _proof()
    p = _plan(monkeypatch, "Email my landlord the proof and remind me before the agreement ends",
              XStep(tool="draft_email", recipient="my landlord", subject="Proof", body="Hi", attach_proof=True),
              XStep(tool="create_reminder", title="Agreement", relative_to="agreement_end_date", days_before=7))
    assert [c.tool for c in p.calls] == ["draft_email", "create_reminder"]
    assert all(executor.validate_call(c, p.instruction) is None for c in p.calls)


def test_context_never_offers_disclosable_values_or_owner_stated_facts(graph):
    ctx = planner._context(TODAY.isoformat())
    assert "Ravi Kumar (landlord)" in ctx and "Nimbus Analytics (employer)" in ctx
    assert f"agreement_end_date = {END}" in ctx
    assert "62000" not in ctx and "1500" not in ctx
    assert "ravi.landlord@example.com" not in ctx  # addresses are resolved in code, never by the model


# --- real model ----------------------------------------------------------------------------------------------


@pytest.mark.llm
def test_real_model_plans_the_demo_task(graph):
    p = planner.plan("Email my landlord that I'd like to renew the agreement, and remind me a week before it ends.")
    print(f"\n[planner llm] calls={[c.tool for c in p.calls]} warnings={p.warnings}")
    for c in p.calls:
        print(f"  {ascii(c.preview)}")
    assert [c.tool for c in p.calls] == ["draft_email", "create_reminder"]
    assert p.calls[0].args.to == "ravi.landlord@example.com"
    assert p.calls[1].args.date == (date.fromisoformat(END) - timedelta(days=7)).isoformat()
    assert all(executor.validate_call(c, p.instruction) is None for c in p.calls)
