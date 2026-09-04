# Continuum

Continuity tracking and agent memory for novels. Chapters go in; a
queryable, spoiler-safe knowledge base comes out: characters, locations,
objects, factions, relationships, plot threads, commitments, who-knows-what,
and per-chapter continuity critiques — browsable in a React wiki and
exposed to writing agents over MCP.

Continuum **analyzes** prose; it does not write it. The intended writer is
an agent (e.g. Claude via the MCP server) that queries Continuum for
ground truth while drafting and saves finished chapters back.

## Architecture (two spines, one database)

- **Write spine** — `analyze_chapter` (`backend/pipeline/pipeline.py`):
  CRITIQUE (5 deterministic continuity checks) → INGEST → EXTRACT (13 LLM
  passes, including typed `state_deltas`) → PERSIST (one transaction,
  immutable extraction tier) → MATERIALIZE (`StateReplay` folds deltas into
  projections; sole writer of `character_states` and the bitemporal edge
  tables) → RECORD the critique against the committed chapter.
  The critique runs **first and exactly once**, for every caller: before
  extraction, so a refusal costs one claims-extraction call instead of 13
  passes per chunk and refused text never mints entities. The later phase
  only persists the report the first one produced. What a FAIL *costs* is the
  caller's `on_continuity_fail` policy: `"warn"` (the default, used by the
  CLI and the Process page, where a human is already the review step)
  records the findings and ingests anyway; `"block"` (asked for only by the
  MCP `save_chapter` tool, because an agent writes unattended) refuses the
  write and parks the draft in `draft_submissions` for human review. An
  outage — critique disabled, or no real model — refuses a blocking caller
  outright with nothing parked, since there is no verdict to record. So a
  row in `chapters` written through `save_chapter` either passed continuity
  or was human-overridden through the Review queue with the blocking
  findings recorded on it; rows from the un-blocking callers carry their
  findings without that guarantee.
- **Read layer** — `backend/reads/`: every public function takes
  `up_to_chapter` (None = whole novel) so both the wiki and MCP serve
  spoiler-safe, point-in-time views. API routes and MCP tools contain no
  SQL — enforced by contract tests.

Chapter `raw_text` is ground truth: projections can always be deleted and
rebuilt, and novels can be re-processed after schema changes.

## Docs (start here)

Open `docs/index.html` in a browser. It is the landing page and the onboarding
path — run the tests, read the docs in order, read the six files that are the
actual system — and it links out to everything below.

The reference docs, in the order that page walks you through them:

1. `docs/architecture.html` — what the system is, the mental model, the
   pipeline phase by phase, the DB design, and a directory map.
2. `docs/reference.html` — schema, API endpoints, CLI, MCP tools, env vars,
   eval harness.
3. `docs/state-of-the-system.html` — how it got here, what is wired, and the
   honest list of what's still open (currently: cross-type duplicate
   prevention, per-chapter cost accounting, embedding-dimension migration,
   in-memory job state, the write gate's word-overlap knowledge check, and
   `save_chapter`'s hardcoded `use_mock_llm=None`).

`docs/blog/` is an eleven-part narrative walkthrough of the whole system, written
from first principles — start at `docs/blog/README.md` if you want the reasoning
rather than the reference. `docs/research/` holds background research.

## Setup

Backend (Python 3.11+, Postgres with pgvector):

```bash
cd backend
uv sync
createdb novel_wiki
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS vector;'
cp .env.example .env   # set DATABASE_URL, DEFAULT_MODEL, EMBEDDING_MODEL
uv run novel-pipeline init-db
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

API server: `cd backend && uv run novel-webapp`.
MCP server (for writing agents): `cd backend && uv run novel-mcp`.

## Usage

```bash
# create a novel and process a chapter
uv run novel-pipeline create-novel --title "My Novel"
uv run novel-pipeline process-chapter --novel-id <id> --number 1 --file ch1.txt
```

Or paste chapter text into the wiki's Process page. Writing agents use the
MCP tools (`save_chapter`, `check_continuity`, `search_story`, and the
cutoff-aware read tools that take `writing_chapter`).

## Writing agents (MCP)

The data layer is exposed to writing agents as an MCP server (`novel-mcp`,
stdio). Every lookup takes a `writing_chapter` and returns only facts from
earlier chapters, so an agent drafting chapter N sees the world as of N−1.

- **Claude Code (this repo):** picked up automatically via `.mcp.json`.
- **Claude Code (anywhere):**
  `claude mcp add continuum -- uv run --directory /path/to/continuum/backend novel-mcp`
- **Claude Desktop:** add to `claude_desktop_config.json`:

  ```json
  {
    "mcpServers": {
      "continuum": {
        "command": "uv",
        "args": ["run", "--directory", "/path/to/continuum/backend", "novel-mcp"]
      }
    }
  }
  ```

Suggested agent workflow: `open_threads` + `unresolved_commitments` +
`get_character` → draft → `check_continuity` → revise → `save_chapter`.
The full tool table is in `docs/reference.html`.

`save_chapter` is gated: it refuses a draft that fails the continuity
critic and routes it to `draft_submissions` for human review instead of
ingesting it (`check_continuity` is a self-check an agent can run first,
but it isn't the enforcement point — `save_chapter` runs the same checks
itself). A refusal returns `{ingested: false, status: "pending_review",
submission_id, reason, fails, warns}`; revise against `fails` and
resubmit — a resubmission supersedes the earlier parked draft. Only a
human, via the wiki's Review page, can accept a failing draft anyway.

## Tests and evals

```bash
cd backend && .venv/bin/python -m pytest        # full suite (needs Postgres)
cd backend && .venv/bin/python -m pytest evals/ # eval harness (offline)
cd backend && RUN_LLM_EVALS=1 .venv/bin/python -m pytest evals/  # + real-LLM evals
cd frontend && npm run build                    # frontend gate
```

Use `backend/.venv` (not a repo-root venv) — the DB-integration tests
resolve their connection from `backend/.env`. Note that `backend/.env.branch`,
if present, overrides `DATABASE_URL` from `.env`; `backend/scripts/branch_db.sh
show` tells you which database you are actually on.

The eval harness (`backend/evals/`) grades the system against a
hand-written golden novel: extraction fidelity, retrieval recall@k, critic
precision/recall, and entity resolution. `RUN_LLM_EVALS=1` additionally
runs the two real-LLM evals (extraction fidelity, entity resolution) —
those spend API credits.

Entity resolution is the weakest link and is documented as such: the
canonicalizer compares candidates within one `entity_type` at a time, so
cross-type duplicates (the same thing filed as a character in one chapter
and an object in the next) are not caught at ingest. They can be found
(`GET /api/novels/{id}/entities/duplicates`) and repaired
(`POST .../entities/merge`, which reclassifies across types), but not yet
prevented — see `docs/state-of-the-system.html#entity-resolution`.

## Contributing

CI (`.github/workflows/ci.yml`) runs on every push and pull request: the
backend job stands up a `pgvector/pgvector:pg16` service, applies the schema,
and runs pytest; the frontend job runs lint, typecheck, and build. The suite
is mock-LLM end to end, so it needs no provider credentials.

Before opening a PR, run what CI runs:

```bash
cd backend  && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run typecheck && npm run build
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
