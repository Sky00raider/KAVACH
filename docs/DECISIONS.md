# DECISIONS.md

One line per decision: date, decision, why. Newest at the bottom.

| Date | Decision | Why |
|---|---|---|
| 2026-09-22 | Track 1 (Sovereign AI); individual-first, organisation mode is roadmap only | Brief allows "individual or organization"; keeps scope honest |
| 2026-09-22 | SD-JWT-style selective disclosure, issuers simulated with Ed25519 | Right primitive for minimal proofs; real issuer onboarding is out of reach |
| 2026-09-22 | Temporal graph memory and vector search in SQLite + NumPy, not Graphiti/Mem0/pgvector/sqlite-vec | No extra services or extensions to fail in an offline demo |
| 2026-09-26 | Build window is 26 Sep to 1 Oct, not 24 h | Organisers want a prototype at the on-campus round plus a 28 Sep submission with video |
| 2026-09-26 | React + Vite + TS + Tailwind + shadcn/ui frontend, served by FastAPI; Streamlit and pyvis dropped | Product experience is judged; verification and citation moments need a real UI; still one port, fully offline |
| 2026-09-26 | react-force-graph-2d for the graph view | Interactive, bundles offline, simple API |
| 2026-09-26 | Owner API under `/api`, requester backend under `/r`; owner routes need token + loopback | Lets one frontend build serve both modes; keeps owner routes off the LAN |
| 2026-09-26 | `kavach-gate` uses MCP streamable HTTP on :8001 and forwards to `/api/ask`; `kavach-tools` stays stdio | stdio cannot cross laptops; forwarding keeps web and MCP on one consent path |
| 2026-09-26 | Requester laptop runs its own backend (`requester/app.py`) that verifies with its own trust list | Verification must not depend on the owner's software |
| 2026-09-26 | Emailed proofs attach the presentation from an already-answered request (`request_id`) | Keeps nonce/aud binding meaningful; no owner-invented nonces |
| 2026-09-26 | Model names only in `config.py`; `OLLAMA_URL` can point at a campus GPU box | Benchmark newer open-weight models on day 1 without code changes |
| 2026-09-26 | Real redacted documents live in gitignored `private/` | Never publish personal data, even redacted |
| 2026-09-26 | No separate FRONTEND track: BRAIN and TRUST each build the UI pages for their features on a shared shell; new non-code DATA track owns demo data, eval sets, README, deck and video | Only two members use coding agents; whoever builds a feature builds its screen, so no handoff |
| 2026-09-26 | Scaffold fills shapes CONTRACT leaves open (marked "(scaffold)" in `models.py`): `Proposal`, `FactVersion`, `SignatureResult`, `CredentialRef`, `RequestView`, `IngestResult`, `Plan.warnings`; `AskIn.ts` is ISO 8601 UTC; `Health.models` maps role -> name; `WalletStatus.low` lists credential types; audit `detail_json` is compact sorted-key JSON, hash hex | CONTRACT defines names but not fields; pinned now so tracks code against one shape. Change via CONTRACT owner |
