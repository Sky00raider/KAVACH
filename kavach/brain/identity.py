"""Identity anchor and holder check (CONTRACT §6.6). Plain code, no model.

A signature proves who issued a document, not whose it is. The anchor is the owner's name and date of birth: from
their UIDAI Aadhaar offline e-KYC (`import_aadhaar`, verified by `trust.aadhaar`), else from the first issuer-signed
ID card of an identity issuer (`IDENTITY_ISSUERS`), pinned in the `identity` table; with neither,
`config.OWNER_NAME` without a date of birth (`not_verified`). A live Aadhaar anchor has no document, so no ID card
pins or retires it. `holder_status` checks a signed document's
text against it: the name right after the document type's holder label ("Account holder:", "Candidate:",
"Name:"; the first such label decides, anywhere in the text when there is none), then any date of birth it states.

Texts are raw page text (`textnorm.pdf_pages` joined by newlines), so a label's value is the rest of its line; on
text without newlines the first n + 2 words after the label are used, which is the same window.
ingest.py decides when to pin or retire the anchor and rechecks documents when it changes.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date

from kavach import config, db
from kavach.db import new_id, utc_now
from kavach.models import HolderStatus, Identity
from kavach.trust import aadhaar, audit

IDENTITY_ISSUERS = frozenset({"mock_govt"})

_LETTERS = re.compile(r"[^\W\d_]+")
_HOLDER_LABELS: dict[str, tuple[str, ...]] = {
    "bank_statement": ("account holder name", "account holder", "account name", "customer name"),
    "marksheet": ("name of the candidate", "candidate name", "candidate", "student name"),
    "id_card": ("name",),
}
_ALL_LABELS = tuple(dict.fromkeys(label for labels in _HOLDER_LABELS.values() for label in labels))
# "Father's Name:", "Nominee name:": a name label that belongs to someone else
_NOT_HOLDER = frozenset({"father", "mother", "spouse", "husband", "wife", "guardian", "nominee", "parent", "bank",
                         "branch", "company", "employer", "landlord", "school", "college", "institution"})
_DOB_LABEL = re.compile(r"(?<![^\W_])(?:date\s+of\s+birth|birth\s+date|d\.?\s?o\.?\s?b\.?)(?![^\W_])\s*[:\-]?",
                        re.IGNORECASE)
_NEXT_LABEL = re.compile(r"\s(?:[^\W\d_]+\s+){0,2}[^\W\d_]+\s*:")  # " Date of birth:" after a name on one line
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                        "dec"), 1)}
_DATE_FORMS = (
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b"), ("d", "m", "y")),
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]+(\d{4})\b"), ("d", "mon", "y")),
    (re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"), ("mon", "d", "y")),
)


@dataclass(frozen=True)
class Anchor:
    name: str
    dob: str | None
    source: str  # signed_id | config
    issuer: str | None = None
    doc_id: str | None = None
    verified_at: str | None = None


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _LETTERS.findall(unicodedata.normalize("NFKC", text))]


def _label_re(labels: tuple[str, ...]) -> re.Pattern[str]:
    body = "|".join(r"\s+".join(map(re.escape, label.split())) for label in sorted(labels, key=len, reverse=True))
    return re.compile(rf"(?<![^\W_])(?:{body})\s*:", re.IGNORECASE)


_LABEL_RES = {doc_type: _label_re(labels) for doc_type, labels in _HOLDER_LABELS.items()}
_ANY_LABEL_RE = _label_re(_ALL_LABELS)


def _value_after(text: str, end: int) -> str:
    """The rest of the label's line, or the next non-empty line when the value wraps below the label."""
    return next((line for line in text[end:].split("\n") if line.strip()), "")


def _holder_value(text: str, doc_type: str | None) -> str | None:
    """What follows the first holder label of `doc_type` (any type's labels when unknown); None without one."""
    for m in _LABEL_RES.get(doc_type or "", _ANY_LABEL_RE).finditer(text):
        before = [w for w in _words(text[max(0, m.start() - 40):m.start()]) if w != "s"]  # father's -> father
        if before and before[-1] in _NOT_HOLDER:
            continue
        return _value_after(text, m.end())
    return None


def _contains_name(words: list[str], name: list[str]) -> bool:
    return not Counter(name) - Counter(words)


def parse_date(text: str) -> str | None:
    """ISO date from the start of `text`: 2003-05-14, 14/05/2003 (day first), 14 May 2003, May 14, 2003."""
    text = text.strip()
    for pattern, order in _DATE_FORMS:
        m = pattern.match(text)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        month = int(parts["m"]) if "m" in parts else _MONTHS.get(parts["mon"][:3].casefold())
        try:
            return date(int(parts["y"]), month or 0, int(parts["d"])).isoformat()
        except (TypeError, ValueError):
            return None
    return None


def _dob_status(text: str, dob: str | None) -> HolderStatus:
    """Every date of birth the document states must be the anchor's (only its year when the anchor has just a year,
    as some Aadhaar records do); `unknown` if one cannot be read."""
    if dob is None:
        return "verified"
    stated = [parse_date(_value_after(text, m.end())[:40]) for m in _DOB_LABEL.finditer(text)]
    if len(dob) == 4:
        stated = [d[:4] if d else None for d in stated]
    if any(d is not None and d != dob for d in stated):
        return "mismatch"
    return "unknown" if None in stated else "verified"


def check(text: str, doc_type: str | None, name: str, dob: str | None) -> HolderStatus:
    """`holder_status` of `text` for a holder named `name` born on `dob` (None: not checked)."""
    tokens = _words(name)
    if not tokens:
        return "mismatch"
    window = len(tokens) + 2
    value = _holder_value(text, doc_type)
    if value is not None:
        found = _contains_name(_words(value)[:window], tokens)
    else:
        words = _words(text)
        found = any(_contains_name(words[i:i + window], tokens) for i in range(max(1, len(words) - window + 1)))
    return _dob_status(text, dob) if found else "mismatch"


def read_id_card(text: str) -> tuple[str, str | None] | None:
    """(name, date of birth or None) from an ID card's text: the words after its `Name:` label (up to the next
    label on the same line), and the date after its date-of-birth label. None when there is no readable name."""
    value = _holder_value(text, "id_card")
    if value is None:
        return None
    cut = _NEXT_LABEL.search(value)
    name = " ".join((value[:cut.start()] if cut else value).split()).strip(" ,.;")
    if not _words(name) or len(name.split()) > 6:
        return None
    dob = next((parse_date(_value_after(text, m.end())[:40]) for m in _DOB_LABEL.finditer(text)), None)
    return name, dob


# --- the anchor ------------------------------------------------------------------------------------------------


def anchor() -> Anchor:
    row = db.live_identity()
    if row is not None:
        return Anchor(name=row["name"], dob=row["dob"], source=row["source"], issuer=row["issuer"],
                      doc_id=row["doc_id"], verified_at=row["verified_at"])
    return Anchor(name=config.OWNER_NAME, dob=None, source="config")


def is_identity_document(doc_type: str | None, signature_status: str, iss: str | None) -> bool:
    return signature_status == "issuer_signed" and doc_type == "id_card" and iss in IDENTITY_ISSUERS


def pin(doc_id: str, text: str, issuer: str) -> bool:
    """Make this signed ID card the anchor when none is in force; True if it did."""
    if db.live_identity() is not None:
        return False
    read = read_id_card(text)
    if read is None:
        return False
    name, dob = read
    db.set_identity({"identity_id": new_id("id"), "source": "signed_id", "issuer": issuer, "name": name, "dob": dob,
                     "last4": None, "doc_id": doc_id, "verified_at": utc_now(), "removed_at": None})
    return True


def initials(name: str) -> str:
    return " ".join(f"{w[0].upper()}." for w in _LETTERS.findall(name))


# --- public (CONTRACT §7) ------------------------------------------------------------------------------------


def holder_status(text: str, doc_type: str | None) -> HolderStatus:
    """Whether a signed document's text is in the current anchor's name (§6.6)."""
    a = anchor()
    return check(text, doc_type, a.name, a.dob)


def import_aadhaar(zip_bytes: bytes, share_code: str) -> Identity:
    """Verify the owner's offline e-KYC ZIP and make it the anchor; audit either way (never a value), then recheck
    every signed document against it. Raises `aadhaar.AadhaarError`."""
    try:
        found = aadhaar.verify_okyc(zip_bytes, share_code)
    except aadhaar.AadhaarError as exc:
        audit.log("identity_verify_failed", None, {"source": "aadhaar_okyc", "reason": exc.reason})
        raise
    identity_id, now = new_id("id"), utc_now()
    db.set_identity({"identity_id": identity_id, "source": "aadhaar_okyc", "issuer": aadhaar.ISSUER, "name": found.name,
                     "dob": found.dob, "last4": found.last4, "doc_id": None, "verified_at": now, "removed_at": None})
    audit.log("identity_verified", identity_id, {"source": "aadhaar_okyc", "issuer": aadhaar.ISSUER, "verified_at": now})
    from kavach.brain import ingest  # ingest imports this module

    ingest.recheck_holders()
    return current()


class NotRemovable(Exception):
    """The anchor in force comes from a document (or is the configured name): remove the document instead."""


def remove_aadhaar() -> Identity:
    """Retire the owner's Aadhaar anchor (its name, DOB and digits are wiped from the row), fall back to the next
    verified ID card or the configured name, audit `identity_removed`, recheck every signed document. Raises
    NotRemovable when the anchor in force is not an Aadhaar import."""
    live = db.live_identity()
    if live is None or live["source"] != "aadhaar_okyc":
        raise NotRemovable()
    db.retire_identity(utc_now())
    audit.log("identity_removed", live["identity_id"], {"source": "aadhaar_okyc"})
    from kavach.brain import ingest  # ingest imports this module

    ingest.pin_next_id()
    ingest.recheck_holders()
    return current()


def current() -> Identity:
    """The anchor, masked: initials and year of birth only (never the last 4 digits)."""
    a = anchor()
    return Identity(status="not_verified" if a.source == "config" else "verified", source=a.source,
                    issuer=a.issuer, name_initials=initials(a.name),
                    birth_year=int(a.dob[:4]) if a.dob else None, doc_id=a.doc_id, verified_at=a.verified_at)
