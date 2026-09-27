"""Requester keys and pairing (BUILD_PLAN §4.6): an unknown key becomes a `pending` requester; the owner's approval
pairs it and creates a per-requester pairwise owner key (so attestations can't be linked across requesters).
"""

from __future__ import annotations

from typing import Any

from kavach import db
from kavach.db import utc_now
from kavach.models import Requester
from kavach.trust import audit, crypto


class UnknownRequester(KeyError):
    pass


def get(fp: str) -> dict[str, Any] | None:
    return db.fetch_one("SELECT * FROM requesters WHERE fingerprint = ?", (fp,))


def view(fp: str) -> Requester:
    row = get(fp)
    if row is None:
        raise UnknownRequester(fp)
    return Requester(fingerprint=row["fingerprint"], pubkey=row["pubkey"], name=row["name"], type=row["type"],
                     status=row["status"], paired_at=row["paired_at"])


def register_pending(pubkey: str, name: str, type_: str) -> str:
    """First contact from an unknown key: a pending pairing card for the owner. Returns the fingerprint."""
    fp = crypto.fingerprint(pubkey)
    with db.connect() as conn:
        created = conn.execute(
            "INSERT OR IGNORE INTO requesters (fingerprint, pubkey, name, type, status) VALUES (?, ?, ?, ?, 'pending')",
            (fp, pubkey, name[:80], type_[:40])).rowcount == 1
    if created:
        audit.log("requester_pending", fp, {"name": name[:80], "type": type_[:40]})
    return fp


def pairwise_pubkey(fp: str) -> str | None:
    row = get(fp)
    if row is None or row["status"] != "paired" or not row["owner_pairwise_privkey"]:
        return None
    return crypto.public_key(row["owner_pairwise_privkey"])


def decide(fp: str, approve: bool) -> Requester:
    """approve -> paired (+ pairwise owner key, kept if it already exists); else blocked.

    Requests that were waiting for this pairing then go through the pipeline (or are refused when blocked).
    """
    row = get(fp)
    if row is None:
        raise UnknownRequester(fp)
    if approve:
        key = row["owner_pairwise_privkey"] or crypto.new_private_key()
        db.update("requesters", "fingerprint", fp, {"status": "paired", "owner_pairwise_privkey": key,
                                                    "paired_at": row["paired_at"] or utc_now()})
        audit.log("requester_paired", fp, {"name": row["name"], "type": row["type"]})
    else:
        db.update("requesters", "fingerprint", fp, {"status": "blocked"})
        audit.log("requester_blocked", fp, {"name": row["name"], "type": row["type"]})
    from kavach.trust import consent  # consent imports pairing

    consent.release_waiting(fp)
    return view(fp)
