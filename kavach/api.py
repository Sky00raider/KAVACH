"""Owner API on :8000 (CONTRACT §9, §10) and the built frontend.

Owner routes need `X-Owner-Token` and a loopback client. Requester routes (`/api/ask*`, `/api/claims`) are
signed instead and never return document text, chunks or raw facts (hard rule 5).
Run with `--no-proxy-headers` (or `python -m kavach.api`) so uvicorn never rewrites the client address.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import threading
from collections.abc import Iterable, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from kavach import config, db
from kavach.agent import executor, planner
from kavach.brain import chat, decide, embed, identity, ingest, memory, watcher
from kavach.brain.llm import LLMError
from kavach.db import new_id, utc_now
from kavach.models import (
    AskAck,
    AskIn,
    AskResult,
    AuditOut,
    CandidateDecisionIn,
    CandidateDecisionOut,
    ChatErrorData,
    ChatErrorEvent,
    ChatEvent,
    ChatIn,
    ChatResult,
    Chunk,
    ClaimsOut,
    Document,
    Entity,
    EntityType,
    Fact,
    FactVersion,
    Graph,
    Health,
    Identity,
    IngestEvents,
    IngestSyncOut,
    IngestUploadOut,
    OutboxItem,
    QueueOut,
    Requester,
    RequesterDecisionIn,
    RequestDecisionIn,
    RequestView,
    Task,
    TaskDecisionIn,
    TaskIn,
    TeachIn,
    TeachResult,
    ToolResult,
    WalletStatus,
)
from kavach.trust import aadhaar, audit, consent, pairing, wallet

LOOPBACK = frozenset({"127.0.0.1", "::1"})
LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "[::1]"})
VAULT_SUBDIRS = {".pdf": "pdfs", ".md": "notes", ".txt": "chats"}
_UPLOAD_TMP = ".uploads"  # inside VAULT_DIR but outside the watched subdirs
_OUTBOX_KINDS = {".eml": "eml", ".ics": "ics", ".pdf": "pdf"}
# unknown_request and wrong_requester share one response so request ids cannot be probed
_REJECT_STATUS: dict[str, tuple[int, str]] = {
    "bad_sig": (401, "bad_sig"), "stale_ts": (401, "stale_ts"), "nonce_reuse": (409, "nonce_reuse"),
    "unknown_requester_blocked": (403, "blocked"), "unknown_request": (404, "not_found"),
    "wrong_requester": (404, "not_found"), "malformed": (422, "malformed"),
}


# --- auth ------------------------------------------------------------------------------------------------


def is_loopback(request: Request) -> bool:
    """Only the socket peer address counts; proxy headers are never consulted."""
    return request.client is not None and request.client.host in LOOPBACK


def is_loopback_host(request: Request) -> bool:
    """The Host header names loopback, with or without a port. Stops DNS rebinding: a page on
    evil.example resolved to 127.0.0.1 still sends Host: evil.example."""
    host = request.headers.get("host", "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        name, rest = (host[: end + 1], host[end + 1 :]) if end != -1 else ("", "")
    else:
        name, colon, port = host.partition(":")
        rest = colon + port
    if rest and not (rest.startswith(":") and rest[1:].isdigit()):
        return False
    return name in LOOPBACK_HOSTNAMES


def is_owner_client(request: Request) -> bool:
    """Loopback peer address AND loopback Host header: required for owner routes and token injection."""
    return is_loopback(request) and is_loopback_host(request)


def require_owner(request: Request, x_owner_token: str | None = Header(default=None)) -> None:
    if not is_owner_client(request):
        raise HTTPException(403, "owner routes are loopback only")
    if x_owner_token is None or not secrets.compare_digest(x_owner_token.encode(), config.OWNER_TOKEN.encode()):
        raise HTTPException(401, "missing or wrong X-Owner-Token")


def _rejected(exc: consent.RequestRejected) -> HTTPException:
    status, detail = _REJECT_STATUS[exc.reason]
    return HTTPException(status, detail)


# --- app -------------------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    for sub in VAULT_SUBDIRS.values():
        (config.VAULT_DIR / sub).mkdir(parents=True, exist_ok=True)
    observer = None
    if config.KAVACH_WATCH:
        observer = watcher.start(config.VAULT_DIR)
        # build the search index (and backfill missing vectors) off the event loop, so startup doesn't wait
        threading.Thread(target=embed.warm, name="kavach-index-warm", daemon=True).start()
        # load the chat and embedding models now, so the first real answer isn't a cold model load
        threading.Thread(target=chat.warm_up, name="kavach-model-warm", daemon=True).start()
    try:
        yield
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)


app = FastAPI(title="KAVACH owner API", lifespan=lifespan)
_FP = re.compile(r"^[0-9a-f]{16}$")


@app.exception_handler(LLMError)
async def local_model_unavailable(request: Request, exc: LLMError) -> JSONResponse:
    """The local model is down or failed: a 503 the UI can explain, not an opaque 500."""
    return JSONResponse({"detail": f"local model unavailable: {exc}"[:300]}, status_code=503)


@app.exception_handler(RequestValidationError)
async def audit_malformed_ask(request: Request, exc: RequestValidationError):
    """Malformed /api/ask* requests are audited (hard rule 6) with route, client and error type only, never the
    body; the response is FastAPI's usual 422."""
    path = request.url.path
    if path == "/api/ask" or path.startswith("/api/ask/"):
        fp = request.headers.get("x-requester-fp", "")
        errors = exc.errors()
        audit.log("request_rejected", fp if _FP.match(fp) else None, {
            "reason": "malformed", "route": "/api/ask" if path == "/api/ask" else "/api/ask/{id}",
            "client_ip": request.client.host if request.client else None,
            "error_type": str(errors[0].get("type", "invalid")) if errors else "invalid"})
    return await request_validation_exception_handler(request, exc)
owner = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])
public = APIRouter(prefix="/api")


# --- health, ingestion -----------------------------------------------------------------------------------


def _full_name(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


def _ollama_status() -> tuple[bool, set[str]]:
    """(reachable, names of resident models). Short timeout so the health poll never hangs."""
    try:
        with httpx.Client(base_url=config.OLLAMA_URL, timeout=1.0) as client:
            client.get("/api/tags").raise_for_status()
            ps = client.get("/api/ps").json()
    except (httpx.HTTPError, ValueError):
        return False, set()
    return True, {_full_name(m.get("name") or m.get("model") or "") for m in ps.get("models", [])}


def local_inference(url: str | None = None) -> bool:
    """OLLAMA_URL's host is loopback (`localhost` or a loopback address): the models run on this machine."""
    host = urlsplit(url or config.OLLAMA_URL).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@owner.get("/health")
def health() -> Health:
    reachable, loaded = _ollama_status()
    models = {"llm": config.LLM_MODEL, "fast": config.FAST_MODEL, "embed": config.EMBED_MODEL}
    return Health(ollama=reachable, models=models, db=db.ping(), vault_dir=str(config.VAULT_DIR),
                  model_loaded={role: _full_name(name) in loaded for role, name in models.items()},
                  local_inference=local_inference())


def safe_upload_name(raw: str | None) -> str:
    """Basename of an uploaded filename; 400 on empty, `..`, absolute, drive or control-character names."""
    if not raw or any(ord(ch) < 32 for ch in raw):
        raise HTTPException(400, "bad filename")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise HTTPException(400, "absolute paths are not allowed")
    parts = re.split(r"[\\/]", raw)
    if ".." in parts:
        raise HTTPException(400, "'..' is not allowed")
    name = parts[-1].strip()
    if name in ("", ".") or ":" in name:
        raise HTTPException(400, "bad filename")
    return name


@owner.post("/ingest")
async def ingest_upload(file: UploadFile) -> IngestUploadOut:
    """Copies into vault/<subdir>/ for the watcher. Never overwrites: same bytes -> 200, different -> 409."""
    name = safe_upload_name(file.filename)
    sub = VAULT_SUBDIRS.get(Path(name).suffix.lower())
    if sub is None:
        raise HTTPException(415, f"only {', '.join(VAULT_SUBDIRS)} files are accepted")
    content = await file.read()
    target_dir = config.VAULT_DIR / sub
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / name
    if dest.resolve().parent != target_dir.resolve():
        raise HTTPException(400, "bad filename")
    rel = dest.relative_to(config.VAULT_DIR).as_posix()

    # Write outside the watched dirs, then hard-link into place: the link fails if the name exists,
    # so an existing file is never replaced and the watcher never sees a half-written file.
    tmp_dir = config.VAULT_DIR / _UPLOAD_TMP
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{secrets.token_hex(8)}.part"
    tmp.write_bytes(content)
    try:
        os.link(tmp, dest)
    except FileExistsError:
        if dest.read_bytes() != content:
            raise HTTPException(409, f"{rel} already exists with different content") from None
    finally:
        tmp.unlink(missing_ok=True)
    return IngestUploadOut(path=rel)


@owner.post("/ingest/sync")
def ingest_sync() -> IngestSyncOut:
    results = []
    for suffix, sub in VAULT_SUBDIRS.items():
        folder = config.VAULT_DIR / sub
        if folder.is_dir():
            results += [ingest.ingest_file(p) for p in sorted(folder.iterdir())
                        if p.is_file() and p.suffix.lower() == suffix]
    return IngestSyncOut(ingested=results)


@owner.get("/ingest/events")
def ingest_events(since: int = Query(0, ge=0)) -> IngestEvents:
    return db.ingest_events(since)


# --- knowledge -------------------------------------------------------------------------------------------


@owner.get("/documents")
def documents() -> list[Document]:
    return db.list_documents()


@owner.get("/identity")
def identity_anchor() -> Identity:
    return identity.current()


@owner.post("/identity/aadhaar")
async def identity_aadhaar(file: UploadFile, share_code: str = Form(..., max_length=64)) -> Identity:
    """CONTRACT §6.6: the owner's UIDAI offline e-KYC ZIP + share code. Read in memory; never written or echoed."""
    content = await file.read(aadhaar.MAX_ZIP_BYTES + 1)
    try:
        return identity.import_aadhaar(content, share_code)
    except aadhaar.AadhaarError as exc:
        raise HTTPException(422 if exc.reason == "bad_signature" else 400, exc.reason) from None


@owner.get("/entities")
def entities(type: EntityType | None = None) -> list[Entity]:
    return db.list_entities(type)


@owner.get("/facts")
def facts(field: str | None = None, current: bool = True) -> list[Fact]:
    return db.list_facts(field, current)


@owner.get("/graph")
def graph(entity_id: str | None = None, hops: int = Query(1, ge=0, le=3)) -> Graph:
    return db.graph(entity_id, hops)


@owner.get("/chunks/{chunk_id}")
def chunk(chunk_id: str) -> Chunk:
    found = db.get_chunk(chunk_id)
    if found is None:
        raise HTTPException(404, "chunk not found")
    return found


# --- chat and memory -------------------------------------------------------------------------------------


@owner.post("/chat")
def chat_sync(body: ChatIn) -> ChatResult:
    return chat.answer(body.question, body.history)


def sse(events: Iterable[ChatEvent]) -> Iterator[str]:
    """§10 framing. An exception mid-stream becomes an `error` event instead of a dropped connection."""
    try:
        for ev in events:
            yield f"event: {ev.event}\ndata: {ev.data.model_dump_json()}\n\n"
    except Exception as exc:  # noqa: BLE001 - reported to the owner's UI, not swallowed
        err = ChatErrorEvent(data=ChatErrorData(message=f"{type(exc).__name__}: {exc}"))
        yield f"event: error\ndata: {err.data.model_dump_json()}\n\n"


@owner.post("/chat/stream", response_class=StreamingResponse, responses={200: {
    "model": ChatEvent,
    "description": "text/event-stream of CONTRACT §10 events; each `data:` line is the `data` of one of these",
}})
def chat_stream(body: ChatIn) -> StreamingResponse:
    return StreamingResponse(sse(chat.answer_stream(body.question, body.history)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@owner.post("/memory")
def teach(body: TeachIn) -> TeachResult:
    return memory.teach(body.statement)


@owner.post("/memory/candidates/{candidate_id}/decision")
def candidate_decision(candidate_id: str, body: CandidateDecisionIn) -> CandidateDecisionOut:
    return CandidateDecisionOut(stored=memory.decide_candidate(candidate_id, body.remember))


@owner.get("/memory/timeline")
def memory_timeline(field: str | None = None) -> list[FactVersion]:
    return memory.timeline(field)


# --- tasks -----------------------------------------------------------------------------------------------


@owner.post("/tasks")
def create_task(body: TaskIn) -> Task:
    plan = planner.plan(body.instruction)
    task = Task(task_id=new_id("t"), instruction=body.instruction, plan=plan, status="planned", created_at=utc_now())
    db.save_task(task)
    audit.log("task_planned", task.task_id, {"tools": [c.tool for c in plan.calls]})
    return task


@owner.post("/tasks/{task_id}/decision")
def task_decision(task_id: str, body: TaskDecisionIn) -> Task:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.status != "planned":
        raise HTTPException(409, f"task is already {task.status}")
    task.decided_at = utc_now()
    if not body.approve:
        task.status = "rejected"
        db.save_task(task)
        audit.log("task_rejected", task_id, {})
        return task
    task.status = "approved"
    db.save_task(task)
    audit.log("task_approved", task_id, {})
    try:
        task.result = executor.execute(task_id)
    except Exception as exc:  # noqa: BLE001 - the task is recorded as failed, never left half-approved
        task.result = [ToolResult(tool=c.tool, ok=False, detail=f"not run: {type(exc).__name__}: {exc}"[:300])
                       for c in task.plan.calls]
    task.status = "done" if all(r.ok for r in task.result) else "failed"
    db.save_task(task)
    audit.log("task_executed" if task.status == "done" else "task_failed", task_id,
              {"tools": [r.tool for r in task.result], "ok": [r.ok for r in task.result]})
    return task


@owner.get("/tasks")
def tasks() -> list[Task]:
    return db.list_tasks()


# --- queue, requests, wallet, audit, outbox --------------------------------------------------------------


@owner.get("/queue")
def queue() -> QueueOut:
    """All requesters (pending first-contact and paired), undecided requests and planned tasks."""
    return QueueOut(requesters=db.list_requesters(),
                    requests=[r for r in db.list_requests() if r.status != "done"],
                    tasks=db.list_tasks("planned"), wallet=wallet.status())


@owner.post("/requesters/{fp}/decision")
def requester_decision(fp: str, body: RequesterDecisionIn) -> Requester:
    try:
        return pairing.decide(fp, body.approve)
    except pairing.UnknownRequester:
        raise HTTPException(404, "requester not found") from None


@owner.post("/requests/{request_id}/decision")
def request_decision(request_id: str, body: RequestDecisionIn) -> RequestView:
    try:
        return consent.decide_request(request_id, body.action)
    except consent.RequestNotFound:
        raise HTTPException(404, "request not found") from None
    except consent.RequestConflict as exc:
        raise HTTPException(409, str(exc)) from None


@owner.get("/wallet")
def wallet_status() -> WalletStatus:
    return wallet.status()


@owner.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=5000)) -> AuditOut:
    chain = audit.verify_chain()
    return AuditOut(entries=db.audit_entries(limit), chain_intact=chain.intact, broken_at=chain.broken_at)


@owner.get("/outbox")
def outbox() -> list[OutboxItem]:
    if not config.OUTBOX_DIR.is_dir():
        return []
    items = []
    for p in config.OUTBOX_DIR.iterdir():
        kind = _OUTBOX_KINDS.get(p.suffix.lower())
        if p.is_file() and kind:
            st = p.stat()
            created = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            items.append(OutboxItem(name=p.name, kind=kind, size=st.st_size, created_at=created))
    return sorted(items, key=lambda i: i.created_at, reverse=True)


# --- requester-facing (signed, no owner token) -----------------------------------------------------------


def request_channel(request: Request, x_channel: str | None) -> str:
    """`mcp` only when the gate (a loopback client) says so; everyone else is `web`."""
    return "mcp" if x_channel == "mcp" and is_loopback(request) else "web"


@public.post("/ask")
def ask(body: AskIn, request: Request, x_channel: str | None = Header(default=None)) -> AskAck:
    try:
        return consent.receive(body, request_channel(request, x_channel))
    except consent.RequestRejected as exc:
        raise _rejected(exc) from None


@public.get("/ask/{request_id}")
def ask_result(request_id: str, x_requester_fp: str = Header(), x_ts: int = Header(), x_sig: str = Header()
               ) -> AskResult:
    try:
        return consent.poll(request_id, x_requester_fp, x_ts, x_sig)
    except consent.RequestRejected as exc:
        raise _rejected(exc) from None


@public.get("/claims")
def claims() -> ClaimsOut:
    return decide.claims()


app.include_router(owner)
app.include_router(public)


def _openapi() -> dict:
    """FastAPI files the stream's `responses` model under application/json; it is served as text/event-stream."""
    if app.openapi_schema is None:
        schema = FastAPI.openapi(app)
        content = schema["paths"]["/api/chat/stream"]["post"]["responses"]["200"]["content"]
        content["text/event-stream"] = content.pop("application/json")
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _openapi


# --- frontend --------------------------------------------------------------------------------------------

_PLACEHOLDER = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>KAVACH</title></head>"
                "<body><p>Frontend not built. Run <code>npm run build</code> in <code>frontend/</code>.</p></body></html>")

app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets", check_dir=False), name="assets")


def index_html(request: Request) -> HTMLResponse:
    """index.html with window.__KAVACH__ injected; the owner token only goes to loopback clients
    that also sent a loopback Host header."""
    index = config.FRONTEND_DIST / "index.html"
    page = index.read_text(encoding="utf-8") if index.is_file() else _PLACEHOLDER
    boot: dict[str, str] = {"mode": "owner"}
    if is_owner_client(request):
        boot["token"] = config.OWNER_TOKEN
    boot_json = json.dumps(boot).replace("</", "<\\/")
    script = f"<script>window.__KAVACH__={boot_json}</script>"
    page = page.replace("</head>", script + "</head>", 1) if "</head>" in page else script + page
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str, request: Request):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(404, "not found")
    dist = config.FRONTEND_DIST.resolve()
    candidate = (dist / full_path).resolve()
    if full_path and candidate.is_file() and candidate.is_relative_to(dist) and candidate.name != "index.html":
        return FileResponse(candidate)
    return index_html(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT, proxy_headers=False)
