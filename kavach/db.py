"""SQLite schema (CONTRACT §8) and the only helpers allowed to touch the database.

Each helper opens its own short-lived connection, so it is safe from FastAPI's thread pool.
The path is read from `config.DB_PATH` at call time, so tests can point it elsewhere.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from kavach import config
from kavach.models import (
    AuditEntry,
    Chunk,
    Document,
    Entity,
    Fact,
    Graph,
    GraphEdge,
    GraphNode,
    IngestedDetail,
    IngestEvent,
    IngestEvents,
    Plan,
    Requester,
    RequestView,
    Task,
    ToolResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (doc_id TEXT PRIMARY KEY, path TEXT UNIQUE, source TEXT, doc_type TEXT,
  signature_status TEXT, iss TEXT, text_hash TEXT, ingested_at TEXT, removed_at TEXT, holder_status TEXT);
CREATE TABLE IF NOT EXISTS identity (identity_id TEXT PRIMARY KEY, source TEXT, issuer TEXT, name TEXT, dob TEXT,
  last4 TEXT, doc_id TEXT, verified_at TEXT, removed_at TEXT);
CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, locator TEXT, text TEXT, embedding BLOB);
CREATE TABLE IF NOT EXISTS entities (entity_id TEXT PRIMARY KEY, type TEXT, name TEXT, norm_name TEXT, attrs_json TEXT);
CREATE TABLE IF NOT EXISTS edges (edge_id TEXT PRIMARY KEY, src TEXT, rel TEXT, dst TEXT,
  valid_from TEXT, valid_to TEXT, source_chunk_id TEXT);
CREATE TABLE IF NOT EXISTS facts (fact_id TEXT PRIMARY KEY, entity_id TEXT, field TEXT, value TEXT, source_type TEXT,
  doc_id TEXT, quote TEXT, valid_from TEXT, valid_to TEXT, superseded_by TEXT, confidence TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS memory_candidates (candidate_id TEXT PRIMARY KEY, statement TEXT, kind TEXT,
  field TEXT, value TEXT, valid_from TEXT, project_entity_id TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS credentials (cred_id TEXT PRIMARY KEY, iss TEXT, credential_type TEXT, copy INT,
  credential_json TEXT, disclosures_json TEXT, holder_privkey BLOB, used INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS requesters (fingerprint TEXT PRIMARY KEY, pubkey TEXT, name TEXT, type TEXT,
  status TEXT, owner_pairwise_privkey BLOB, paired_at TEXT);
CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, requester_fp TEXT, channel TEXT, question TEXT,
  claim_json TEXT, proposal_json TEXT, nonce TEXT, status TEXT, answer_type TEXT, payload_json TEXT,
  created_at TEXT, decided_at TEXT, UNIQUE(requester_fp, nonce));
CREATE TABLE IF NOT EXISTS disclosure_ledger (field TEXT PRIMARY KEY, lo REAL, hi REAL, attested_json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, instruction TEXT, plan_json TEXT, status TEXT,
  result_json TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, ref_id TEXT,
  detail_json TEXT, prev_hash TEXT, entry_hash TEXT);
"""

TABLES = (
    "documents", "identity", "chunks", "entities", "edges", "facts", "memory_candidates", "credentials",
    "requesters", "requests", "disclosure_ledger", "tasks", "audit_log",
)


def new_id(prefix: str) -> str:
    """`{prefix}_{uuid4().hex[:10]}`; prefixes per AGENTS.md conventions."""
    return f"{prefix}_{uuid4().hex[:10]}"


def utc_now() -> str:
    """UTC ISO 8601 timestamp with `Z`, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> Path:
    return Path(config.DB_PATH)


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Connection with Row access; commits on success, rolls back on error."""
    conn = sqlite3.connect(path or db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with connect(target) as conn:
        conn.executescript(SCHEMA)
        # columns added after a database was first created (CONTRACT §8)
        doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
        if "holder_status" not in doc_cols:
            conn.execute("ALTER TABLE documents ADD COLUMN holder_status TEXT")


def ping() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


# --- generic helpers ---------------------------------------------------------


def _check_table(table: str) -> None:
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}")


def insert(table: str, row: dict[str, Any]) -> int:
    """Insert one row; returns lastrowid (the `seq` for audit_log)."""
    _check_table(table)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
        return cur.lastrowid or 0


def update(table: str, key_col: str, key: Any, fields: dict[str, Any]) -> int:
    """Update columns on one row by key; returns rows changed."""
    _check_table(table)
    sets = ", ".join(f"{c} = ?" for c in fields)
    with connect() as conn:
        cur = conn.execute(f"UPDATE {table} SET {sets} WHERE {key_col} = ?", (*fields.values(), key))
        return cur.rowcount


def fetch_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- ingestion writes (one transaction each) ------------------------------------

_DOC_COLS = ("doc_id", "path", "source", "doc_type", "signature_status", "iss", "text_hash", "ingested_at", "removed_at",
             "holder_status")


def get_document_by_path(path: str) -> dict[str, Any] | None:
    """The documents row for a vault-relative path, removed or not."""
    return fetch_one("SELECT * FROM documents WHERE path = ?", (path,))


def document_status(doc_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """{doc_id: {path, signature_status, holder_status}} for the given ids (unknown ids are absent)."""
    ids = list(dict.fromkeys(doc_ids))
    if not ids:
        return {}
    rows = fetch_all(f"SELECT doc_id, path, signature_status, holder_status FROM documents WHERE doc_id IN "
                     f"({', '.join('?' * len(ids))})", tuple(ids))
    return {r["doc_id"]: r for r in rows}


def _close_document_knowledge(conn: sqlite3.Connection, doc_id: str, closed_on: str) -> None:
    """Close (never delete) the doc's current facts and the edges sourced from its chunks. A closed fact's best
    supporting source from another live document (or the owner) takes its place (`_promote_supporter`)."""
    conn.execute("UPDATE edges SET valid_to = ? WHERE valid_to IS NULL AND source_chunk_id IN "
                 "(SELECT chunk_id FROM chunks WHERE doc_id = ?)", (closed_on, doc_id))
    closing = conn.execute("SELECT * FROM facts WHERE doc_id = ? AND valid_to IS NULL AND superseded_by IS NULL",
                           (doc_id,)).fetchall()
    conn.execute("UPDATE facts SET valid_to = ? WHERE doc_id = ? AND valid_to IS NULL AND superseded_by IS NULL",
                 (closed_on, doc_id))
    for r in closing:
        _promote_supporter(conn, dict(r), doc_id)


def close_document_knowledge(doc_id: str, closed_on: str) -> None:
    """Close the doc's current facts and edges without touching its chunks (its signature check started failing)."""
    with connect() as conn:
        _close_document_knowledge(conn, doc_id, closed_on)


def store_document(doc: dict[str, Any], chunks: list[dict[str, Any]], closed_on: str) -> None:
    """Upsert one documents row and replace all its chunks; knowledge from the old chunks is closed on `closed_on`."""
    row = {c: doc.get(c) for c in _DOC_COLS}
    updates = ", ".join(f"{c} = excluded.{c}" for c in _DOC_COLS[1:])
    with connect() as conn:
        _close_document_knowledge(conn, row["doc_id"], closed_on)
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row["doc_id"],))
        conn.execute(f"INSERT INTO documents ({', '.join(_DOC_COLS)}) VALUES ({', '.join('?' * len(_DOC_COLS))}) "
                     f"ON CONFLICT(doc_id) DO UPDATE SET {updates}", tuple(row.values()))
        conn.executemany("INSERT INTO chunks (chunk_id, doc_id, locator, text, embedding) VALUES (?, ?, ?, ?, ?)",
                         [(c["chunk_id"], row["doc_id"], c["locator"], c["text"], c.get("embedding")) for c in chunks])


def remove_document(doc_id: str, removed_at: str, closed_on: str) -> None:
    """Mark a document removed, close its knowledge and drop its chunks."""
    with connect() as conn:
        _close_document_knowledge(conn, doc_id, closed_on)
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("UPDATE documents SET removed_at = ? WHERE doc_id = ?", (removed_at, doc_id))


def signed_documents() -> list[dict[str, Any]]:
    """Live `issuer_signed` documents (doc_id, path, doc_type, iss, holder_status), oldest ingest first."""
    return fetch_all("SELECT doc_id, path, doc_type, iss, holder_status FROM documents WHERE removed_at IS NULL "
                     "AND signature_status = 'issuer_signed' ORDER BY ingested_at, rowid")


# --- identity anchor (brain/identity.py, CONTRACT §6.6) ---------------------------


def live_identity() -> dict[str, Any] | None:
    """The identity row in force: the newest one not removed."""
    return fetch_one("SELECT * FROM identity WHERE removed_at IS NULL ORDER BY verified_at DESC, rowid DESC LIMIT 1")


def set_identity(row: dict[str, Any]) -> None:
    """Retire any live identity row and insert `row` as the one in force, in one transaction."""
    cols = ("identity_id", "source", "issuer", "name", "dob", "last4", "doc_id", "verified_at", "removed_at")
    with connect() as conn:
        conn.execute("UPDATE identity SET removed_at = ? WHERE removed_at IS NULL", (row["verified_at"],))
        conn.execute(f"INSERT INTO identity ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(row.get(c) for c in cols))


def retire_identity(removed_at: str) -> None:
    """No identity row in force any more (its document went)."""
    with connect() as conn:
        conn.execute("UPDATE identity SET removed_at = ? WHERE removed_at IS NULL", (removed_at,))


# --- search index (brain/embed.py) ----------------------------------------------


def search_chunks() -> list[dict[str, Any]]:
    """Every chunk with its embedding BLOB (or None), in insertion order."""
    return fetch_all("SELECT chunk_id, doc_id, locator, text, embedding FROM chunks ORDER BY rowid")


def chunks_signature() -> tuple:
    """Cheap fingerprint of the chunks table; changes when chunks are added, removed or (re)embedded."""
    row = fetch_one("SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS last, COALESCE(SUM(length(text)), 0) AS chars, "
                    "COALESCE(SUM(length(embedding)), 0) AS vec_bytes FROM chunks")
    return (str(db_path()), row["n"], row["last"], row["chars"], row["vec_bytes"])


def chunks_missing_embeddings(nbytes: int) -> list[dict[str, Any]]:
    """chunk_id and text of chunks with no embedding or one of the wrong size (`nbytes`)."""
    return fetch_all("SELECT chunk_id, text FROM chunks WHERE embedding IS NULL OR length(embedding) != ? "
                     "ORDER BY rowid", (nbytes,))


def set_chunk_embeddings(pairs: list[tuple[str, bytes]]) -> None:
    """Store `(chunk_id, blob)` embeddings; ids of chunks deleted meanwhile are ignored."""
    with connect() as conn:
        conn.executemany("UPDATE chunks SET embedding = ? WHERE chunk_id = ?", [(b, cid) for cid, b in pairs])


# --- entities and graph (brain/entities.py, chat retrieval) ----------------------


def entities_by_norm(norm_names: Iterable[str]) -> list[dict[str, Any]]:
    """Entity rows whose norm_name is one of `norm_names`, oldest first."""
    names = list(dict.fromkeys(norm_names))
    if not names:
        return []
    return fetch_all(f"SELECT * FROM entities WHERE norm_name IN ({', '.join('?' * len(names))}) ORDER BY rowid",
                     tuple(names))


def entity_types(entity_ids: Iterable[str]) -> dict[str, str]:
    """entity_id -> type for the given ids that exist."""
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return {}
    rows = fetch_all(f"SELECT entity_id, type FROM entities WHERE entity_id IN ({', '.join('?' * len(ids))})",
                     tuple(ids))
    return {r["entity_id"]: r["type"] for r in rows}


def entity_index() -> list[dict[str, Any]]:
    """entity_id, type, name, norm_name of every entity, oldest first (question matching in chat)."""
    return fetch_all("SELECT entity_id, type, name, norm_name FROM entities ORDER BY rowid")


def entities_signature() -> tuple:
    """Cheap fingerprint of the entities table; changes when an entity is added or renamed."""
    row = fetch_one("SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS last, "
                    "COALESCE(SUM(length(norm_name)), 0) AS chars FROM entities")
    return (str(db_path()), row["n"], row["last"], row["chars"])


def insert_edges(edges: list[dict[str, Any]]) -> None:
    cols = ("edge_id", "src", "rel", "dst", "valid_from", "valid_to", "source_chunk_id")
    with connect() as conn:
        conn.executemany(f"INSERT INTO edges ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [tuple(e.get(c) for c in cols) for e in edges])


def current_edges(entity_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Open edges (valid_to NULL) with an end in `entity_ids` whose source chunk still exists, oldest first."""
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    return fetch_all(f"SELECT e.* FROM edges e JOIN chunks c ON c.chunk_id = e.source_chunk_id "
                     f"WHERE e.valid_to IS NULL AND (e.src IN ({marks}) OR e.dst IN ({marks})) ORDER BY e.rowid",
                     (*ids, *ids))


def merge_entity(from_id: str, into_id: str, attrs_json: str, closed_on: str) -> None:
    """Fold entity `from_id` into `into_id` in one transaction: edges, facts and memory candidates are repointed,
    edges that became self-loops are closed on `closed_on`, `into_id` gets `attrs_json`, `from_id` is deleted."""
    with connect() as conn:
        conn.execute("UPDATE edges SET src = ? WHERE src = ?", (into_id, from_id))
        conn.execute("UPDATE edges SET dst = ? WHERE dst = ?", (into_id, from_id))
        conn.execute("UPDATE edges SET valid_to = ? WHERE src = ? AND dst = ? AND valid_to IS NULL",
                     (closed_on, into_id, into_id))
        conn.execute("UPDATE facts SET entity_id = ? WHERE entity_id = ?", (into_id, from_id))
        conn.execute("UPDATE memory_candidates SET project_entity_id = ? WHERE project_entity_id = ?",
                     (into_id, from_id))
        conn.execute("UPDATE entities SET attrs_json = ? WHERE entity_id = ?", (attrs_json, into_id))
        conn.execute("DELETE FROM entities WHERE entity_id = ?", (from_id,))


def chunks_by_ids(chunk_ids: Iterable[str]) -> list[dict[str, Any]]:
    """chunk_id, doc_id, locator, text for the given ids that exist, in insertion order."""
    ids = list(dict.fromkeys(chunk_ids))
    if not ids:
        return []
    return fetch_all(f"SELECT chunk_id, doc_id, locator, text FROM chunks WHERE chunk_id IN "
                     f"({', '.join('?' * len(ids))}) ORDER BY rowid", tuple(ids))


def chunks_for_document(doc_id: str) -> list[dict[str, Any]]:
    """chunk_id, locator, text of one document's chunks, in insertion order (chat's fact citations)."""
    return fetch_all("SELECT chunk_id, doc_id, locator, text FROM chunks WHERE doc_id = ? ORDER BY rowid", (doc_id,))


# --- facts: temporal supersession (brain/extract.py, brain/memory.py) ------------------------------------

_FACT_COLS = ("fact_id", "entity_id", "field", "value", "source_type", "doc_id", "quote", "valid_from",
             "valid_to", "superseded_by", "confidence", "created_at")


def current_fact(entity_id: str, field: str, today: str | None = None) -> dict[str, Any] | None:
    """The fact for `(entity_id, field)` that is current as of `today` (default: today's date): its
    `valid_from` has arrived, and it is neither closed (`valid_to`) nor superseded. Picks the greatest
    `valid_from` on a tie (relevant once a scheduled fact's date has passed one already open)."""
    today = today or date.today().isoformat()
    return fetch_one(
        "SELECT * FROM facts WHERE entity_id = ? AND field = ? AND valid_from <= ? "
        "AND valid_to IS NULL AND superseded_by IS NULL ORDER BY valid_from DESC LIMIT 1",
        (entity_id, field, today))


def scheduled_facts(entity_id: str, today: str | None = None) -> list[dict[str, Any]]:
    """Open facts for `entity_id` not yet in effect (`valid_from` in the future): known changes that have not
    happened yet, oldest first."""
    today = today or date.today().isoformat()
    return fetch_all("SELECT * FROM facts WHERE entity_id = ? AND valid_to IS NULL AND superseded_by IS NULL "
                     "AND valid_from > ? ORDER BY valid_from", (entity_id, today))


_SOURCE_RANK = {"issuer_doc": 2, "extracted": 1, "owner_stated": 0}


def fact_rank(fact: dict[str, Any]) -> tuple[int, int]:
    """How strongly a fact is backed: high confidence first, then issuer-signed > extracted > owner-stated (a
    document can be cited; the owner's own word cannot)."""
    return (fact.get("confidence") == "high", _SOURCE_RANK.get(fact.get("source_type") or "", 0))


def same_value(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Values equal ignoring case and whitespace ("RAVI KUMAR" == "Ravi Kumar")."""
    def norm(v: Any) -> str:
        return " ".join(str(v or "").split()).casefold()
    return norm(a["value"]) == norm(b["value"])


def _repoint_supporters(conn: sqlite3.Connection, old_id: str, new_id_: str) -> None:
    """Facts that supported `old_id` (closed against it with the same value) now support `new_id_`."""
    conn.execute("UPDATE facts SET superseded_by = ? WHERE superseded_by = ? AND fact_id != ? AND value = "
                 "(SELECT value FROM facts WHERE fact_id = ?)", (new_id_, old_id, new_id_, old_id))


def _promote_supporter(conn: sqlite3.Connection, closed: dict[str, Any], doc_id: str) -> None:
    """`closed` just lost its document: its strongest supporting fact (same value, closed against it) from a live
    document that still verifies, or from the owner, reopens in its place (earliest `valid_from` on a tie), so a
    value several sources state does not vanish when one of them does."""
    rows = [dict(r) for r in conn.execute(
        "SELECT f.* FROM facts f LEFT JOIN documents d ON d.doc_id = f.doc_id WHERE f.superseded_by = ? "
        "AND (f.doc_id IS NULL OR (f.doc_id != ? AND d.removed_at IS NULL AND d.signature_status != 'invalid'))",
        (closed["fact_id"], doc_id)).fetchall()]
    rows = [r for r in rows if same_value(r, closed)]
    if not rows:
        return
    best = max(sorted(rows, key=lambda r: r["valid_from"] or ""), key=fact_rank)  # max keeps the first on a tie
    conn.execute("UPDATE facts SET valid_to = NULL, superseded_by = NULL WHERE fact_id = ?", (best["fact_id"],))
    conn.execute("UPDATE facts SET superseded_by = ? WHERE fact_id != ? AND fact_id IN (%s)"
                 % ", ".join("?" * len(rows)), (best["fact_id"], best["fact_id"], *[r["fact_id"] for r in rows]))


def supersede_and_insert_fact(fact: dict[str, Any], today: str | None = None) -> list[dict[str, Any]]:
    """Insert one fact for `(entity_id, field)`, threading it into that field's open facts (`valid_to IS NULL
    AND superseded_by IS NULL`), one transaction. Returns the existing facts this call closed with a different
    value - their state just before closing, for a caller that wants to report what a new value replaced
    (`memory.teach`'s `TeachResult.superseded`).

    The chain is over "blockers": every open fact, except that a high-confidence fact ignores low-confidence ones
    (and closes them once it is in effect), so a vague "landlord = unknown" never outranks a grounded value.
    `prior` is the blocker in force at the new fact's `valid_from` (greatest `valid_from` not after it),
    `successor` the nearest strictly-later blocker.
    - Supporting source: a fact with the same value as `prior` (`same_value`) is not a change. The stronger of
      the two (`fact_rank`; the earlier on a tie, i.e. `prior`) stays open, the other is closed against it
      (`superseded_by` = the stronger, `valid_to` = its own `valid_from`). A low-confidence fact arriving while a
      high-confidence one is in force is closed against it the same way, whatever its value.
    - Backdated: a fact strictly earlier than `successor` is inserted closed against it (it never disturbs a
      newer fact) - unless it has `successor`'s value and outranks it, in which case it takes `successor`'s place
      (a signed statement from June outranks a note restating the same rent in September, whichever arrives
      first).
    - Otherwise, a fact already in effect (`valid_from <= today`) closes every blocker not strictly later than it
      (`valid_to` = its own `valid_from`, `superseded_by` = its own id); on a tie the new fact wins.
    - A not-yet-effective (scheduled) fact closes nothing: it is inserted open alongside the fact it will
      eventually replace, so `current_fact` keeps returning the right one until that date arrives (and picks the
      new one once it does, via its `ORDER BY valid_from DESC`).
    A closed fact's supporters follow it to whatever closed it only when that fact has the same value (they
    support a value, not a row)."""
    today = today or date.today().isoformat()
    with connect() as conn:
        open_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM facts WHERE entity_id = ? AND field = ? AND valid_to IS NULL AND superseded_by IS NULL",
            (fact["entity_id"], fact["field"])).fetchall()]
        high = fact.get("confidence") == "high"
        blockers = [r for r in open_rows if r["confidence"] == "high"] if high else open_rows
        weak = [r for r in open_rows if r["confidence"] != "high"] if high else []
        vf = fact["valid_from"] or ""
        strictly_later = sorted((r for r in blockers if (r["valid_from"] or "") > vf),
                                key=lambda r: r["valid_from"] or "")
        not_later = [r for r in blockers if (r["valid_from"] or "") <= vf]
        prior = max(not_later, key=lambda r: r["valid_from"] or "", default=None)
        successor = strictly_later[0] if strictly_later else None
        row = dict(fact)
        to_close: list[tuple[dict[str, Any], str]] = []  # (open fact, its valid_to)
        # a low-confidence fact always ranks below a high one, so this also covers "low while high is in force"
        if prior is not None and (same_value(prior, fact) or prior["confidence"] == "high" and not high) \
                and fact_rank(fact) <= fact_rank(prior):
            row["valid_to"], row["superseded_by"] = vf, prior["fact_id"]
        elif successor is not None and not (same_value(successor, fact) and fact_rank(fact) > fact_rank(successor)):
            row["valid_to"], row["superseded_by"] = successor["valid_from"], successor["fact_id"]
        else:
            if successor is not None:  # outranks the later fact stating the same value: takes its place
                to_close.append((successor, successor["valid_from"]))
            if vf <= today:
                to_close += [(r, vf) for r in not_later]
                to_close += [(r, max(vf, r["valid_from"] or "")) for r in weak]
        conn.execute(f"INSERT INTO facts ({', '.join(_FACT_COLS)}) VALUES ({', '.join('?' * len(_FACT_COLS))})",
                     tuple(row.get(c) for c in _FACT_COLS))
        closed: list[dict[str, Any]] = []
        for r, valid_to in to_close:
            conn.execute("UPDATE facts SET valid_to = ?, superseded_by = ? WHERE fact_id = ?",
                         (valid_to, fact["fact_id"], r["fact_id"]))
            if same_value(r, fact):
                _repoint_supporters(conn, r["fact_id"], fact["fact_id"])
            else:
                closed.append({**r, "valid_to": valid_to, "superseded_by": fact["fact_id"]})
        return closed


# --- typed readers used by the API --------------------------------------------


def list_documents(include_removed: bool = False) -> list[Document]:
    where = "" if include_removed else "WHERE removed_at IS NULL"
    return [Document(**r) for r in fetch_all(f"SELECT * FROM documents {where} ORDER BY ingested_at DESC")]


def get_chunk(chunk_id: str) -> Chunk | None:
    row = fetch_one("SELECT chunk_id, doc_id, locator, text FROM chunks WHERE chunk_id = ?", (chunk_id,))
    return Chunk(**row) if row else None


def _entity(row: dict[str, Any]) -> Entity:
    return Entity(entity_id=row["entity_id"], type=row["type"], name=row["name"],
                  attrs=json.loads(row["attrs_json"] or "{}"))


def list_entities(type: str | None = None) -> list[Entity]:
    if type:
        rows = fetch_all("SELECT * FROM entities WHERE type = ? ORDER BY name", (type,))
    else:
        rows = fetch_all("SELECT * FROM entities ORDER BY type, name")
    return [_entity(r) for r in rows]


def _fact(row: dict[str, Any]) -> Fact:
    row = dict(row)
    row.pop("created_at", None)
    return Fact(**row)


def list_facts(field: str | None = None, current: bool = False, today: str | None = None) -> list[Fact]:
    """`current=True`: valid_to IS NULL AND superseded_by IS NULL AND valid_from <= today (excludes a scheduled
    fact not yet in effect); may still return more than one per field until an eventually-effective fact closes
    every older open one (`supersede_and_insert_fact`) - callers wanting one value per field pick the greatest
    `valid_from` (as `decide.py` and `current_fact` do)."""
    clauses, params = [], []
    if field:
        clauses.append("field = ?")
        params.append(field)
    if current:
        clauses.append("valid_to IS NULL AND superseded_by IS NULL AND (valid_from IS NULL OR valid_from <= ?)")
        params.append(today or date.today().isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return [_fact(r) for r in fetch_all(f"SELECT * FROM facts {where} ORDER BY created_at DESC", tuple(params))]


_GRAPH_EDGE_COLS = "e.*, c.doc_id"
_GRAPH_EDGE_FROM = "FROM edges e LEFT JOIN chunks c ON c.chunk_id = e.source_chunk_id"


def graph(entity_id: str | None = None, hops: int = 1) -> Graph:
    """Whole graph, or the `hops`-neighbourhood of one entity."""
    if entity_id is None:
        entities = fetch_all("SELECT entity_id, type, name FROM entities")
        edges = fetch_all(f"SELECT {_GRAPH_EDGE_COLS} {_GRAPH_EDGE_FROM}")
    else:
        seen = {entity_id}
        frontier = {entity_id}
        edges_by_id: dict[str, dict[str, Any]] = {}
        for _ in range(max(hops, 0)):
            if not frontier:
                break
            marks = ", ".join("?" for _ in frontier)
            rows = fetch_all(f"SELECT {_GRAPH_EDGE_COLS} {_GRAPH_EDGE_FROM} WHERE e.src IN ({marks}) "
                             f"OR e.dst IN ({marks})", (*frontier, *frontier))
            nxt: set[str] = set()
            for e in rows:
                edges_by_id[e["edge_id"]] = e
                nxt.update({e["src"], e["dst"]})
            frontier = nxt - seen
            seen |= nxt
        marks = ", ".join("?" for _ in seen)
        entities = fetch_all(f"SELECT entity_id, type, name FROM entities WHERE entity_id IN ({marks})", tuple(seen))
        edges = list(edges_by_id.values())
    return Graph(
        nodes=[GraphNode(id=e["entity_id"], type=e["type"], name=e["name"]) for e in entities],
        edges=[GraphEdge(id=e["edge_id"], src=e["src"], dst=e["dst"], rel=e["rel"], valid_from=e["valid_from"],
                         valid_to=e["valid_to"], source_chunk_id=e["source_chunk_id"], doc_id=e["doc_id"])
               for e in edges],
    )


def list_requesters(status: str | None = None) -> list[Requester]:
    cols = "fingerprint, pubkey, name, type, status, paired_at"
    if status:
        rows = fetch_all(f"SELECT {cols} FROM requesters WHERE status = ?", (status,))
    else:
        rows = fetch_all(f"SELECT {cols} FROM requesters")
    return [Requester(**r) for r in rows]


def _request_view(row: dict[str, Any]) -> RequestView:
    return RequestView(
        request_id=row["request_id"], requester_fp=row["requester_fp"], requester_name=row.get("name"),
        channel=row["channel"], question=row["question"],
        claim=json.loads(row["claim_json"]) if row["claim_json"] else None,
        proposal=json.loads(row["proposal_json"]) if row["proposal_json"] else None,
        status=row["status"], answer_type=row["answer_type"],
        created_at=row["created_at"], decided_at=row["decided_at"],
    )


def list_requests(status: str | None = None) -> list[RequestView]:
    sql = ("SELECT r.*, q.name FROM requests r LEFT JOIN requesters q ON q.fingerprint = r.requester_fp"
           + (" WHERE r.status = ?" if status else "") + " ORDER BY r.created_at DESC")
    return [_request_view(r) for r in fetch_all(sql, (status,) if status else ())]


def _task(row: dict[str, Any]) -> Task:
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return Task(
        task_id=row["task_id"], instruction=row["instruction"], plan=Plan.model_validate_json(row["plan_json"]),
        status=row["status"], result=[ToolResult(**r) for r in result] if result is not None else None,
        created_at=row["created_at"], decided_at=row["decided_at"],
    )


def list_tasks(status: str | None = None) -> list[Task]:
    if status:
        rows = fetch_all("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        rows = fetch_all("SELECT * FROM tasks ORDER BY created_at DESC")
    return [_task(r) for r in rows]


def get_task(task_id: str) -> Task | None:
    row = fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    return _task(row) if row else None


def save_task(task: Task) -> None:
    """Insert or replace a task row from the model."""
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tasks (task_id, instruction, plan_json, status, result_json, created_at, decided_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task.task_id, task.instruction, task.plan.model_dump_json(), task.status,
             json.dumps([r.model_dump() for r in task.result]) if task.result is not None else None,
             task.created_at, task.decided_at),
        )


def audit_entries(limit: int = 200) -> list[AuditEntry]:
    rows = fetch_all("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))
    return [AuditEntry(seq=r["seq"], ts=r["ts"], event=r["event"], ref_id=r["ref_id"],
                       detail=json.loads(r["detail_json"] or "{}"), prev_hash=r["prev_hash"],
                       entry_hash=r["entry_hash"]) for r in rows]


def ingest_events(since: int = 0) -> IngestEvents:
    """`ingested` audit entries after `since` (CONTRACT §9, §13). Unparseable details are skipped, not fatal."""
    rows = fetch_all("SELECT seq, ts, detail_json FROM audit_log WHERE event = 'ingested' AND seq > ? ORDER BY seq",
                     (since,))
    events = []
    for r in rows:
        try:
            d = IngestedDetail.model_validate_json(r["detail_json"] or "{}")
        except ValidationError:
            continue
        events.append(IngestEvent(seq=r["seq"], ts=r["ts"], path=d.path, doc_id=d.doc_id,
                                  signature_status=d.signature_status, holder_status=d.holder_status,
                                  entities_added=d.entities_added,
                                  facts_added=d.facts_added))
    return IngestEvents(events=events, last_seq=rows[-1]["seq"] if rows else since)


def last_audit_hash() -> str:
    row = fetch_one("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1")
    return row["entry_hash"] if row else "0" * 64
