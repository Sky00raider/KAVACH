"""Check that the requester laptop is ready for the two-laptop demo (BUILD_PLAN.md section 10).

Usage (on the requester laptop):
    python scripts/requester_preflight.py --owner http://<owner-ip>:8000          run every check
    python scripts/requester_preflight.py --owner http://<owner-ip>:8000 --reset  also clear old requests first
    python scripts/requester_preflight.py --fingerprints                          issuer key fingerprints only

Checks: the built frontend, the issuer trust list (the owner laptop's keys/issuers/trusted_issuers.json copied to
requester/data/), the owner API and the MCP gate reachable, the two clocks close enough for the 120 s request
window, and no requests or pinned owner key left from an earlier run. `--reset` removes requests.json, nonces.json,
proofs.json and owner_keys.json; the requester's keys, identity and trust list stay.

`--fingerprints` prints the fingerprints of this machine's own issuer keys: run it on the owner laptop and compare
with the trust list this script prints on the requester laptop. The trust list is never fetched from the owner: a
verifier that asks the prover which issuers to trust checks nothing.

The requester side makes no model calls, so this laptop needs no Ollama.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach import config  # noqa: E402
from kavach.mock_issuers import make_keys  # noqa: E402
from kavach.trust import crypto  # noqa: E402
from requester import common, verifier  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"
Check = tuple[str, str, str]  # (status, name, detail)

REQUEST_WINDOW_S = 120   # CONTRACT section 9: ts must be within 120 s of the owner's clock
SKEW_WARN_S = 30
SKEW_FAIL_S = 90
STALE_FILES = ("requests.json", "nonces.json", "proofs.json", "owner_keys.json")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def fingerprints(trust: dict[str, str]) -> dict[str, str]:
    """iss -> fingerprint of its public key (`?` for a key that is not valid)."""
    out = {}
    for iss, pub in sorted(trust.items()):
        try:
            out[iss] = crypto.fingerprint(pub)
        except Exception:  # noqa: BLE001 - a bad key is reported, not fatal
            out[iss] = "?"
    return out


def check_frontend() -> Check:
    index = config.FRONTEND_DIST / "index.html"
    if index.is_file():
        return OK, "frontend", f"built ({index})"
    return FAIL, "frontend", "not built: run `npm install` then `npm run build` in frontend/"


def check_trust_list(owner_url: str) -> Check:
    path = verifier.trust_list_path()
    trust = verifier.trusted_issuers()
    missing = [iss for iss in make_keys.ISSUERS if iss not in trust]
    prints = ", ".join(f"{iss} {fp}" for iss, fp in fingerprints(trust).items())
    if missing:
        return FAIL, "trust list", (f"{path} lacks {', '.join(missing)}: copy keys/issuers/trusted_issuers.json "
                                    f"from the owner laptop to {common.data_dir()}")
    remote = urlsplit(owner_url).hostname not in LOOPBACK
    if remote and trust == make_keys.trust_list():
        return WARN, "trust list", (f"{path} holds THIS laptop's issuer keys ({prints}); with the owner on another "
                                    f"laptop every proof fails 'Issuer signature' unless the issuer keys were copied "
                                    f"here too. Compare with `--fingerprints` on the owner laptop")
    return OK, "trust list", f"{path}: {prints}"


def check_owner(owner_url: str) -> tuple[Check, httpx.Response | None]:
    try:
        r = httpx.get(f"{owner_url.rstrip('/')}/api/claims", timeout=httpx.Timeout(5.0))
    except httpx.HTTPError as exc:
        return (FAIL, "owner API", f"{owner_url} unreachable ({type(exc).__name__}): same network? owner started "
                                   f"with --host 0.0.0.0? port {urlsplit(owner_url).port or 80} open in its firewall?"), None
    if r.status_code != 200:
        return (FAIL, "owner API", f"{owner_url}/api/claims returned {r.status_code}"), r
    return (OK, "owner API", f"{owner_url} answers"), r


def check_clock(response: httpx.Response | None, now: datetime | None = None) -> Check:
    """Skew from the owner's `Date` header (1 s resolution)."""
    if response is None or "date" not in response.headers:
        return WARN, "clock", "could not read the owner's clock; check both laptops show the same time"
    try:
        theirs = parsedate_to_datetime(response.headers["date"])
    except (TypeError, ValueError):
        return WARN, "clock", "owner sent an unreadable Date header; check both laptops show the same time"
    skew = abs(((now or datetime.now(timezone.utc)) - theirs).total_seconds())
    detail = f"{skew:.0f} s apart (requests are rejected beyond {REQUEST_WINDOW_S} s)"
    if skew > SKEW_FAIL_S:
        return FAIL, "clock", detail + ": set both laptops to the same time"
    return (WARN if skew > SKEW_WARN_S else OK), "clock", detail


def check_gate(owner_url: str) -> Check:
    host = urlsplit(owner_url).hostname or "127.0.0.1"
    try:
        with socket.create_connection((host, config.GATE_PORT), timeout=3):
            pass
    except OSError as exc:
        return WARN, "MCP gate", (f"{host}:{config.GATE_PORT} not reachable ({type(exc).__name__}): the agent demo "
                                  f"needs `python -m kavach.gate_mcp` on the owner laptop and the port open")
    return OK, "MCP gate", f"{host}:{config.GATE_PORT} accepts connections"


def check_state(reset: bool) -> Check:
    d = common.data_dir()
    present = [n for n in STALE_FILES if (d / n).is_file()]
    if reset:
        for n in present:
            (d / n).unlink()
        return OK, "old state", f"removed {', '.join(present)}" if present else "nothing to remove"
    n_requests = len(common.load_requests())
    pins = json.loads((d / "owner_keys.json").read_text(encoding="utf-8")) if (d / "owner_keys.json").is_file() else {}
    if n_requests or pins:
        return WARN, "old state", (f"{n_requests} earlier request(s) and {len(pins)} pinned owner key(s) in {d}: an "
                                   f"owner key pinned from another owner database makes every owner-attested answer "
                                   f"fail 'Owner signature'. Run again with --reset")
    return OK, "old state", "no earlier requests or pinned owner keys"


def run(owner_url: str, reset: bool) -> list[Check]:
    owner, response = check_owner(owner_url)
    return [check_frontend(), check_trust_list(owner_url), owner, check_clock(response), check_gate(owner_url),
            check_state(reset)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default=config.OWNER_URL, help="owner API base URL (default: $OWNER_URL)")
    parser.add_argument("--reset", action="store_true", help="remove earlier requests, proofs and pinned owner keys")
    parser.add_argument("--fingerprints", action="store_true", help="print this machine's issuer key fingerprints")
    args = parser.parse_args(argv)

    if args.fingerprints:
        own = fingerprints(make_keys.trust_list())
        if not own:
            print(f"No issuer keys in {make_keys.issuers_dir()} (run scripts/reset_demo.py on the owner laptop)")
            return 1
        for iss, fp in own.items():
            print(f"{iss}  {fp}")
        return 0

    checks = run(args.owner, args.reset)
    for status, name, detail in checks:
        print(f"{status:<5} {name}: {detail}")
    failed = any(status == FAIL for status, _, _ in checks)
    print()
    if failed:
        print("Not ready: fix the FAIL lines above.")
        return 1
    print("Ready. Start the requester (PowerShell):")
    print(f'  $env:OWNER_URL = "{args.owner}"')
    print(f"  uvicorn requester.app:app --host 127.0.0.1 --port {config.REQUESTER_PORT}")
    print(f"  then open http://127.0.0.1:{config.REQUESTER_PORT}")
    print("Landlord agent over MCP (same OWNER_URL):  python -m requester.agent_client")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
