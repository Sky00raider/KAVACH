"""Entities and graph (BRAIN step 6): names, grounding, dedupe, links, ingest wiring, question matching,
graph-neighbour retrieval in chat; llm tests on the demo notes (precision, drop-to-ingested time)."""

from __future__ import annotations

import json
import shutil
import time
from types import SimpleNamespace

import numpy as np
import pytest
from test_ingest import _make_pdf, vault  # noqa: F401  (fixture)

from kavach import config, db
from kavach.brain import budget, chat, embed, entities, ingest, llm
from kavach.models import OWNER_ENTITY_ID, ScoredChunk, SignatureResult
from kavach.trust import issuer_check

E = entities


def X(**lists):
    return E.Extraction(**lists)


@pytest.fixture
def extractor(monkeypatch):
    """`entities.extract` answers from `table` ({substring of the chunk: Extraction}), else nothing."""
    state = SimpleNamespace(table={}, calls=[])

    def fake(text):
        state.calls.append(text)
        return next((x for key, x in state.table.items() if key in text), X())

    monkeypatch.setattr(E, "extract", fake)
    return state


def _entities():
    return db.fetch_all("SELECT * FROM entities ORDER BY rowid")


def _edges(current=True):
    return db.fetch_all("SELECT * FROM edges" + (" WHERE valid_to IS NULL" if current else "") + " ORDER BY rowid")


def _by_name(name):
    rows = db.entities_by_norm([E.normalise_name(name)])
    assert len(rows) == 1, rows
    return rows[0]


def _write(root, rel, text):
    path = root / rel
    path.write_text(text, encoding="utf-8")
    return path


# --- names -------------------------------------------------------------------------------------------------


def test_normalise_name_strips_honorifics_case_and_punctuation():
    assert E.normalise_name("Mr. Ravi  Kumar") == "ravi kumar"
    assert E.normalise_name("Smt. Dr. Lakshmi") == "lakshmi"
    assert E.normalise_name("flat_move-2026") == "flat move 2026"
    assert E.normalise_name("Dr") == "dr"  # a bare honorific is a name, not nothing
    assert E.normalise_name("Ravi's") == "ravi s"


def test_owner_aliases(monkeypatch):
    for name in ("I", "me", "My", "Ananya", "Ananya Iyer", "Ms. Ananya Iyer", "the owner"):
        assert E.is_owner(name), name
    for name in ("Ravi", "Ananya Rao Kumar", "Iyer"):
        assert not E.is_owner(name), name
    monkeypatch.setattr(config, "OWNER_NAME", "Kiran Shah")
    assert E.is_owner("Kiran") and not E.is_owner("Ananya")


def test_note_title():
    assert E.note_title("notes/flat_move_2026.md") == "Flat move 2026"
    assert E.note_title("notes/budget.md") == "Budget"


def test_decision_name_from_quote():
    assert E.decision_name("Decision: I decided to renew only if rent stays under ₹15,000.") == \
        "I decided to renew only if rent stays under ₹15,000"
    long = E.decision_name("we will " + "keep saving money " * 10 + ".")
    assert len(long) <= E.DECISION_NAME_CHARS + 1 and long.endswith("…") and long.startswith("We will keep")


# --- grounding ---------------------------------------------------------------------------------------------

DECISION_TEXT = ("Date: 18 September 2026\n\nDecision: I decided to renew only if rent stays under ₹15,000.\n"
                 "Project: [[Flat move 2026]]. Topic: rent renewal. Landlord is Mr. Ravi, 98450 12345, "
                 "ravi@example.com.")


def test_ground_keeps_named_entities_and_drops_invented_ones():
    g = E.ground(X(people=["Ravi", "Arjun Rao", "I", "Ananya"], projects=["Flat move 2026"], organisations=[""]),
                 DECISION_TEXT)
    assert {k: v["type"] for k, v in g.entities.items()} == {"ravi": "PERSON", "flat move 2026": "PROJECT"}
    assert g.dropped == [("Arjun Rao", "name not in text"), ("", "empty name")]
    assert g.owner_mentions == 2


def test_ground_a_name_listed_twice_takes_the_first_type_in_priority_order():
    g = E.ground(X(topics=["rent renewal", "Ravi"], obligations=["Rent renewal"], people=["Mr. Ravi"]), DECISION_TEXT)
    assert {k: (v["type"], v["name"]) for k, v in g.entities.items()} == {
        "ravi": ("PERSON", "Mr. Ravi"), "rent renewal": ("OBLIGATION", "Rent renewal")}


def test_ground_decision_needs_exact_quote_and_grounded_date():
    ok = {"quote": "decision: i decided to renew only if rent  stays under ₹15,000.", "date": "2026-09-18"}
    g = E.ground(X(decisions=[ok], projects=["Flat move 2026"], topics=["rent renewal"], people=["Ravi"]),
                 DECISION_TEXT)
    name = "I decided to renew only if rent stays under ₹15,000"
    d = E.normalise_name(name)
    assert g.entities[d] == {"type": "DECISION", "name": name, "attrs": {
        "quote": "decision: i decided to renew only if rent stays under ₹15,000.", "date": "2026-09-18"}}
    assert g.relations == [(E.OWNER, "DECIDED", d, "2026-09-18"), (d, "PART_OF", "flat move 2026", "2026-09-18"),
                           (d, "ABOUT", "rent renewal", "2026-09-18")]  # nothing to Ravi (a PERSON)
    bad = [
        {**ok, "quote": "I decided to move out."},       # not in the text
        {**ok, "quote": "  "},
        {**ok, "date": None},
        {**ok, "date": "18 September 2026"},             # not ISO
        {**ok, "date": "2026-09-19"},                    # day not in the text
        {**ok, "date": "2025-09-18"},                    # year not in the text
    ]
    for b in bad:
        g = E.ground(X(decisions=[b]), DECISION_TEXT)
        assert not g.entities and len(g.dropped) == 1, b


def test_ground_decision_must_state_a_choice():
    text = ("18/09/26, 19:46 - Duk: Is the rent still the same?\n18/09/26, 19:48 - Ravi: Yes, the current rent is "
            "14500.\n21/09/26, 11:18 - Ravi: I'll send them tonight.\n20/09/26, 18:21 - Duk: I will probably renew "
            "if the rent stays below 15000.\n18/09/26, 19:50 - Duk: I decided to wait.")
    quotes = {"Is the rent still the same?": "2026-09-18", "Yes, the current rent is 14500.": "2026-09-18",
              "I'll send them tonight.": "2026-09-21", "I will probably renew if the rent stays below 15000.":
              "2026-09-20", "I decided to wait.": "2026-09-18"}
    g = E.ground(X(decisions=[{"quote": q, "date": d} for q, d in quotes.items()]), text)
    assert sorted(v["attrs"]["quote"] for v in g.entities.values()) == [
        "I decided to wait.", "I will probably renew if the rent stays below 15000."]
    assert [r for _, r in g.dropped] == ["decision states no choice"] * 3


def test_ground_decision_date_from_a_whatsapp_timestamp():
    text = "20/09/26, 18:21 - Duk: I will probably renew if the rent stays below 15000."
    d = {"quote": "I will probably renew if the rent stays below 15000.", "date": "2026-09-20"}
    assert E.ground(X(decisions=[d]), text).entities


def test_ground_contacts_only_when_in_text_and_on_a_kept_name():
    g = E.ground(X(people=["Mr. Ravi"], organisations=["Rent"],
                   contacts=[{"name": "Ravi", "phone": "+91 98450-12345", "email": "ravi@example.com"},
                             {"name": "Rent", "phone": "99999 00000", "email": "x@y.com"},
                             {"name": "Arjun", "email": "ravi@example.com"}]), DECISION_TEXT)
    assert g.entities["ravi"]["attrs"] == {"phone": "+91 98450-12345", "email": "ravi@example.com"}
    assert g.entities["rent"]["attrs"] == {} and "arjun" not in g.entities


def test_ground_relations_resolve_names_and_the_owner():
    g = E.ground(X(people=["Mr. Ravi"], projects=["Flat move 2026"],
                   relations=[{"src": "Ravi", "rel": "LANDLORD_OF", "dst": "I"},
                              {"src": "Ananya", "rel": "WORKS_ON", "dst": "flat move 2026"},
                              {"src": "Ravi", "rel": "LANDLORD_OF", "dst": "me"},              # duplicate
                              {"src": "Ravi", "rel": "RELATES_TO", "dst": "Arjun"},            # unknown end
                              {"src": "Ravi", "rel": "RELATES_TO", "dst": "Ravi"},             # self
                              {"src": "Ravi", "rel": "MENTIONED_IN", "dst": "Flat move 2026"},  # links only
                              {"src": "I", "rel": "DECIDED", "dst": "Flat move 2026"},         # code only
                              {"src": "Ravi", "rel": "WORKS_ON", "dst": "Flat move 2026"},     # not one sentence
                              {"src": "Ravi", "rel": "PART_OF", "dst": "Flat move 2026"}]),   # PERSON PART_OF
                 DECISION_TEXT)
    assert g.relations == [("ravi", "LANDLORD_OF", E.OWNER, None), (E.OWNER, "WORKS_ON", "flat move 2026", None)]
    assert g.relations_dropped == 7


# --- graph writes through ingest ---------------------------------------------------------------------------


def test_owner_named_in_a_note_and_a_pdf_is_one_entity(vault, extractor):
    extractor.table["Ananya went"] = X(people=["Ananya"], organisations=["Mock Bank"],
                                       relations=[{"src": "Ananya", "rel": "BANKS_WITH", "dst": "Mock Bank"}])
    extractor.table["Ananya Iyer"] = X(people=["Ananya Iyer"], organisations=["Mock Bank"],
                                       relations=[{"src": "Ananya Iyer", "rel": "BANKS_WITH", "dst": "Mock Bank"}])
    ingest.ingest_file(_write(vault.root, "notes/bank.md", "Ananya went to Mock Bank today."))
    pdf = vault.root / "pdfs" / "statement.pdf"
    _make_pdf(pdf, ["Mock Bank statement of account. Customer: Ananya Iyer. Salary credit 62,000."])
    ingest.ingest_file(pdf)

    owners = [r for r in _entities() if r["norm_name"] in ("ananya", "ananya iyer")]
    assert [(r["entity_id"], r["type"], r["name"]) for r in owners] == [(OWNER_ENTITY_ID, "PERSON", "Ananya Iyer")]
    bank = _by_name("Mock Bank")
    banks_with = [(e["src"], e["dst"]) for e in _edges() if e["rel"] == "BANKS_WITH"]
    assert banks_with == [(OWNER_ENTITY_ID, bank["entity_id"])] * 2  # one per document (provenance)


def test_same_person_across_notes_merges_attrs_and_keeps_the_first_type(vault, extractor):
    extractor.table["landlord is Mr. Ravi"] = X(people=["Mr. Ravi"], contacts=[{"name": "Ravi", "phone": "98450 12345"}])
    extractor.table["Ravi's email"] = X(organisations=["Ravi"], contacts=[
        {"name": "Ravi", "email": "ravi@example.com", "phone": "11111 22222"}])
    first = ingest.ingest_file(_write(vault.root, "notes/a.md", "My landlord is Mr. Ravi, 98450 12345."))
    second = ingest.ingest_file(_write(vault.root, "notes/b.md", "Ravi's email is ravi@example.com, 11111 22222."))
    ravi = _by_name("Ravi")
    assert (ravi["name"], ravi["type"]) == ("Ravi", "PERSON")  # the variant without an honorific is shown
    assert json.loads(ravi["attrs_json"]) == {"phone": "98450 12345", "email": "ravi@example.com"}  # first value wins
    assert first.entities_added == 2 and second.entities_added == 1  # Ravi + a DOCUMENT, then only b's DOCUMENT


def test_links_become_mentioned_in_edges_to_the_note(vault, extractor):
    res = ingest.ingest_file(_write(vault.root, "notes/budget.md",
                                    "# Budget\nKeep rent low.\n\nRelated:\n- [[Rent renewal]]\n- [[Flat move 2026|flat]]"
                                    "\n- [[Rent renewal#Notes]]\n- [[Budget]]"))
    doc = _by_name("Budget")
    assert doc["type"] == "DOCUMENT" and json.loads(doc["attrs_json"]) == {"path": "notes/budget.md"}
    renewal, flat = _by_name("Rent renewal"), _by_name("Flat move 2026")
    assert (renewal["type"], json.loads(renewal["attrs_json"])) == ("CONCEPT", {"origin": "link"})
    chunk_id = db.fetch_one("SELECT chunk_id FROM chunks WHERE doc_id = ?", (res.doc_id,))["chunk_id"]
    assert [(e["src"], e["rel"], e["dst"], e["source_chunk_id"], e["valid_from"]) for e in _edges()] == [
        (renewal["entity_id"], "MENTIONED_IN", doc["entity_id"], chunk_id, None),
        (flat["entity_id"], "MENTIONED_IN", doc["entity_id"], chunk_id, None),  # self-link [[Budget]] skipped
        # the "Related:" list, from the note itself (no "Project:" line)
        (doc["entity_id"], "RELATES_TO", renewal["entity_id"], chunk_id, None),
        (doc["entity_id"], "RELATES_TO", flat["entity_id"], chunk_id, None)]
    assert res.entities_added == 3


@pytest.mark.parametrize("order", ["short first", "full first"])
def test_first_name_person_merges_into_the_full_name(vault, extractor, order):
    extractor.table["Talk to Ravi"] = X(people=["Ravi"], projects=["Flat move 2026"],
                                        contacts=[{"name": "Ravi", "phone": "98450 12345"}],
                                        relations=[{"src": "Ravi", "rel": "WORKS_ON", "dst": "Flat move 2026"}])
    extractor.table["Ravi Kumar"] = X(people=["Ravi Kumar", "Ravi"], contacts=[{"name": "Ravi Kumar",
                                                                               "email": "ravi@example.com"}],
                                      relations=[{"src": "Ravi Kumar", "rel": "LANDLORD_OF", "dst": "I"},
                                                 {"src": "Ravi", "rel": "RELATES_TO", "dst": "Ravi Kumar"}])
    notes = [("notes/flat.md", "Talk to Ravi about Flat move 2026, 98450 12345."),
             ("notes/landlord.md", "The landlord is Ravi Kumar (ravi@example.com). Ravi lives upstairs.")]
    added = [ingest.ingest_file(_write(vault.root, *n)).entities_added for n in
             (notes if order == "short first" else notes[::-1])]

    persons = [r for r in _entities() if r["type"] == "PERSON" and r["entity_id"] != OWNER_ENTITY_ID]
    assert [r["name"] for r in persons] == ["Ravi Kumar"]
    kumar = persons[0]
    assert json.loads(kumar["attrs_json"]) == {"phone": "98450 12345", "email": "ravi@example.com"}
    flat = next(r for r in _entities() if r["type"] == "PROJECT")
    assert {(e["src"], e["rel"], e["dst"]) for e in _edges() if e["rel"] != "MENTIONED_IN"} == {
        (kumar["entity_id"], "WORKS_ON", flat["entity_id"]), (kumar["entity_id"], "LANDLORD_OF", OWNER_ENTITY_ID)}
    assert not [e for e in _edges() if e["src"] == e["dst"]]  # "Ravi RELATES_TO Ravi Kumar" became a loop: closed
    assert sum(added) == len(_entities()) - 1  # net growth, the owner row aside


def test_first_name_merge_needs_exactly_one_candidate_and_never_the_owner(fresh_db):
    E.ensure_owner()
    ravi, _ = E.upsert("PERSON", "Ravi")
    E.upsert("PERSON", "Ravi Kumar")
    E.upsert("PERSON", "Ravi Shah")
    db.insert("entities", {"entity_id": "e_ananya0001", "type": "PERSON", "name": "Ananya", "norm_name": "ananya",
                           "attrs_json": "{}"})  # cannot come from extraction (it resolves to e_owner); still unmerged
    sita, _ = E.upsert("ORG", "Sita")
    E.upsert("PERSON", "Sita Rao")  # only a PERSON is folded
    assert E.merge_first_names() == 0
    assert {r["entity_id"] for r in _entities()} >= {ravi, "e_ananya0001", sita, OWNER_ENTITY_ID}


# --- graph cleanup: role words, display names, company suffixes, relation grounding, contact lines ---------

STATEMENT = ("Statement of Account Mock Bank of India Account holder: Ananya Iyer Date Description Debit Credit "
             "2026-06-01 SALARY CREDIT Nimbus Analytics Pvt L 62,000.00 80,250.00 2026-06-05 UPI/RENT/RAVI KUMAR "
             "14,500.00 65,750.00")


def test_role_words_are_not_entities():
    for name in ("Landlord", "the landlord", "My Employer", "bank", "Tenant", "account holder"):
        assert E.is_role_word(name), name
    for name in ("Ravi Kumar", "Mock Bank", "Landlord Kumar"):
        assert not E.is_role_word(name), name
    text = "Tenant: Ananya Iyer. Landlord: Ravi Kumar. My bank is Mock Bank. Landlord notes."
    g = E.ground(X(people=["Landlord", "Tenant", "Ravi Kumar"], organisations=["Bank", "Mock Bank"],
                   documents=["Landlord"]), text)
    assert {k: v["type"] for k, v in g.entities.items()} == {
        "ravi kumar": "PERSON", "mock bank": "ORG", "landlord": "DOCUMENT"}  # a note may be called Landlord
    assert [r for _, r in g.dropped] == ["role word, not a name"] * 3


def test_a_role_next_to_a_name_becomes_the_relation():
    cases = [
        ("The landlord is Ravi Kumar.", X(people=["Ravi Kumar"]), ("ravi kumar", "LANDLORD_OF", E.OWNER)),
        ("Landlord: Ravi Kumar Property: Flat 402", X(people=["Ravi Kumar"]), ("ravi kumar", "LANDLORD_OF", E.OWNER)),
        ("My landlord Ravi lives upstairs.", X(people=["Ravi"]), ("ravi", "LANDLORD_OF", E.OWNER)),
        ("My employer is Nimbus Analytics Pvt Ltd.", X(organisations=["Nimbus Analytics Pvt Ltd"]),
         (E.OWNER, "EMPLOYED_BY", "nimbus analytics")),
        ("Bank: Mock Bank of India", X(organisations=["Mock Bank of India"]), (E.OWNER, "BANKS_WITH", "mock bank of india")),
    ]
    for text, x, want in cases:
        assert [r[:3] for r in E.ground(x, text).relations] == [want], text
    none = [
        ("Tenant: Priya Landlord: Ravi Kumar", X(people=["Priya"])),        # the name must follow the role
        ("Landlord email: ravi@example.com", X(people=["Ravi"])),           # role and name in different places
        ("The landlord is Mock Bank.", X(organisations=["Mock Bank"])),    # LANDLORD_OF needs a PERSON
        ("The landlord.\nRavi Kumar called.", X(people=["Ravi Kumar"])),    # not one line
    ]
    for text, x in none:
        assert E.ground(x, text).relations == [], text


def test_display_names():
    assert E.display_name("PERSON", "RAVI KUMAR") == "Ravi Kumar"
    assert E.display_name("PERSON", "D'SOUZA  ANIL") == "D'Souza Anil"
    assert E.display_name("ORG", "NIMBUS ANALYTICS PVT LTD") == "Nimbus Analytics Pvt Ltd"
    assert E.display_name("ORG", "HDFC BANK") == "HDFC Bank"
    assert E.display_name("ORG", "SBI") == "SBI"
    assert E.display_name("ORG", "Nimbus Analytics Pvt L") == "Nimbus Analytics"
    assert E.display_name("ORG", "Nimbus Analytics Pvt") == "Nimbus Analytics"
    assert E.display_name("ORG", "Nimbus Analytics Pvt Ltd") == "Nimbus Analytics Pvt Ltd"
    assert E.display_name("CONCEPT", "EMI") == "EMI"
    assert E.display_name("PERSON", "Ravi kumar") == "Ravi kumar"   # not all caps: left as written
    assert E.display_name("DECISION", "I WILL RENEW IF RENT STAYS LOW") == "I WILL RENEW IF RENT STAYS LOW"
    assert E.better_name("RAVI KUMAR", "Ravi Kumar") == "Ravi Kumar"
    assert E.better_name("Ravi Kumar", "RAVI KUMAR") == "Ravi Kumar"
    assert E.better_name("rent renewal", "Rent renewal") == "Rent renewal"
    assert E.better_name("Mr. Ravi", "Ravi") == "Ravi"
    assert E.better_name("Nimbus Analytics", "Nimbus Analytics Pvt Ltd") == "Nimbus Analytics Pvt Ltd"


def test_company_suffixes_match_for_dedupe():
    for name in ("Nimbus Analytics Pvt L", "Nimbus Analytics Pvt. Ltd.", "NIMBUS ANALYTICS PRIVATE LIMITED",
                 "Nimbus Analytics Ltd", "Nimbus Analytics (P) Ltd", "Nimbus Analytics LLP"):
        assert E.normalise_name(name) == "nimbus analytics", name
    assert E.normalise_name("Mock Bank of India") == "mock bank of india"
    assert E.normalise_name("Ltd") == "ltd"
    g = E.ground(X(organisations=["Nimbus Analytics Pvt L"]), STATEMENT)
    assert g.entities["nimbus analytics"]["name"] == "Nimbus Analytics"


@pytest.mark.parametrize("order", ["caps first", "cased first"])
def test_variants_merge_into_the_best_display_name(fresh_db, order):
    E.ensure_owner()
    people = ["RAVI KUMAR", "Ravi Kumar"] if order == "caps first" else ["Ravi Kumar", "RAVI KUMAR"]
    orgs = ["Nimbus Analytics", "Nimbus Analytics Pvt Ltd"] if order == "caps first" else \
        ["Nimbus Analytics Pvt Ltd", "Nimbus Analytics"]
    ids = {E.upsert("PERSON", n)[0] for n in people} | {E.upsert("ORG", n)[0] for n in orgs}
    assert len(ids) == 2
    assert sorted(r["name"] for r in _entities() if r["entity_id"] != OWNER_ENTITY_ID) == [
        "Nimbus Analytics Pvt Ltd", "Ravi Kumar"]


def test_statement_caps_and_note_name_are_one_person_named_properly(vault, extractor):
    extractor.table["UPI/RENT"] = X(people=["RAVI KUMAR"], organisations=["Nimbus Analytics Pvt L"])
    extractor.table["landlord is"] = X(people=["Ravi Kumar"])
    pdf = vault.root / "pdfs" / "statement.pdf"
    _make_pdf(pdf, [STATEMENT])
    ingest.ingest_file(pdf)
    ingest.ingest_file(_write(vault.root, "notes/landlord.md", "The landlord is Ravi Kumar."))
    kumar = _by_name("Ravi Kumar")
    assert (kumar["name"], kumar["type"]) == ("Ravi Kumar", "PERSON")
    assert _by_name("Nimbus Analytics Pvt Ltd")["name"] == "Nimbus Analytics"
    assert [(e["src"], e["rel"], e["dst"]) for e in _edges() if e["rel"] == "LANDLORD_OF"] == [
        (kumar["entity_id"], "LANDLORD_OF", OWNER_ENTITY_ID)]


def test_segments_split_lines_sentences_and_dated_rows():
    assert E.segments("Mr. Ravi called. He said Rs. 500 is due!\nNext line; more") == [
        " mr ravi called ", " he said rs 500 is due ", " next line ", " more "]
    rows = E.segments(STATEMENT)
    assert " 2026 06 01 salary credit nimbus analytics pvt l 62 000 00 80 250 00 " in rows
    assert " 2026 06 05 upi rent ravi kumar 14 500 00 65 750 00 " in rows
    chat = E.segments("18/09/26, 19:42 - Ananya: Hi Ravi 18/09/26, 19:44 - Ravi Kumar: Yes")
    assert chat == [" 18 09 26 19 42 ananya hi ravi ", " 18 09 26 19 44 ravi kumar yes "]


def test_relation_needs_both_names_in_one_line_or_sentence():
    x = X(people=["RAVI KUMAR"], organisations=["Nimbus Analytics Pvt L"],
          relations=[{"src": "RAVI KUMAR", "rel": "STUDIED_AT", "dst": "Nimbus Analytics Pvt L"},   # other row
                     {"src": "I", "rel": "EMPLOYED_BY", "dst": "Nimbus Analytics Pvt L"},          # owner implicit
                     {"src": "I", "rel": "PAID", "dst": "RAVI KUMAR"}])
    g = E.ground(x, STATEMENT)
    assert [r[:3] for r in g.relations] == [(E.OWNER, "EMPLOYED_BY", "nimbus analytics"), (E.OWNER, "PAID", "ravi kumar")]
    assert g.relations_dropped == 1
    one_line = "2026-06-01 SALARY CREDIT Nimbus Analytics Pvt L RAVI KUMAR"
    assert [r[1] for r in E.ground(x, one_line).relations][0] == "STUDIED_AT"   # same row: type-valid, kept


def test_relation_types_follow_the_domain_range_table():
    ok = [("LANDLORD_OF", "PERSON", "PERSON"), ("EMPLOYED_BY", "PERSON", "ORG"), ("STUDIED_AT", "PERSON", "ORG"),
          ("BANKS_WITH", "PERSON", "ORG"), ("WORKS_ON", "PERSON", "PROJECT"), ("PART_OF", "CONCEPT", "PROJECT"),
          ("PART_OF", "DECISION", "PROJECT"), ("ABOUT", "DECISION", "CONCEPT"), ("PAID", "PERSON", "OBLIGATION"),
          ("PARTY_TO", "PERSON", "DOCUMENT"), ("DUE_ON", "OBLIGATION", "EVENT"), ("RELATES_TO", "PLACE", "PERSON"),
          ("MENTIONED_IN", "PERSON", "DOCUMENT"), ("DECIDED", "PERSON", "DECISION")]
    bad = [("LANDLORD_OF", "PERSON", "PROJECT"), ("LANDLORD_OF", "PERSON", "PLACE"), ("STUDIED_AT", "ORG", "PERSON"),
           ("EMPLOYED_BY", "PERSON", "PERSON"), ("PART_OF", "PERSON", "CONCEPT"), ("PART_OF", "PROJECT", "DOCUMENT"),
           ("ABOUT", "PLACE", "PERSON"), ("MENTIONED_IN", "PERSON", "PROJECT"), ("WORKS_ON", "PERSON", "CONCEPT")]
    assert all(E.relation_allowed(*r) for r in ok)
    assert not any(E.relation_allowed(*r) for r in bad)
    text = "Ravi Kumar, Flat 402, Flat move 2026 and flat agreement, all in one line."
    x = X(people=["Ravi Kumar"], places=["Flat 402"], projects=["Flat move 2026"], topics=["flat agreement"],
          relations=[{"src": "Ravi Kumar", "rel": "LANDLORD_OF", "dst": "Flat move 2026"},
                     {"src": "I", "rel": "LANDLORD_OF", "dst": "Flat 402"},
                     {"src": "Ravi Kumar", "rel": "PART_OF", "dst": "flat agreement"},
                     {"src": "Flat 402", "rel": "ABOUT", "dst": "Ravi Kumar"},
                     {"src": "flat agreement", "rel": "PART_OF", "dst": "Flat move 2026"}])
    g = E.ground(x, text)
    assert [r[:3] for r in g.relations] == [("flat agreement", "PART_OF", "flat move 2026")]
    assert g.relations_dropped == 4


def test_stored_type_is_checked_again_after_dedupe(vault, extractor):
    extractor.table["topic"] = X(topics=["Mock Bank"])               # stored first as a CONCEPT
    extractor.table["account"] = X(organisations=["Mock Bank"],
                                   relations=[{"src": "I", "rel": "BANKS_WITH", "dst": "Mock Bank"}])
    ingest.ingest_file(_write(vault.root, "notes/a.md", "A topic: Mock Bank."))
    ingest.ingest_file(_write(vault.root, "notes/b.md", "My account is at Mock Bank."))
    assert _by_name("Mock Bank")["type"] == "CONCEPT"
    assert not [e for e in _edges() if e["rel"] == "BANKS_WITH"]   # BANKS_WITH needs an ORG


def test_decision_quote_is_never_a_contact_line():
    text = ("22/09/26, 20:08 - Ravi Kumar: My email is ravi.landlord@example.com if you need to send the paperwork.\n"
            "22/09/26, 20:09 - Ravi Kumar: Call me on 98450 12345 if the rent changes.\n"
            "22/09/26, 20:10 - Ananya: Email: ananya@example.com, I decided to keep it.\n"
            "22/09/26, 20:11 - Ananya: Phone no: 080-2345 6789 if you decide to call.\n"
            "22/09/26, 20:12 - Ananya: I decided to renew only if rent stays under ₹15,000 from 2026-01-01.")
    quotes = [line.split(": ", 1)[1] for line in text.splitlines()]
    g = E.ground(X(decisions=[{"quote": q, "date": "2026-09-22"} for q in quotes]), text)
    assert [v["attrs"]["quote"] for v in g.entities.values()] == [quotes[-1]]
    assert [r for _, r in g.dropped] == ["decision quote is a contact line"] * 4
    assert not E.is_contact_line("Pay 1,20,000 by 2026-12-31 if the loan comes through.")


# --- structural edges from the owner's note layout; no PAID from bank statements ---------------------------


def test_note_structure_reads_project_and_related():
    demo = ("# Rent Renewal Decision\nDecision: renew. See [[Budget]] first.\n\nProject: [[Flat move 2026]]\n\n"
            "Related:\n- [[Budget]]\n- [[Landlord|my landlord]]\n* [[Rent renewal#Notes]]\n")
    assert ingest.note_structure(demo) == ("Flat move 2026", ["Budget", "Landlord", "Rent renewal"])
    assert ingest.note_structure("Related: [[A]], [[B]]\n[[C]]\n\n[[D]]") == (None, ["A", "B", "C"])
    assert ingest.note_structure("## Related\n1. [[A]]\n2. [[B]]\nSome prose with [[C]].\n- [[D]]") == (None, ["A", "B"])
    assert ingest.note_structure("- Project: [[X]] and [[Y]]\nProject: [[Z]]") == ("X", [])
    assert ingest.note_structure("Related to the rent: [[A]]\nProject: Flat move") == (None, [])


@pytest.fixture
def project_note(vault, extractor):
    extractor.table["Decided"] = X(people=["Ravi Kumar"])
    extractor.table["Project:"] = X(projects=["Flat move 2026"])       # chunk 2, where the name is
    filler = "Some thoughts about the flat and the budget. " * 16          # pushes the Related list to chunk 2
    text = (f"# Rent decision\nDecided to renew if rent stays low. Ravi Kumar agreed. See [[Budget note]].\n\n"
            f"{filler}\n\nProject: [[Flat move 2026]]\n\nRelated:\n- [[Landlord]]\n- [[Rent renewal]]\n- [[Ravi Kumar]]")
    res = ingest.ingest_file(_write(vault.root, "notes/rent_decision.md", text))
    chunks = db.fetch_all("SELECT chunk_id, text FROM chunks WHERE doc_id = ? ORDER BY rowid", (res.doc_id,))
    assert len(chunks) >= 2
    return SimpleNamespace(chunks=chunks, doc=_by_name("Rent decision"))


def test_project_note_links_become_part_of_the_project(project_note):
    flat = next(r for r in _entities() if r["type"] == "PROJECT")
    names = {r["entity_id"]: r["name"] for r in _entities()}
    chunk_of = lambda name: next(c["chunk_id"] for c in project_note.chunks if f"[[{name}]]" in c["text"])  # noqa: E731
    structural = [(names[e["src"]], e["rel"], names[e["dst"]], e["source_chunk_id"]) for e in _edges()
                  if e["rel"] in ("PART_OF", "RELATES_TO")]
    assert sorted(structural) == sorted([
        ("Budget note", "PART_OF", "Flat move 2026", chunk_of("Budget note")),
        ("Landlord", "PART_OF", "Flat move 2026", chunk_of("Landlord")),
        ("Rent renewal", "PART_OF", "Flat move 2026", chunk_of("Rent renewal")),
        ("Flat move 2026", "RELATES_TO", "Ravi Kumar", chunk_of("Ravi Kumar")),   # a PERSON is not PART_OF
    ])
    assert chunk_of("Budget note") != chunk_of("Rent renewal")
    assert not [e for e in _edges() if e["src"] == flat["entity_id"] and e["dst"] == flat["entity_id"]]


def test_a_link_spelling_the_name_better_renames_the_entity(fresh_db):
    budget, _ = E.upsert("CONCEPT", "budget")
    assert E.resolve_link("Budget") == (budget, False)
    assert _by_name("budget")["name"] == "Budget"
    assert E.resolve_link("BUDGET") == (budget, False) and _by_name("budget")["name"] == "Budget"


def test_bank_statement_paid_edges_are_dropped(vault, extractor):
    paid = [{"src": "I", "rel": "PAID", "dst": "Ravi Kumar"}]
    extractor.table["UPI/RENT"] = X(people=["Ravi Kumar"], relations=paid)
    extractor.table["Paid rent"] = X(people=["Ravi Kumar"], relations=paid)
    pdf = vault.root / "pdfs" / "statement.pdf"
    _make_pdf(pdf, ["Statement of Account Mock Bank 2026-06-05 UPI/RENT/Ravi Kumar 14,500.00"])
    stmt = ingest.ingest_file(pdf)
    assert db.fetch_one("SELECT doc_type FROM documents WHERE doc_id = ?", (stmt.doc_id,))["doc_type"] == "bank_statement"
    assert not [e for e in _edges() if e["rel"] == "PAID"]
    ingest.ingest_file(_write(vault.root, "notes/rent.md", "Paid rent to Ravi Kumar today."))
    assert [(e["src"], e["rel"]) for e in _edges() if e["rel"] == "PAID"] == [(OWNER_ENTITY_ID, "PAID")]


def test_link_placeholder_is_taken_over_by_the_extracted_entity(vault, extractor):
    ingest.ingest_file(_write(vault.root, "notes/budget.md", "Budget.\n[[Flat move 2026]]"))
    placeholder = _by_name("Flat move 2026")
    extractor.table["Project: Flat"] = X(projects=["Flat Move 2026"])
    ingest.ingest_file(_write(vault.root, "notes/flat_move_2026.md", "# Flat Move 2026\nProject: Flat Move 2026"))
    rows = db.entities_by_norm(["flat move 2026"])
    assert [(r["entity_id"], r["type"]) for r in rows] == [(placeholder["entity_id"], "PROJECT"),
                                                           (rows[1]["entity_id"], "DOCUMENT")]
    assert json.loads(rows[0]["attrs_json"]) == {}


def test_link_placeholder_becomes_the_note_when_nothing_is_extracted(vault, extractor):
    ingest.ingest_file(_write(vault.root, "notes/budget.md", "See [[Landlord]]."))
    placeholder = _by_name("Landlord")
    ingest.ingest_file(_write(vault.root, "notes/landlord.md", "The landlord is Ravi."))
    assert [(r["entity_id"], r["type"]) for r in db.entities_by_norm(["landlord"])] == [
        (placeholder["entity_id"], "DOCUMENT")]


def test_link_to_an_existing_entity_prefers_a_non_document(vault, extractor):
    extractor.table["Project: Flat"] = X(projects=["Flat move 2026"])
    ingest.ingest_file(_write(vault.root, "notes/flat_move_2026.md", "Project: Flat move 2026, see [[Flat move 2026]]"))
    project = next(r for r in db.entities_by_norm(["flat move 2026"]) if r["type"] == "PROJECT")
    doc = next(r for r in db.entities_by_norm(["flat move 2026"]) if r["type"] == "DOCUMENT")
    assert [(e["src"], e["rel"], e["dst"]) for e in _edges()] == [(project["entity_id"], "MENTIONED_IN", doc["entity_id"])]


def test_invalid_signature_documents_are_not_extracted(vault, extractor, monkeypatch):
    monkeypatch.setattr(issuer_check, "verify_pdf", lambda p: SignatureResult(status="invalid", iss="mock_bank"))
    extractor.table["Ananya"] = X(organisations=["Mock Bank"])
    pdf = vault.root / "pdfs" / "tampered.pdf"
    _make_pdf(pdf, ["Mock Bank statement. Customer: Ananya Iyer. Salary credit 92,000."])
    res = ingest.ingest_file(pdf)
    assert res.entities_added == 0 and extractor.calls == []
    assert _entities() == [] and _edges(current=False) == []


def test_document_that_turns_invalid_has_its_edges_closed_and_regains_them_when_valid(vault, extractor, monkeypatch):
    status = {"s": "issuer_signed"}
    monkeypatch.setattr(issuer_check, "verify_pdf", lambda p: SignatureResult(status=status["s"], iss="mock_bank"))
    extractor.table["Mock Bank"] = X(organisations=["Mock Bank"],
                                     relations=[{"src": "I", "rel": "BANKS_WITH", "dst": "Mock Bank"}])
    pdf = vault.root / "pdfs" / "statement.pdf"
    _make_pdf(pdf, ["Mock Bank statement. Customer: Ananya Iyer."])
    ingest.ingest_file(pdf)
    assert len(_edges()) == 1

    status["s"] = "invalid"  # e.g. the trust list changed; same text
    ingest.ingest_file(pdf)
    assert _edges() == [] and len(_edges(current=False)) == 1

    status["s"] = "issuer_signed"
    res = ingest.ingest_file(pdf)
    assert res.chunks_added == 1 and len(_edges()) == 1 and len(_edges(current=False)) == 2


def test_changed_file_closes_old_edges_and_adds_new_ones(vault, extractor):
    extractor.table["Ravi"] = X(people=["Ravi"], relations=[{"src": "Ravi", "rel": "LANDLORD_OF", "dst": "I"}])
    note = _write(vault.root, "notes/landlord.md", "The landlord is Ravi.")
    ingest.ingest_file(note)
    old = [e for e in _edges() if e["rel"] == "LANDLORD_OF"]
    _write(vault.root, "notes/landlord.md", "The landlord is Ravi. He lives upstairs.")
    ingest.ingest_file(note)
    closed = [e for e in _edges(current=False) if e["valid_to"] is not None]
    assert [e["edge_id"] for e in closed] == [e["edge_id"] for e in old]
    now = [e for e in _edges() if e["rel"] == "LANDLORD_OF"]
    assert len(now) == 1 and now[0]["source_chunk_id"] != old[0]["source_chunk_id"]
    assert len(db.entities_by_norm(["ravi"])) == 1


def test_ollama_down_keeps_ingest_and_links(vault, monkeypatch):
    calls = []

    def down(text):
        calls.append(text)
        raise llm.LLMError("connection refused")

    monkeypatch.setattr(E, "extract", down)
    monkeypatch.setattr(ingest, "chunk_text", lambda text: [text[:40], text[40:]])
    res = ingest.ingest_file(_write(vault.root, "notes/n.md", "A long enough note about the flat move, see "
                                                              "[[Flat move 2026]] and more text here."))
    assert res.chunks_added == 2 and len(calls) == 1  # stops after the first failure
    assert [e["rel"] for e in _edges()] == ["MENTIONED_IN"]


def test_extraction_is_capped_per_document(vault, extractor, monkeypatch):
    monkeypatch.setattr(budget, "MAX_DOC_CHUNKS", 2)
    monkeypatch.setattr(ingest, "chunk_text", lambda text: [f"part {i}" for i in range(5)])
    ingest.ingest_file(_write(vault.root, "notes/long.md", "long note"))
    assert extractor.calls == ["part 0", "part 1"]


def test_owner_row_follows_config(fresh_db, monkeypatch):
    E.ensure_owner()
    monkeypatch.setattr(config, "OWNER_NAME", "Kiran Shah")
    E.ensure_owner()
    assert [(r["entity_id"], r["name"], r["norm_name"]) for r in _entities()] == [(OWNER_ENTITY_ID, "Kiran Shah",
                                                                                   "kiran shah")]


def test_document_entities_only_merge_with_documents(fresh_db):
    project, _ = E.upsert("PROJECT", "Flat move 2026")
    doc, created = E.upsert("DOCUMENT", "Flat move 2026", {"path": "notes/flat_move_2026.md"})
    assert created and doc != project
    assert E.upsert("CONCEPT", "flat move 2026") == (project, False)
    assert E.upsert("DOCUMENT", "Flat Move 2026") == (doc, False)


# --- question matching -------------------------------------------------------------------------------------


def _unit(*xs):
    v = np.zeros(config.EMBED_DIM, np.float32)
    v[: len(xs)] = xs
    return v / np.linalg.norm(v)


@pytest.fixture
def named(fresh_db, monkeypatch):
    """Owner, Ravi (PERSON), Flat move 2026 (PROJECT), Rent renewal (CONCEPT), Ed (PERSON); fake vectors."""
    E.ensure_owner()
    ids = {name: E.upsert(t, name)[0] for t, name in (("PERSON", "Ravi"), ("PROJECT", "Flat move 2026"),
                                                      ("CONCEPT", "Rent renewal"), ("PERSON", "Ed"))}
    vectors = {"ravi": _unit(1, 0, 0), "flat move 2026": _unit(0, 1, 0), "rent renewal": _unit(0, 0, 1),
               "ed": _unit(0, 0, 0, 1)}
    state = SimpleNamespace(ids=ids, qvec=None, embedded=[])

    def fake_embed(texts):
        state.embedded += texts
        return np.stack([vectors.get(t.removeprefix(config.EMBED_QUERY_PREFIX), _unit(0, 0, 0, 0, 1)) for t in texts])

    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(embed, "query_vector", lambda q: state.qvec)
    return state


def test_find_by_name_in_the_question(named):
    ids = named.ids
    assert E.find_in_question("When does my flat move 2026 project end, and does Mr. Ravi know?") == [
        ids["Flat move 2026"], ids["Ravi"]]
    assert E.find_in_question("What did I decide?") == []  # the owner is never matched
    assert E.find_in_question("Is Edward home?") == []      # "ed" is too short and not a whole word anyway


def test_find_a_person_by_first_name(named):
    kumar, _ = E.upsert("PERSON", "Suresh Kumar")
    topic, _ = E.upsert("CONCEPT", "Suresh market")  # only a PERSON matches by first word
    assert E.find_in_question("Did Suresh reply?") == [kumar]
    assert E.find_in_question("Is Suresh Kumar coming, and is Ravi?") == [kumar, named.ids["Ravi"]]
    assert topic not in E.find_in_question("suresh")


def test_find_by_embedding_adds_close_names(named):
    ids = named.ids
    named.qvec = _unit(0.2, 1, 0.9)  # close to flat move (0.73) and rent renewal (0.66), not Ravi
    assert E.MATCH_MIN_COSINE == 0.72
    assert E.find_in_question("when do I have to move out?") == [ids["Flat move 2026"]]
    named.qvec = _unit(1, 0.05, 0)
    assert E.find_in_question("Ravi's number?") == [ids["Ravi"]]  # already found by name, not twice
    E.find_in_question("again")
    assert sorted(named.embedded) == sorted(config.EMBED_QUERY_PREFIX + n for n in
                                            ("ravi", "flat move 2026", "rent renewal", "ed"))  # names embedded once


def test_find_without_ollama_is_string_only(named, monkeypatch):
    def down(texts):
        raise llm.LLMError("down")

    monkeypatch.setattr(llm, "embed", down)
    named.qvec = _unit(0, 1, 0)
    assert E.find_in_question("tell me about rent renewal") == [named.ids["Rent renewal"]]


def test_find_on_empty_graph_never_embeds(fresh_db, monkeypatch):
    monkeypatch.setattr(embed, "query_vector", lambda q: pytest.fail("embedded"))
    assert E.find_in_question("anything") == []


# --- graph-neighbour retrieval in chat ---------------------------------------------------------------------


def _doc(doc_id, status="unsigned"):
    db.insert("documents", {"doc_id": doc_id, "path": f"notes/{doc_id}.md", "source": "note",
                            "signature_status": status, "ingested_at": "2026-09-27T10:00:00Z"})


def _chunk(cid, doc_id, text="text"):
    db.insert("chunks", {"chunk_id": cid, "doc_id": doc_id, "locator": f"note: {doc_id}.md", "text": text})
    return ScoredChunk(chunk_id=cid, doc_id=doc_id, locator=f"note: {doc_id}.md", text=text, score=0.0)


@pytest.fixture
def graph_chat(named, monkeypatch):
    """One chunk per doc; Ravi LANDLORD_OF owner (from c_ravi), Rent renewal PART_OF Flat move (from c_plan), Ravi
    RELATES_TO Ed (from c_bad, an invalid doc); search returns `state.ranked`; the model cites [1]."""
    ids = named.ids
    for d in ("a", "b", "c", "ravi", "plan", "bad"):
        _doc(f"d_{d}", "invalid" if d == "bad" else "unsigned")
    named.chunks = {c: _chunk(f"c_{c}", f"d_{c}", f"chunk {c}") for c in ("a", "b", "c", "ravi", "plan", "bad")}
    db.insert_edges([
        {"edge_id": "x_1", "src": ids["Ravi"], "rel": "LANDLORD_OF", "dst": OWNER_ENTITY_ID, "source_chunk_id": "c_ravi"},
        {"edge_id": "x_2", "src": ids["Rent renewal"], "rel": "PART_OF", "dst": ids["Flat move 2026"],
         "source_chunk_id": "c_plan"},
        {"edge_id": "x_3", "src": ids["Ravi"], "rel": "RELATES_TO", "dst": ids["Ed"], "source_chunk_id": "c_bad"},
        {"edge_id": "x_4", "src": ids["Ravi"], "rel": "RELATES_TO", "dst": ids["Flat move 2026"],
         "source_chunk_id": "c_a", "valid_to": "2026-09-01"},  # closed: ignored
    ])
    named.ranked = []
    monkeypatch.setattr(embed, "search", lambda q, k=8: named.ranked[:k])
    monkeypatch.setattr(llm, "chat_stream", lambda messages, model=None, stats=None: iter(["Chunk [1]."]))
    return named


def _scored(chunk, score):
    return chunk.model_copy(update={"score": score})


def test_linked_chunks_are_boosted_and_pulled_in(graph_chat):
    c = graph_chat.chunks
    graph_chat.ranked = [_scored(c["a"], 1.0), _scored(c["b"], 0.9), _scored(c["plan"], 0.5), _scored(c["c"], 0.4)]
    chunks, excluded, named, linked = chat.retrieve("Who is Ravi and what about the flat move 2026?")
    assert named == [graph_chat.ids["Flat move 2026"], graph_chat.ids["Ravi"]]
    assert [x.chunk_id for x in chunks] == ["c_a", "c_b", "c_plan", "c_c", "c_ravi"]
    assert [round(x.score, 2) for x in chunks] == [1.0, 0.9, 0.8, 0.4, 0.3]
    assert "c_bad" not in [x.chunk_id for x in chunks]
    assert excluded == ["notes/d_bad.md"]  # linked via Ravi and pulled in at GRAPH_BOOST, so it would have been sent
    assert linked["c_ravi"] == [OWNER_ENTITY_ID] and linked["c_plan"] == [graph_chat.ids["Rent renewal"]]


def test_no_named_entity_leaves_ranking_unchanged(graph_chat):
    c = graph_chat.chunks
    graph_chat.ranked = [_scored(c["a"], 1.0), _scored(c["ravi"], 0.2)]
    chunks, _, named, linked = chat.retrieve("what is the weather?")
    assert named == [] and linked == {} and [x.score for x in chunks] == [1.0, 0.2]


def test_meta_lists_named_entities_then_neighbours_of_sent_chunks(graph_chat, monkeypatch):
    c = graph_chat.chunks
    ids = graph_chat.ids
    graph_chat.ranked = [_scored(c["plan"], 1.0), _scored(c["a"], 0.9)]
    meta = list(chat.answer_stream("Tell me about the flat move 2026 and Ravi", []))[0].data
    assert [r.chunk_id for r in meta.chunks] == ["c_plan", "c_a", "c_ravi"]
    assert meta.entities_used == [ids["Flat move 2026"], ids["Ravi"], ids["Rent renewal"], OWNER_ENTITY_ID]

    monkeypatch.setattr(chat, "TOP_K", 2)  # c_ravi not sent -> the owner is not a used neighbour
    meta = list(chat.answer_stream("Tell me about the flat move 2026 and Ravi", []))[0].data
    assert meta.entities_used == [ids["Flat move 2026"], ids["Ravi"], ids["Rent renewal"]]


def test_warm_up_embeds_entity_names(named, monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda messages, model=None: "OK")
    chat.warm_up()
    assert {config.EMBED_QUERY_PREFIX + n for n in ("ravi", "flat move 2026")} <= set(named.embedded)


# --- bank statement rows (BRAIN step 7: code, never the model) ----------------------------------------------


def _row(date_, desc, amount, balance):
    return f"{date_}  {desc}  {amount}  {balance}"


def test_bank_rows_reads_upi_debits_and_credit_rows():
    text = " ".join([
        _row("2026-06-01", "SALARY CREDIT Nimbus Analytics", "48,000.00", "80,250.00"),
        _row("2026-06-05", "UPI/RENT/RAVI KUMAR", "15,000.00", "65,250.00"),
        _row("2026-06-12", "CARD/GROCERIES AND UTILITIES", "9,120.50", "56,129.50"),
    ])
    rows = E.bank_rows(text)
    assert rows == [("credit", "Nimbus Analytics", "48,000.00"), ("debit", "RAVI KUMAR", "15,000.00")]


def test_bank_rows_ignores_a_row_with_no_named_counterparty():
    assert E.bank_rows(_row("2026-06-12", "CARD/GROCERIES AND UTILITIES", "9,120.50", "56,129.50")) == []


def test_bank_rows_survives_trailing_summary_text_after_the_last_row():
    text = (_row("2026-08-12", "UPI/RENT/RAVI KUMAR", "15,000.00", "56,129.50")
           + " Average monthly salary credit: INR 48000. This statement is digitally signed by the issuing bank.")
    assert E.bank_rows(text) == [("debit", "RAVI KUMAR", "15,000.00")]


def test_bank_rows_ignores_the_preamble_before_the_first_dated_row():
    text = ("Mock Bank of India - Statement of Account Account holder: Ananya Iyer Period: 01 Jun 2026 to 31 Aug "
           "2026 " + _row("2026-06-05", "UPI/RENT/RAVI KUMAR", "15,000.00", "65,250.00"))
    assert E.bank_rows(text) == [("debit", "RAVI KUMAR", "15,000.00")]


def _entity(entity_id, type_, name):
    db.insert("entities", {"entity_id": entity_id, "type": type_, "name": name,
                           "norm_name": E.normalise_name(name), "attrs_json": "{}"})


def test_bank_edges_links_only_existing_entities(fresh_db):
    _entity("e_ravi", "PERSON", "Ravi Kumar")
    chunk = {"chunk_id": "c_1", "text": " ".join([
        _row("2026-06-01", "SALARY CREDIT Nimbus Analytics", "48,000.00", "80,250.00"),  # no such entity: skipped
        _row("2026-06-05", "UPI/RENT/RAVI KUMAR", "15,000.00", "65,250.00"),
    ])}
    edges = E.bank_edges([chunk])
    assert len(edges) == 1
    assert (edges[0]["src"], edges[0]["rel"], edges[0]["dst"]) == (OWNER_ENTITY_ID, "PAID", "e_ravi")
    assert edges[0]["source_chunk_id"] == "c_1"


def test_bank_edges_credit_row_pays_the_owner(fresh_db):
    _entity("e_acme", "ORG", "Nimbus Analytics")
    chunk = {"chunk_id": "c_1", "text": _row("2026-06-01", "SALARY CREDIT Nimbus Analytics", "48,000.00", "80,250.00")}
    edges = E.bank_edges([chunk])
    assert (edges[0]["src"], edges[0]["rel"], edges[0]["dst"]) == ("e_acme", "PAID", OWNER_ENTITY_ID)


def test_bank_edges_ignores_a_non_person_non_org_match(fresh_db):
    _entity("e_proj", "PROJECT", "Ravi Kumar")  # same name, wrong type
    chunk = {"chunk_id": "c_1", "text": _row("2026-06-05", "UPI/RENT/RAVI KUMAR", "15,000.00", "65,250.00")}
    assert E.bank_edges([chunk]) == []


def test_bank_edges_dedupes_repeated_rows_across_chunks(fresh_db):
    _entity("e_ravi", "PERSON", "Ravi Kumar")
    row = _row("2026-06-05", "UPI/RENT/RAVI KUMAR", "15,000.00", "65,250.00")
    edges = E.bank_edges([{"chunk_id": "c_1", "text": row}, {"chunk_id": "c_2", "text": row}])
    assert len(edges) == 1


def test_ingest_wires_bank_edges_for_bank_statement_documents(vault, fresh_db, extractor):
    pdf = vault.root / "pdfs" / "bank_statement_signed.pdf"
    _make_pdf(pdf, [_row("2026-06-05", "UPI/RENT/RAVI KUMAR", "15,000.00", "65,250.00")])
    _entity("e_ravi", "PERSON", "Ravi Kumar")
    ingest.ingest_file(pdf)
    edge = db.fetch_one("SELECT * FROM edges WHERE dst = 'e_ravi' AND rel = 'PAID'")
    assert edge is not None and edge["src"] == OWNER_ENTITY_ID


# --- real model (Ollama) -----------------------------------------------------------------------------------

DEMO = config.ROOT / "demo_data"


@pytest.fixture
def real_vault(fresh_db, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    for sub in ("pdfs", "notes", "chats"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setattr(config, "VAULT_DIR", root)
    monkeypatch.setattr(embed, "_index", None)
    return root


def _extract_note(name, text):
    start = time.perf_counter()
    x = E.extract(text)
    ms = int((time.perf_counter() - start) * 1000)
    g = E.ground(x, text)
    print(f"\n[entities llm] {name}: {ms} ms, kept {len(g.entities)} + owner x{g.owner_mentions}, "
          f"dropped {len(g.dropped)}, relations kept {len(g.relations)} dropped {g.relations_dropped}"
          f"\n  kept: {ascii(sorted((v['type'], v['name']) for v in g.entities.values()))}"
          f"\n  dropped: {ascii(g.dropped)}")
    return g


@pytest.mark.llm
def test_real_extraction_precision_on_demo_notes():
    """Prints kept vs dropped per demo note (how much the model invents), then checks the rent decision note."""
    kept = dropped = 0
    for path in sorted((DEMO / "notes").glob("*.md")) + [DEMO / "chats" / "landlord.txt"]:
        text = ingest.clean_text(path.read_text(encoding="utf-8"))[:config.CHUNK_SIZE]
        g = _extract_note(path.name, text)
        kept += len(g.entities)
        dropped += len(g.dropped)
    print(f"\n[entities llm] precision on demo notes: kept {kept}, dropped {dropped} "
          f"({100 * kept // max(kept + dropped, 1)}% kept)")

    text = ingest.clean_text((DEMO / "notes" / "rent_decision.md").read_text(encoding="utf-8"))
    g = _extract_note("rent_decision.md (check)", text)
    decisions = [v for v in g.entities.values() if v["type"] == "DECISION"]
    assert decisions and decisions[0]["attrs"]["date"] == "2026-09-18"
    assert "flat move 2026" in g.entities


@pytest.mark.llm
def test_real_landlord_is_ravi(real_vault):
    for name in ("landlord.md", "flat_move_2026.md"):
        shutil.copy(DEMO / "notes" / name, real_vault / "notes" / name)
        ingest.ingest_file(real_vault / "notes" / name)
    ravi = _by_name("Ravi Kumar")  # landlord.md: "The landlord is Ravi Kumar."
    assert ravi["type"] == "PERSON"
    assert db.entities_by_norm(["ravi"]) == []  # flat_move_2026.md's "Talk to Ravi" folded into Ravi Kumar
    assert E.find_in_question("What do I need to confirm with Ravi?")[0] == ravi["entity_id"]


@pytest.mark.llm
def test_real_drop_to_ingested_time_for_a_one_chunk_note(real_vault):
    """Single-phase ingest: the time from a file landing to its `ingested` event (chunk, embed, extract, store).
    Over 15 s means proposing two-phase ingest instead."""
    E.extract("warm-up: Ravi is my landlord.")  # FAST_MODEL resident, as it is on the demo laptop
    llm.embed([config.EMBED_DOC_PREFIX + "warm-up"])
    path = real_vault / "notes" / "inbox_note.md"
    shutil.copy(DEMO / "notes" / "inbox_note.md", path)
    start = time.perf_counter()
    res = ingest.ingest_file(path)
    seconds = time.perf_counter() - start
    total = seconds + config.WATCH_DEBOUNCE_S
    print(f"\n[entities llm] drop-to-ingested for inbox_note.md: ingest_file {seconds:.1f} s + watcher debounce "
          f"{config.WATCH_DEBOUNCE_S:g} s = {total:.1f} s (chunks {res.chunks_added}, entities added "
          f"{res.entities_added}); target <= 15 s")
    assert res.chunks_added == 1
    assert total < 60
