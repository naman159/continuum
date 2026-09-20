# Backend

Python backend for Continuum. Three entry points over one Postgres database:

| Command | Entry point | What it is |
|---|---|---|
| `novel-pipeline` | `pipeline.cli:main` | CLI: init-db, create-novel, list-novels, process-chapter |
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
│   ├── critic/     #   4 continuity checks over model-extracted claims
│   ├── retrieval/  #   BM25 + dense + RRF + MMR
│   ├── db/         #   schema, sessions, metadata history, entity repair, duplicates
│   └── ingestion/  #   raw chapter INSERT
├── mcp_server/     # MCP tool definitions; read tools call reads/ directly
├── evals/          # Golden fixture novel + answer key + scoring + 4 evals
└── scripts/        # branch_db.sh
```

Tests are colocated with the code they test (`api/tests/`, `reads/tests/`,
`pipeline/*/tests/`, `evals/tests/`) — there is no top-level `tests/`.

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and PostgreSQL 14+
with `pgvector`.

```bash
uv sync --frozen
createdb novel_wiki
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS vector;'
cp .env.example .env      # then fill in the values below
uv run novel-pipeline init-db
```

`init-db` reads `pipeline/db/schema.sql`, substitutes `__EMBEDDING_DIM__`
from `EMBEDDING_DIMENSIONS`, and applies it in one transaction.

The consolidated schema includes explicit migrations for existing databases.
Run `init-db` with processing stopped; it preserves chapter text, migrates legacy
knowledge, records the available metadata baseline, and rebuilds projections.
It then verifies the resulting database shape. A `CREATE TABLE IF NOT EXISTS`
statement alone cannot update an existing constraint; those changes need explicit
migration SQL and the drift check below.

```bash
uv run novel-pipeline check-schema
```

applies `schema.sql` into a throwaway schema and diffs the catalogs, reporting
anything the live database is missing or carrying extra. `init-db` runs the
same check and exits if it finds drift. Constraint *names* are ignored — only
definitions are compared — so a constraint that arrived inline in one database
and via a named `ALTER` in another is not reported as a difference.

### Environment

Defaults are the values `.env.example` ships; `pipeline/config.py` holds the
same ones.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://localhost/novel_wiki` | Postgres connection string |
| `DEFAULT_MODEL` | `gemini/gemini-3.1-flash-lite` | LiteLLM model string for extraction |
| `LLM_TEMPERATURE` | `0.1` | Sampling temperature |
| `EMBEDDING_MODEL` | `gemini/gemini-embedding-2` | LiteLLM embedding model |
| `EMBEDDING_DIMENSIONS` | `768` | Substituted into `VECTOR(__EMBEDDING_DIM__)` at `init-db` time. `init-db` exits if it drifts from the live column |
| `CHUNK_SIZE` | `2000` | Tokens per extraction window |
| `CHUNK_OVERLAP` | `200` | Token overlap between windows |
| `CANONICALIZER_MAX_ROSTER` | `80` | Cap on canonicalizer candidates per LLM call |
| `CONTEXT_MAX_CHARACTERS` | `40` | Cap on characters injected into prompts |
| `CONTEXT_MAX_LOCATIONS` | `30` | Cap on locations injected into prompts |
| `USE_MOCK_LLM` | `false` | `true` skips all LLM calls (deterministic mock extractor) and uses hash embeddings |
| `CRITIC_ENABLED` | `true` | `false` skips the continuity critique during ingestion. A caller that blocks on a FAIL (the MCP `save_chapter` tool) then refuses the write rather than treating a missing verdict as a pass |
| `DB_MAX_CONNECTIONS` | `20` | Connection-pool ceiling |
| `RUN_LLM_EVALS` | unset | `1` enables the evals that call a real model |

Provider keys are read by LiteLLM directly from the environment and depend
on the model string you choose (`GEMINI_API_KEY` for `gemini/…`,
`OPENAI_API_KEY` for `openai/…`, and so on). `.env.example` ships with the
Gemini setup.

Chat and embedding calls retry transient provider failures up to three times.
Each attempt has a 60-second timeout; retries honor HTTP `Retry-After` and
Gemini's `RetryInfo` delay, with waits capped at 60 seconds. Authentication
and invalid-request errors fail immediately. If an extraction pass still fails
or returns empty/invalid JSON, processing stops before chapter persistence.
This matters on Gemini's free tier: one chapter requires more than thirteen
calls once critique and entity resolution are included, so processing can
pause at a requests-per-minute limit.

### Tokenization — three different things

"Tokens" means something different in each layer, and the numbers are not
comparable. Nothing in this system tokenizes text once and reuses it.

| Layer | Tokenizer | Where it runs |
|---|---|---|
| Chunking (`CHUNK_SIZE`) | tiktoken BPE, encoding derived from `DEFAULT_MODEL` | Locally, from the vendored vocab |
| Keyword retrieval | Postgres `to_tsvector('english')` — Snowball stemming + stopword removal, not BPE | In Postgres |
| Dense retrieval | Whatever `EMBEDDING_MODEL` uses internally; never exposed | Provider-side |
| Mock embeddings (`USE_MOCK_LLM=true`) | None. SHA-256 over raw UTF-8 bytes | Locally |

The consequence worth internalizing: **`CHUNK_SIZE=2000` is an estimate, not a
measurement.** tiktoken maps OpenAI models only, so on the shipped Gemini config
it falls back to `o200k_base` and counts in a vocabulary the serving model does
not use. That is fine for sizing a window — it is stable, local, and close
enough — but it is not the model's own count, and no `count_tokens` call is
made anywhere. If you need exact counts for cost or context-limit work, ask the
provider; don't read them off `CHUNK_SIZE`.

A tokenizer that cannot load raises rather than degrading to a whitespace
split — see `pipeline/extraction/chunker.py` for why that mattered.

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
it rebuilds that chapter and all later chapters atomically).
It runs the full write spine: CRITIQUE → EXTRACT (13 passes per chunk, then
intra-extraction dedup and cross-chapter canonicalization) → INGEST + PERSIST
(one transaction) → MATERIALIZE → RECORD the original critique. The CLI
records continuity findings and continues; the MCP save tool blocks on FAIL.

Manual state rebuild (phases 4–5 are normally automatic, so this is for
backfills after a failed materialize):

```bash
uv run python -m pipeline.state.cli --novel-id <uuid>
```

## Tests

```bash
.venv/bin/python -m pytest              # full suite — needs Postgres
.venv/bin/python -m pytest evals/ -s    # eval harness, offline (mock LLM + real Postgres)
RUN_LLM_EVALS=1 .venv/bin/python -m pytest evals/   # + paid synthetic provider evals (spends credits)
```

Run pytest from this directory with **this** `.venv`. The suite mixes
`FakeDB` unit tests with integration tests that hit a real database, and the
integration ones resolve `DATABASE_URL` from `backend/.env` — a repo-root
venv will collect them against an uninitialized database and fail.

## Persistence and migrations

Core extraction writes now live in `pipeline/extraction/persist.py`; the main
pipeline coordinates them. Human draft acceptance commits its review status and
original findings with the chapter. The unused style-fingerprint writer is retired.

Run `uv run ruff check .` alongside pytest. Ingest chapters sequentially per novel;
replacing a chapter restores the preceding metadata and re-extracts every later
chapter atomically. A failed replacement preserves the original chapters. Model
calls for the rebuilt suffix incur their normal cost. Per-novel locks reject
concurrent processing; chapters append in order, starting at 1.

Schema version 4 establishes a metadata baseline for an existing database at its
latest chapter. Earlier metadata was never recorded: re-import the original
chapters into a fresh novel for historical metadata before that baseline.
Knowledge deltas are migrated into `knows_edges` and projections rebuilt, preserving
recorded facts. `knows_edges` now feeds character snapshots, the knowledge page, and
the critic. Alias writes share the chapter transaction; enrichment failures abort
persistence, while unresolved references return visible warnings.

### 2026-09-18 cleanup

The critic has four active checks: canon assertions, knowledge, possession, and
possible commitment payoffs. Unused planner and location-claim paths were removed.
Relationship endings now preserve earlier assertions and chapter-cutoff reads.
Run `uv run novel-pipeline init-db` after updating an existing installation to
apply schema version 4's knowledge migration and metadata history.
