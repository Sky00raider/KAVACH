# BUILD_PLAN.md

Interfaces live in `CONTRACT.md`. This file covers behaviour, order and dates.

## 1. Dates that matter

| When | What |
|---|---|
| Sat 26 Sep | Setup + scaffold (M0) |
| Sun 27 Sep | Organiser online session; CP1 (M1) |
| **Mon 28 Sep, 11:59 pm** | **Submission: refined idea + refined architecture + progress + demo video.** Likely decides the shortlist on 29th, so treat it as the real deadline. Target submitting by 10 pm |
| Tue 29 Sep | Shortlist; build M3 |
| Wed 30 Sep to Thu 1 Oct | On-campus 24 h final sprint |
| **Thu 1 Oct, 11:59 am** | **Final submission.** Target 10:30 am |

## 2. Milestones

| Milestone | Due | Done when |
|---|---|---|
| **M0 Setup** | Sat 26, night | Repo public/private decided, docs committed, scaffold pushed (stubs serve fixtures, frontend runs in fixture mode, `pytest -m "not llm"` green). Every laptop runs Ollama; models benchmarked; owner laptop chosen |
| **M1 CP1** | Sun 27, night | Owner asks the vault (PDFs + notes) and gets a cited answer, streamed. Mock bank issues a signed PDF + credential batch; a requester on laptop 2 gets a presentation and all five checks pass. UI: Ask page (streaming, citation popovers) and Verify page (five checks) working live. DATA: demo vault content drafted, outside friend has the files for eval set A |
| **M2 Submission slice** | Mon 28, 6 pm | One real end-to-end run with Wi-Fi off: drop a file, watcher ingests, owner asks a cited question, landlord asks from laptop 2, pairing card, owner approves in UI, verifier shows five ticks, audit shows chain intact. UI: Queue and Audit pages live. Evening: video, deck, architecture, submit |
| **M3 Brain + agents** | Tue 29, night | Entities + graph (incl. PROJECT/CONCEPT/DECISION) feeding chat; facts + grounding; temporal memory + "Remember this?"; ledger; `kavach-gate` + agent client; planner + executor with `draft_email` + `create_reminder`; graph view |
| **M4 Complete** | Wed 30, 11 pm | WhatsApp source, `fill_rental_form`, Memory screen, 6 attacks demoable, two-laptop test, eval run. **Feature freeze at 11 pm** |
| **M5 Ship** | Thu 1, 10:30 am | Eval numbers in deck, README final, backup video on two devices, 3 offline rehearsals, submitted |

**Checkpoint rule:** if a milestone slips by more than 3 hours, apply the cut list (§8) in order. Do not keep pushing the date.

## 3. Setup (M0)

1. Create the GitHub repo. Pick MIT or Apache-2.0 in GitHub's licence dropdown. Add teammates. Commit `AGENTS.md`, `CLAUDE.md`, `CONTRACT.md`, `docs/`, `.gitignore`, `README.md` as the first commit.
2. Everyone: `git clone`, `python3.11 -m venv .venv`, install Ollama, pull candidate models.
3. CONTRACT owner runs the **scaffold prompt** (§6.1) in Claude Code, pushes.
4. Code tracks: `pip install -r requirements.txt`, `cd frontend && npm install`, `pytest -m "not llm"`, `npm run dev` with fixtures. Then start track work. DATA: clone with GitHub Desktop, start on §6.4 tasks right away (they don't depend on the scaffold).
5. Benchmark on each laptop with `scripts/bench_models.py` (part of the scaffold). Targets on the owner laptop: chat first token < 5 s, question parse < 3 s, embedding 100 chunks < 20 s. Record results and the chosen models in `DECISIONS.md`.
6. Questions for the organisers: campus GPU access; whether AI coding assistants must be disclosed; README and deck templates.

## 4. Component behaviour

### 4.1 Ingestion (BRAIN)
- `watcher.py` watches `vault/pdfs`, `vault/notes`, `vault/chats`; 2 s debounce; create/modify -> `ingest_file`, delete -> `remove_file`. Changed file: replace chunks, supersede (never delete) its old facts. Log `ingested`.
- PDF: PyMuPDF text per page, normalise, `text_hash`, `issuer_check.verify_pdf()` (stub returns `unsigned` until TRUST lands it).
- Notes: `.md`, Obsidian-compatible; `[[links]]` become `MENTIONED_IN` edges.
- WhatsApp `.txt`: parse `DD/MM/YY, HH:MM - Name: message` (handle both 12 h and 24 h, multi-line messages); chunk by conversation window.
- Chunks ~600 chars, 100 overlap. Locators: `page 2`, `note: rent.md`, `chat: 2026-08-14 21:03`.

### 4.2 Search (BRAIN)
Embeddings as float32 BLOBs, loaded into one NumPy matrix cached in memory (invalidated on ingest, and rebuilt when the chunks table changes underneath). Score = 0.7 cosine + 0.3 keyword (BM25-lite over tokens), **each min-max normalised over the vault** (a component with all-equal values counts as 0); scores are relative to the query, not absolute. Keyword tokens pass through `brain/amounts.py` first, so `15k`, `₹15,000` and `15000` match. Top-k 8. Missing vectors are backfilled in a background thread (embed, write, then swap the index; searches meanwhile use the old one; a failure waits 60 s before retrying). Without a query vector (Ollama down) search is keyword-only. The API warms the index at startup.
nomic task prefixes: ingest embeds every chunk as `config.EMBED_DOC_PREFIX + text` (done in step 2); step 3 search must embed every query as `config.EMBED_QUERY_PREFIX + query`. Chunk text is stored without the prefix.

### 4.3 Entities and graph (BRAIN)
- Per chunk, structured output `{entities:[{type,name,attrs}], relations:[{src,rel,dst,date?}]}`.
- Dedupe on normalised name (lowercase, strip Mr./Mrs./Smt./Dr.) + type; merge attrs (phone, email).
- Prompt rules: PROJECT = ongoing goal with tasks or deadlines; CONCEPT = topic recurring across sources; DECISION must quote its sentence and carry a date. Example: "Decided to renew only if rent stays under ₹15k" -> `DECISION` -ABOUT-> `CONCEPT(rent renewal)`, -PART_OF-> `PROJECT(Flat move 2026)`.

### 4.4 Facts and memory (BRAIN)
- `extract.py` pulls the fields in CONTRACT §4 with quotes. Grounding: quote in text and contains the value's digits -> `high`, else `low`.
- New fact for the same `(entity, field)` sets old `valid_to` + `superseded_by`.
- `memory.teach("My salary went up to ₹70k from October")` -> `{field, value, valid_from}` -> `owner_stated`, supersedes for chat only.
- After each chat turn, list durable facts or decisions **the owner** stated (never the assistant). Store as `pending` candidates, returned in `final.memory_candidates`. Nothing stored without a click.
- Chat answers "current" questions from the latest valid fact and names the source ("You told me on 21 Sep; your last bank statement shows ₹62k").

### 4.5 Ask my vault (BRAIN)
1. Find entities named in the question (string + embedding match on entity names).
2. Retrieve top-k hybrid chunks plus chunks linked to 1-hop graph neighbours.
3. Add current facts for those entities.
4. Prompt: answer only from context, cite every sentence `[n]`, say "I don't have that" when absent.
5. Citation check in code; set `citation_ok`.

### 4.6 Disclosure pipeline (TRUST orchestrates, BRAIN decides)
`consent.receive`: verify sig, ts window, nonce -> pairing check -> `parse_question.parse` -> `decide.decide` -> auto-resolve `REFUSED` / `CANNOT_CONFIRM`, else queue a proposal. Owner action -> `present.build_*` -> `ledger.record` -> mark copy used -> audit.

### 4.7 Ledger (TRUST)
Per numeric field keep `[lo, hi)` implied by all answers to everyone. YES to `>= t`: `lo = max(lo, t)`. Disclosed NO: `hi = min(hi, t)`. Refuse if the new width < min width (income ₹25,000, percentage 15) or if it would be the 4th distinct owner-attested threshold for that field in 30 days. Requester identity is ignored on purpose.

### 4.8 Planner and executor
- Planner (BRAIN): structured output of tool calls using the graph (resolve "my landlord" to the entity and its email) and facts (agreement end date). Validate against arg models + CONTRACT §11.2 rules. Build a human preview per call.
- Executor (TRUST): only `approved` tasks. Spawns `kavach-tools` over stdio, re-validates, attaches presentations by `request_id`, writes results, audits.

### 4.9 Audit (TRUST)
Hash chain per CONTRACT §8. `verify_chain()` walks the log and returns the first broken `seq`.

### 4.10 Question parsing (BRAIN)
- `parse_question` normalises amounts **in code, before the LLM sees the question**, reusing `kavach/brain/amounts.py` (`normalize_amounts`, `parse_amount`; built in step 3 for search tokens), which already covers the currency, grouping, multiplier and range rules below; step 8 adds the per-period and percentage stripping. Every amount becomes a plain integer of rupees in the text passed to the model: `₹`, `Rs`, `Rs.`, `INR` dropped; commas dropped, Indian grouping included (`1,20,000`); `k` = ×1,000; `lakh` / `lac` / `L` = ×1,00,000 (`1.2 lakh` -> `120000`); `crore` / `cr` = ×1,00,00,000; `/month`, `per month`, `pm`, `a month` stripped. Examples: `₹50k` -> `50000`, `50,000/month` -> `50000`, `1.2 lakh` -> `120000`.
- Percentages the same way (`75 %`, `75 percent` -> `75`).
- Unit tests in `tests/test_parse_question.py` cover every form above, plus text with no amount (unchanged) and ambiguous input (e.g. `50-60k`, left unchanged).
- After the LLM: numeric claims (`income`, `percentage`, `age`) must carry an `int` value, else `unsupported`. `issuer_claim` is set in code from the §5.1 table, never by the model.
- Reason: qwen3:4b parsed "50k" as the string `"50k"` 3/3 in the benchmark (DECISIONS.md, Appendix A); doing it in code removes that failure for any model.

## 5. Demo vault (DATA writes content, TRUST generates signed parts)

| File | Source | Purpose |
|---|---|---|
| `bank_statement_signed.pdf` + income batch | mock bank | Issuer proofs |
| `bank_statement_REAL_redacted.pdf` | real statement, redacted, kept in `private/` | Messy real format, eval credibility |
| `rent_agreement.pdf` | filled template | Landlord, rent, end date |
| `marksheet_signed.pdf` + batch | mock board | Marks, result, board |
| `id_card_signed.pdf` + batch | mock govt | Age proofs |
| `notes/` 5-8 `.md` | written by hand, Obsidian style with `[[links]]` | "Flat move 2026" project, dated decisions, recurring concepts |
| `inbox_note.md` (held back) | "Landlord said rent goes to ₹16k from January" | Live auto-ingest moment; breaks the ₹15k renewal condition |
| `chats/landlord.txt` | realistic WhatsApp export | Landlord contact + rent discussion |
| `bank_statement_TAMPERED.pdf` | signed file with salary edited | Signature failure |

`scripts/reset_demo.py`: wipe runtime state, copy `demo_data/` into `vault/`, run issuers, pre-ingest, back up `kavach.db`. `--restore` restores the backup in seconds.

## 6. Prompts

### 6.1 Scaffold prompt (CONTRACT owner, M0)
```
Read AGENTS.md and CONTRACT.md fully. Build the project scaffold only; no real logic.

1. Python package exactly per CONTRACT §1: every module with the functions in §7 as stubs that return
   realistic placeholder values typed with models.py. config.py per §3.
2. kavach/models.py: Pydantic v2 models for every shape in CONTRACT (Fact, Entity, Claim, Proposal,
   ChatResult, ChatEvent, MemoryCandidate, Task, Plan, ToolCall + arg models, RequestView, Requester,
   WalletStatus, AuditEntry, ChainStatus, VerifierOutput, etc.).
3. db.py: schema §8, init + simple helpers.
4. api.py: every route in §9 wired to the stubs, with owner auth + index.html token injection.
   /api/chat/stream emits the §10 events from a canned answer.
5. fixtures/api/*.json per §15, plus tests/test_fixtures.py validating each against models.py.
6. requester/app.py with §12 routes stubbed; gate_mcp.py and tools_mcp.py with tools registered, bodies stubbed.
7. frontend/: Vite + React + TS + Tailwind + shadcn/ui (init, add button card badge dialog table tabs
   toast input textarea scroll-area separator tooltip). src/shell/: app layout, sidebar, mode switch
   (owner vs requester), theme (calm, dark, product-like; bundled fonts). React Router routes per §14,
   each pointing at an empty page in src/pages/brain/ or src/pages/trust/. src/api/client.ts reading window.__KAVACH__, sending X-Owner-Token, fixture mode
   via VITE_USE_FIXTURES, a streaming chat helper for §10. npm scripts: dev (proxy /api -> 8000, /r -> 9000),
   build, gen:types (openapi-typescript). Bundle fonts; no CDN.
8. scripts/bench_models.py: times first token, full answer, structured parse and embedding for the models
   in config.py against a running Ollama. scripts/reset_demo.py skeleton.
9. requirements.txt (pinned), pytest.ini with the llm marker, tests/ skeleton, README untouched.
Commit in logical steps with Conventional Commits. Stop and list anything in CONTRACT that is ambiguous.
```

### 6.2 BRAIN kickoff
```
Read AGENTS.md and CONTRACT.md §4, §5, §7, §8, §10, then docs/BUILD_PLAN.md §4.1-4.5 and §4.8.
You own the BRAIN track. Replace stubs with real code, in this order, tests for each, commit after each:
1. brain/llm.py (Ollama chat, structured output, embeddings, streaming; keep_alive from config)
2. ingest.py for PDFs + notes, watcher.py
3. embed.py hybrid search
4. chat.py sync + stream with citation check
5. pages/brain/Ask: streaming chat, numbered citations with popovers (GET /api/chunks/{id}),
   live auto-ingest strip (poll /api/ingest/events)                          <- M1
6. entities.py + graph-neighbour retrieval in chat
7. extract.py with grounding, memory.py with supersession + candidates + teach;
   "Remember this?" chips on Ask
8. parse_question.py with amount normalisation in code before the LLM (§4.10: ₹, commas, "50k",
   "1.2 lakh", "50,000/month" -> integers; unit tests) + decide.py (pure code rules, CONTRACT §5.4)
9. agent/planner.py
10. pages/brain/Vault: documents with signature badges, entity list, graph (react-force-graph-2d),
    highlight entities_used from the last answer                             <- M3
11. WhatsApp ingestion; pages/brain/Memory: per-field timeline, superseded values, "Teach KAVACH" box
Never decide disclosures in an LLM. Never import from kavach/trust except the functions in CONTRACT §7.
UI: build against fixtures first, then live API; regenerate types with npm run gen:types after API changes.
```

### 6.3 TRUST kickoff
```
Read AGENTS.md and CONTRACT.md §2, §5, §6, §7, §8, §9, §11, §12, §13, then docs/BUILD_PLAN.md §4.6-4.9.
You own the TRUST track. Replace stubs with real code, in this order, tests for each, commit after each:
1. trust/crypto.py, mock_issuers/ (keys, signed PDFs, tampered PDF, credential batches via wallet key export)
2. issuer_check.py, wallet.py, present.py
3. requester/verifier.py + requester/app.py, owner /api/ask path
4. pages/trust/Verify (requester mode): ask box, request list, five checks animating to ticks or
   crosses, "Owner-attested" label, storage panel            <- M1: five checks pass on laptop 2
5. pairing.py, consent.py, audit.py, all remaining api.py routes
6. malformed /api/ask* requests (FastAPI validation errors: bad body, missing X-* headers) audited as
   request_rejected, reason=malformed, detail = {route, client_ip, error_type} only, never the body; still 422
7. pages/trust/Queue: pairing cards, disclosure proposals (claim, proposed answer, trust level,
   favourable or not, Approve/Answer/Decline/Deny), planned tasks with previews, wallet warning;
   pages/trust/Audit: table + "Chain intact" badge                            <- M2
8. ledger.py
9. gate_mcp.py (streamable HTTP, forwards to /api/ask) + requester/agent_client.py
10. tools_mcp.py + agent/executor.py
11. scripts/run_eval.py and scripts/reset_demo.py
12. (low priority) /r/identity gains owner_reachable: bool (requester backend pings the owner) so the
    requester status stops relying on the browser's no-cors probe; CONTRACT §12 change via CONTRACT owner
13. (low priority) document the §10 stream in OpenAPI (responses= on /api/chat/stream with the ChatEvent
    models) so gen:types covers it and client.ts stops deriving stream types
Nothing requester-reachable may return document text, chunks or raw facts. Audit every decision.
UI: build against fixtures first, then live API; regenerate types with npm run gen:types after API changes.
```

### 6.4 DATA track tasks (non-code; any AI chat tool is fine for drafting)
Commit your own files through GitHub Desktop or the GitHub web editor, a few times a day.

**Today (Sat 26)**
- [ ] Send the vault files (once drafted) to a friend outside the team; ask for 15 questions per eval set A rules (§7). They must not see the system first
- [ ] Redact the real bank statement (names, account numbers, address). Keep it in `private/`, never commit it

**By Sun 27 night (M1)**
- [ ] `demo_data/notes/`: 5-8 informal Markdown notes with `[[links]]`: a "Flat move 2026" project note with tasks and deadlines, dated decisions (incl. "decided to renew only if rent stays under ₹15k"), recurring concepts (rent renewal, education loan)
- [ ] `demo_data/notes/inbox_note.md` (held back for the live moment): "Landlord said rent goes to ₹16k from January"
- [ ] `demo_data/chats/landlord.txt`: realistic WhatsApp export (`DD/MM/YY, HH:MM - Name: message`), with the landlord's name, email and rent discussion
- [ ] Rent agreement text for `demo_data/templates/` (TRUST turns it into a PDF): landlord, rent, deposit, start and end dates
- [ ] Details for the mock bank statement, marksheet and ID card (TRUST generates the signed PDFs)

**By Mon 28, 6 pm (M2)**
- [ ] `docs/pitch/demo_script.md`: 3-minute script for the submission video, built around the M2 slice
- [ ] Deck updated to the organisers' template: refined idea + architecture diagram + what's built so far
- [ ] Test every screen in the running app; post bugs and confusing UI in the group with screenshots
- [ ] Record and edit the demo video (evening, once M2 works)

**Tue 29 to Thu 1**
- [ ] Eval sets B, C (the 6 attacks), D, E as `eval/set_x.jsonl` (format agreed with TRUST)
- [ ] README filled in against the organisers' template; deck numbers from the eval run; final video; backup copies on two devices
- [ ] Rehearsal timing and Q&A prep (use the Q&A table in `docs/archive/plan_v3.md` §14)

## 7. Evaluation

| Set | Size | Written by | Measures |
|---|---|---|---|
| A. Ask my vault | 15 | a friend outside the team, from the vault files only, before seeing the system | answer + citation correct; ≥4 span 2+ sources; ≥3 about projects or decisions |
| B. Disclosures | 15 | DATA, incl. the real redacted statement | correct answer type; wrong-disclosure count (target 0) |
| C. Attacks | 6 | DATA writes, TRUST automates | tampered doc, replayed presentation, forwarded presentation (wrong aud), altered disclosure value, colluding narrowing with 3 keys, unpaired requester |
| D. Tasks | 5 | DATA | valid plan, right recipient + date, no unapproved execution |
| E. Memory | 5 | DATA | after a new file or confirmed candidate, current answer changes and old value shows superseded |

Format: `eval/set_x.jsonl`, one case per line. Report real vs synthetic separately, median latency, and the sample size on the slide. Only measured numbers go in the deck.

## 8. Cut list (in order, only when a milestone slips)
1. Graph visualisation (keep graph retrieval; screenshot)
2. `fill_rental_form`
3. Owner-attested arbitrary thresholds (issuer cutoffs only)
4. WhatsApp source
5. Second laptop (two browser windows, one laptop)
6. Memory screen (timeline folds into Ask)

## 9. Never cut
Automatic ingestion · cited Ask my vault · graph of people, projects, concepts, decisions · temporal + conversation memory · issuer-verifiable disclosure with holder binding · pairing + global ledger · one executed MCP task · inbound MCP gate · approval queue + audit chain · local-only runtime

## 10. Demo day checklist
- [ ] Models pulled on the demo laptop; `keep_alive` set; warm-up call before going on stage
- [ ] `frontend` built; `reset_demo.py` run; `kavach.db` backup restorable
- [ ] `agent_client.py` tested offline
- [ ] Hotspot with mobile data off; static IPs; ports 8000, 8001, 9000 open in both firewalls; `OWNER_URL` set
- [ ] Single-laptop fallback rehearsed
- [ ] Backup video on two devices
