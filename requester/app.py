"""Requester backend on :9000 (CONTRACT §12) and the built frontend in requester mode.

Holds only small JSON in requester/data/ (identity, requests, received presentations/attestations);
/r/storage lists exactly that so the demo can show no documents ever arrive. Keys never live in .json files.
Run: uvicorn requester.app:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter

from kavach import config
from kavach.db import new_id
from kavach.models import RAskIn, RAskOut, RIdentity, RRequest, RStorage, RStorageFile

DATA_DIR = Path(os.environ.get("REQUESTER_DATA_DIR", Path(__file__).resolve().parent / "data")).resolve()
PREVIEW_MAX = 2000
_REQUESTS = TypeAdapter(list[RRequest])

app = FastAPI(title="KAVACH requester")


@app.get("/r/identity")
def identity() -> RIdentity:
    """Stub: TRUST step 3 creates the keypair in DATA_DIR on first run and derives the fingerprint."""
    return RIdentity(name="Ramesh Kumar", type="person", fingerprint="a1b2c3d4e5f60718", owner_url=config.OWNER_URL)


@app.post("/r/ask")
def ask(body: RAskIn) -> RAskOut:
    """Stub: TRUST step 3 creates a nonce, signs, calls the owner's /api/ask and records the request."""
    return RAskOut(local_id=f"l_{uuid4().hex[:10]}", request_id=new_id("rq"), status="pending_pairing")


@app.get("/r/requests")
def requests() -> list[RRequest]:
    """Stored requests, web and agent. TRUST step 3 polls the owner and verifies on arrival."""
    path = DATA_DIR / "requests.json"
    return _REQUESTS.validate_json(path.read_bytes()) if path.is_file() else []


def _preview(text: str) -> str:
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        pass
    return text if len(text) <= PREVIEW_MAX else text[:PREVIEW_MAX] + "…"


@app.get("/r/storage")
def storage() -> RStorage:
    if not DATA_DIR.is_dir():
        return RStorage(files=[])
    files = [RStorageFile(name=p.name, size=p.stat().st_size, preview_json=_preview(p.read_text(encoding="utf-8")))
             for p in sorted(DATA_DIR.iterdir()) if p.is_file() and p.suffix == ".json"]
    return RStorage(files=files)


# --- frontend --------------------------------------------------------------------------------------------

_BOOT = '<script>window.__KAVACH__={"mode": "requester"}</script>'
_PLACEHOLDER = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>KAVACH</title></head>"
                "<body><p>Frontend not built. Run <code>npm run build</code> in <code>frontend/</code>.</p></body></html>")

app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets", check_dir=False), name="assets")


def index_html() -> HTMLResponse:
    index = config.FRONTEND_DIST / "index.html"
    page = index.read_text(encoding="utf-8") if index.is_file() else _PLACEHOLDER
    page = page.replace("</head>", _BOOT + "</head>", 1) if "</head>" in page else _BOOT + page
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    if full_path == "r" or full_path.startswith("r/"):
        raise HTTPException(404, "not found")
    dist = config.FRONTEND_DIST.resolve()
    candidate = (dist / full_path).resolve()
    if full_path and candidate.is_file() and candidate.is_relative_to(dist) and candidate.name != "index.html":
        return FileResponse(candidate)
    return index_html()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.REQUESTER_PORT)
