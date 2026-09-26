# AGENTS.md: KAVACH agents brief

Read this fully before any task. Then read only the CONTRACT.md sections your task needs.

## What KAVACH is
A local-first second brain for an individual. It ingests their documents, notes and chats into a knowledge graph with temporal memory, answers the owner with citations, and executes owner-approved tasks. Outsiders and their AI agents get issuer-verifiable minimal disclosures (SD-JWT-style), never documents. Every request, decision and action lands in a hash-chained audit log.

## Hard rules (never break these)
1. **No cloud inference at runtime.** All model calls go to local Ollama via `config.OLLAMA_URL`. No OpenAI, Anthropic, Gemini or any hosted API in product code. No CDNs; the frontend bundles every asset.
2. **The LLM never decides.** It extracts, summarises, maps questions to claims and proposes tool calls. Plain code compares values, checks signatures, validates tool arguments and decides disclosures.
3. **Every fact is grounded.** Extraction returns an exact quote; code checks the quote exists in the source and contains the value. Otherwise `confidence = "low"`.
4. **`owner_stated` facts never become proofs.** Used in chat only, never in disclosures.
5. **Nothing leaves except presentations, owner attestations and approved task outputs.** No requester-reachable endpoint or MCP tool returns document text, chunks or raw facts.
6. **Everything is audited**, refused requests included, via `trust.audit.log()`.
7. **CONTRACT.md is the source of truth.** If your task needs an interface change, stop and propose the change (what, why, who it affects). Do not silently change schemas, endpoints, function signatures or JSON shapes.
8. **Stay in your track's directories** (table below). Touching another track's files needs an explicit instruction from the human.
9. **No copied code.** Use libraries normally; do not paste code from other repos or tutorials.

## Tracks and ownership
| Track | Directories / files | Owner |
|---|---|---|
| CONTRACT | `CONTRACT.md`, `kavach/models.py`, `fixtures/`, frontend shell: `frontend/src/{api,shell,components}/`, `frontend/*.config.*` | TBD |
| BRAIN | `kavach/db.py`, `kavach/brain/`, `kavach/agent/planner.py`, `frontend/src/pages/brain/` (Ask, Vault, Memory) | TBD |
| TRUST | `kavach/trust/`, `kavach/mock_issuers/`, `kavach/agent/executor.py`, `kavach/api.py`, `kavach/gate_mcp.py`, `kavach/tools_mcp.py`, `requester/`, `scripts/run_eval.py`, `frontend/src/pages/trust/` (Queue, Verify, Audit) | TBD |
| DATA | `demo_data/` content, `eval/` question sets, `README.md`, `docs/pitch/`. Non-code track: agents only touch these files when the owner asks | TBD |

Each code track builds the UI pages for its own features. Shared UI (layout, sidebar, API client, theme, shadcn components) lives in the shell; if you need a new shared component, add it under `frontend/src/components/shared/` and mention it in the commit message.

## Stack
- Python 3.11+, FastAPI + Uvicorn, Pydantic v2, SQLite, NumPy (vector search, no sqlite-vec), PyMuPDF, watchdog, cryptography (Ed25519, SHA-256), `mcp` Python SDK (FastMCP), httpx, pytest.
- Ollama: models are named only in `config.py` (`LLM_MODEL`, `FAST_MODEL`, `EMBED_MODEL`). Never hard-code a model name elsewhere.
- Frontend: React + Vite + TypeScript + Tailwind + shadcn/ui, `react-force-graph-2d` for the graph. Built to `frontend/dist`, served by FastAPI.

## Conventions
- All request/response and cross-module types live in `kavach/models.py` (Pydantic). Import them; never redefine a shape locally.
- IDs: `{prefix}_{uuid4().hex[:10]}` with prefixes `d` doc, `c` chunk, `e` entity, `x` edge, `f` fact, `cr` credential, `rq` request, `t` task, `mc` memory candidate.
- Money is integer rupees. Dates are ISO `YYYY-MM-DD`; timestamps are UTC ISO 8601 with `Z`.
- LLM calls: Ollama structured output (`format` = JSON schema from the Pydantic model), `temperature: 0`. Validate the result with Pydantic; on failure, retry once, then return a typed error.
- DB access only through helpers in `kavach/db.py`.
- Owner API routes are under `/api`. Requester backend routes are under `/r`.

## Commands
```
python -m venv .venv                                  # once (Python 3.11+)
source .venv/bin/activate                             # macOS / Linux
pip install -r requirements.txt
uvicorn kavach.api:app --host 0.0.0.0 --port 8000     # owner API + built UI
python -m kavach.gate_mcp                             # inbound MCP gate on :8001
uvicorn requester.app:app --host 0.0.0.0 --port 9000  # requester laptop
cd frontend && npm run dev                            # UI dev server :5173, proxies /api
cd frontend && npm run gen:types                      # regenerate TS types from OpenAPI
pytest -m "not llm"                                   # fast tests
pytest                                                # all tests, needs Ollama running
python scripts/reset_demo.py                          # restore clean demo state
python scripts/bench_models.py                        # time the config.py models on local Ollama
```

Windows (PowerShell) differences:
```
py -3.12 -m venv .venv                                # or any 3.11+
.\.venv\Scripts\Activate.ps1                          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
cd frontend; npm run dev                              # PowerShell 5.1 has no &&; use ; or separate lines
$env:VITE_USE_FIXTURES = "1"; npm run dev             # env vars: $env:NAME = "value", not NAME=value cmd
```
Everything else is identical. Scripts are Python, not shell, so they run the same on every OS.

Progress lives in docs/STATUS.md; update your track's section in every commit that completes a step.

## Tests and commits
- Every module gets pytest tests. Tests that call Ollama are marked `@pytest.mark.llm`.
- Run `pytest -m "not llm"` before every commit; it must pass.
- Small commits, one logical change each, Conventional Commits with the track as scope: `feat(brain): hybrid search`, `fix(trust): nonce reuse check`, `docs(contract): add wallet endpoint`.
- Never commit keys, `kavach.db`, `vault/`, `private/` or anything under `.gitignore`.
- Any design change: add one line to `docs/DECISIONS.md` in the same commit.

## Where to look
- Interfaces, schemas, endpoints, JSON formats: `CONTRACT.md`
- Component behaviour, build order, milestones, cut list: `docs/BUILD_PLAN.md`
- Why something is the way it is: `docs/DECISIONS.md`
- `docs/archive/` is history only. Do not read it unless asked.
