# CONTRACT.md

The shared interfaces every track codes against. Changes go through the CONTRACT owner, get a line in `docs/DECISIONS.md`, and update `kavach/models.py` + `fixtures/` in the same commit.

---

## 1. Repo layout

```
kavach/                         repo root
├── AGENTS.md  CLAUDE.md  CONTRACT.md  README.md  LICENSE
├── requirements.txt  pytest.ini  .gitignore
├── kavach/                     Python package
│   ├── config.py               ports, paths, model names, owner token        (CONTRACT)
│   ├── models.py               all Pydantic types in this contract            (CONTRACT)
│   ├── db.py                   schema (§8) + helpers                          (BRAIN)
│   ├── api.py                  FastAPI app, routers, serves frontend/dist     (TRUST)
│   ├── gate_mcp.py             inbound MCP "kavach-gate", streamable HTTP     (TRUST)
│   ├── tools_mcp.py            outbound MCP "kavach-tools", stdio             (TRUST)
│   ├── brain/                                                                 (BRAIN)
│   │   ├── llm.py              Ollama client: chat, structured, embed, stream
│   │   ├── watcher.py          watchdog on vault/
│   │   ├── ingest.py           PDF / notes / WhatsApp -> documents + chunks
│   │   ├── embed.py            embeddings + hybrid search (NumPy)
│   │   ├── entities.py         entity + relation extraction, dedupe, graph
│   │   ├── extract.py          structured facts + grounding check
│   │   ├── memory.py           temporal facts, supersession, candidates, teaching
│   │   ├── chat.py             Ask my vault (sync + stream)
│   │   ├── parse_question.py   outsider question -> Claim
│   │   └── decide.py           Claim -> Proposal (deterministic)
│   ├── agent/
│   │   ├── planner.py          instruction -> validated Plan                  (BRAIN)
│   │   └── executor.py         runs approved plans via MCP stdio client       (TRUST)
│   ├── trust/                                                                 (TRUST)
│   │   ├── crypto.py           canonical JSON, Ed25519 sign/verify, sha256
│   │   ├── issuer_check.py     PDF signature verification
│   │   ├── wallet.py           credential copies, one-time holder keys
│   │   ├── present.py          presentations + owner attestations
│   │   ├── pairing.py          requester keys, pairing
│   │   ├── consent.py          request pipeline + approval queue
│   │   ├── ledger.py           global cumulative disclosure ledger
│   │   └── audit.py            hash-chained log
│   └── mock_issuers/           make_keys.py, issue.py                         (TRUST)
├── requester/                  runs on laptop 2                               (TRUST)
│   ├── app.py                  FastAPI :9000, /r/* routes, serves frontend/dist
│   ├── verifier.py             the five checks (§6.4) with its OWN trust list
│   ├── agent_client.py         scripted landlord agent, MCP streamable HTTP client
│   └── trusted_issuers.json
├── frontend/                   Vite + React + TS + Tailwind + shadcn/ui
│   └── src/  api/ shell/ components/ (CONTRACT) · pages/brain/ (BRAIN) · pages/trust/ (TRUST)
├── fixtures/api/*.json         one sample response per endpoint               (CONTRACT)
├── demo_data/                  notes/, chats/, templates/ (DATA, committed); generated/ (ignored)
├── eval/                       set_a..set_e.jsonl (DATA), results/
├── scripts/                    run_eval.py (TRUST), reset_demo.py, bench_models.py
├── tests/
├── docs/                       BUILD_PLAN.md, DECISIONS.md, pitch/ (DATA), archive/
└── runtime, gitignored:        vault/{pdfs,notes,chats}/  outbox/  keys/  kavach.db  private/
```

## 2. Processes and ports

| Process | Port | Where |
|---|---|---|
| Owner API + built UI (`kavach.api:app`) | 8000 | Owner laptop, binds 0.0.0.0 |
| `kavach-gate` MCP (streamable HTTP, path `/mcp`) | 8001 | Owner laptop, binds 0.0.0.0 |
| `kavach-tools` MCP (stdio, spawned by executor) | none | Owner laptop |
| Requester API + built UI (`requester.app:app`) | 9000 | Requester laptop |
| Ollama | 11434 | Owner laptop (or campus GPU box via `OLLAMA_URL`) |
| Vite dev server (dev only, proxies `/api`, `/r`) | 5173 | Any |

`kavach-gate` is a thin adapter: every tool call is forwarded to `http://127.0.0.1:8000/api/ask*` with header `X-Channel: mcp`, so web and MCP requests share one code path.

## 3. Config (`kavach/config.py`, env-overridable)

`OLLAMA_URL`, `LLM_MODEL`, `FAST_MODEL`, `EMBED_MODEL`, `EMBED_DIM`, `OLLAMA_KEEP_ALIVE` (default `"24h"`), `DB_PATH`, `VAULT_DIR`, `OUTBOX_DIR`, `KEYS_DIR`, `OWNER_TOKEN` (random per install, stored in `keys/owner_token`), `API_PORT`, `GATE_PORT`, `REQUESTER_PORT`, `OWNER_URL` (requester side), `CHUNK_SIZE=600`, `CHUNK_OVERLAP=100`, `WATCH_DEBOUNCE_S=2`, `LEDGER_MIN_WIDTH={"income":25000,"percentage":15}`, `LEDGER_MAX_ATTESTED_PER_30D=3`, `WALLET_LOW_COPIES=3`.

## 4. Knowledge model

**Entity types:** `PERSON`, `PROJECT` (ongoing goal), `CONCEPT` (recurring topic), `DECISION` (choice + date + quote), `ORG`, `PLACE`, `DOCUMENT`, `OBLIGATION` (rent, EMI, fee, renewal), `EVENT`. The owner is the fixed entity `e_owner`.

**Edge types:** `WORKS_ON`, `PART_OF`, `ABOUT`, `RELATES_TO`, `DECIDED`, `LANDLORD_OF`, `EMPLOYED_BY`, `BANKS_WITH`, `STUDIED_AT`, `PAID`, `DUE_ON`, `PARTY_TO`, `MENTIONED_IN`. Every edge has `valid_from`, `valid_to`, `source_chunk_id`.

**Fact:**
```json
{"fact_id":"f_ab12cd34ef","entity_id":"e_owner","field":"monthly_income","value":"62000",
 "source_type":"issuer_doc","doc_id":"d_03","quote":"Salary Credit ... 62,000.00",
 "valid_from":"2026-04-01","valid_to":null,"superseded_by":null,"confidence":"high"}
```
`source_type`: `issuer_doc` (issuer-signed PDF) | `extracted` (unsigned doc, note, chat) | `owner_stated` (taught or confirmed by owner). `confidence`: `high` | `low`.

**Fields extracted:** `monthly_income`, `loan_default_12m`, `date_of_birth`, `percentage`, `result`, `board`, `rent_amount`, `agreement_end_date`, `id_expiry`, `emi_date`, `employer`, `landlord`.

## 5. Disclosure model

### 5.1 Disclosable claims
| Claim | Issuer-provable names (fixed cutoffs) | Favourable |
|---|---|---|
| income | `income_ge_25000`, `income_ge_50000`, `income_ge_75000`, `income_ge_100000` | YES |
| loan_default_12m | `loan_default_12m` | NO |
| age | `age_over_18`, `age_over_21` | YES |
| percentage | `percentage_ge_60`, `percentage_ge_75`, `percentage_ge_90` | YES |
| result | `result_pass` | YES |
| board | `board` (value disclosed) | n/a |

Never disclosable: name, DOB, account numbers, address, exact amounts, document text.

### 5.2 Claim (parser output)
```json
{"claim":"income","op":"ge","value":50000,"issuer_claim":"income_ge_50000"}
```
`op`: `ge` | `is` | `eq`. `issuer_claim` is set only when the request matches a fixed cutoff exactly. Unmappable question: `{"claim":"unsupported"}`.

### 5.3 Answer types
| `answer_type` | Meaning | Set by |
|---|---|---|
| `ISSUER_PROOF` | Issuer-signed disclosure, holder-bound | Owner approval |
| `OWNER_ATTESTED` | Owner-signed statement for a non-issuer threshold | Owner approval |
| `DECLINED` | Owner chose not to answer | Owner |
| `CANNOT_CONFIRM` | No grounded `issuer_doc` fact and no credential | Automatic |
| `REFUSED` | Unsupported claim, not disclosable, or blocked by ledger | Automatic |

### 5.4 Decision rules (`brain/decide.py`, pure code)
```
claim unsupported or not disclosable                          -> REFUSED
no grounded issuer_doc fact and no credential for the claim   -> CANNOT_CONFIRM
issuer_claim set and wallet has an unused copy                -> ISSUER_PROOF, result from credential
else grounded issuer_doc fact exists                          -> ledger.check(); if blocked -> REFUSED
                                                                 else OWNER_ATTESTED, result = python comparison
favourable result   -> proposal to owner: Approve / Deny
unfavourable result -> proposal to owner: Answer / Decline (Decline -> DECLINED)
```

## 6. Credentials, presentations, attestations

All signatures: Ed25519 over `crypto.canonical(obj_without_sig)` where `canonical = json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()`. Keys and signatures are base64url. Fingerprint = first 16 hex chars of sha256(raw public key).

### 6.1 Credential (one copy)
```json
{"iss":"mock_bank","credential_type":"income_proof","copy":7,
 "holder_pubkey":"<b64>","iat":"2026-09-01T00:00:00Z","exp":"2027-03-01T00:00:00Z",
 "digests":["<b64 sha256(salt|claim|value)>", "..."],
 "issuer_sig":"<b64>"}
```
Batch of 20 copies per credential type, fresh salts per copy, digests shuffled, one one-time holder key per copy. Issuance: `wallet.export_holder_pubkeys(n=20) -> pubkeys.json`, then `python -m kavach.mock_issuers.issue --type income_proof --holder-keys pubkeys.json`, then `wallet.import_batch(path)`. Private keys never leave the wallet. Digest input: `f"{salt}|{claim}|{json.dumps(value)}"`.

### 6.2 Presentation (`ISSUER_PROOF` payload)
```json
{"type":"kavach/presentation","credential":{"...as issued..."},
 "disclosures":[{"salt":"<b64>","claim":"income_ge_50000","value":true}],
 "binding":{"nonce":"<from requester>","aud":"<requester fp>","iat":"...","sig":"<by holder key>"}}
```
`binding.sig` signs canonical `{credential_digest_list_hash, disclosures, nonce, aud, iat}`. Each copy is marked used after one presentation.

### 6.3 Owner attestation (`OWNER_ATTESTED` payload)
```json
{"type":"kavach/attestation","claim":{"claim":"income","op":"ge","value":60000},"answer":true,
 "nonce":"...","aud":"<requester fp>","iat":"...","exp":"...","owner_pairwise_pubkey":"<b64>","sig":"<b64>"}
```
Signed with the per-requester pairwise owner key created at pairing.

### 6.4 Verifier checks (`requester/verifier.py`)
Presentation: (1) `issuer_sig` valid under `trusted_issuers.json[iss]`; (2) each disclosure hashes to a digest; (3) `binding.sig` valid under `holder_pubkey`; (4) `nonce` and `aud` match the stored request; (5) not expired.
Attestation: (1) `sig` valid under the pairwise key received at pairing; (2) nonce/aud match; (3) not expired; always labelled "Owner-attested".
Output: `{"answer_type","claim","result","checks":[{"name","ok","detail"}],"all_ok"}`.

### 6.5 Signed PDFs
Issuer signs sha256 of the PDF's normalised text (NFKC, collapsed whitespace). Stored in PDF metadata `keywords` as `{"iss":"mock_bank","sig":"<b64>"}`. `issuer_check.verify_pdf()` returns `issuer_signed` | `unsigned` | `invalid` (sig present but fails, e.g. tampered).

## 7. Cross-module Python interfaces

Types come from `kavach/models.py`. Anything not listed is private to its module.

```python
# brain (BRAIN)
ingest.ingest_file(path: Path) -> IngestResult
ingest.remove_file(path: Path) -> None               # supersede facts, drop chunks
watcher.start(vault_dir: Path) -> Observer
embed.search(query: str, k: int = 8) -> list[ScoredChunk]
chat.answer(question: str, history: list[ChatTurn]) -> ChatResult
chat.answer_stream(question: str, history: list[ChatTurn]) -> Iterator[ChatEvent]
memory.teach(statement: str) -> TeachResult
memory.decide_candidate(candidate_id: str, remember: bool) -> Fact | Entity | None
memory.timeline(field: str | None) -> list[FactVersion]
parse_question.parse(question: str) -> Claim
decide.decide(claim: Claim, requester_fp: str) -> Proposal
planner.plan(instruction: str) -> Plan                # validated, never executes

# trust (TRUST)
crypto.canonical(obj) -> bytes; crypto.sign(priv, obj) -> str; crypto.verify(pub, obj, sig) -> bool
issuer_check.verify_pdf(path: Path) -> SignatureResult
wallet.find_copy(issuer_claim: str) -> CredentialRef | None   # unused copy containing the claim
wallet.status() -> WalletStatus
present.build_presentation(ref: CredentialRef, issuer_claim: str, nonce: str, aud: str) -> dict
present.build_attestation(claim: Claim, answer: bool, requester_fp: str, nonce: str) -> dict
ledger.check(claim: Claim, answer: bool) -> LedgerCheck          # allowed, reason
ledger.record(claim: Claim, answer: bool) -> None
consent.receive(req: AskIn, channel: str) -> AskAck
consent.decide_request(request_id: str, action: str) -> RequestView
audit.log(event: str, ref_id: str | None, detail: dict) -> int
audit.verify_chain() -> ChainStatus
executor.execute(task_id: str) -> list[ToolResult]
```

## 8. SQLite schema

```sql
CREATE TABLE documents (doc_id TEXT PRIMARY KEY, path TEXT UNIQUE, source TEXT, doc_type TEXT,
  signature_status TEXT, iss TEXT, text_hash TEXT, ingested_at TEXT, removed_at TEXT);
CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, locator TEXT, text TEXT, embedding BLOB);
CREATE TABLE entities (entity_id TEXT PRIMARY KEY, type TEXT, name TEXT, norm_name TEXT, attrs_json TEXT);
CREATE TABLE edges (edge_id TEXT PRIMARY KEY, src TEXT, rel TEXT, dst TEXT,
  valid_from TEXT, valid_to TEXT, source_chunk_id TEXT);
CREATE TABLE facts (fact_id TEXT PRIMARY KEY, entity_id TEXT, field TEXT, value TEXT, source_type TEXT,
  doc_id TEXT, quote TEXT, valid_from TEXT, valid_to TEXT, superseded_by TEXT, confidence TEXT, created_at TEXT);
CREATE TABLE memory_candidates (candidate_id TEXT PRIMARY KEY, statement TEXT, kind TEXT,
  field TEXT, value TEXT, valid_from TEXT, project_entity_id TEXT, status TEXT, created_at TEXT);
CREATE TABLE credentials (cred_id TEXT PRIMARY KEY, iss TEXT, credential_type TEXT, copy INT,
  credential_json TEXT, disclosures_json TEXT, holder_privkey BLOB, used INT DEFAULT 0);
CREATE TABLE requesters (fingerprint TEXT PRIMARY KEY, pubkey TEXT, name TEXT, type TEXT,
  status TEXT, owner_pairwise_privkey BLOB, paired_at TEXT);
CREATE TABLE requests (request_id TEXT PRIMARY KEY, requester_fp TEXT, channel TEXT, question TEXT,
  claim_json TEXT, proposal_json TEXT, nonce TEXT, status TEXT, answer_type TEXT, payload_json TEXT,
  created_at TEXT, decided_at TEXT, UNIQUE(requester_fp, nonce));
CREATE TABLE disclosure_ledger (field TEXT PRIMARY KEY, lo REAL, hi REAL, attested_json TEXT, updated_at TEXT);
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, instruction TEXT, plan_json TEXT, status TEXT,
  result_json TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, ref_id TEXT,
  detail_json TEXT, prev_hash TEXT, entry_hash TEXT);
```
- `memory_candidates.kind`: `fact` | `decision`; `status`: `pending` | `accepted` | `discarded`
- `requesters.status`: `pending` | `paired` | `blocked`
- `requests.status`: `pending_pairing` | `pending` | `done`; `channel`: `web` | `mcp`
- `tasks.status`: `planned` | `approved` | `rejected` | `done` | `failed`
- `audit_log.entry_hash = sha256(prev_hash + ts + event + (ref_id or "") + detail_json)`; genesis `prev_hash = "0"*64`
- Audit `detail_json` never contains document text, chunk text or raw fact values.

## 9. Owner API (`kavach/api.py`, :8000)

**Auth:** owner routes need header `X-Owner-Token` AND a loopback client address. `GET /` (and any non-`/api` path) serves `frontend/dist/index.html` with `<script>window.__KAVACH__={"mode":"owner","token":"..."}</script>` injected, token included only for loopback clients.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ollama, models, model_loaded, db, vault_dir}` |
| POST | `/api/ingest` | multipart file | copies into `vault/` (watcher ingests) -> `{path}` |
| POST | `/api/ingest/sync` | | rescan -> `{ingested:[IngestResult]}` |
| GET | `/api/ingest/events` | `?since=<seq>` | `{events:[{seq, ts, path, doc_id, signature_status, entities_added, facts_added}], last_seq}` |
| GET | `/api/documents` | | `[Document]` |
| GET | `/api/entities` | `?type=` | `[Entity]` |
| GET | `/api/facts` | `?field=&current=true` | `[Fact]` |
| GET | `/api/graph` | `?entity_id=&hops=1` | `{nodes:[{id,type,name}], edges:[{id,src,dst,rel,valid_from,valid_to,source_chunk_id}]}` |
| GET | `/api/chunks/{chunk_id}` | | `{chunk_id, doc_id, locator, text}` (owner only, for citation popovers) |
| POST | `/api/chat` | `{question, history}` | `ChatResult` |
| POST | `/api/chat/stream` | `{question, history}` | `text/event-stream`, see §10 |
| POST | `/api/memory` | `{statement}` | `TeachResult {fact, superseded:[Fact]}` |
| POST | `/api/memory/candidates/{id}/decision` | `{remember}` | `{stored: Fact|Entity|null}` |
| GET | `/api/memory/timeline` | `?field=` | `[FactVersion]` |
| POST | `/api/tasks` | `{instruction}` | `Task` (status `planned`, plan with previews) |
| POST | `/api/tasks/{id}/decision` | `{approve}` | `Task` (executed if approved) |
| GET | `/api/tasks` | | `[Task]` |
| GET | `/api/queue` | | `{requesters:[Requester], requests:[RequestView], tasks:[Task], wallet:WalletStatus}` |
| POST | `/api/requesters/{fp}/decision` | `{approve}` | `Requester` |
| POST | `/api/requests/{id}/decision` | `{action:"approve"|"answer"|"decline"|"deny"}` | `RequestView` |
| GET | `/api/wallet` | | `WalletStatus {by_type:[{credential_type, iss, unused, total}], low:[...]}` |
| GET | `/api/audit` | `?limit=200` | `{entries:[AuditEntry], chain_intact, broken_at}` |
| GET | `/api/outbox` | | `[{name, kind, size, created_at}]` |

**Requester-facing routes (no owner token, signed):**

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/ask` | `{requester_pubkey, requester_name, requester_type, question, nonce, ts, sig}` | `{request_id, status}` |
| GET | `/api/ask/{id}` | headers `X-Requester-Fp`, `X-Ts`, `X-Sig` (sig over `"{id}|{ts}"`) | `{status, answer_type, payload, owner_pairwise_pubkey?}` |
| GET | `/api/claims` | | disclosable claim names + which are issuer-provable (no values) |

`sig` on `/api/ask` covers canonical JSON of all other fields. `ts` must be within 120 s. Nonce reuse per requester is rejected. Unknown key creates a `pending` requester and the request waits as `pending_pairing`. `owner_pairwise_pubkey` is returned on the first poll after pairing.

## 10. Chat stream protocol (`POST /api/chat/stream`)

Server-sent events over a POST response (frontend reads with `fetch` + `ReadableStream`, not `EventSource`):
```
event: meta        data: {"entities_used":["e_..."], "chunks":[{"n":1,"chunk_id":"c_...","doc_id":"d_...","locator":"page 2"}]}
event: token       data: {"text":"..."}
event: final       data: {"answer":"...","citations":[{"n":1,"chunk_id":"...","doc_id":"...","locator":"...","quote":"..."}],
                          "citation_ok":true,"flags":[],"memory_candidates":[MemoryCandidate]}
event: done        data: {"latency_ms":1234,"first_token_ms":800}
event: error       data: {"message":"..."}
```
`citation_ok=false` when any `[n]` is not a supplied chunk or the answer has no citation.

## 11. MCP

### 11.1 Inbound `kavach-gate` (`gate_mcp.py`, streamable HTTP :8001 `/mcp`)
| Tool | Args | Returns |
|---|---|---|
| `list_disclosable_claims` | | same as `GET /api/claims` |
| `ask` | `question, nonce, requester_pubkey, requester_name, requester_type, ts, sig` | `{request_id, status}` |
| `get_answer` | `request_id, requester_fp, ts, sig` | same as `GET /api/ask/{id}` |

### 11.2 Outbound `kavach-tools` (`tools_mcp.py`, stdio, only `executor.py` spawns it)
Arg models live in `models.py` so the planner validates without spawning MCP.

| Tool | Args | Output |
|---|---|---|
| `draft_email` | `to, subject, body, attachments:[{type:"presentation", request_id}]` | `outbox/<ts>_<slug>.eml` |
| `create_reminder` | `title, date, notes` | `outbox/<ts>_<slug>.ics` |
| `fill_rental_form` | `fields:{...}` (approved subset only) | `outbox/rental_application_filled.pdf` from `demo_data/templates/` |
| `save_note` | `title, markdown` | `vault/notes/<slug>.md` (re-ingested by watcher) |

Validation (code, in planner and again in executor): `to` must be an email attribute of an entity in the graph unless typed by the owner in the instruction; `request_id` attachments must be `done` requests with `ISSUER_PROOF` or `OWNER_ATTESTED`; dates must be ISO and in the future.

## 12. Requester backend (`requester/app.py`, :9000)

Keypair created on first run in `requester/data/`. Serves `frontend/dist` with `window.__KAVACH__={"mode":"requester"}`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/r/identity` | | `{name, type, fingerprint, owner_url}` |
| POST | `/r/ask` | `{question}` | creates nonce, signs, calls owner `/api/ask` -> `{local_id, request_id, status}` |
| GET | `/r/requests` | | `[{local_id, request_id, question, status, via:"web"|"agent", result: VerifierOutput|null}]` (polls owner, verifies on arrival) |
| GET | `/r/storage` | | `{files:[{name, size, preview_json}]}` (proves only small JSON is held) |

`agent_client.py` has its own keypair (`via:"agent"`), talks to `kavach-gate` over MCP, runs the same verifier, and appends results to `requester/data/requests.json` so they appear in `/r/requests`.

## 13. Audit events

`ingested`, `document_signature_failed`, `requester_pending`, `requester_paired`, `requester_blocked`, `request_received`, `request_auto_refused`, `request_cannot_confirm`, `request_refused_ledger`, `disclosure_answered`, `disclosure_declined`, `disclosure_denied`, `memory_taught`, `memory_candidate_accepted`, `task_planned`, `task_approved`, `task_rejected`, `task_executed`, `task_failed`, `wallet_low`.

## 14. Frontend contract

- Routes (owner mode): `/` Ask, `/vault` (documents + graph), `/memory` (BRAIN pages); `/queue`, `/audit` (TRUST pages). Requester mode: `/verify` only (TRUST).
- Pages import only from `src/api`, `src/shell`, `src/components`; never from the other track's `pages/`.
- Mode and token come from `window.__KAVACH__`. Every owner call sends `X-Owner-Token`.
- Types: `npm run gen:types` runs `openapi-typescript` against `http://localhost:8000/openapi.json` and `:9000/openapi.json` into `src/api/types.ts` and `src/api/requester-types.ts`. Never hand-write API types.
- Fixture mode: `VITE_USE_FIXTURES=1` makes the API client return `fixtures/api/*.json` (and a canned chat stream), so every screen works with no backend.
- Polling: `/api/ingest/events` and `/api/queue` every 2 s; `/r/requests` every 1.5 s.
- No external network at runtime: fonts and icons are bundled.

## 15. Fixtures

`fixtures/api/<name>.json` for: `health`, `documents`, `entities`, `facts`, `graph`, `chat`, `chat_stream` (`.jsonl` of events), `memory_timeline`, `queue`, `wallet`, `tasks`, `task_planned`, `audit`, `ingest_events`, `outbox`, `r_identity`, `r_requests`, `r_storage`. A pytest test validates every fixture against its Pydantic model, so fixtures can't drift from the contract.
