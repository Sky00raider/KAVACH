"""Time the config.py models against a running Ollama (BUILD_PLAN.md section 3, step 5).

Measures chat first token, full answer, a structured question parse and embedding 100 chunks.
Request bodies come from kavach.brain.llm, so this times exactly what the product sends
(structured calls with think=false; embeddings as one batched /api/embed call).
Targets on the owner laptop: first token < 5 s, parse < 3 s, 100 embeddings < 20 s.

Usage: python scripts/bench_models.py [--runs 3] [--llm MODEL] [--fast MODEL] [--embed MODEL]
Record the results and the chosen models in docs/DECISIONS.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach import config  # noqa: E402
from kavach.brain import llm  # noqa: E402
from kavach.models import Claim  # noqa: E402

TARGETS = {"first_token_s": 5.0, "parse_s": 3.0, "embed_100_s": 20.0}
CHAT_PROMPT = [
    {"role": "system", "content": "Answer only from the context. Cite every sentence with [n]."},
    {"role": "user", "content": "Context:\n[1] Monthly rent: Rs. 15,000 payable on or before the 5th.\n"
                                "[2] This agreement is valid until 31/03/2027.\n\n"
                                "Question: How much is my rent and when does the agreement end?"},
]
PARSE_PROMPT = [
    {"role": "system", "content": "Map the question to a claim. claim is one of income, loan_default_12m, age, "
                                  "percentage, result, board, unsupported. op is ge, is or eq."},
    {"role": "user", "content": "Can you confirm the tenant makes at least 50k a month?"},
]
CHUNK = ("Salary credit from Acme Analytics of Rs. 62,000.00 on 01/04/2026. Rent paid to Ramesh Kumar Rs. 15,000. "
         "Electricity bill Rs. 1,240. Transfer to savings Rs. 10,000. Closing balance Rs. 48,315.22. ") * 4


def bench_chat(client: httpx.Client, model: str) -> tuple[float, float]:
    start = time.perf_counter()
    first = None
    body = llm.chat_request(CHAT_PROMPT, model, stream=True)
    with client.stream("POST", llm.CHAT_PATH, json=body) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            msg = json.loads(line)
            if first is None and msg.get("message", {}).get("content"):
                first = time.perf_counter() - start
            if msg.get("done"):
                break
    total = time.perf_counter() - start
    return (first if first is not None else total), total


def bench_parse(client: httpx.Client, model: str) -> tuple[float, bool]:
    start = time.perf_counter()
    resp = client.post(llm.CHAT_PATH, json=llm.structured_request(PARSE_PROMPT, Claim, model))
    resp.raise_for_status()
    elapsed = time.perf_counter() - start
    try:
        claim = Claim.model_validate_json(resp.json()["message"]["content"])
        ok = claim.claim == "income" and claim.op == "ge" and claim.value == 50000
    except ValueError:
        ok = False
    return elapsed, ok


def bench_embed(client: httpx.Client, model: str, n: int = 100) -> tuple[float, int]:
    start = time.perf_counter()
    resp = client.post(llm.EMBED_PATH, json=llm.embed_request([f"{i}: {CHUNK}" for i in range(n)], model))
    resp.raise_for_status()
    dim = len(resp.json()["embeddings"][0])
    return time.perf_counter() - start, dim


def _fmt(values: list[float], target: float | None = None) -> str:
    med = statistics.median(values)
    mark = "" if target is None else ("  OK" if med < target else "  SLOW")
    return f"median {med:6.2f}s  (min {min(values):.2f}, max {max(values):.2f}){mark}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--llm", default=config.LLM_MODEL)
    parser.add_argument("--fast", default=config.FAST_MODEL)
    parser.add_argument("--embed", default=config.EMBED_MODEL)
    args = parser.parse_args()

    client = httpx.Client(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(300.0, connect=5.0))
    try:
        tags = client.get("/api/tags").json()
    except httpx.HTTPError as exc:
        print(f"Ollama not reachable at {config.OLLAMA_URL}: {exc}")
        return 1
    have = {m["name"] for m in tags.get("models", [])}
    missing = [m for m in {args.llm, args.fast, args.embed} if m not in have and f"{m}:latest" not in have]
    if missing:
        print(f"Missing models: {', '.join(missing)}. Run: ollama pull <model>")
        return 1

    print(f"Ollama {config.OLLAMA_URL}  llm={args.llm}  fast={args.fast}  embed={args.embed}  runs={args.runs}")
    print("Warming up (first load is excluded)...")
    bench_chat(client, args.llm)
    bench_parse(client, args.fast)
    bench_embed(client, args.embed, n=2)

    firsts, totals, parses, embeds = [], [], [], []
    parse_ok = 0
    dim = 0
    for _ in range(args.runs):
        f, t = bench_chat(client, args.llm)
        firsts.append(f)
        totals.append(t)
        p, ok = bench_parse(client, args.fast)
        parses.append(p)
        parse_ok += ok
        e, dim = bench_embed(client, args.embed)
        embeds.append(e)

    print(f"chat first token  {_fmt(firsts, TARGETS['first_token_s'])}")
    print(f"chat full answer  {_fmt(totals)}")
    print(f"question parse    {_fmt(parses, TARGETS['parse_s'])}  correct {parse_ok}/{args.runs}")
    print(f"embed 100 chunks  {_fmt(embeds, TARGETS['embed_100_s'])}  dim {dim}"
          + ("" if dim == config.EMBED_DIM else f"  (config EMBED_DIM={config.EMBED_DIM}, update it)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
