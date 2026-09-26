# KAVACH — A Private Second Brain With a Safe Front Door

**ASYNC 2026 · Sovereign AI Track · Project Plan v3 (checked clause by clause against the track brief)**
Team of 3 · Build window: under 24 hours · Two members using Claude Code

---

## 0. Change log

### 0.1 v2 → v3: compliance audit against the brief

v2 was checked line by line against the official track brief (quoted verbatim in section 3.1). Most requirements were already met. These gaps were found and fixed:

| # | Brief wording | Gap in v2 | v3 fix | Section |
|---|---|---|---|---|
| 1 | "Automatic ingestion of documents and user-owned data" | Section 3 said "watched folder", but the API only had a manual rescan, and PDFs needed uploading | A folder watcher (`watchdog`) ingests new or changed files in `vault/` within seconds. Upload and rescan remain as extras | 6.7, 7.1 |
| 2 | "Connections between people, projects, concepts and decisions" | Entity types had PERSON and DECISION but no PROJECT or CONCEPT | Added `PROJECT` and `CONCEPT` entity types and edges (`WORKS_ON`, `PART_OF`, `ABOUT`, `RELATES_TO`). The demo vault and eval include project and decision questions | 6.2, 7.2, 9, 10 |
| 3 | "remember what it learns" | Memory learned from documents and an explicit "teach" box, but not from normal conversation | Chat proposes durable facts and decisions the owner states ("Remember this?"). One click stores them as owner-stated memory. Nothing is stored without confirmation | 6.7, 7.3, 8 |
| 4 | "an individual or organization" | Not addressed | Stated plainly: KAVACH is individual-first, which the brief's "or" allows. Organisation mode is roadmap only and not claimed | 2, 15, 16 |
| 5 | "Technologies teams can explore" | Not stated which ones are used | Stated: Ollama and an Obsidian-compatible vault (both on the list). Graphiti, Mem0 etc. are optional in the brief; temporal graph memory is implemented in SQLite, and we say so | 3.1, 14 |
| 6 | (internal consistency) | How holder keys reach the issuer, what happens when credential copies run out, and how requesters get the owner's pairwise key were unspecified | Specified | 6.4 |

Net effect on scope: about 2 extra hours for Member A, absorbed in the 7–12 block. Difficulty stays at 7.5/10.

### 0.2 v1 → v2: fixes from the first judging review

v1 was reviewed by a simulated judging panel. It scored about 5/10: strong governance, but "a bouncer with no house behind the door." v2 fixed every point in the design itself, not just in the pitch.

| # | Review point | v2 fix | Section |
|---|---|---|---|
| 1 | Not a second brain; owner can't ask anything; 10 hard-coded fields | **Owner-first brain:** ingests PDFs, a notes folder and a WhatsApp export; builds an entity graph with temporal memory; owner asks open questions in "Ask my vault" with cited answers | 3, 7.1–7.5 |
| 2 | Attestation is owner-signed, so it proves nothing | **Issuer-verifiable selective disclosure (SD-JWT-style):** the verifier checks the *bank's* signature on the one disclosed claim, bound to this request by the holder's key. Answers the issuer didn't sign are labelled "owner-attested," never dressed up | 6.4, 7.7 |
| 3 | Memory, graph and retrieval are decorative | Each one is now load-bearing, proven by a **delete test** (what breaks if the module is removed). Learned-policy "memory" dropped as a claim | 3 |
| 4 | No agentic action; MCP points the wrong way | **MCP in both directions.** Outbound: KAVACH plans and executes tasks for the owner (draft the reply with proof attached, create a renewal reminder, fill the rental form) behind approval + audit. Inbound: other agents ask through the same consent gate | 6.8, 7.9 |
| 5 | Inference guard beaten by changing requester ID | Requesters are **keypairs paired by the owner**. A **global cumulative disclosure ledger** assumes all requesters collude and refuses any answer that would narrow a value below a minimum range | 7.8 |
| 6 | Self-scored fit table (9/10, all "Strong") | Removed. Replaced by the delete test and a "claims we make / don't make" table | 3, 15 |
| 7 | "DigiLocker-ready" overclaim | Dropped. Now: "modelled on DigiLocker's issuer-signed documents; our mock uses Ed25519." | 6.5, 15 |
| 8 | `doc_commitment` is a tracking handle; `issuer_id` leaks | `doc_commitment` removed. **Batch-issued credential copies with one-time holder keys** make presentations unlinkable across verifiers. Issuer identity is revealed only on issuer proofs, because it's the minimum needed for trust | 6.4 |
| 9 | `hide_no` polarity-blind; CANNOT_CONFIRM read as NO | **Polarity-aware:** favourable answers flow; for an unfavourable one the owner chooses *Answer* or *Decline*, and a decline is labelled a decline, not disguised | 7.7 |
| 10 | Circular eval on script-made PDFs | Eval uses **real redacted documents**, a **held-out question set written by someone outside the team**, and reports real vs synthetic separately | 10 |
| 11 | "Why AI?" not supported by the demo | The demo uses messy real inputs (a real bank statement format, a WhatsApp chat, free-form notes), cross-document questions and action planning. None of that is regex territory | 7, 13 |
| 12 | Agent angle buried at 2:15 | Agents appear by 0:55, and both MCP directions are the spine of the demo | 13 |
| 13 | Member A overloaded | Rebalanced: A = brain, B = trust + actions, C = Streamlit UI + data + eval + deck | 11, 12 |
| 14 | Demo risks: sqlite-vec, offline `npx`, 7B latency, hotspot IP | NumPy search (no extension), own offline MCP client, pre-ingestion + measured latency budget, static IPs + single-laptop fallback | 5, 16 |

---

## 1. Problem statement (50 words)

People's important knowledge (bank statements, agreements, IDs, marksheets, chats) is scattered, and using AI on it means uploading everything to a vendor's cloud. When others need proof, people surrender whole documents. Each leaks more than necessary, defeats the DPDP Act's data-minimisation principle, and leaves individuals without an AI they own.

## 2. Solution (50 words)

KAVACH is a local-first second brain running on the owner's device. It ingests documents, notes and chats into a searchable knowledge graph with temporal memory, answers the owner's questions with citations, and executes approved tasks. Outsiders and AI agents get issuer-verifiable minimal disclosures, never documents, and every action is audited.

**One-line pitch:** "Your life's paperwork, understood by an AI that lives on your laptop. It answers you fully, and answers everyone else minimally."

**Who it's for:** anyone whose paperwork is private: students (marksheets, scholarships, rentals), young professionals (salary slips, rent, loans), families (IDs, insurance, agreements).

**Scope, stated plainly:** KAVACH is built for an **individual**. The brief asks for an AI that understands "an individual or organization's knowledge," so individual-first is within scope. The same architecture could serve a small organisation (a shared vault with per-member permissions), but that is roadmap, not something we claim to have built.

---

## 3. Compliance with the track brief

### 3.1 Clause-by-clause compliance matrix

Every sentence and listed element of the official brief, quoted verbatim, with where KAVACH meets it and where the demo shows it.

| # | Brief says (verbatim) | How KAVACH meets it | Where | Shown in demo |
|---|---|---|---|---|
| C1 | "run intelligent systems without handing over all of their data, knowledge and operational context to a third party" | Models, data and processing all stay on the owner's laptop. Only owner-approved disclosures and task outputs ever leave | 5, design rule 4 | 0:00, Wi-Fi off for the whole demo |
| C2 | "build towards a Sovereign Second Brain" | Owner-first brain: vault + graph + temporal memory + Ask my vault | 7.1–7.5 | 0:10–0:55 |
| C3 | "privately understand an individual or organization's knowledge" | Understands the individual's documents, notes and chats locally; individual-first (section 2) | 7.1, 7.2, 7.4 | 0:20 cross-document answer |
| C4 | "remember what it learns" | Temporal facts from documents with supersession; memory learned from conversation with confirmation; explicit teaching | 7.3 | 0:40 "Remember this?" + memory timeline |
| C5 | "reason across that information" | Multi-hop answers over graph neighbours + retrieved chunks, with citations | 7.5 | 0:20 project + agreement + new note in one answer |
| C6 | "safely execute useful tasks" | Planner → validated tool calls → owner approval → MCP execution → audit | 7.9 | 1:45 reply + reminder |
| C7 | "Think beyond a chatbot" | Automatic ingestion, executed tasks, verifiable disclosures, an MCP gate for other agents | 6.8, 7.7, 7.9 | 0:55–2:10 |
| C8 | "Open-weight/local models for private inference." | Ollama `qwen2.5:7b` / `3b`, `nomic-embed-text` | 5 (stack) | Wi-Fi off throughout |
| C9 | "A personal or organizational knowledge base." | Personal knowledge base: chunks, entities, edges, facts in SQLite | 6.6 | Vault screen |
| C10 | "Persistent long-term memory." | Facts persist with `valid_from` / `valid_to` / `superseded_by`; memory timeline screen | 6.6, 7.3 | 0:40 |
| C11 | "Semantic, keyword or graph-based retrieval." | All three: vector (NumPy cosine), keyword score, graph-neighbour expansion | 7.5 | 0:20 |
| C12 | "Automatic ingestion of documents and user-owned data." | Folder watcher ingests new or changed PDFs, notes and chat exports automatically | 7.1 | 0:10 drop a file, it appears |
| C13 | "Connections between people, projects, concepts and decisions." | Entity types `PERSON`, `PROJECT`, `CONCEPT`, `DECISION` (+ ORG, OBLIGATION, EVENT, PLACE, DOCUMENT) with typed, dated, cited edges | 6.2, 7.2 | 0:20 answer + graph view |
| C14 | "Agentic tool execution through systems such as MCP." | Outbound MCP tool server executed by KAVACH's agent; inbound MCP gate for other agents | 6.8, 7.9 | 0:55 (inbound), 1:45 (outbound) |
| C15 | "Controlled execution where actions can be reviewed and audited." | One approval queue for tasks and disclosures; hash-chained audit log | 7.7, 7.10 | 1:45 approve, 2:35 audit |
| C16 | "Local-first or self-hosted deployment." | Runs on one laptop; no internet at runtime | 5 | Throughout |
| C17 | "Technologies teams can explore" (optional list) | Uses **Ollama** and an **Obsidian**-compatible Markdown vault from the list. Graphiti, Zep, Mem0, Letta and pgvector are suggestions, not requirements; we implement temporal graph memory and vector search in SQLite to keep the offline demo reliable, and say so | 5 | — |
| C18 | "a working system where: Data → Knowledge → Memory → Reasoning → Action" | Every stage implemented and shown live (table 3.3) | 3.3 | Whole demo |
| C19 | "should feel like the beginning of an AI that an individual or organization could actually own" | Daily use for the owner (ask, remember, reminders, drafts), plus control over every interaction with the outside world | 8, 13 | 0:10–0:55, 1:45 |

### 3.2 Delete test: every part is load-bearing

No self-scores. For each element, this is **what visibly breaks in the demo if you delete it.**

| Brief element | Where KAVACH uses it | Delete test: what breaks without it |
|---|---|---|
| Open-weight / local models | Ollama `qwen2.5:7b` for extraction, chat, planning; `nomic-embed-text` for embeddings | Everything. There's no cloud fallback by design |
| Automatic ingestion of owned data | Folder watcher over `vault/` (PDFs, Obsidian-compatible notes, WhatsApp exports) | New files never reach the brain; the 0:10 demo moment fails |
| Personal knowledge base | Chunks + extracted entities + facts in one SQLite file | Chat has nothing to ground answers in |
| Semantic / keyword retrieval | Hybrid search feeds **Ask my vault** | "How much rent did I pay last quarter?" can't be answered |
| Graph-based retrieval + connections between people, projects, concepts, decisions | Entity graph (landlord, employer, bank, the "Flat move 2026" project, the "rent renewal" concept, dated decisions from notes, deadlines) with typed, time-stamped edges | "The landlord" can't be resolved to a person or contact, so the **draft-reply action has no recipient**, and multi-hop questions lose the link between agreement, payments and chat |
| Persistent long-term memory that learns | **Temporal memory:** facts have `valid_from` / `valid_to`; newer facts supersede older ones; it learns from conversation ("Remember this?") and explicit teaching | After the owner's correction, "What's my current salary?" gives the stale answer |
| Agentic tool execution via MCP | **Outbound:** KAVACH's planner calls tools on a local MCP tool server. **Inbound:** other agents ask via KAVACH's MCP gate | No task gets done for the owner; the landlord's agent can't ask at all |
| Controlled, reviewed, audited execution | One approval queue for disclosures *and* tasks; hash-chained audit log | Actions run unchecked; no tamper evidence |
| Local-first / self-hosted | Runs on one laptop; demo with Wi-Fi off | The core promise |

### 3.3 The five stages, end to end

| Stage | In KAVACH |
|---|---|
| **Data** | Watched `vault/` folder: PDFs (issuer-signed and real redacted), notes, WhatsApp export, ingested automatically |
| **Knowledge** | Chunks + embeddings, entities + relations, structured facts with source quotes |
| **Memory** | Temporal facts with supersession, memory learned from conversation, consent and disclosure ledgers |
| **Reasoning** | Owner chat over graph + retrieval; NL question → claim → deterministic decision |
| **Action** | Approved tasks executed via MCP tools; issuer-verifiable disclosures to outsiders and agents |

---

## 4. Feasibility and difficulty (honest)

| Version | Practical in 24 h? | Difficulty |
|---|---|---|
| Production (real DigiLocker / Account Aggregator, zero-knowledge range proofs, secure-hardware attestation) | **No.** Partner onboarding and ZK circuits take weeks | 9.5 / 10 |
| **v3 MVP** (brain + automatic ingestion + chat + temporal and conversation memory + graph + SD disclosures + MCP both ways + 3 tools + audit) | **Yes, but tight.** The cut list (section 12) protects the demo | **7.5 / 10** |

**Simulated, and said openly:** the issuers. A mock bank, board and government office sign documents and issue credentials with their own Ed25519 keys. The *mechanism* is real; the *issuers* are stand-ins. "Sending" an email means writing a `.eml` file to an outbox folder, because the demo is offline.

---

## 5. Architecture

```
 OWNER LAPTOP                                                  REQUESTER LAPTOP
 ┌──────────────────────────────────────────────────────────┐  ┌───────────────────────────┐
 │ Streamlit Owner UI (:8501)                                │  │ Requester app (:9000)     │
 │  Ask my vault · Vault · Requests & Tasks · Memory · Audit │  │  paired keypair           │
 │                        │ HTTP                             │  │  sends signed questions   │
 │ FastAPI core (:8000) ◄─┘                                  │◄─┤  verifies ISSUER sig,     │
 │  ├─ brain/   (A)  ingest · chunk · embed · entities ·     │  │  holder binding, nonce    │
 │  │                graph · temporal memory · chat ·        │  │  own trusted_issuers.json │
 │  │                question→claim · decide                 │  │  stores presentations only│
 │  ├─ trust/   (B)  issuer checks · wallet (credential      │  ├───────────────────────────┤
 │  │                copies + one-time keys) · presentations │  │ Landlord's agent          │
 │  │                · requester pairing · consent · ledger  │◄─┤ (MCP client → kavach-gate)│
 │  │                · audit chain                           │  └───────────────────────────┘
 │  ├─ agent/   (A plans, B executes)                        │
 │  │     planner (LLM → tool calls) ──► approval ──► MCP    │
 │  │                                     client ──► kavach-tools (B):
 │  │                                     draft_email · create_reminder ·
 │  │                                     fill_rental_form · save_note → ./outbox
 │  ├─ gate_mcp.py   (B)  inbound MCP server "kavach-gate"   │
 │ SQLite kavach.db (vectors as BLOBs, NumPy search)         │
 │ Ollama :11434  qwen2.5:7b · nomic-embed-text              │
 └──────────────────────────────────────────────────────────┘
   Network: phone hotspot, mobile data OFF, static IPs. Fallback: everything on one laptop.
```

### Design rules (give these to both Claude Code sessions)

1. **The LLM never makes the decision.** It extracts, summarises, maps questions to claims and proposes tool calls. Code compares values, checks signatures and validates tool arguments.
2. **Every fact is grounded.** Extraction returns an exact quote; code checks it exists and contains the value. Otherwise confidence is `low`.
3. **Owner-taught facts never become proofs.** They're labelled `owner_stated`, used in chat, and never in disclosures.
4. **Nothing leaves except presentations, owner attestations and approved task outputs.** No endpoint reachable by requesters or agents returns document text or raw facts.
5. **Every request, decision and action is logged**, refused ones included.
6. **No internet at runtime.** No CDNs; pyvis graphs embed their JS inline.

### Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | One language |
| Core API | FastAPI + Uvicorn | Fast, auto docs |
| Owner UI | **Streamlit** | Pure Python, fast for C to build, works offline |
| DB | SQLite | Zero setup |
| Vector search | Embeddings as BLOBs + **NumPy cosine** | A few thousand chunks is instant; no extension to fail on stage |
| LLM | Ollama `qwen2.5:7b` (fallback `qwen2.5:3b`) | Local, strong structured output |
| Embeddings | `nomic-embed-text` (768-dim) | Local |
| PDFs | PyMuPDF | Text + metadata |
| Folder watching | `watchdog` | Automatic ingestion |
| Crypto | `cryptography` (Ed25519, SHA-256) | Standard |
| MCP | `mcp` Python SDK: `FastMCP` servers + stdio client | Official; our own client works offline (no `npx`) |
| Graph view | `pyvis` with `cdn_resources="in_line"` | Offline |
| Calendar/email outputs | `.ics` text + Python `email` library (`.eml`) | Real files, no network |

---

## 6. CONTRACT (copy into the repo as `CONTRACT.md`)

### 6.1 Repo layout

```
kavach/
├── CONTRACT.md · README.md · requirements.txt · config.py
├── api.py                    # FastAPI app, mounts routers              (B)
├── db.py                     # schema + helpers                         (A)
├── brain/                    # MEMBER A
│   ├── watcher.py            # watchdog: auto-ingest new/changed files in vault/
│   ├── ingest.py             # PDF / notes folder / WhatsApp → text + chunks
│   ├── embed.py              # embeddings + hybrid search (NumPy)
│   ├── entities.py           # entity + relation extraction, dedupe → graph
│   ├── memory.py             # temporal facts, supersession, conversation memory, teaching
│   ├── extract.py            # structured fact extraction + grounding check
│   ├── chat.py               # Ask my vault: graph + retrieval → cited answer
│   ├── parse_question.py     # NL question → Claim
│   └── decide.py             # claim → proposed answer (deterministic)
├── agent/
│   ├── planner.py            # MEMBER A: instruction → validated tool-call plan
│   └── executor.py           # MEMBER B: runs approved plans via MCP client
├── trust/                    # MEMBER B
│   ├── issuer_check.py       # verify document signatures
│   ├── wallet.py             # credential copies, one-time holder keys
│   ├── present.py            # build presentations + owner attestations
│   ├── pairing.py            # requester keys, owner approval
│   ├── consent.py            # one queue for disclosures + tasks
│   ├── ledger.py             # cumulative disclosure ledger
│   └── audit.py              # hash-chained log
├── gate_mcp.py               # inbound MCP server "kavach-gate"          (B)
├── tools_mcp.py              # outbound tool server "kavach-tools"       (B)
├── ui/app.py                 # Streamlit owner UI                        (C)
├── requester/                # second laptop                             (B)
│   ├── app.py · agent_client.py · trusted_issuers.json · static/index.html
├── mock_issuers/             # (B)
│   ├── make_keys.py · issue.py   # signed PDFs + credential batches
├── scripts/run_eval.py       # (B builds, C specifies)
├── vault/                    # owner's data: pdfs/, notes/, chats/
├── outbox/                   # task outputs (.eml, .ics, filled forms)
├── keys/                     # gitignored
└── eval/                     # C: question sets
```

### 6.2 Knowledge model

**Entity** types: `PERSON`, `PROJECT` (an ongoing goal, e.g. "Flat move 2026", "Scholarship application"), `CONCEPT` (a recurring topic, e.g. "rent renewal", "education loan"), `DECISION` (a choice made, with date and reason), `ORG`, `PLACE`, `DOCUMENT`, `OBLIGATION` (rent, EMI, fee, renewal), `EVENT`.
**Edge** types (examples): `WORKS_ON` (person → project), `PART_OF` (document / obligation / decision → project), `ABOUT` (decision / document → concept), `RELATES_TO` (concept ↔ concept), `DECIDED` (person → decision), `LANDLORD_OF`, `EMPLOYED_BY`, `BANKS_WITH`, `STUDIED_AT`, `PAID`, `DUE_ON`, `PARTY_TO`, `MENTIONED_IN`. Every edge has `valid_from`, `valid_to`, `source_chunk_id`.

**Fact** (structured values; used by chat and by the decision engine)
```json
{
  "fact_id": "f_12", "entity_id": "e_owner", "field": "monthly_income", "value": 62000,
  "source_type": "issuer_doc", "doc_id": "d_03", "quote": "Salary Credit ... 62,000.00",
  "valid_from": "2026-04-01", "valid_to": null, "superseded_by": null, "confidence": "high"
}
```
`source_type` ∈ `issuer_doc` (from an issuer-signed document) · `extracted` (unsigned document, chat, notes) · `owner_stated` (taught by the owner).

**Disclosable claims (outsiders can only ask about these):**

| Claim | Issuer-provable form (fixed cutoffs, like ISO 18013-5 `age_over_NN`) | Favourable polarity |
|---|---|---|
| Income | `income_ge_25000`, `income_ge_50000`, `income_ge_75000`, `income_ge_100000` | YES |
| Loan default (12 m) | `loan_default_12m` | NO |
| Age | `age_over_18`, `age_over_21` | YES |
| Marks | `percentage_ge_60`, `percentage_ge_75`, `percentage_ge_90` | YES |
| Result | `result_pass` | YES |
| Board | `board` (value disclosed) | n/a |

Never disclosable: name, date of birth, account numbers, address, exact amounts.

### 6.3 Answer types

| Answer | Meaning | Trust level |
|---|---|---|
| `ISSUER_PROOF` | Disclosed claim signed by the issuer; verifier checks the issuer | Highest |
| `OWNER_ATTESTED` | Owner's signed statement for a threshold the issuer didn't pre-sign (e.g. ₹60k) | Owner's word, labelled as such |
| `DECLINED` | Owner chose not to answer | Labelled plainly |
| `CANNOT_CONFIRM` | KAVACH doesn't have grounded data | — |
| `REFUSED` | Blocked by policy or the disclosure ledger, or not a disclosable claim | — |

### 6.4 Credentials, presentations, attestations (SD-JWT-style)

"SD-JWT-style" means the same idea as the IETF SD-JWT selective-disclosure design (salted hashes of each claim, issuer signs the list of hashes, holder reveals chosen claims and proves possession with a key), encoded as plain JSON. **We say "SD-JWT-style," not "SD-JWT compliant."**

**Credential (issued by mock issuer, stored in owner's wallet)**
```json
{
  "iss": "mock_bank", "credential_type": "income_proof", "copy": 7,
  "holder_pubkey": "base64...",              // one-time key for this copy
  "iat": "2026-09-01T00:00:00Z", "exp": "2027-03-01T00:00:00Z",
  "digests": ["sha256(salt|claim|value)", "..."],   // one per claim, shuffled
  "issuer_sig": "base64..."
}
```
- The issuer issues a **batch of 20 copies**, each with fresh salts and a **different one-time holder key**. Each copy is used for one presentation only, so two verifiers can't link their presentations by comparing signatures, digests or keys.
- The wallet stores the salts and values for every digest (the "disclosures") plus the private key for each copy.
- **Issuance flow:** the owner's wallet generates 20 one-time Ed25519 keypairs and exports only the public keys to the issuer (`mock_issuers/issue.py --holder-keys pubkeys.json`). The issuer binds one public key to each copy. Private keys never leave the wallet.
- **When copies run out:** the Queue screen warns when fewer than 3 unused copies remain. At zero, only owner-attested answers are possible until the issuer re-issues a batch.

**Presentation (sent to a requester for `ISSUER_PROOF`)**
```json
{
  "type": "kavach/presentation",
  "credential": { ...exactly as issued... },
  "disclosures": [{ "salt": "x9...", "claim": "income_ge_50000", "value": true }],
  "binding": { "nonce": "from-requester", "aud": "requester-key-fingerprint",
               "iat": "...", "sig": "signed by the copy's holder key" }
}
```
**Verifier checks (on the requester laptop, with the verifier's own issuer trust list):**
1. `issuer_sig` is valid under the issuer's public key from the verifier's `trusted_issuers.json`.
2. Each disclosure hashes to a digest in the credential.
3. `binding.sig` is valid under `credential.holder_pubkey`, so the presenter holds the key.
4. `nonce` and `aud` match this request, so the presentation isn't replayed or forwarded.
5. Not expired.

**Owner attestation (for `OWNER_ATTESTED`)**: `{claim, answer, nonce, aud, iat, exp, owner_pairwise_pubkey, sig}`, signed with a **pairwise owner key per requester** (not linkable across requesters), whose public key is handed to the requester once, at pairing. Displayed on the verifier as "Owner-attested: this is the owner's signed word, not the bank's."

### 6.5 Document signatures (for the vault)

- The mock issuer signs SHA-256 of the PDF's normalised text; the signature goes in PDF metadata `keywords` as `{"iss": ..., "sig": ...}`.
- Valid → document is `issuer_signed`, and facts from it can be `issuer_doc`. Edited text → signature fails → `unsigned`.
- **Positioning:** "Modelled on DigiLocker's issuer-signed documents. Real DigiLocker documents carry the issuing department's certificate-based digital signature; ours uses Ed25519 for the demo."

### 6.6 SQLite schema

```sql
CREATE TABLE documents (doc_id TEXT PRIMARY KEY, path TEXT, source TEXT, doc_type TEXT,
  signature_status TEXT, iss TEXT, text_hash TEXT, ingested_at TEXT);
CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, locator TEXT, text TEXT, embedding BLOB);
CREATE TABLE entities (entity_id TEXT PRIMARY KEY, type TEXT, name TEXT, norm_name TEXT, attrs_json TEXT);
CREATE TABLE edges (edge_id TEXT PRIMARY KEY, src TEXT, rel TEXT, dst TEXT,
  valid_from TEXT, valid_to TEXT, source_chunk_id TEXT);
CREATE TABLE facts (fact_id TEXT PRIMARY KEY, entity_id TEXT, field TEXT, value TEXT, source_type TEXT,
  doc_id TEXT, quote TEXT, valid_from TEXT, valid_to TEXT, superseded_by TEXT, confidence TEXT);
CREATE TABLE credentials (cred_id TEXT PRIMARY KEY, iss TEXT, credential_type TEXT, copy INT,
  credential_json TEXT, disclosures_json TEXT, holder_privkey BLOB, used INT DEFAULT 0);
CREATE TABLE requesters (fingerprint TEXT PRIMARY KEY, pubkey TEXT, name TEXT, type TEXT,
  status TEXT, owner_pairwise_privkey BLOB, paired_at TEXT);
CREATE TABLE requests (request_id TEXT PRIMARY KEY, requester_fp TEXT, channel TEXT, question TEXT,
  claim_json TEXT, nonce TEXT, status TEXT, answer TEXT, payload_json TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE disclosure_ledger (field TEXT PRIMARY KEY, lo REAL, hi REAL, distinct_thresholds INT, updated_at TEXT);
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, instruction TEXT, plan_json TEXT, status TEXT,
  result_json TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, ref_id TEXT,
  detail_json TEXT, prev_hash TEXT, entry_hash TEXT);
```
- `requesters.status` ∈ `pending` · `paired` · `blocked`
- `requests.status` ∈ `pending` · `answered` · `refused`; `channel` ∈ `web` · `mcp`
- `tasks.status` ∈ `planned` · `approved` · `rejected` · `done` · `failed`
- `audit_log`: `entry_hash = sha256(prev_hash + ts + event + ref_id + detail_json)`

### 6.7 Core API (FastAPI :8000)

Owner endpoints require `X-Owner-Token`. Requester endpoints require a **signed body**: `sig` = Ed25519 over the canonical JSON of the rest, by `requester_pubkey`.

| Method | Path | Who | Body / Query | Returns |
|---|---|---|---|---|
| POST | `/ingest` | owner | multipart file (optional: copies into `vault/`, the watcher does the rest) | `{doc_id, signature_status, entities_added, facts:[Fact]}` |
| POST | `/ingest/sync` | owner | — | forces a rescan of `vault/` (the watcher normally does this automatically) |
| GET | `/ingest/events` | owner | — | recent automatic ingestions (file, time, entities added), polled by the UI |
| GET | `/documents`, `/entities`, `/facts` | owner | — | lists |
| GET | `/graph` | owner | `?entity_id=` optional | `{nodes, edges}` |
| POST | `/chat` | owner | `{question, history}` | `{answer, citations:[{doc_id, locator, quote}], entities_used, memory_candidates:[{candidate_id, statement, field, value, valid_from}]}` |
| POST | `/memory/candidates/{id}/decision` | owner | `{remember: bool}` | stores it as an `owner_stated` fact or a DECISION node, or discards it |
| POST | `/memory` | owner | `{statement}` | new `owner_stated` fact + what it superseded |
| GET | `/memory/timeline` | owner | `?field=` | fact versions over time |
| POST | `/tasks` | owner | `{instruction}` | `{task_id, plan:[{tool, args, preview}]}` status `planned` |
| POST | `/tasks/{id}/decision` | owner | `{approve}` | executes if approved; returns results |
| GET | `/queue` | owner | — | pending disclosure requests, new requesters, planned tasks |
| POST | `/requests/{id}/decision` | owner | `{action: "answer" \| "decline" \| "deny", remember: bool}` | updated request |
| POST | `/requesters/{fp}/decision` | owner | `{approve}` | pairing result |
| GET | `/audit` | owner | — | `{entries, chain_intact}` |
| POST | `/ask` | requester | `{requester_pubkey, requester_name, requester_type, question, nonce, ts, sig}` | `{request_id, status}` (unknown key → pairing request to owner) |
| GET | `/ask/{id}` | requester | — | `{status, answer, payload}` (presentation or attestation) |

### 6.8 MCP, both directions

**Inbound: `kavach-gate` (`gate_mcp.py`, `FastMCP`)** — for other people's agents.

| Tool | Args | Returns |
|---|---|---|
| `list_disclosable_claims` | — | claim names + which are issuer-provable (no values) |
| `ask` | `question, nonce, requester_pubkey, sig` | `{request_id, status}` — same pairing, consent and ledger as `/ask` |
| `get_answer` | `request_id` | `{status, answer, payload}` |

**Outbound: `kavach-tools` (`tools_mcp.py`, `FastMCP`)** — tools KAVACH uses for the owner. **Only `agent/executor.py` calls these, and only for approved tasks.**

| Tool | Does | Output |
|---|---|---|
| `draft_email` | `to, subject, body, attachments[]` | `outbox/*.eml` (attachments may include a presentation JSON) |
| `create_reminder` | `title, date, notes` | `outbox/*.ics` |
| `fill_rental_form` | `fields{}` | `outbox/rental_application_filled.pdf` from a template (only fields the owner approved) |
| `save_note` | `title, markdown` | `vault/notes/*.md` (ingested back into the brain) |

`requester/agent_client.py` is a ~40-line Python MCP stdio client that plays the landlord's agent. It needs no `npx` or internet.

---

## 7. Component specs

### 7.1 Ingestion (`brain/watcher.py`, `brain/ingest.py`) — A
- **Automatic:** `brain/watcher.py` uses `watchdog` to monitor `vault/pdfs`, `vault/notes` and `vault/chats`. A new or changed file is ingested within seconds (2 s debounce). A changed note replaces its old chunks, and facts from the old version are superseded, not deleted. Each ingestion is logged and shown in the UI.
- **PDF:** PyMuPDF text per page; normalise; `text_hash`; `trust.issuer_check.verify()`.
- **Notes:** every `.md` in `vault/notes` (Obsidian-compatible; `[[links]]` become `MENTIONED_IN` edges).
- **WhatsApp export:** parse `.txt` lines `date, time - name: message` into messages; chunk by conversation window.
- Chunk ~600 characters with overlap; `locator` = `page 2` / `note: rent.md` / `chat: 2026-08-14 21:03`.
- **Pre-ingest the demo vault before going on stage.** Live, drop in only two small files: one short note (automatic-ingestion moment) and the tampered PDF (signature moment).

### 7.2 Entities and graph (`brain/entities.py`) — A
- Per chunk, the LLM returns `{entities:[{type, name, attrs}], relations:[{src, rel, dst, date?}]}` via Ollama structured output, `temperature: 0`.
- Dedupe by normalised name (lowercase, strip titles, "Mr./Smt.") + type; merge attributes (phone, email).
- Every edge keeps `source_chunk_id`, so the graph is citable.
- Prompt guidance: a PROJECT is an ongoing goal with tasks or deadlines; a CONCEPT is a topic that recurs across sources; a DECISION must quote the sentence where it was made and carry its date. Example: the note line "Decided to renew only if rent stays under ₹15k" → `DECISION(renew only if rent < ₹15k, 2026-08-20)` –ABOUT→ `CONCEPT(rent renewal)` and –PART_OF→ `PROJECT(Flat move 2026)`.

### 7.3 Temporal memory (`brain/memory.py`) — A
- A new fact for the same `(entity, field)` sets the old fact's `valid_to` and `superseded_by`.
- Owner teaching: `/memory` with "My salary went up to ₹70k from October" → LLM maps it to `{field, value, valid_from}` → stored as `owner_stated` → supersedes for *chat* purposes only.
- Chat answers "current" questions from the latest valid fact, and says where it came from ("You told me on 21 Sep; your last bank statement shows ₹62k").
- **Learning from conversation:** after each chat turn, the LLM lists durable facts or decisions the *owner* stated (never ones KAVACH said). Each becomes a candidate shown under the answer as "Remember this?". One click stores it (an `owner_stated` fact, or a DECISION node linked to its project); ignoring it discards it. Nothing enters memory from conversation without confirmation.

### 7.4 Structured facts (`brain/extract.py`) — A
Extracts the disclosable fields (6.2) and useful everyday ones (rent amount, agreement end date, ID expiry, EMI date), each with a quote. **Grounding check:** the quote must appear in the text and contain the value's digits. Pass → `high`, fail → `low`.

### 7.5 Ask my vault (`brain/chat.py`) — A
1. Find entities named in the question (string match + embedding match against entity names).
2. Retrieve: top-k hybrid chunks (0.7 vector + 0.3 keyword) **plus** chunks linked to those entities' graph neighbours (1 hop).
3. Add current facts for those entities.
4. Prompt: answer **only** from the context; every sentence cites `[n]`; say "I don't have that" when absent.
5. **Citation check:** code verifies each `[n]` refers to a supplied chunk; answers with no valid citation are flagged in the UI.

### 7.6 Question → claim (`brain/parse_question.py`) — A
LLM maps an outsider's question to `{claim, op, value}` from the disclosable list, or `unsupported`. Code validates it. `"at least ₹50k"` → `income ≥ 50000`.

### 7.7 Decision + disclosure (`brain/decide.py` + `trust/present.py`) — A + B
```
claim unsupported or not disclosable            → REFUSED
no grounded issuer_doc fact and no credential   → CANNOT_CONFIRM
exact issuer-provable claim exists (e.g. income_ge_50000) and an unused copy exists
      → proposed ISSUER_PROOF (value from the credential)
other threshold (e.g. ≥ 60000) with a grounded issuer_doc fact
      → ledger check (7.8) → proposed OWNER_ATTESTED
result = plain Python comparison
if result is favourable → proposed answer shown to owner for approval
if unfavourable         → owner chooses: "Answer" (discloses it) or "Decline" (DECLINED, labelled)
```
- The owner approves every disclosure unless they set an explicit `auto_approve` rule for that requester and claim.
- **We never claim a decline is invisible.** Our claim is narrower: the owner decides, and a decline is shown as a decline.

### 7.8 Requester pairing + cumulative disclosure ledger (`trust/pairing.py`, `trust/ledger.py`) — B
- **Pairing:** a request from an unknown public key creates a pairing card: name, type, key fingerprint. Only paired keys get answers. Every `/ask` is signed; nonces are single-use.
- **Global ledger, assuming collusion:** for each numeric field, keep the tightest interval `[lo, hi)` implied by *all* answers ever given to *anyone*. A YES to "≥ t" sets `lo = max(lo, t)`; a disclosed NO sets `hi = min(hi, t)`.
- **Rule:** refuse any answer that would make `hi − lo` smaller than the minimum width (income ₹25,000; percentage 15 points), or that exceeds **3 distinct owner-attested thresholds per field per 30 days**.
- Issuer-provable cutoffs are spaced at the minimum width, so issuer proofs can't narrow below it either.
- Changing requester IDs doesn't help: the ledger doesn't care who asked.

### 7.9 Agent planner + executor (`agent/`) — A plans, B executes
1. Owner instruction, e.g.: "Reply to my landlord with proof I earn over ₹50k, and remind me before the agreement ends."
2. Planner (LLM, structured output) produces tool calls using the graph ("my landlord" → Ravi Kumar, `ravi.k@example.com` from the WhatsApp chat) and facts (agreement end date).
3. **Code validates** each call against the tool's JSON schema and allowed values (no sending to addresses not in the graph unless the owner types them).
4. The plan shows in the queue with a preview (email body, reminder date, form fields).
5. Owner approves → executor calls `kavach-tools` over MCP → files land in `outbox/` → audit entries.
6. If the plan needs a proof, the executor creates the presentation first (addressed to the landlord's paired key) and attaches it.

### 7.10 Audit (`trust/audit.py`) — B
Events: `ingested`, `requester_paired`, `request_received`, `disclosure_answered`, `disclosure_declined`, `request_refused_ledger`, `task_planned`, `task_approved`, `task_executed`, `memory_taught`. Hash-chained; the UI shows "Chain intact ✓".

---

## 8. Owner UI (Streamlit, built by C)

1. **Ask my vault:** chat with numbered citations; click a citation to see the source snippet; "Remember this?" chips under answers; a live strip of files auto-ingested from `vault/`.
2. **Vault:** documents with signature badges, entity list, graph view (pyvis inline).
3. **Queue:** new requesters, disclosure requests (claim, proposed answer, trust level, polarity), planned tasks (preview), with Approve / Decline / Deny buttons.
4. **Memory:** timeline per field (versions, superseded values, sources); "Teach KAVACH" box.
5. **Audit:** table + chain badge.

The requester app is a separate, deliberately plain page (built by B): ask, see status, verify with each check ticked or crossed.

---

## 9. Demo vault (C assembles, B generates the signed parts)

| Item | Source | Purpose |
|---|---|---|
| `bank_statement_signed.pdf` + income credential batch | mock bank | Issuer proofs |
| `bank_statement_REAL_redacted.pdf` | A team member's real statement, names and numbers redacted | Messy real format; eval credibility |
| `rent_agreement.pdf` | Realistic template, filled in | Obligations, end date, landlord entity |
| `marksheet_signed.pdf` + credential batch | mock board | Marks, result, board |
| `id_card_signed.pdf` + credential batch | mock government | Age proofs |
| `notes/` (5–8 Markdown notes) | Written by C, informal, Obsidian-style with `[[links]]` | A "Flat move 2026" project note with tasks; dated decisions ("decided to renew only if rent stays under ₹15k"); recurring concepts (rent renewal, education loan) |
| `inbox_note.md` (kept aside) | Short note: "Landlord said rent goes to ₹16k from January" | Dropped live for the automatic-ingestion moment; the next answer should notice the ₹15k renewal condition no longer holds |
| `chats/landlord.txt` | WhatsApp-format export, written realistically | Landlord contact, rent discussions |
| `bank_statement_TAMPERED.pdf` | Signed file with salary edited | Signature failure demo |

---

## 10. Evaluation (not circular)

| Set | Size | Written by | Measures |
|---|---|---|---|
| **A. Ask my vault** | 15 | **A friend outside the team**, given only the vault files, before seeing the system | Correct answer + correct citation. At least 4 questions must span 2+ sources, and 3 must involve projects or decisions |
| **B. Disclosures** | 15 | C, including questions about the **real redacted statement** | Correct answer type; wrong-disclosure count |
| **C. Attacks** | 6 | B | Tampered doc, replayed presentation, forwarded presentation (wrong `aud`), altered disclosure value, colluding narrowing with 3 keys, unpaired requester |
| **D. Tasks** | 5 | C | Plan validity, correct recipient and date, no unapproved execution |
| **E. Memory** | 5 | C | After a new file or a confirmed "Remember this?", the current answer changes correctly and the old value shows as superseded |

**Report:** vault accuracy and citation accuracy (real vs synthetic separately), disclosure accuracy, wrong-disclosure count (target 0), attacks blocked out of 6, task success out of 5, memory updates correct out of 5, median latency. **State the small sample size on the slide.** A real small number is worth more than an impressive-looking one.

---

## 11. Roles and Claude Code prompts

| Person | Owns |
|---|---|
| **A (Claude Code): the brain** | folder watcher + ingest, embeddings + search, entities + graph (incl. projects, concepts, decisions), temporal + conversation memory, fact extraction, chat, question parser, decision engine, task planner |
| **B (Claude Code): trust + action** | mock issuers, wallet, presentations + verifier, pairing, consent, ledger, audit, both MCP servers, executor, requester app, eval runner, API wiring |
| **C (Claude in a browser): UI + data + story** | Streamlit UI, demo vault assembly and redaction, eval sets (recruit the outside friend in hour 1), deck, demo script, backup video |

### Member A kickoff
```
Read CONTRACT.md fully. You own /brain, agent/planner.py and db.py. Don't edit /trust, the MCP servers,
/requester or /ui. Follow the design rules in section 5 exactly: the LLM never decides, every fact is
grounded, owner_stated facts never become proofs.

Build in order, with pytest tests for each step:
1. db.py per 6.6 (embeddings as BLOBs; NumPy cosine search, no sqlite-vec).
2. brain/ingest.py for PDFs, the notes folder and WhatsApp exports, plus brain/watcher.py for automatic
   ingestion (7.1). Stub trust.issuer_check if absent.
3. brain/embed.py hybrid search (0.7 vector + 0.3 keyword).
4. brain/chat.py per 7.5 with the citation check. This is checkpoint 1: it must work by hour 7.
5. brain/entities.py (7.2) including PROJECT, CONCEPT and DECISION types, and graph-neighbour retrieval in chat.
6. brain/extract.py with the grounding check (7.4), brain/memory.py with supersession and
   conversation-memory candidates (7.3).
7. brain/parse_question.py and brain/decide.py (7.6, 7.7).
8. agent/planner.py (7.9): structured tool calls validated against the kavach-tools schemas.
Use Ollama structured outputs, temperature 0. Commit after every step.
```

### Member B kickoff
```
Read CONTRACT.md fully. You own /trust, /mock_issuers, agent/executor.py, gate_mcp.py, tools_mcp.py,
/requester, api.py and scripts/run_eval.py. Don't edit /brain or /ui.

Build in order, with pytest tests for each step:
1. mock_issuers/: Ed25519 keys; signed PDFs (6.5); credential batches of 20 copies with fresh salts, each
   bound to a one-time holder public key exported by trust/wallet.py (6.4 issuance flow). Include the tampered PDF.
2. trust/issuer_check.py, trust/wallet.py, trust/present.py (presentations + pairwise owner attestations).
3. requester/app.py: signed /ask, polling, and a verifier that runs the five checks in 6.4 using its OWN
   trusted_issuers.json. This is checkpoint 1: a bank-signed disclosure verified on laptop 2 by hour 7.
4. trust/pairing.py, trust/consent.py, trust/ledger.py (7.8), trust/audit.py (7.10).
5. api.py: every endpoint in 6.7. Bind 0.0.0.0:8000.
6. gate_mcp.py and requester/agent_client.py (inbound MCP, same pairing/consent/ledger path).
7. tools_mcp.py (4 tools, outputs to ./outbox) and agent/executor.py (runs only approved plans).
8. scripts/run_eval.py for the four sets in section 10.
Commit after every step.
```

### Member C
- Hour 1: send the vault files to the outside friend for eval set A; redact the real bank statement.
- Build `ui/app.py` in Streamlit against the API contract (use stub JSON until the API is ready), including the "Remember this?" chips and the auto-ingest strip.
- Write notes, the WhatsApp export and the rent agreement; eval sets B and D; deck; demo script; backup video.

---

## 12. Timeline (about 23 hours)

| Hours | A | B | C | Checkpoint |
|---|---|---|---|---|
| **0–1** | **All:** repo + `CONTRACT.md`; install; `ollama pull`; **measure latency on the demo laptop** (targets: chat first token < 5 s, question parse < 3 s; if missed, use a GPU / Apple Silicon laptop as owner, or `3b` for parsing); lock scope | | | Scope frozen |
| **1–7** | ingest (3 sources), embeddings, chat with citations | mock issuers, wallet, presentations, requester verifier | redaction, vault content, eval A handoff, Streamlit skeleton | |
| **7** | | | | ✅ **CP1:** owner asks vault → cited answer; landlord laptop verifies a **bank-signed** disclosure |
| **7–12** | folder watcher, entities + graph retrieval (projects, concepts, decisions), facts + grounding, temporal + conversation memory, parser + decide | pairing, consent, ledger, audit, API endpoints | UI: Ask, Vault, Queue, Audit screens | |
| **12–14** | task planner | kavach-gate + agent client, kavach-tools + executor | deck | |
| **14** | | | | ✅ **CP2:** landlord's agent asks via MCP → owner approves → issuer proof; owner task → approved → `.eml` + `.ics` in outbox → logged |
| **14–17** | sleep | sleep (stagger by 1 h if behind) | deck, demo script | |
| **17–19** | memory timeline, chat quality fixes | attack demos, two-laptop test, latency tuning | Memory screen, graph view, rehearsal 1 | |
| **19** | | | | ✅ **Feature freeze** |
| **19–21** | fix eval failures | run eval | numbers into deck, backup video | |
| **21–23** | **Buffer:** 3 rehearsals Wi-Fi off, IP check, charge everything | | | |

### Cut list (in order, only at a missed checkpoint)
1. Graph visualisation (keep graph retrieval; show a screenshot)
2. `fill_rental_form` tool (keep email + reminder)
3. Owner-attested arbitrary thresholds (issuer cutoffs only; the ledger becomes trivial)
4. WhatsApp source (keep PDFs + notes)
5. Second laptop (two windows, one laptop)

### Never cut
Automatic ingestion (folder watcher) · Ask my vault with citations · graph of people, projects, concepts and decisions · temporal + conversation memory · issuer-verifiable disclosure with holder binding · pairing + global ledger · one executed MCP task · inbound MCP gate · approval queue + audit chain · local-only operation

---

## 13. Demo script (3 minutes)

| Time | Action | Line |
|---|---|---|
| 0:00 | Wi-Fi off on both laptops | "No internet from here on." |
| 0:10 | Drag `inbox_note.md` ("Landlord said rent goes to ₹16k from January") into `vault/notes` → it appears in the auto-ingest strip within seconds | "No upload button. It watches my folder." |
| 0:20 | **Ask my vault:** "Where does my flat-move project stand, and should I renew?" → cited answer linking the project note, the agreement's end date and the ₹15k renewal decision, noting that the new ₹16k rent breaks that condition | "People, projects and decisions from my documents, notes and chats, connected and reasoned over on my laptop." |
| 0:40 | In chat: "By the way, my salary went up to ₹70k from October." → "Remember this?" → confirm. Memory timeline shows ₹70k (owner-stated, October) next to ₹62k (bank statement) | "It remembers what it learns from me, and keeps proven facts separate from what I said." |
| 0:55 | Landlord's **agent** on laptop 2 (MCP) asks "Does the tenant earn at least ₹50,000?" → pairing card → approve | "Other people's AIs can ask. Only paired ones get answers." |
| 1:15 | Approve disclosure → laptop 2 verifier: **issuer signature ✓ (Mock Bank), claim ✓, holder binding ✓, nonce ✓** | "The landlord isn't trusting me. He's checking the bank's signature on one fact." |
| 1:35 | Show laptop 2's storage: one small JSON, no documents | "He never held a document." |
| 1:45 | Owner task: "Reply to my landlord with the proof and remind me a month before the agreement ends." → plan preview → approve → `.eml` with proof attached + `.ics` in outbox | "It does the work, but only after I say yes." |
| 2:10 | Attacks: tampered statement → signature fails; three paired landlord keys narrow 60k/70k/65k → **refused by the global ledger** | "Colluding doesn't help. The ledger assumes everyone talks to each other." |
| 2:35 | Audit: chain intact ✓, every step logged | "Every question and every action, on a tamper-evident record." |
| 2:50 | Eval slide (real vs synthetic) | "Measured on real documents, with questions written by someone outside the team." |

---

## 14. Stage Q&A: the review's questions, answered in the product

| Judge asks | Answer |
|---|---|
| "I'm the owner. Can I ask what I spent on rent, or when my ID expires?" | Yes, and that's the first thing we demo. Cross-document, cited, from PDFs, notes and chats. |
| "What does your green tick prove that a WhatsApp message saying 'yes' doesn't?" | For issuer proofs, the landlord verifies the **bank's** signature on that one claim, bound to his request by a one-time key. The tenant can't forge it. For thresholds the bank didn't pre-sign, we show **"Owner-attested,"** which is the owner's signed word, and we label it that way. |
| "Delete embed, graph and memory. Which answer changes?" | Without embeddings, Ask my vault can't answer. Without the graph, "my landlord" can't be resolved, so the reply task has no recipient. Without temporal memory, the salary answer is stale after the owner's correction. |
| "Name one task KAVACH completes for its owner." | Drafts the reply to the landlord with a verifiable proof attached, and creates the renewal reminder. Both after approval, both logged. |
| "I'll rerun the narrowing attack with three requester IDs." | Please do. Requesters are paired keypairs, and the disclosure ledger is global: it assumes every requester colludes. |
| "Two landlords can link your presentations." | Each presentation uses a separate credential copy with fresh salts and a one-time key, so there's nothing shared to link on. |
| "Isn't CANNOT_CONFIRM just a NO?" | We don't pretend otherwise. Unfavourable answers are the owner's choice: answer or decline, and a decline is shown as a decline. |
| "Isn't this just SD-JWT / Account Aggregator?" | We use the SD-JWT *idea* on purpose; it's the right primitive. Account Aggregator moves financial data between regulated institutions. KAVACH is holder-side, works across domains (bank, board, government), serves individual verifiers like landlords, and adds what neither has: a private brain that understands messy documents, maps plain-language questions from people and AI agents to minimal disclosures, and executes tasks under the same consent and audit. |
| "What if the owner rigs their own KAVACH?" | Issuer proofs don't depend on the owner's software: the verifier checks the issuer. Owner-attested answers do, which is why they're labelled. Closing that gap needs zero-knowledge range proofs or trusted hardware; that's our roadmap. Anon Aadhaar is an existing open-source example of ZK over issuer-signed data. |
| "Why do you need an LLM?" | Cross-document questions over real statements, free-form notes and chats; entity resolution; planning tasks. The eval uses a real statement and questions written by someone outside the team. |
| "Is ingestion actually automatic?" | Yes. A folder watcher picks up new or changed files within seconds, shown live at 0:10. |
| "Where are projects and concepts?" | They're entity types in the graph. The flat-move project and the rent-renewal concept link documents, decisions and deadlines, and the 0:20 answer uses those links. |
| "Does it remember what it learns from you, or only from files?" | Both. Facts from conversation become memory after one-click confirmation, and newer facts supersede older ones with dates kept. |
| "Why not Graphiti, Mem0 or pgvector?" | They're suggestions in the brief, not requirements. We built temporal graph memory and vector search on SQLite so the offline demo has no extra services to fail. We do use Ollama and an Obsidian-compatible vault from the list. |
| "Is this for organisations too?" | It's built for individuals, which the brief allows ("an individual or organization"). A shared organisation vault is on the roadmap; we don't claim it today. |

---

## 15. Claims we make / claims we don't make

**Put this discipline into every slide.**

| We say | We don't say |
|---|---|
| "Runs entirely on this laptop; demo is offline" | "Unhackable" or "perfectly private" |
| "Issuer-verifiable disclosure: the verifier checks the issuer's signature" | "Anyone can verify anything" (owner-attested answers are the owner's word) |
| "SD-JWT-style selective disclosure" | "SD-JWT compliant" |
| "Modelled on DigiLocker's issuer-signed documents; issuers simulated" | "DigiLocker-ready" or "integrated" |
| "Presentations are unlinkable across verifiers by design" | "Zero knowledge" |
| "The owner decides whether to answer or decline" | "A 'no' is never revealed" |
| "Measured: [real numbers], small sample" | Any number we didn't measure |
| "Automatic ingestion from watched local folders" | "Connects to your email, cloud drives or WhatsApp" (the chat is an exported file) |
| "Built for individuals; organisation mode is roadmap" | "Works for teams and businesses today" |
| "Temporal graph memory built on SQLite" | "Uses Graphiti / Mem0" |

**No self-scored fit table in the deck.** Show the delete-test table instead; it proves fit rather than asserting it.

---

## 16. Roadmap slide

- Organisation mode: a shared vault for a small team, clinic or NGO, with per-member permissions
- Live connectors: email and calendar ingestion
- Real issuers: DigiLocker issuer signatures, Account Aggregator for financial data
- Zero-knowledge range proofs (any threshold becomes issuer-backed)
- Trusted-hardware attestation for owner-attested answers
- Kannada/Hindi documents and OCR for scans
- Phone app; W3C Verifiable Credentials / spec-compliant SD-JWT formats

---

## 17. Setup checklist (while you still have internet)

- [ ] `pip install fastapi uvicorn streamlit pymupdf cryptography numpy mcp pyvis watchdog httpx pytest python-multipart`
- [ ] Folder watcher tested: a file dropped into `vault/notes` shows in the UI within seconds
- [ ] `ollama pull qwen2.5:7b`, `ollama pull qwen2.5:3b`, `ollama pull nomic-embed-text`
- [ ] Latency measured on the owner laptop against the hour-1 targets; owner laptop chosen accordingly
- [ ] Ollama `keep_alive` set so the model stays loaded (no cold start on stage)
- [ ] Demo vault pre-ingested; `kavach.db` backed up so you can restore a clean state in seconds
- [ ] `requester/agent_client.py` tested offline (no `npx` anywhere)
- [ ] Hotspot with mobile data off; **static IPs** set on both laptops; ports 8000, 8501, 9000 allowed through the firewall
- [ ] Owner URL configurable in the requester app; single-laptop fallback rehearsed
- [ ] Backup video saved locally on two devices
