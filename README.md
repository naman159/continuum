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
  INGEST → EXTRACT (13 LLM passes, including typed `state_deltas`) →
  PERSIST (one transaction, immutable extraction tier) → MATERIALIZE
  (`StateReplay` folds deltas into projections; sole writer of
  `character_states` and the bitemporal edge tables) → CRITIQUE
  (5 deterministic continuity checks, persisted per chapter).
- **Read layer** — `backend/reads/`: every public function takes
  `up_to_chapter` (None = whole novel) so both the wiki and MCP serve
  spoiler-safe, point-in-time views. API routes and MCP tools contain no
  SQL — enforced by contract tests.

Chapter `raw_text` is ground truth: projections can always be deleted and
rebuilt, and novels can be re-processed after schema changes.

See `docs/architecture.html` for the full design and
`docs/reference.html` for schema/API/CLI/MCP reference.

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

## Tests and evals

```bash
cd backend && .venv/bin/python -m pytest        # full suite (needs Postgres)
cd backend && .venv/bin/python -m pytest evals/ # eval harness (offline)
cd backend && RUN_LLM_EVALS=1 .venv/bin/python -m pytest evals/  # + real-LLM extraction fidelity
cd frontend && npm run build                    # frontend gate
```

The eval harness (`backend/evals/`) grades the system against a
hand-written golden novel: extraction fidelity, retrieval recall@k, and
critic precision/recall.
