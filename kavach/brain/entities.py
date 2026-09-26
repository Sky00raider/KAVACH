"""Entity + relation extraction, dedupe on normalised name + type, graph writes (BRAIN step 6).

Private to BRAIN (used by ingest.py and chat.py). Stub module.
"""

from __future__ import annotations


def normalise_name(name: str) -> str:
    """Stub: lowercase + collapse whitespace. BRAIN adds honorific stripping (Mr./Mrs./Smt./Dr.)."""
    return " ".join(name.lower().split())
