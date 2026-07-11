# Continuum: Centralized Analyzer Architecture

**Date:** 2026-07-11
**Status:** Approved (brainstorming complete)

## Product statement

Continuum is a **continuity engine for serial fiction**. It ingests chapter
prose (written by a human, or by a writing agent through MCP), builds a
queryable knowledge base of the story world, projects point-in-time state from
it, and gates new chapters with a continuity critic. Two surfaces read it: the
React wiki (humans browsing) and the MCP server (agents writing). **It does
not generate prose** — the in-repo generation loop is deleted; Claude-via-MCP
is the writer.

## Decisions (from brainstorming)

1. **Product:** analyzer + agent memory. Generation loop deleted.
2. **Schema gap:** cut unproduced extras (SVO columns, story-time,
   `temporal_constraints`); fix state properly via typed extraction deltas
   replacing verb-regex inference. Materializer becomes the sole writer of
   derived state.
3. **Data compatibility:** breaking changes allowed. `chapters.raw_text` is
   ground truth; existing novels are re-processed. One consolidated
   `schema.sql` + version table; `migrate_*.py` scripts retired.
4. **Scope includes:** critic surfaced in wiki + MCP, `/api/search` + UI
   search page, eval harness, docs/README consolidation.

## Architecture: two spines over one database

```
WRITE SPINE (only writer)                READ LAYER (only reader for surfaces)
analyze_chapter()                        backend/reads/
  1 INGEST                                 every fn takes up_to_chapter
  2 EXTRACT (13 passes + typed deltas)     cutoff filtering in SQL, once
  3 PERSIST (one transaction)                ├── FastAPI routes (wiki)
  4 MATERIALIZE (replay deltas)              └── MCP tools (writing agents)
  5 CRITIQUE (persist findings)
```

Entry points — CLI `process-chapter`, API job (Process page), MCP
`save_chapter` — all call `analyze_chapter`. Nothing else writes to the DB.

## Component map

| Component | Fate |
|---|---|
| Ingestion, 13-pass extractor, dedup, canonicalizer, resolver | Keep (state pass upgraded to typed deltas) |
| State materializer + event replay | Keep; sole writer of derived state; regex heuristics deleted |
| Continuity critic (5 of 6 checks) | Keep; in the spine; findings persisted |
| Hybrid retriever | Keep; gains `/api/search` + UI page; still backs MCP `search_story` |
| FastAPI + React wiki | Keep; reads via read layer; new Search page + Continuity panel |
| MCP server | Keep; reads via read layer; cutoff fixes |
| `generation/` (planner, drafter, loop), `planner/` package | Delete (`draft_claims` prose→DraftChapter adapter survives → `critic/adapter.py`; `generation/style.py` moves to `pipeline/style.py` — the fingerprint stays as wiki chapter metadata) |
| `generate-chapter` CLI; generation path in Process page/route | Delete |
| Temporal critic check, `temporal_constraints`, SVO/story-time columns | Delete |
| `migrate_*.py` scripts | Delete; folded into consolidated versioned `schema.sql` |

New packages: `backend/reads/`, `backend/evals/`.

## The write spine

`analyze_chapter(novel_id, number, raw_text, ...)` in `pipeline/pipeline.py`
(renamed from `process_chapter`; same signature surface). Phases:

1. **INGEST** — upsert `chapters` row. `replace=True` cascade-deletes the
   chapter's prior extraction output, including its `state_deltas`.
2. **EXTRACT** — LLM passes as today, except the `entity_deltas` pass is
   replaced by a **`state_deltas` pass** emitting typed facts:
   `{kind: possession|location|knowledge|status, subject, object/location/
   detail, change}`. Name-based strings only at this stage.
3. **PERSIST** — single transaction. Resolver maps names→IDs (reference-only
   `create=False` for non-authoritative passes — the phantom-character fix).
   Writes entities, events, relationships, dynamics, threads, flags, scenes,
   knows_edges, commitments, canon_facts, multi-summaries, and the new
   `state_deltas` rows. **The extractor no longer writes `character_states`.**
4. **MATERIALIZE** — replay folds `state_deltas` (chapter order, insertion
   order within a chapter) into `character_states`, `located_in_edges`,
   `possesses_edges`. Verb-regex (`GAIN_VERBS`/`LOSS_VERBS`),
   `EMOTIONAL_HINTS`, and seed-from-existing-`character_states` are deleted;
   replay reads deltas only. Idempotent novel-scoped rebuild as today.
5. **CRITIQUE** — the prose→DraftChapter adapter runs over already-extracted
   claims (no extra LLM calls); the five surviving checks compare chapter N's
   claims against the world as of N−1; report persisted to
   `critique_reports`/`critique_findings` (replacing any prior report for the
   chapter). Non-blocking on the save path (informational); MCP
   `check_continuity` remains the pre-save gate for agents.

**Failure semantics:** phases 1–3 are one transaction, all-or-nothing.
Phases 4–5 derive from committed data and are idempotent; on failure the
chapter stays saved and a re-run repairs them (materialize CLI retained for
manual repair).

**Invariant:** DB state is always explainable as "raw chapters passed through
the spine." Re-processing a chapter yields the same downstream state as fresh
ingestion; deleting all derived tables and re-materializing reconstructs them
without LLM calls.

## Typed delta lifecycle

Extraction emits name-based deltas → persistence resolves and stores them as
immutable tier-2 rows (`state_deltas`, cascade-deleted with their chapter) →
materialization folds them into tier-3 projections. Deltas are persisted (not
consumed in-memory) so replay bugs and new projections are fixable/buildable
without re-paying extraction tokens.

## Schema changes (one consolidated, versioned schema.sql)

**Added**

- `state_deltas`: `id, chapter_id FK cascade, event_id NULL FK, ordinal INTEGER
  NOT NULL (narrative order within the chapter), kind CHECK
  (possession|location|knowledge|status), subject_id FK entities, object_id
  NULL FK entities, location_id NULL FK locations, change CHECK
  (gain|loss|move|learn|update), attribute TEXT NULL (which character_states
  field a status delta updates), detail TEXT, certainty FLOAT, created_at`.
- `critique_reports`: `id, chapter_id UNIQUE FK cascade, passed BOOL, ran_at,
  stats JSONB`.
- `critique_findings`: `id, report_id FK cascade, check_name, severity CHECK
  (fail|warn|info), message, quote, evidence JSONB`.
- `schema_version`: single-row version table stamped by `init-db`.

**Removed**

- `temporal_constraints` (whole table).
- `events.subject_entity_id`, `events.verb`, `events.object_entity_id`,
  `events.story_time_ordinal`, `events.narrative_order`, `events.scene_id`.
- `knows_edges.fact_id` (never linked; knowledge stays free-text
  `fact_description`).
- `chapters.generation_meta`; `source` simplifies to a plain tag
  (`human | agent`).

**Unchanged:** entities + typed tables, events (description, involved arrays,
embeddings, tsvector), scenes, canon_facts (+ lock semantics), commitments,
knows_edges, relationships, dynamics, plot_threads/thread_events,
continuity_flags, bitemporal edges, HNSW/GIN indexes. Replay ordering:
`(chapter number, insertion order)` — matches today's effective behavior.

## Read layer (backend/reads/)

The only code that SELECTs for presentation. Domain-shaped modules:
`characters.py`, `timeline.py`, `threads.py`, `knowledge.py`, `search.py`,
`continuity.py`, etc.

**Contract:** every function takes `novel_id` and `up_to_chapter: int | None`
(None = whole novel) and never returns data from later chapters. Cutoff
filtering happens in SQL here, once.

- FastAPI routes call `reads.*`; `api/queries.py` (~2,200 lines) dissolves
  into the read layer. Wiki "as of chapter N" slider → `up_to_chapter=N`.
- MCP tools call the same functions with `up_to_chapter=writing_chapter-1`;
  `mcp_server/queries.py` dissolves likewise. Fixes `timeline_events`
  (currently uncapped → spoilers). `open_threads` / `unresolved_commitments`
  become truly point-in-time: status derived from `closed_chapter` /
  `payoff_chapter` relative to the cutoff, not novel-wide status columns.
- **Search:** `reads/search.py` wraps `HybridRetriever`;
  `GET /api/novels/{id}/search?q=&as_of=`; new wiki Search page; MCP
  `search_story` calls the same function.

Writer-side queries (resolver, replay loads, critic checks) stay in the
pipeline; the read layer is for surfaces only.

## Critic as a product feature

- Every spine run persists a report + findings for the chapter.
- **Wiki Continuity page:** per-chapter pass/fail badges; expandable findings
  (check name, severity, message, quote, evidence link). Extraction-time
  `continuity_flags` remain as a separate tab (observations, not verdicts).
- **MCP:** `check_continuity(draft_text)` unchanged (adapter + critic on
  unsaved prose). `list_chapters` gains a per-chapter
  `critique: {passed, fails}` summary.
- **Checks kept:** knowledge_state, commitments, thread_coverage,
  location_possession (reads delta-derived edges), entity_mention/canon.
  Temporal check deleted with its tables.

## Eval harness (backend/evals/)

Pytest-runnable, offline-safe:

- **Golden set:** ~10 hand-written mini-chapters (fixture novel) + YAML answer
  key: expected entities, possession/location intervals, knowledge facts, and
  ~10 retrieval queries with expected hits.
- **Metrics:** (a) extraction fidelity — process fixture novel, assert
  projections match key (real-LLM run env-gated; mock mode keeps the harness
  itself testable); (b) retrieval recall@k over golden queries; (c) critic
  precision/recall — 5–6 seeded violations (wrong possessor, unknown
  knowledge, teleporting character) must FAIL, clean chapters must PASS.

## Hygiene, docs, testing

- **Delete:** `generation/`, `planner/` (adapter survives →
  `critic/adapter.py`), `generate-chapter` CLI, generation path in
  `api/routes/process.py` + Process page, `migrate_*.py`,
  `critic/checks/temporal_check.py`, stale top-level files (`BLOG_OUTLINE.md`,
  `architecture.md`, `architecture-recommendations.html`;
  `continuity-arch-deep-research.md` → `docs/research/` or delete).
- **Docs site (CLAUDE.md mandate):** regenerate `architecture.html` for the
  two-spine architecture; update `state-of-the-system.html` (roadmap
  superseded) and `reference.html` (new schema/API). Rewrite `README.md`
  (currently documents deleted `wiki/` modules and stale commands).
- **Tests:** adapt extraction/resolver/canonicalizer/materializer/critic
  suites to the delta flow; delete generation tests with their modules; new
  parametrized cutoff-contract suite for the read layer (no function leaks
  post-cutoff rows); critique persistence tests.

## Implementation sequencing (each step leaves the repo green)

1. Land the uncommitted phantom-character resolver fix.
2. Schema consolidation + `schema_version` table.
3. `state_deltas` extraction pass + persistence.
4. Materializer reads deltas; extractor stops writing `character_states`;
   delete replay heuristics.
5. Critic into the spine + `critique_reports`/`critique_findings`.
6. Read layer; re-point API routes and MCP tools; cutoff fixes.
7. `/api/search` + wiki Search page.
8. Delete `generation/`/`planner/` + stale docs/files.
9. Eval harness.
10. Docs site + README regeneration.

## Success criteria

- One write path: grep finds no `INSERT`/`UPDATE`/`DELETE` against story
  tables outside the spine (+ explicit admin endpoints like entity merge,
  canon lock, novel delete).
- One read path: API routes and MCP tools contain no SQL.
- `character_states` has a single writer (materializer).
- All persisted critic checks can fire; findings visible in the wiki.
- Full-novel re-materialize and re-process are idempotent.
- Eval harness reports recall@k and critic precision/recall.
