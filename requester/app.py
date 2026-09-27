"""Requester backend on :9000 (CONTRACT §12) and the built frontend in requester mode.

Holds only small JSON in requester/data/ (identity, requests, received presentations/attestations);
/r/storage lists exactly that so the demo can show no documents ever arrive. Keys never live in .json files.
Run: uvicorn requester.app:app --host 0.0.0.0 --port 9000   (OWNER_URL=http://<owner-ip>:8000)
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from kavach import config
from kavach.models import RAskIn, RAskOut, RIdentity, RRequest, RStorage, RStorageFile
from requester import common

PREVIEW_MAX = 2000

app = FastAPI(title="KAVACH requester")


@app.get("/r/identity")
def identity() -> RIdentity:
    ident = common.Identity("web")
    return RIdentity(name=ident.name, type=ident.type, fingerprint=ident.fingerprint, owner_url=config.OWNER_URL)


@app.post("/r/ask")
def ask(body: RAskIn) -> RAskOut:
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "question is empty")
    ident = common.Identity("web")
    try:
        ack, nonce = common.http_ask(ident, question[:500])
    except common.OwnerError as exc:
        raise HTTPException(502, str(exc)) from None
    rec = common.record_request(ident, question[:500], ack, nonce)
    return RAskOut(local_id=rec.local_id, request_id=rec.request_id, status=rec.status)


@app.get("/r/requests")
def requests() -> list[RRequest]:
    """Stored requests, web and agent, newest first. Polls the owner for open ones and verifies on arrival."""
    return list(reversed(common.refresh_all()))


def _preview(text: str) -> str:
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        pass
    return text if len(text) <= PREVIEW_MAX else text[:PREVIEW_MAX] + "…"


@app.get("/r/storage")
def storage() -> RStorage:
    d = common.data_dir()
    if not d.is_dir():
        return RStorage(files=[])
    files = [RStorageFile(name=p.name, size=p.stat().st_size, preview_json=_preview(p.read_text(encoding="utf-8")))
             for p in sorted(d.iterdir()) if p.is_file() and p.suffix == ".json"]
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
