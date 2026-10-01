"""Evaluation runner (BUILD_PLAN.md section 7). Writes eval/results/<utc>.json and prints a summary.

Usage:
    python scripts/run_eval.py                 every set whose input exists (C always runs)
    python scripts/run_eval.py --sets B,C      only these sets

Set C (attacks) is self-contained: it builds a throwaway runtime in a temp dir (own issuer keys, wallet, owner API
and requester in-process) and runs the six attacks. Sets A, B, D and E read eval/set_<x>.jsonl (one JSON object per
line, written by DATA) and run against a temporary COPY of kavach.db, so the demo state is never changed:

    A  {"id", "question", "expect_any": ["substring", ...], "expect_docs": ["pdfs/x.pdf", ...], "real": bool}
       correct if the answer contains any expected substring (case-insensitive); citation correct if a citation
       points at one of expect_docs. Needs Ollama.
    B  {"id", "question", "expect": "ISSUER_PROOF|OWNER_ATTESTED|DECLINED|CANNOT_CONFIRM|REFUSED",
        "expect_result": true|false|null, "real": bool}
       parse_question + decide only (nothing is disclosed). A wrong disclosure is a proposed proof or attestation
       where none was expected, or one whose result differs from expect_result.
    D  {"id", "instruction", "expect_tools": ["draft_email", ...], "expect_to": "a@b.in"|null,
        "expect_date": "YYYY-MM-DD"|null}
       planner.plan + the executor's validation; nothing is executed.
    E  {"id", "statement", "field", "expect_value", "expect_scheduled"?}
       memory.teach, then the field's current timeline value must equal expect_value, and every other version must
       be superseded/closed or scheduled (open, valid_from after today: "from January" does not change today's
       value). With expect_scheduled, a scheduled version must hold that value.

Only measured numbers belong in the deck; the report states the sample size of every set.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach import config, db  # noqa: E402

EVAL_DIR = config.ROOT / "eval"
PROOFS = ("ISSUER_PROOF", "OWNER_ATTESTED")


def _load(name: str) -> list[dict[str, Any]] | None:
    path = EVAL_DIR / f"set_{name.lower()}.jsonl"
    if not path.is_file():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _timed(fn: Callable[[], Any]) -> tuple[Any, float, str | None]:
    t0 = time.perf_counter()
    try:
        return fn(), time.perf_counter() - t0, None
    except Exception as exc:  # noqa: BLE001 - a crash is a failed case, reported with its type
        return None, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"[:300]


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 3) if xs else None


class _Runtime:
    """Point config at a temp dir; restore on exit. `copy_db` starts from a copy of the real kavach.db."""

    ATTRS = ("DB_PATH", "VAULT_DIR", "OUTBOX_DIR", "KEYS_DIR", "DEMO_DATA_DIR")

    def __init__(self, copy_db: bool):
        self.copy_db = copy_db

    def __enter__(self) -> Path:
        self.saved = {a: getattr(config, a) for a in self.ATTRS}
        self.tmp = Path(tempfile.mkdtemp(prefix="kavach-eval-"))
        src_db = config.DB_PATH
        config.DB_PATH = self.tmp / "kavach.db"
        config.VAULT_DIR, config.OUTBOX_DIR = self.tmp / "vault", self.tmp / "outbox"
        if self.copy_db:
            if src_db.is_file():
                with db.connect(src_db) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                shutil.copy2(src_db, config.DB_PATH)
        else:
            config.KEYS_DIR, config.DEMO_DATA_DIR = self.tmp / "keys", self.tmp / "demo_data"
        db.init_db()
        return self.tmp

    def __exit__(self, *exc) -> None:
        for a, v in self.saved.items():
            setattr(config, a, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


# --- set C: attacks ----------------------------------------------------------------------------------------


def run_attacks() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from kavach import api
    from kavach.mock_issuers import issue, make_keys
    from kavach.models import Claim, Proposal
    from kavach.trust import consent, crypto, issuer_check, wallet
    from requester import common, verifier

    cases: list[dict[str, Any]] = []

    def case(name: str, blocked: bool, how: str) -> None:
        cases.append({"id": name, "blocked": bool(blocked), "how": how})

    with _Runtime(copy_db=False) as tmp:
        saved_dir, saved_client = common.DATA_DIR, common.owner_client
        common.DATA_DIR = tmp / "requester"
        real_parse, real_decide = consent.parse_question.parse, consent.decide.decide

        # The attacks exercise the trust layer, not question parsing: the claim is fixed in code, so the set
        # gives the same result with Ollama stopped or no model pulled.
        def pinned(question: str) -> Claim:
            return Claim(claim="income", op="ge", value=50000)

        consent.parse_question.parse = pinned
        try:
            make_keys.export_trust_list(common.DATA_DIR / "trusted_issuers.json")
            pubkeys = json.loads(wallet.export_holder_pubkeys(10).read_text(encoding="utf-8"))
            batch = tmp / "batch.json"
            batch.write_text(json.dumps(issue.issue_batch("income_proof", pubkeys)), encoding="utf-8")
            wallet.import_batch(batch)
            owner = TestClient(api.app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8000")
            tok = {"X-Owner-Token": config.OWNER_TOKEN}
            common.owner_client = lambda: TestClient(api.app, client=("192.168.50.2", 2), base_url="http://owner")

            def approve_all(action: str = "approve") -> None:
                q = owner.get("/api/queue", headers=tok).json()
                for r in q["requesters"]:
                    if r["status"] == "pending":
                        owner.post(f"/api/requesters/{r['fingerprint']}/decision", headers=tok, json={"approve": True})
                for r in owner.get("/api/queue", headers=tok).json()["requests"]:
                    if r["status"] == "pending":
                        owner.post(f"/api/requests/{r['request_id']}/decision", headers=tok,
                                   json={"action": action if action in r["proposal"]["actions"] else
                                         r["proposal"]["actions"][0]})

            # 1. tampered document
            pdfs = {p.name: p for p in issue.make_pdfs(tmp / "generated")}
            sig = issuer_check.verify_pdf(pdfs["bank_statement_TAMPERED.pdf"])
            case("tampered_document", sig.status == "invalid", f"issuer_check -> {sig.status} ({sig.detail})")

            # a genuine presentation to attack
            ident = common.Identity("web")
            ack, nonce = common.http_ask(ident, "Does the tenant earn at least 50k?")
            rec = common.record_request(ident, "Does the tenant earn at least 50k?", ack, nonce)
            approve_all()
            common.refresh_all()
            pres = json.loads((common.DATA_DIR / "proofs.json").read_text(encoding="utf-8"))[rec.request_id]["payload"]
            genuine = verifier.verify("ISSUER_PROOF", pres, nonce, ident.fingerprint)

            # 2. replayed presentation (same proof offered for a new request)
            replay = verifier.verify("ISSUER_PROOF", pres, "fresh-nonce-of-a-new-request", ident.fingerprint)
            body = ident.ask_body("Does the tenant earn at least 50k?")
            first, again = (owner.post("/api/ask", json=body).status_code, owner.post("/api/ask", json=body).status_code)
            case("replayed_presentation", genuine.all_ok and not replay.all_ok and again == 409,
                 f"verifier nonce check failed; replayed ask -> {first}/{again}")

            # 3. forwarded presentation (landlord passes it to someone else)
            other_fp = crypto.fingerprint(crypto.public_key(crypto.new_private_key()))
            fwd = verifier.verify("ISSUER_PROOF", pres, nonce, other_fp)
            case("forwarded_presentation", not fwd.all_ok, "audience is the original requester's key")

            # 4. altered disclosure value
            altered = json.loads(json.dumps(pres))
            altered["disclosures"][0]["claim"] = "income_ge_100000"
            alt = verifier.verify("ISSUER_PROOF", altered, nonce, ident.fingerprint)
            case("altered_disclosure", not alt.all_ok,
                 ", ".join(c.name for c in alt.checks if not c.ok) + " failed")

            # 5. colluding narrowing: three paired keys probe 60k / 70k / 65k (decide fixed to OWNER_ATTESTED)
            salary, answers = 62000, []
            try:
                for t in (60000, 70000, 65000):
                    consent.parse_question.parse = lambda q, t=t: Claim(claim="income", op="ge", value=t)

                    def attested(c: Claim, fp: str) -> Proposal:
                        r = salary >= c.value
                        return Proposal(answer_type="OWNER_ATTESTED", claim=c, result=r, favourable=r,
                                        actions=["approve", "deny"] if r else ["answer", "decline"], reason="eval")

                    consent.decide.decide = attested
                    colluder = crypto.new_private_key()
                    b = {"requester_pubkey": crypto.public_key(colluder), "requester_name": f"colluder {t}",
                         "requester_type": "landlord", "question": f">= {t}?", "nonce": f"n{t}", "ts": int(time.time())}
                    rid = owner.post("/api/ask", json=b | {"sig": crypto.sign(colluder, b)}).json()["request_id"]
                    approve_all(action="answer")
                    answers.append(db.fetch_one("SELECT answer_type FROM requests WHERE request_id = ?",
                                                (rid,))["answer_type"])
            finally:
                consent.parse_question.parse, consent.decide.decide = pinned, real_decide
            case("colluding_narrowing", answers[1:] == ["REFUSED", "REFUSED"], f"answers {answers}")

            # 6. unpaired requester
            stranger = common.Identity("agent")
            ack2, _ = common.http_ask(stranger, "Does the tenant earn at least 50k?")
            res = common.http_poll(stranger, ack2["request_id"])
            case("unpaired_requester", res is not None and res.status == "pending_pairing" and res.payload is None,
                 f"status {res.status if res else 'n/a'}, payload withheld")

            chain = owner.get("/api/audit", headers=tok).json()
        finally:
            common.DATA_DIR, common.owner_client = saved_dir, saved_client
            consent.parse_question.parse, consent.decide.decide = real_parse, real_decide
    return {"n": len(cases), "blocked": sum(c["blocked"] for c in cases), "audit_chain_intact": chain["chain_intact"],
            "cases": cases}


# --- sets A, B, D, E ---------------------------------------------------------------------------------------


def run_a(rows: list[dict]) -> dict[str, Any]:
    from kavach.brain import chat

    out, lat = [], []
    with _Runtime(copy_db=True):
        for r in rows:
            res, dt, err = _timed(lambda: chat.answer(r["question"], []))
            lat.append(dt)
            docs = {d["path"]: d["doc_id"] for d in db.fetch_all("SELECT path, doc_id FROM documents")}
            want = {docs.get(p) for p in r.get("expect_docs", [])} - {None}
            ok = bool(res) and any(s.lower() in res.answer.lower() for s in r.get("expect_any", []))
            cited = bool(res) and any(c.doc_id in want for c in res.citations)
            out.append({"id": r["id"], "real": r.get("real", False), "correct": ok, "citation_correct": cited,
                        "error": err, "latency_s": round(dt, 2)})
    return _summary(out, lat, extra=("correct", "citation_correct"))


def run_b(rows: list[dict]) -> dict[str, Any]:
    from kavach.brain import decide, parse_question

    out, lat = [], []
    with _Runtime(copy_db=True):
        for r in rows:
            res, dt, err = _timed(lambda: decide.decide(parse_question.parse(r["question"]), "eval"))
            lat.append(dt)
            got = res.answer_type if res else "ERROR"
            wrong = got in PROOFS and (r["expect"] not in PROOFS or
                                       (r.get("expect_result") is not None and res.result != r["expect_result"]))
            out.append({"id": r["id"], "real": r.get("real", False), "correct": got == r["expect"],
                        "got": got, "wrong_disclosure": wrong, "error": err, "latency_s": round(dt, 2)})
    s = _summary(out, lat, extra=("correct",))
    s["wrong_disclosures"] = sum(o["wrong_disclosure"] for o in out)
    return s


def run_d(rows: list[dict]) -> dict[str, Any]:
    from kavach.agent import executor, planner

    out, lat = [], []
    with _Runtime(copy_db=True):
        for r in rows:
            plan, dt, err = _timed(lambda: planner.plan(r["instruction"]))
            lat.append(dt)
            ok = plan is not None and [c.tool for c in plan.calls] == r.get("expect_tools", [])
            if ok and r.get("expect_to"):
                ok = any(getattr(c.args, "to", None) == r["expect_to"] for c in plan.calls)
            if ok and r.get("expect_date"):
                ok = any(getattr(c.args, "date", None) == r["expect_date"] for c in plan.calls)
            valid = plan is not None and all(executor.validate_call(c, r["instruction"]) is None for c in plan.calls)
            out.append({"id": r["id"], "correct": ok, "passes_validation": valid, "error": err,
                        "latency_s": round(dt, 2), "executed": False})
    return _summary(out, lat, extra=("correct", "passes_validation"))


def score_e(versions: list, expect_value: Any, expect_scheduled: Any = None, today: str | None = None) -> bool:
    """Set E: exactly one current version with expect_value; every other one closed (superseded / valid_to) or
    scheduled (still open, valid_from after today); expect_scheduled, if given, among the scheduled values."""
    today = today or date.today().isoformat()
    current = [v for v in versions if v.current]
    scheduled = [v for v in versions if not v.current and not v.superseded_by and not v.valid_to
                 and (v.valid_from or "") > today]
    if len(current) != 1 or str(current[0].value) != str(expect_value):
        return False
    if not all(v.superseded_by or v.valid_to or v in scheduled for v in versions if not v.current):
        return False
    return expect_scheduled is None or any(str(v.value) == str(expect_scheduled) for v in scheduled)


def run_e(rows: list[dict]) -> dict[str, Any]:
    from kavach.brain import memory

    out, lat = [], []
    with _Runtime(copy_db=True):
        for r in rows:
            _, dt, err = _timed(lambda: memory.teach(r["statement"]))
            lat.append(dt)
            versions = memory.timeline(r["field"]) if err is None else []
            ok = score_e(versions, r["expect_value"], r.get("expect_scheduled"))
            out.append({"id": r["id"], "correct": ok, "error": err, "latency_s": round(dt, 2)})
    return _summary(out, lat, extra=("correct",))


def _summary(out: list[dict], lat: list[float], extra: tuple[str, ...]) -> dict[str, Any]:
    s: dict[str, Any] = {"n": len(out), "median_latency_s": _median(lat), "cases": out}
    for key in extra:
        s[key] = sum(bool(o.get(key)) for o in out)
        if any("real" in o for o in out):
            for label, flag in (("real", True), ("synthetic", False)):
                sub = [o for o in out if o.get("real", False) == flag]
                s[f"{key}_{label}"] = f"{sum(bool(o.get(key)) for o in sub)}/{len(sub)}"
    return s


RUNNERS = {"A": run_a, "B": run_b, "D": run_d, "E": run_e}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sets", default="A,B,C,D,E", help="comma-separated subset of A,B,C,D,E")
    parser.add_argument("--out", type=Path, help="results file (default eval/results/<utc>.json)")
    args = parser.parse_args(argv)
    wanted = [s.strip().upper() for s in args.sets.split(",") if s.strip()]
    report: dict[str, Any] = {"ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sets": {}}
    for name in wanted:
        if name == "C":
            report["sets"]["C"] = run_attacks()
        elif name in RUNNERS:
            rows = _load(name)
            if rows is None:
                print(f"set {name}: eval/set_{name.lower()}.jsonl not found, skipped")
                continue
            report["sets"][name] = RUNNERS[name](rows)
    out = args.out or EVAL_DIR / "results" / f"{report['ran_at'].replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    for name, s in report["sets"].items():
        head = {k: v for k, v in s.items() if k != "cases"}
        print(f"set {name} (n={s['n']}): {json.dumps(head, ensure_ascii=False)}")
        if name == "C":
            for c in s["cases"]:
                print(f"   {'BLOCKED' if c['blocked'] else 'NOT BLOCKED'}  {c['id']}: {c['how']}")
    print(f"results: {out}")
    c = report["sets"].get("C")
    return 0 if c is None or c["blocked"] == c["n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
