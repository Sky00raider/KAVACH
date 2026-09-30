"""Which chunks of a document get the per-chunk model calls of entities.py and extract.py: a CPU budget.

Each chosen chunk costs two FAST_MODEL calls (about 10 s each on the owner laptop's CPU), so a long document cannot
send every chunk. Plain code, no model:
- PDFs and notes: the first MAX_DOC_CHUNKS, in order (statements, agreements and notes state what matters early;
  bank rows are parsed in code from every chunk anyway).
- WhatsApp chats (one chunk per conversation window, oldest first): all of them when they fit in MAX_CHAT_WINDOWS.
  Otherwise windows with a cue come first (a number, a month, a money / relationship / decision word, looked for
  in the messages, not in their `[YYYY-MM-DD HH:MM]` stamps), newest first because later messages carry the latest
  values; spare room goes to the newest windows without one. Returned in chronological order.
"""

from __future__ import annotations

import re

MAX_DOC_CHUNKS = 8
MAX_CHAT_WINDOWS = 16

_STAMP = re.compile(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]")  # ingest.WaMessage.line() prefix
_CUE = re.compile(
    r"\d|₹|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
    r"|\b(?:rent|deposit|salary|income|pay|paid|fee|emi|loan|landlord|owner|lease|agreement|employer|job|work\w*|"
    r"office|joined|board|marks?|percentage|result|exam|birthday|born|decid\w*|renew\w*|agree\w*|confirm\w*|"
    r"promise\w*|plan\w*|will|going to|from now|starting|increase\w*|change\w*|move\w*|shift\w*)\b",
    re.IGNORECASE)


def has_cue(text: str) -> bool:
    return bool(_CUE.search(_STAMP.sub(" ", text)))


def model_chunks(chunks: list[dict], source: str) -> list[dict]:
    """The chunks, in document order, that get model extraction (see the module docstring)."""
    if source != "chat":
        return chunks[:MAX_DOC_CHUNKS]
    if len(chunks) <= MAX_CHAT_WINDOWS:
        return chunks
    order = list(range(len(chunks)))[::-1]  # newest first
    cued = [i for i in order if has_cue(chunks[i]["text"])]
    rest = [i for i in order if i not in set(cued)]
    keep = set((cued + rest)[:MAX_CHAT_WINDOWS])
    return [c for i, c in enumerate(chunks) if i in keep]
