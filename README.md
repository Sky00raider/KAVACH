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

## Architecture
_TODO: final diagram. See `CONTRACT.md` §1-2 for components and ports._

## Real vs simulated
- **Real:** local inference (Ollama, open-weight models), ingestion, graph, memory, selective-disclosure cryptography, holder binding, pairing, disclosure ledger, MCP servers and client, hash-chained audit.
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
MIT (see LICENSE).

## Team
_TODO_
