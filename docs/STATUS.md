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
- Docs committed (AGENTS, CONTRACT, BUILD_PLAN, DECISIONS); STATUS + `/next` command
- 1. Python package per §1: every §7 function stubbed with typed placeholders; `config.py` per §3
- 2. `kavach/models.py`: every CONTRACT shape (open shapes marked "(scaffold)", see DECISIONS)
- 3. `db.py`: §8 schema, `init_db`, generic + typed helpers
- 5. `fixtures/api/*.json` per §15 (+ `chunk.json`), `tests/test_fixtures.py` (incl. audit hash chain)
- 8. `scripts/bench_models.py` (verified against local Ollama), `scripts/reset_demo.py` skeleton (file steps work)
- 9. `requirements.txt` (pinned, 3.11+), `pytest.ini` with `llm` marker, tests for config/db/stubs/scripts
- 4. `api.py`: every §9 route wired to stubs; owner auth (token + `request.client.host` loopback, no proxy headers, `--no-proxy-headers`); index.html token injection (loopback only, placeholder until `frontend/dist` exists); §10 SSE stream with `error` on exceptions; safe non-overwriting uploads; `/api/ingest/events` from `ingested` audit entries; `X-Channel: mcp` only from loopback. CONTRACT: `consent.poll` (+ fp match), `consent.RequestRejected`, `pairing.decide`, `decide.claims`, reject reasons `unknown_request`/`wrong_requester`/`malformed`, `ingested` detail shape, queue contents, facts `current=true` default, per-role `model_loaded`. `tests/test_api.py`
- 6. `requester/` (`app.py` §12 routes stubbed + requester-mode frontend, `verifier.py` stub that never passes a check, `agent_client.py` stub, `trusted_issuers.json`); `gate_mcp.py` (3 tools, streamable HTTP `:8001/mcp`) and `tools_mcp.py` (4 tools, stdio, args validated by models.py) with stub bodies; verified live over HTTP and stdio. `tests/test_requester.py`, `tests/test_mcp.py`
- Owner token is lazy: `keys/owner_token` created on the owner API's first use, never on import
- 7. `frontend/` shell: Vite 8 + React 19 + TS + Tailwind v4 + shadcn/ui (button card badge dialog table tabs sonner input textarea scroll-area separator tooltip; `sonner` replaces deprecated `toast`), bundled Inter/JetBrains Mono + lucide, dark theme. `src/shell/`: layout, sidebar, §14 routes (owner and requester routes never mount together), dev-only owner/requester switch, owner health pill (`/api/health`), requester owner-connection status (`/r/identity` + no-cors probe). `src/api/`: `client.ts` (every §9/§12 route, `X-Owner-Token`, `ApiError`, `chatStream` for §10), `sse.ts`, `poll.ts` (`usePoll`, §14 intervals), fixture mode (lazy, absent from normal builds), generated `types.ts`/`requester-types.ts`. Dev: proxy `/api`->8000, `/r`->9000, `kavach-dev-boot` plugin injects the token only for loopback bind + loopback client + loopback Host (`--host` refuses). Empty pages in `pages/brain/` and `pages/trust/`. Vitest (31 tests); verified live: built UI via :8000 (token injected) and :9000 (requester), dev proxy, chat stream, `--host` refusal

- `api.py`: owner routes and token injection also require a loopback `Host` header (DNS rebinding); tests for `Host: evil` on `GET /` (no token) and every owner route (`403`)

- `kavach/textnorm.py` (§6.5): `normalize_text`, `text_hash`, `pdf_pages`, `pdf_text_hash`; issuers sign the generated PDF's `pdf_text_hash`; `documents.path` vault-relative (fixtures fixed, audit fixture re-chained); `document_signature_failed` detail `{path, doc_id, iss, reason}` (§13). `tests/test_textnorm.py`

**Next**
- Scaffold complete. CONTRACT owner: review CONTRACT change proposals from BRAIN/TRUST

**Blocked**
- (none)

**Models chosen:** qwen2.5:7b / qwen2.5:3b / nomic-embed-text (DECISIONS.md, Appendix A).

**Model benchmark** (26 Sep, owner laptop: Ryzen 7 7730U, 16 GB, Ollama 0.34.4, CPU only; `scripts/bench_models.py --runs 3`, all models unloaded before each run; medians; embed = nomic-embed-text, dim 768)

| LLM / FAST | Chat think | Chat first token (< 5 s) | Chat full answer | Parse (< 3 s) | Parse correct | Embed 100 chunks (< 20 s) |
|---|---|---|---|---|---|---|
| qwen2.5:7b / qwen2.5:3b | n/a | 0.18 s | 6.9 s | 1.40 s | 3/3 | 21.6 s |
| qwen3:8b / qwen3:4b | default (on) | 51.5 s | 57.1 s | 4.39 s | 0/3 | 21.6 s |
| qwen3:8b / qwen3:4b | false (bench only) | 0.19 s | 6.1 s | 2.51 s | 0/3 | 22.5 s |

Notes: structured parse always sends `think=false`. qwen3:4b returns `"value": "50k"` (a string, which the Claim schema allows), hence 0/3; that's a prompt/validation fix for BRAIN, not speed. First-token times benefit from Ollama's prompt cache (same short prompt every run); real chat with ~8 retrieved chunks will be slower on CPU. Embedding is ~1.5 s over target on every family.

## BRAIN (§6.2)

**Done**
- 1. `brain/llm.py`: `chat`, `chat_stream` (NDJSON, 120 s per-chunk read timeout), `structured` (retry once with the validation error appended, then `LLMError`), `embed` (one batched `/api/embed`, float32 `(n, EMBED_DIM)`, no call for empty input); every failure is `LLMError`; `keep_alive` on every call, `temperature 0` + `num_ctx=config.NUM_CTX` on chat/structured. `tests/test_llm.py`: mock-transport tests + llm tests incl. a ~5k-token recall test (fails without `num_ctx`: Ollama's default context truncated it to ~2k tokens)
- 2. `brain/ingest.py`: PDFs (per-page locators, `textnorm` hash, `issuer_check.verify_pdf`, `doc_type` by keyword), notes (`note: x.md`), `.txt` as plain text until step 11; ~600/100 boundary-aware chunks; batched embeddings (NULL if Ollama is down); vault-relative paths; unchanged `text_hash` is skipped; a changed file keeps its `doc_id`, replaces chunks and closes (never deletes) its facts and edges; `remove_file` likewise; audits `ingested` + `document_signature_failed`; `note_links()` parses `[[links]]` for step 6. `brain/watcher.py`: `pdfs/notes/chats` only, ignores temp/lock/partial files (`.tmp`, `.crdownload`, `~$`, dotfiles...), per-path 2 s debounce on one worker, file existence decides ingest vs remove, PermissionError retried with backoff for 10 s, catch-up on start. `db.py`: `get_document_by_path`, `store_document`, `remove_document` (one transaction each). `tests/test_ingest.py`, `tests/test_watcher.py`

**Next**
3. `embed.py` hybrid search
4. `chat.py` sync + stream with citation check
5. `pages/brain/Ask`: streaming chat, citation popovers, live auto-ingest strip (M1)
6. `entities.py` + graph-neighbour retrieval in chat
7. `extract.py` with grounding, `memory.py` with supersession + candidates + teach; "Remember this?" chips
8. `parse_question.py` with amount normalisation in code before the LLM (BUILD_PLAN §4.10; unit tests) + `decide.py` (pure code, CONTRACT §5.4)
9. `agent/planner.py`
10. `pages/brain/Vault`: documents, signature badges, entities, graph, `entities_used` highlight (M3)
11. WhatsApp ingestion; `pages/brain/Memory`: timeline, superseded values, "Teach KAVACH"

**Blocked**
- (none)

## TRUST (§6.3)

**Done**
- (none)

**Next**
1. `trust/crypto.py`, `mock_issuers/` (keys, signed PDFs, tampered PDF, credential batches)
2. `issuer_check.py`, `wallet.py`, `present.py`
3. `requester/verifier.py` + `requester/app.py`, owner `/api/ask` path
4. `pages/trust/Verify` (requester mode): five checks, "Owner-attested" label, storage panel (M1)
5. `pairing.py`, `consent.py`, `audit.py`, remaining `api.py` routes
6. Malformed `/api/ask*` (validation errors) audited as `request_rejected`, `reason=malformed`, detail `{route, client_ip, error_type}` only, never the body
7. `pages/trust/Queue` and `pages/trust/Audit` (M2)
8. `ledger.py`
9. `gate_mcp.py` (streamable HTTP -> `/api/ask`) + `requester/agent_client.py`
10. `tools_mcp.py` + `agent/executor.py`
11. `scripts/run_eval.py` and `scripts/reset_demo.py`
12. (low priority) `/r/identity` gains `owner_reachable: bool` (requester backend pings the owner), replacing the browser no-cors probe; CONTRACT §12 change via CONTRACT owner
13. (low priority) Document the §10 stream in OpenAPI (`responses=` on `/api/chat/stream` with the `ChatEvent` models) so `gen:types` covers it; then drop the derived stream types in `client.ts`

**Blocked**
- (none)

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
