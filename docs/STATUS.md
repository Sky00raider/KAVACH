# STATUS.md

Progress per track. Update your track's section in every commit that completes a step.
Step numbers refer to `docs/BUILD_PLAN.md` §6.

## Milestones

| Milestone | Due | Status |
|---|---|---|
| M0 Setup | Sat 26, night | In progress |
| M1 CP1 | Sun 27, night | Not started |
| M2 Submission slice | Mon 28, 6 pm | Not started |
| M3 Brain + agents | Tue 29, night | Not started |
| M4 Complete | Wed 30, 11 pm | Not started |
| M5 Ship | Thu 1, 10:30 am | Not started |

## CONTRACT / scaffold (§6.1)

**Done**
- Docs committed (AGENTS, CONTRACT, BUILD_PLAN, DECISIONS)

**Next**
1. Python package per CONTRACT §1, stubs for every §7 function, `config.py` per §3
2. `kavach/models.py`: Pydantic v2 models for every CONTRACT shape
3. `db.py`: schema §8, init + helpers
4. `api.py`: every §9 route wired to stubs, owner auth, token injection, canned §10 chat stream
5. `fixtures/api/*.json` per §15 + `tests/test_fixtures.py`
6. `requester/app.py` (§12 stubs); `gate_mcp.py`, `tools_mcp.py` with tools registered
7. `frontend/` shell: Vite + React + TS + Tailwind + shadcn/ui, routes per §14, API client with fixture mode, dev proxy, `gen:types`
8. `scripts/bench_models.py`, `scripts/reset_demo.py` skeleton
9. `requirements.txt` (pinned), `pytest.ini` with `llm` marker, `tests/` skeleton

**Blocked**
- (none)

## BRAIN (§6.2)

**Done**
- (none)

**Next**
1. `brain/llm.py` (Ollama chat, structured output, embeddings, streaming; `keep_alive` from config)
2. `ingest.py` for PDFs + notes, `watcher.py`
3. `embed.py` hybrid search
4. `chat.py` sync + stream with citation check
5. `pages/brain/Ask`: streaming chat, citation popovers, live auto-ingest strip (M1)
6. `entities.py` + graph-neighbour retrieval in chat
7. `extract.py` with grounding, `memory.py` with supersession + candidates + teach; "Remember this?" chips
8. `parse_question.py` + `decide.py` (pure code, CONTRACT §5.4)
9. `agent/planner.py`
10. `pages/brain/Vault`: documents, signature badges, entities, graph, `entities_used` highlight (M3)
11. WhatsApp ingestion; `pages/brain/Memory`: timeline, superseded values, "Teach KAVACH"

**Blocked**
- Waiting on scaffold (M0)

## TRUST (§6.3)

**Done**
- (none)

**Next**
1. `trust/crypto.py`, `mock_issuers/` (keys, signed PDFs, tampered PDF, credential batches)
2. `issuer_check.py`, `wallet.py`, `present.py`
3. `requester/verifier.py` + `requester/app.py`, owner `/api/ask` path
4. `pages/trust/Verify` (requester mode): five checks, "Owner-attested" label, storage panel (M1)
5. `pairing.py`, `consent.py`, `audit.py`, remaining `api.py` routes
6. `pages/trust/Queue` and `pages/trust/Audit` (M2)
7. `ledger.py`
8. `gate_mcp.py` (streamable HTTP -> `/api/ask`) + `requester/agent_client.py`
9. `tools_mcp.py` + `agent/executor.py`
10. `scripts/run_eval.py` and `scripts/reset_demo.py`

**Blocked**
- Waiting on scaffold (M0)

## DATA (§6.4)

**Done**
- (none)

**Next**
- Sat 26: send vault files to an outside friend for eval set A (15 questions); redact the real bank statement into `private/`
- By Sun 27: `demo_data/notes/` (5-8 notes with `[[links]]`), `inbox_note.md`, `chats/landlord.txt`, rent agreement text in `demo_data/templates/`, details for mock bank statement, marksheet and ID card
- By Mon 28, 6 pm: `docs/pitch/demo_script.md`, deck on organisers' template, test every screen, record demo video
- Tue 29 to Thu 1: eval sets B-E, README final, rehearsals and Q&A prep

**Blocked**
- Eval set A needs drafted vault files first
