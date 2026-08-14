# Backend

Python backend for Continuum. Three entry points over one Postgres database:

| Command | Entry point | What it is |
|---|---|---|
| `novel-pipeline` | `pipeline.pipeline:main` | CLI: init-db, create-novel, list-novels, process-chapter |
| `novel-webapp` | `api.app:run` | FastAPI server (`/api/*`, plus the built frontend with SPA fallback) |
| `novel-mcp` | `mcp_server.server:main` | FastMCP stdio server, 13 tools for writing agents |

The design docs live at the repo root in `docs/` — `architecture.html`
(mental model, pipeline, DB design), `reference.html` (schema, API, CLI,
MCP, env vars, evals), `state-of-the-system.html` (what's wired, what's
open). This file only covers setup and the day-to-day commands.

## Layout

```
backend/
├── api/            # FastAPI: 19 routers. No SQL — routes call reads/
│   ├── app.py      #   app + router mounting + run()
│   ├── admin.py    #   novel create/delete, canon-fact mutations (SQL-only, by design)
│   ├── schemas.py  #   Pydantic response models
│   ├── jobs.py     #   in-memory job status store (does not survive restart)
│   └── routes/     #   one file per domain
├── reads/          # The read layer. All presentation SQL lives here, and
│                   # every public function takes up_to_chapter (spoiler cutoff).
│                   # Enforced by reads/tests/test_contract.py
├── pipeline/       # The write spine: analyze_chapter() in pipeline.py
│   ├── extraction/ #   13 LLM passes, intra-chunk dedup, canonicalizer, resolver
│   ├── state/      #   StateMaterializer — sole writer of character_states + edges
│   ├── critic/     #   5 deterministic continuity checks
│   ├── retrieval/  #   BM25 + dense + RRF + rerank + MMR
│   ├── db/         #   schema.sql, client.py, entity_merge.py, duplicates.py
│   └── ingestion/  #   raw chapter INSERT
├── mcp_server/     # MCP tool definitions; read tools call reads/ directly
└── evals/          # Golden fixture novel + answer key + scoring + 4 evals
```

Tests are colocated with the code they test (`api/tests/`, `reads/tests/`,
`pipeline/*/tests/`, `evals/tests/`) — there is no top-level `tests/`.

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and PostgreSQL 14+
with `pgvector`.

```bash
uv sync
createdb novel_wiki
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS vector;'
cp .env.example .env      # then fill in the values below
uv run novel-pipeline init-db
```

`init-db` reads `pipeline/db/schema.sql`, substitutes `__EMBEDDING_DIM__`
from `EMBEDDING_DIMENSIONS`, and applies it in one transaction.

> **No migration system.** Schema changes mean drop, re-`init-db`, and
> re-process chapters. That is affordable on purpose: `chapters.raw_text` is
> ground truth and every projection is rebuildable from it.

### Environment

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://localhost/novel_wiki` | Postgres connection string |
| `DEFAULT_MODEL` | `gpt-4o-mini` | LiteLLM model string for extraction |
| `LLM_TEMPERATURE` | `0.1` | Sampling temperature |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | LiteLLM embedding model |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match `VECTOR(...)` in the initialized schema |
| `CHUNK_SIZE` | `2000` | Tokens per extraction window |
| `CHUNK_OVERLAP` | `200` | Token overlap between windows |
| `CANONICALIZER_MAX_ROSTER` | `80` | Cap on canonicalizer candidates per LLM call |
| `CONTEXT_MAX_CHARACTERS` | `40` | Cap on characters injected into prompts |
| `CONTEXT_MAX_LOCATIONS` | `30` | Cap on locations injected into prompts |
| `USE_MOCK_LLM` | `false` | `true` skips all LLM calls (deterministic mock extractor) |

Provider keys are read by LiteLLM directly from the environment and depend
on the model string you choose (`GEMINI_API_KEY` for `gemini/…`,
`OPENAI_API_KEY` for `openai/…`, and so on). `.env.example` ships with the
Gemini setup.

## Running

```bash
uv run novel-webapp --host 127.0.0.1 --port 8000 --reload
uv run novel-mcp        # stdio; normally launched by the MCP client, not by hand
```

## Pipeline CLI

```bash
uv run novel-pipeline create-novel --title "My Novel" --author "Author Name"
uv run novel-pipeline list-novels

# from a file, or from stdin if --file is omitted
uv run novel-pipeline process-chapter --novel-id <uuid> --number 1 \
  --title "Chapter One" --file chapter1.txt
```

`process-chapter` also takes `--mock-llm`, `--chunk-size`, `--chunk-overlap`,
and `--replace` (required to re-process a chapter number that already exists —
it deletes that chapter's derived rows first).
It runs the full write spine: INGEST → EXTRACT (13 passes per chunk, then
intra-extraction dedup and cross-chapter canonicalization) → PERSIST (one
transaction) → MATERIALIZE → CRITIQUE.

Manual state rebuild (phases 4–5 are normally automatic, so this is for
backfills after a failed materialize):

```bash
uv run python -m pipeline.state.cli --novel-id <uuid>
```

## Tests

```bash
.venv/bin/python -m pytest              # full suite — needs Postgres
.venv/bin/python -m pytest evals/ -s    # eval harness, offline (mock LLM + real Postgres)
RUN_LLM_EVALS=1 .venv/bin/python -m pytest evals/   # + the two real-LLM evals (spends credits)
```

Run pytest from this directory with **this** `.venv`. The suite mixes
`FakeDB` unit tests with integration tests that hit a real database, and the
integration ones resolve `DATABASE_URL` from `backend/.env` — a repo-root
venv will collect them against an uninitialized database and fail.
