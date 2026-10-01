# KAVACH: a private second brain with a safe front door

> Your life's paperwork, understood by an AI that lives on your laptop. It answers you fully, and answers everyone else minimally.

ASYNC'26 · Track 1: Sovereign AI · Team 1st Prize

_Organisers will share a README template; restructure to match it when it arrives._

## Problem
People's important knowledge (bank statements, agreements, IDs, marksheets, chats) is scattered. Using AI on it means uploading everything to a vendor's cloud, and proving one fact to someone means handing over the whole document. Each step leaks more than necessary and works against the DPDP Act's data-minimisation principle.

## Who it's for
Students (marksheets, scholarships, rentals), young professionals (salary slips, rent, loans) and families (IDs, insurance, agreements).

## What KAVACH does
| Stage | In KAVACH |
|---|---|
| Data | Watches a local folder and ingests PDFs, Obsidian-style notes and WhatsApp exports automatically |
| Knowledge | Chunks + embeddings, a graph of people, projects, concepts and decisions, grounded facts with source quotes |
| Memory | Temporal facts with supersession; learns from conversation only after the owner confirms |
| Reasoning | Cited answers across documents; outsider questions mapped to minimal claims, decided by code |
| Action | Owner-approved tasks executed through MCP tools; issuer-verifiable disclosures to people and AI agents |
| Identity | A signed document counts as the owner's only if it is in their name; identity from Aadhaar offline e-KYC (UIDAI signature checked offline) or a signed ID card |

Full feature list in plain words: [`docs/FEATURES.md`](docs/FEATURES.md).

## Architecture
_TODO: final diagram. See `CONTRACT.md` §1-2 for components and ports._

## Real vs simulated
- **Real:** local inference (Ollama, open-weight models), ingestion, graph, memory, selective-disclosure cryptography, holder binding, pairing, disclosure ledger, MCP servers and client, hash-chained audit, the holder check, and Aadhaar offline e-KYC verification against UIDAI's published certificates (tested with UIDAI-format test files; a real-file check is pending).
- **Simulated:** the issuers (a mock bank, board and government office signing with Ed25519, modelled on DigiLocker's issuer-signed documents). "Sending" an email writes a `.eml` file to `outbox/`, because the demo is offline.
- **Not claimed:** SD-JWT compliance (we are SD-JWT-style), zero-knowledge proofs, DigiLocker integration, organisation mode.

## Tech stack
Python, FastAPI, SQLite + NumPy, Ollama, PyMuPDF, watchdog, cryptography (Ed25519), MCP Python SDK, React + Vite + TypeScript + Tailwind + shadcn/ui.

## Run it
_TODO once M2 lands._

## Evaluation
_TODO: measured results only, with sample sizes._

## Prior work and how this was built
- Before this repository we had an idea deck (submitted 22 Sep 2026) and a written project plan (`docs/archive/`). No code existed before the first commit.
- The team used AI assistants during development (Claude Code for code; chat assistants for drafting content). The product itself uses no cloud AI: all inference runs on local open-weight models.
- Third-party libraries are used through their normal package interfaces and listed in `requirements.txt` and `frontend/package.json`.

## Licence
KAVACH's own code is MIT (see LICENSE).

### Third-party licences
- **PyMuPDF** (PDF text extraction, `pymupdf` in `requirements.txt`) is licensed **AGPL-3.0** (or a commercial licence from Artifex). Anyone redistributing KAVACH together with PyMuPDF must meet the AGPL's terms for that combination; see https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright.
- The other Python and frontend dependencies are under permissive licences (MIT, BSD or Apache-2.0); the bundled Inter and JetBrains Mono fonts are under the SIL Open Font License 1.1.
- **Models are not part of this repository.** Ollama downloads them, each under its own licence: `qwen2.5:7b` and `nomic-embed-text` are Apache-2.0; `qwen2.5:3b` (the laptop default in `kavach/config.py`) is under the Qwen Research License, which does not allow commercial use. Check each model's licence before any commercial use.

## Team
_TODO_
