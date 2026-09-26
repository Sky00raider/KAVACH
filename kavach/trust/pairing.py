"""Requester keys and pairing: unknown key -> `pending` requester; owner approval creates a pairwise owner key.

Used by consent.py and api.py. Stub module, filled in TRUST step 5.
"""

from __future__ import annotations

from kavach.db import utc_now
from kavach.models import Requester


def decide(fp: str, approve: bool) -> Requester:
    """Stub: approve -> paired (real version creates the pairwise owner key), else blocked. Nothing is stored."""
    return Requester(fingerprint=fp, pubkey="MCowBQYDK2VwAyEA" + "A" * 44, name="Ramesh Kumar", type="landlord",
                     status="paired" if approve else "blocked", paired_at=utc_now() if approve else None)
