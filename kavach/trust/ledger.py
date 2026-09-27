"""Global cumulative disclosure ledger (BUILD_PLAN §4.7).

Per numeric claim (config.LEDGER_MIN_WIDTH: income, percentage) keep the interval [lo, hi) implied by every answer
ever given to anyone: YES to ">= t" sets lo = max(lo, t), a disclosed NO sets hi = min(hi, t). An answer is refused
if it would leave hi - lo below the minimum width, or if it would be a new owner-attested threshold beyond
LEDGER_MAX_ATTESTED_PER_30D for that claim. Requester identity is ignored on purpose: assume everyone colludes.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone

from kavach import config, db
from kavach.db import utc_now
from kavach.models import Claim, LedgerCheck

_CUTOFF = re.compile(r"^(income|percentage)_ge_(\d+)$")


def _threshold(claim: Claim) -> float | None:
    """The t in ">= t" this claim asks about, or None when the claim cannot narrow a tracked range."""
    if claim.claim not in config.LEDGER_MIN_WIDTH:
        return None
    if claim.issuer_claim and (m := _CUTOFF.match(claim.issuer_claim)):
        return float(m.group(2))
    if claim.op == "ge" and isinstance(claim.value, (int, float)) and not isinstance(claim.value, bool):
        return float(claim.value)
    return None


def _state(field: str) -> tuple[float, float, list[dict]]:
    row = db.fetch_one("SELECT lo, hi, attested_json FROM disclosure_ledger WHERE field = ?", (field,))
    if row is None:
        return -math.inf, math.inf, []
    lo = row["lo"] if row["lo"] is not None else -math.inf
    hi = row["hi"] if row["hi"] is not None else math.inf
    return lo, hi, json.loads(row["attested_json"] or "[]")


def _recent_thresholds(attested: list[dict]) -> set[float]:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {float(a["t"]) for a in attested if a["ts"] >= since}


def _apply(claim: Claim, answer: bool) -> tuple[str, float, float, list[dict], float] | None:
    t = _threshold(claim)
    if t is None:
        return None
    field = claim.claim
    lo, hi, attested = _state(field)
    if answer:
        lo = max(lo, t)
    else:
        hi = min(hi, t)
    return field, lo, hi, attested, t


def check(claim: Claim, answer: bool) -> LedgerCheck:
    applied = _apply(claim, answer)
    if applied is None:
        return LedgerCheck(allowed=True, reason=None)
    field, lo, hi, attested, t = applied
    min_width = config.LEDGER_MIN_WIDTH[field]
    if hi - lo < min_width:
        return LedgerCheck(allowed=False, reason=f"Would narrow {field} to a range narrower than {min_width:g} "
                                                 "across everything already disclosed")
    recent = _recent_thresholds(attested)
    if claim.issuer_claim is None and t not in recent and len(recent) >= config.LEDGER_MAX_ATTESTED_PER_30D:
        return LedgerCheck(allowed=False, reason=f"Already {len(recent)} different owner-attested {field} "
                                                 "thresholds in the last 30 days")
    return LedgerCheck(allowed=True, reason=None)


def record(claim: Claim, answer: bool) -> None:
    applied = _apply(claim, answer)
    if applied is None:
        return
    field, lo, hi, attested, t = applied
    if claim.issuer_claim is None:
        attested = attested + [{"t": t, "ts": utc_now()}]
    with db.connect() as conn:
        conn.execute("INSERT INTO disclosure_ledger (field, lo, hi, attested_json, updated_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(field) DO UPDATE SET lo = excluded.lo, hi = excluded.hi, "
                     "attested_json = excluded.attested_json, updated_at = excluded.updated_at",
                     (field, None if math.isinf(lo) else lo, None if math.isinf(hi) else hi, json.dumps(attested),
                      utc_now()))
