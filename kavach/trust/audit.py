"""Hash-chained audit log (CONTRACT §8, §13). detail never holds document text, chunk text or raw fact values.

entry_hash = sha256(prev_hash + ts + event + (ref_id or "") + detail_json), lowercase hex over UTF-8;
detail_json is compact sorted-key JSON; the genesis prev_hash is "0" * 64.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import get_args

from kavach import db
from kavach.db import utc_now
from kavach.models import AuditEvent, ChainStatus

GENESIS = "0" * 64
_EVENTS = frozenset(get_args(AuditEvent))
_lock = threading.Lock()


def detail_json(detail: dict) -> str:
    return json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(prev_hash: str, ts: str, event: str, ref_id: str | None, detail: str) -> str:
    return hashlib.sha256((prev_hash + ts + event + (ref_id or "") + detail).encode("utf-8")).hexdigest()


def log(event: str, ref_id: str | None, detail: dict) -> int:
    """Append one entry; returns its seq. Reading the chain head and inserting happen in one write transaction."""
    if event not in _EVENTS:
        raise ValueError(f"unknown audit event {event!r}")
    dj = detail_json(detail)
    with _lock, db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["entry_hash"] if row else GENESIS
        ts = utc_now()
        cur = conn.execute("INSERT INTO audit_log (ts, event, ref_id, detail_json, prev_hash, entry_hash) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (ts, event, ref_id, dj, prev, entry_hash(prev, ts, event,
                                                                                               ref_id, dj)))
        return cur.lastrowid or 0


def verify_chain() -> ChainStatus:
    """Walk the log from the start; the first entry whose link or hash is wrong is `broken_at`."""
    prev = GENESIS
    count = 0
    with db.connect() as conn:
        for r in conn.execute("SELECT seq, ts, event, ref_id, detail_json, prev_hash, entry_hash FROM audit_log "
                              "ORDER BY seq"):
            count += 1
            if r["prev_hash"] != prev or entry_hash(prev, r["ts"], r["event"], r["ref_id"],
                                                    r["detail_json"] or "") != r["entry_hash"]:
                return ChainStatus(intact=False, broken_at=r["seq"], entries=count)
            prev = r["entry_hash"]
    return ChainStatus(intact=True, broken_at=None, entries=count)
