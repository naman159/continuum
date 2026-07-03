# Continuum: Road to the Recursive Chapter Loop — Project Roadmap

**Date:** 2026-06-10
**Status:** Approved scope, plans written

## Goal

Complete Continuum into a closed loop: a structured novel repository built one
chapter at a time that can **plan → draft → critique → revise → ingest** its own
chapters, with the generated chapters feeding back into the repository safely.

## Current state (from the 2026-06-10 codebase assessment)

- **Ingest path** (chunk → 12 extraction passes → dedup → resolve → persist) is mature.
- **Read path** (hybrid BM25+dense retrieval) is built and tested but unwired.
- **Generation path** has a planner and a critic but no drafter, no draft→claims
  bridge, and no orchestrator.
- `canon_facts`, `temporal_constraints`, and events' SVO/story-time columns are
  never written, so the critic's strongest checks pass vacuously.
- Ingestion is non-atomic and non-repeatable; there is no entity merge/repair
  path; extraction prompts carry the full character/location roster (unbounded);
  relationships accumulate duplicate rows; chapters have no provenance.

## Projects

| # | Plan file | Delivers | Depends on |
|---|-----------|----------|------------|
| 1 | `2026-06-10-project-1-atomic-replayable-ingestion.md` | One transaction per chapter, `--replace` reprocessing, chapter provenance (`source`, `generation_meta`), `relationships.chapter_id`, injectable `db` in `process_chapter` | — |
| 2 | `2026-06-10-project-2-canon-facts.md` | 13th extraction pass proposing canon facts, upsert-unless-locked persistence, contradiction flags, lock/edit API + UI toggle | — |
| 3 | `2026-06-10-project-3-graph-hygiene.md` | Relationship dedupe at persist time, entity merge operation (API + CLI) | — (uses `db.transaction()`, not P1's session) |
| 4 | `2026-06-10-project-4-retrieval-backed-context.md` | Mention-aware, capped story context for extraction prompts (cost + quality at scale) | — |
| 5 | `2026-06-10-project-5-generation-loop.md` | Scene drafter, draft-claims extractor (critic bridge), style fingerprints, generate-chapter orchestrator with revision loop, critic-gated ingest-back, CLI/API/UI | **P1 required**; P2 strongly recommended; P4 recommended |

## Recommended execution order

```
P1 (ingestion hardening)  ──►  P5 (generation loop)
P2 (canon facts)          ──►  P5 (gives the critic teeth)
P3 (graph hygiene)        ──►  any time; before long novels
P4 (context selection)    ──►  any time; before long novels
```

P1 → P2 → P4 → P3 → P5 is a sensible serial order. P2/P3/P4 are mutually
independent and can be done in any order (or in parallel worktrees) after P1.

## Explicitly deferred (not in any plan)

- **Eval harness** (golden chapter set measuring extraction drift) — valuable,
  but orthogonal; do after P5 when generated text raises the stakes.
- **SVO / `story_time_ordinal` / `temporal_constraints` population** — the
  temporal critic check stays vacuous for now; revisit when timeline reasoning
  is actually needed by the drafter.
- **Embedding-based payoff/foreshadow matching** (today: SequenceMatcher).
- **Entity merge UI** — P3 ships API + CLI only.
- **knows_edges supersede chains** (knowledge invalidation/correction).

## Conventions for all five plans

- Tests-first (pytest); run from `backend/` with `.venv/bin/pytest`.
- New schema goes in **both** `schema.sql` (idempotent) and a new
  `pipeline/db/migrate_*.py` script, run against the dev DB during execution.
- Per CLAUDE.md, `docs/architecture.html` / `docs/reference.html` are updated in
  each plan's final task.
- Commit after every green task.
