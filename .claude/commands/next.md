---
description: Continue building a track from where it left off
argument-hint: <BRAIN|TRUST|SCAFFOLD> [optional focus]
---
Track and focus: $ARGUMENTS
1. Read AGENTS.md and docs/STATUS.md, then this track's step list in docs/BUILD_PLAN.md §6.
   Run git pull --rebase, git log --oneline -15, git status.
2. Pick the first unfinished step for this track (or the focus I gave). Read only the CONTRACT.md
   sections it needs.
3. Tell me the step and a short plan: files, tests, anything unclear in CONTRACT. Wait for my OK.
4. Implement with tests. pytest -m "not llm" must pass; also run llm-marked tests if Ollama is up.
5. Update docs/STATUS.md for this track, commit (Conventional Commits, track scope), push.
6. Report what's done, what's next, and any CONTRACT change needed (proposed, never made silently).
