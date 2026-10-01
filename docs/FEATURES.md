# FEATURES.md: what KAVACH does, in plain words

The current feature list, for writing the demo script, the pitch, the README or answering judges. Kept up to date
with the code (1 Oct 2026). Technical detail and measured runs: `docs/STATUS.md`. Interfaces: `CONTRACT.md`.
`docs/BUILD_PLAN.md` is the original plan and is out of date for features.

**One line:** a private second brain on your laptop. It answers you fully, with citations, and answers everyone else
minimally: yes/no proofs, never documents. No cloud AI; every model runs locally.

## 1. Your second brain (the owner)

**It reads your files by itself.** Drop PDFs, notes (Obsidian-style, with `[[links]]`) or WhatsApp chat exports into
the vault folder (or onto the Ask page) and KAVACH ingests them automatically. Changed files update; deleted files
are forgotten, keeping history instead of wiping it.

**Ask my vault.** Ask anything; the answer streams in with a citation on every sentence. Click a citation to see the
exact passage it came from. If the answer is not in your files, it says so instead of guessing. Text inside your
documents can never give the AI instructions.

**Numbers and dates are checked in code.** For "what's my rent?" KAVACH uses facts it has verified against their
source ("₹14,500 today, from your bank-signed statement, going up to ₹16,000 on 1 Jan 2027"). Decisions are checked
too: "I'll renew only if rent stays under ₹15,000" shows "condition stops being met on 1 Jan 2027". These appear as
a "Checked in code" card.

**Follow-up questions work.** "What's my rent?" then "and who do I pay it to?" uses the earlier question for context.

**Knowledge graph (Vault page).** People, organisations, projects, decisions and obligations from your files,
linked: "Ravi Kumar is your landlord", "this note is part of Flat move 2026". Click a document to see only its
entities; entities used in your last answer are highlighted.

**Memory with time.** One card per fact (rent, income, employer...): the value today, where it came from, a timeline
of earlier values and any change coming ("Changes to ₹16,000 on 1 Jan 2027 · in 3 months"). Signed documents count
more than notes, and notes more than what you told it. Facts it is unsure of are marked "unsure".

**Teach KAVACH.** Type "My gym fee went up to ₹1,500 from November" and it remembers, with the date. Chat also
offers "Remember this?" chips. What you teach is used for your own chat only, never to answer anyone else.

**Long WhatsApp chats.** Later messages in a long chat (a new rent, a decision) are read too, not just the start.

**Tasks with approval.** Say "Email my landlord that I'll renew", "Remind me to pay rent on 5 November", "Fill the
rental form" or "Save a note that...". KAVACH plans it (recipient from your graph, dates, form fields from your
facts), shows a preview, and does nothing until you approve. Then it writes real files: an email draft, a calendar
reminder, a filled PDF form, a note. It warns when an email would reveal private numbers.

## 2. Identity: whose documents are yours

**The problem.** A bank's signature proves the bank issued a document, not that it is yours. Without a check, someone
could drop a friend's genuine bank statement into the vault and KAVACH would vouch with the friend's income.

**Signature check.** Every PDF's issuer signature is verified. An edited (tampered) PDF shows "Signature check failed"
and is ignored for answers, memory and the graph.

**Holder check.** Every signed document must be in your name, and its date of birth must match if it states one. If
not, it gets an amber **"Signed, but not in your name"** badge, its numbers are kept only as "unsure", they never
replace your own, and KAVACH never vouches from it. Demo moment: drop `friend_bank_statement.pdf` (genuinely signed
by the mock bank, for Rohan Mehta): amber badge, chat still says your income is ₹62,000, and a landlord's question
can never be answered from the friend's ₹95,000.

**Where your identity comes from**, strongest first:
1. **Aadhaar (UIDAI offline e-KYC)**, see below.
2. Your first government-signed ID card in the vault.
3. The configured name, shown as "Identity not verified".

**Verify with Aadhaar (UIDAI).**
1. Download your Aadhaar offline e-KYC from myAadhaar: a locked ZIP with a share code you choose.
2. In the Vault, click **"Verify with Aadhaar"**, choose the ZIP, type the share code.
3. KAVACH checks **UIDAI's digital signature** with UIDAI's official public certificates (downloaded once from
   uidai.gov.in and bundled), entirely **offline** on the laptop.
4. It keeps **only your name, date of birth and the last 4 digits**. The photo, address, contact details and the file
   itself are thrown away, never stored or logged.
5. The Vault shows **"Verified by UIDAI ✓ · A. I. · born 2003"** (initials and birth year only; the last 4 digits are
   never shown). Every signed document is rechecked against it. **Remove** takes it back out and deletes what was
   kept.

Clear errors: wrong share code, "UIDAI's signature doesn't match" (changed or fake file), "not an Aadhaar offline
e-KYC ZIP". Every import, failure and removal is in the audit log without any personal value.

Status: tested with files signed exactly the way UIDAI signs them; not yet run on a real UIDAI file. Check one with
`python scripts/check_aadhaar.py private\<file>.zip` (prints only yes/no, issuer, initials, birth year). Do not import
a real Aadhaar into the demo vault: its documents are in "Ananya Iyer's" name and would all turn amber.

## 3. The safe front door (outsiders and their AI agents)

**Ask, don't take.** A landlord or employer uses the requester app (web, or an AI agent over MCP) to ask yes/no
questions: "earns at least ₹50k?", "over 18?", "scored at least 75%?". Every request is signed, timestamped and
can't be replayed. A new requester needs your approval first ("pairing").

**Code decides, not the AI.** The AI only reads the question ("50k", "1.2 lakh" are understood). Plain code decides
the answer. Questions about exact amounts, addresses or documents are refused automatically.

**Five possible answers:**
- **Issuer proof:** a selective-disclosure credential from the bank/board/government (SD-JWT-style). It reveals one
  claim, is used once, and can't be linked across requesters.
- **Owner-attested:** you sign the answer yourself, only when a signed document in your own name backs it.
- **Declined**, **Cannot confirm** (nothing signed in your name covers it), **Refused**.

**Anti-snooping ledger.** Blocks narrowing attacks (asking ₹50k, then ₹55k, then ₹60k to work out your salary) and
limits how many self-attested answers go out per month.

**Queue page.** Approve requesters, see each proposed answer in plain words, approve or decline; plan and approve
tasks; see the outbox and the credential wallet (with a low-copies warning).

**Verify page (requester side).** Shows the five cryptographic checks passing one by one, and "What this laptop
stores" to prove no documents ever arrive. Shows whether the owner laptop is reachable.

**Audit log.** Every request, refusal, decision, file, task and identity change is in a hash-chained log; the Audit
page shows "chain intact" or exactly where it was broken. No document text or values in it.

## 4. Security and privacy, built in
- Local AI only (Ollama, open-weight models); no cloud, no CDNs, works with Wi-Fi off.
- The owner API answers only this laptop (token + local address); proxy tricks and DNS rebinding are blocked.
- Data minimisation everywhere: outsiders get yes/no proofs, Aadhaar keeps three fields, the audit log keeps no values.
- If the local model is down, you get a clear "local model unavailable" message.

## 5. Real vs simulated
- **Real:** local AI, ingestion, graph, memory, citation checking, signature and holder checks, selective-disclosure
  cryptography, pairing, ledger, MCP, audit chain, Aadhaar signature verification code with UIDAI's real certificates.
- **Simulated:** the issuers (a mock bank, exam board and government office signing with Ed25519). "Sending" an email
  writes a `.eml` file, because the demo is offline.
- **Not claimed:** SD-JWT compliance (we are SD-JWT-style), zero-knowledge proofs, DigiLocker integration.

## 6. Evidence
- Eval sets: A vault questions (15), B disclosure questions (15), C attacks (6), D tasks (5), E memory statements (5).
  Latest dry run (owner laptop CPU, qwen2.5:3b): A 15/15 answers, B 15/15 with 0 wrong disclosures, C 6/6 attacks
  blocked with the chain intact, D 5/5, E 5/5. Deck numbers: rerun on the demo laptop (RTX 4060).
- 800+ automated Python tests and 110+ frontend tests.

## 7. Demo tools
- `scripts/reset_demo.py`: clean, pre-loaded demo vault; `--restore` in seconds.
- `scripts/make_friend_statement.py`: the friend's genuinely signed statement for the holder-check moment.
- `scripts/requester_preflight.py`: checks the two-laptop setup (clock, trust list, ports).
- `scripts/check_aadhaar.py`: checks a real Aadhaar file, printing nothing personal.
- `scripts/run_eval.py`: the eval numbers.
