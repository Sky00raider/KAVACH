"""Requester-side identity, signing and storage, shared by app.py (web) and agent_client.py (MCP agent).

Everything lives in DATA_DIR as small files: `<role>.key` (raw private keys, never served), `identity.json`,
`requests.json` (list of RRequest), `nonces.json` (request bookkeeping), `proofs.json` (the presentations and
attestations received: all the requester ever holds) and `owner_keys.json` (owner pairwise keys pinned at pairing).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import TypeAdapter

from kavach import config
from kavach.models import AskResult, RRequest
from kavach.trust import crypto
from requester import verifier

DATA_DIR = Path(os.environ.get("REQUESTER_DATA_DIR", Path(__file__).resolve().parent / "data")).resolve()
ROLES = {
    "web": (os.environ.get("REQUESTER_NAME", "Ravi Kumar"), os.environ.get("REQUESTER_TYPE", "landlord")),
    "agent": (os.environ.get("REQUESTER_AGENT_NAME", "Ravi Kumar's rental agent"),
              os.environ.get("REQUESTER_AGENT_TYPE", "landlord_agent")),
}
_REQUESTS = TypeAdapter(list[RRequest])
_lock = threading.RLock()


def data_dir() -> Path:
    return DATA_DIR


def set_data_dir(path: Path) -> None:
    global DATA_DIR
    DATA_DIR = Path(path).resolve()


# --- files -------------------------------------------------------------------------------------------------


def _read_json(name: str, default: Any) -> Any:
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _write_json(name: str, obj: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".{name}.{uuid4().hex[:6]}.tmp"
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, DATA_DIR / name)


class Identity:
    """One requester keypair (the web requester or the agent)."""

    def __init__(self, role: str):
        self.role = role
        self.name, self.type = ROLES[role]
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            key_path = DATA_DIR / f"{role}.key"
            if not key_path.is_file():
                key_path.write_bytes(crypto.new_private_key())
            self._priv = key_path.read_bytes()
        self.pubkey = crypto.public_key(self._priv)
        self.fingerprint = crypto.fingerprint(self.pubkey)
        with _lock:
            ident = _read_json("identity.json", {})
            if ident.get(role, {}).get("fingerprint") != self.fingerprint:
                ident[role] = {"name": self.name, "type": self.type, "fingerprint": self.fingerprint,
                               "pubkey": self.pubkey}
                _write_json("identity.json", ident)

    def ask_body(self, question: str) -> dict[str, Any]:
        body = {"requester_pubkey": self.pubkey, "requester_name": self.name, "requester_type": self.type,
                "question": question, "nonce": secrets.token_urlsafe(16), "ts": int(time.time())}
        body["sig"] = crypto.sign(self._priv, body)
        return body

    def poll_auth(self, request_id: str) -> dict[str, Any]:
        ts = int(time.time())
        return {"requester_fp": self.fingerprint, "ts": ts,
                "sig": crypto.sign(self._priv, f"{request_id}|{ts}".encode())}


# --- owner HTTP --------------------------------------------------------------------------------------------


def owner_client() -> httpx.Client:
    """The owner's API. Tests replace this with an in-process client."""
    return httpx.Client(base_url=config.OWNER_URL, timeout=httpx.Timeout(60.0, connect=5.0))


class OwnerError(Exception):
    pass


def http_ask(ident: Identity, question: str) -> tuple[dict[str, Any], str]:
    """POST /api/ask; returns (ack, nonce)."""
    body = ident.ask_body(question)
    c = owner_client()
    try:
        r = c.post("/api/ask", json=body)
    except httpx.HTTPError as exc:
        raise OwnerError(f"owner unreachable at {config.OWNER_URL}: {type(exc).__name__}") from exc
    finally:
        c.close()
    if r.status_code != 200:
        raise OwnerError(f"owner refused the request ({r.status_code}: {r.text[:120]})")
    return r.json(), body["nonce"]


def http_poll(ident: Identity, request_id: str) -> AskResult | None:
    auth = ident.poll_auth(request_id)
    headers = {"X-Requester-Fp": auth["requester_fp"], "X-Ts": str(auth["ts"]), "X-Sig": auth["sig"]}
    c = owner_client()
    try:
        r = c.get(f"/api/ask/{request_id}", headers=headers)
    except httpx.HTTPError:
        return None
    finally:
        c.close()
    return AskResult.model_validate(r.json()) if r.status_code == 200 else None


# --- request records ---------------------------------------------------------------------------------------


def load_requests() -> list[RRequest]:
    path = DATA_DIR / "requests.json"
    return _REQUESTS.validate_json(path.read_bytes()) if path.is_file() else []


def _save_requests(reqs: list[RRequest]) -> None:
    _write_json("requests.json", [r.model_dump(mode="json") for r in reqs])


def record_request(ident: Identity, question: str, ack: dict[str, Any], nonce: str) -> RRequest:
    rec = RRequest(local_id=f"l_{uuid4().hex[:10]}", request_id=ack["request_id"], question=question,
                   status=ack["status"], via=ident.role, result=None)
    with _lock:
        reqs = load_requests()
        reqs.append(rec)
        _save_requests(reqs)
        nonces = _read_json("nonces.json", {})
        nonces[rec.local_id] = {"request_id": rec.request_id, "nonce": nonce, "role": ident.role}
        _write_json("nonces.json", nonces)
    return rec


def apply_answer(ident: Identity, local_id: str, res: AskResult) -> RRequest | None:
    """Store the answer, pin the owner's pairwise key on first sight, verify on arrival."""
    with _lock:
        reqs = load_requests()
        rec = next((r for r in reqs if r.local_id == local_id), None)
        meta = _read_json("nonces.json", {}).get(local_id)
        if rec is None or meta is None:
            return None
        pins = _read_json("owner_keys.json", {})
        if res.owner_pairwise_pubkey and ident.fingerprint not in pins:
            pins[ident.fingerprint] = res.owner_pairwise_pubkey
            _write_json("owner_keys.json", pins)
        rec.status = res.status
        if res.status == "done" and res.answer_type is not None:
            if res.payload is not None:
                proofs = _read_json("proofs.json", {})
                proofs[rec.request_id] = {"answer_type": res.answer_type, "payload": res.payload}
                _write_json("proofs.json", proofs)
            rec.result = verifier.verify(res.answer_type, res.payload, meta["nonce"], ident.fingerprint,
                                         owner_pairwise_pubkey=pins.get(ident.fingerprint))
        _save_requests(reqs)
        return rec


def refresh_all() -> list[RRequest]:
    """Poll the owner for every request that has no final result yet (web and agent identities)."""
    idents: dict[str, Identity] = {}
    nonces = _read_json("nonces.json", {})
    for rec in load_requests():
        if rec.result is not None or rec.local_id not in nonces:
            continue
        role = nonces[rec.local_id]["role"]
        ident = idents.setdefault(role, Identity(role))
        res = http_poll(ident, rec.request_id)
        if res is not None:
            apply_answer(ident, rec.local_id, res)
    return load_requests()
