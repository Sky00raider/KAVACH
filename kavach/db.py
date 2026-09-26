"""SQLite schema (CONTRACT §8) and the only helpers allowed to touch the database.

Each helper opens its own short-lived connection, so it is safe from FastAPI's thread pool.
The path is read from `config.DB_PATH` at call time, so tests can point it elsewhere.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    Plan,
    Requester,
    RequestView,
    Task,
    ToolResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (doc_id TEXT PRIMARY KEY, path TEXT UNIQUE, source TEXT, doc_type TEXT,
  signature_status TEXT, iss TEXT, text_hash TEXT, ingested_at TEXT, removed_at TEXT);
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
    "documents", "chunks", "entities", "edges", "facts", "memory_candidates", "credentials",
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


def list_facts(field: str | None = None, current: bool = False) -> list[Fact]:
    clauses, params = [], []
    if field:
        clauses.append("field = ?")
        params.append(field)
    if current:
        clauses.append("valid_to IS NULL AND superseded_by IS NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return [_fact(r) for r in fetch_all(f"SELECT * FROM facts {where} ORDER BY created_at DESC", tuple(params))]


def graph(entity_id: str | None = None, hops: int = 1) -> Graph:
    """Whole graph, or the `hops`-neighbourhood of one entity."""
    if entity_id is None:
        entities = fetch_all("SELECT entity_id, type, name FROM entities")
        edges = fetch_all("SELECT * FROM edges")
    else:
        seen = {entity_id}
        frontier = {entity_id}
        edges_by_id: dict[str, dict[str, Any]] = {}
        for _ in range(max(hops, 0)):
            if not frontier:
                break
            marks = ", ".join("?" for _ in frontier)
            rows = fetch_all(f"SELECT * FROM edges WHERE src IN ({marks}) OR dst IN ({marks})",
                             (*frontier, *frontier))
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
                         valid_to=e["valid_to"], source_chunk_id=e["source_chunk_id"]) for e in edges],
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


def last_audit_hash() -> str:
    row = fetch_one("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1")
    return row["entry_hash"] if row else "0" * 64
