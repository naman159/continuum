# Novel Wiki Pipeline (MVP)

Backend pipeline for novel continuity tracking and wiki generation.

## Implemented Architecture

- Ingestion: chapter text -> `chapters`
- Extraction: chunking + six-pass extraction via LiteLLM (with mock fallback)
- Storage: PostgreSQL schema with `pgvector`
- Output: wiki views for character pages, timeline, threads, and relationships

## Project Structure

- `config.py`
- `pipeline.py`
- `embeddings.py`
- `db/schema.sql`
- `db/client.py`
- `ingestion/ingest.py`
- `extraction/chunker.py`
- `extraction/prompts.py`
- `extraction/extractor.py`
- `extraction/resolver.py`
- `wiki/character.py`
- `wiki/timeline.py`
- `wiki/threads.py`
- `wiki/relationships.py`

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Create database and enable extensions:

```bash
createdb novel_wiki
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

3. Configure environment (`.env`):

```bash
DATABASE_URL=postgresql://localhost/novel_wiki
DEFAULT_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
USE_MOCK_LLM=false
```

## Run

Initialize schema:

```bash
uv run novel-pipeline init-db
```

Create a novel:

```bash
uv run novel-pipeline create-novel --title "My Novel" --author "Author Name"
```

Process chapter (stdin):

```bash
uv run novel-pipeline process-chapter --novel-id <uuid> --number 1
```

Process chapter (file):

```bash
uv run novel-pipeline process-chapter --novel-id <uuid> --number 1 --file chapter1.txt
```

Run with mock extraction/embeddings:

```bash
uv run novel-pipeline process-chapter --novel-id <uuid> --number 1 --file chapter1.txt --mock-llm
```

## Wiki Web App

Local read-only browser UI over the pipeline data. Supports all four data views — characters, chapters, timeline, threads, continuity flags, and an interactive relationship graph — with a global "as of chapter N" cap slider.

### Dev (two processes)

```bash
# Terminal 1: API backend
uv run novel-webapp --port 8000

# Terminal 2: Vite frontend (proxies /api/* to port 8000)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173.

### Production (single process)

```bash
cd frontend && npm run build && cd ..
uv run novel-webapp --port 8000
```

Open http://localhost:8000. The FastAPI server serves both the API and the built frontend.

### Features

- **Novel picker** — select a novel from the home page
- **Chapter cap** — sidebar slider filters all views to "as of chapter N"
- **Characters** — list with aliases and first-appearance chapter; detail page shows every DB field (state history, relationships, events)
- **Chapters** — table of processed chapters with summaries
- **Timeline** — events grouped by chapter with involved characters resolved to names
- **Threads** — plot threads with status filter (open/progressing/closed) and linked events
- **Continuity flags** — foreshadowing, setups, callbacks with resolved/open filter
- **Relationships** — interactive vis-network graph (double-click a node to open the character); table fallback below

## Wiki lookups

The former `novel-wiki-*` commands were replaced by the MCP server — see [Writing agents (MCP)](#writing-agents-mcp).

## Writing agents (MCP)

The data layer is exposed to writing agents as an MCP server (`novel-mcp`,
stdio). Every lookup takes a `writing_chapter` and returns only facts from
earlier chapters, so an agent drafting chapter N sees the world as of N−1.
Tools: list_novels, list_chapters, search_story, get_character,
character_knowledge, relationships, open_threads, unresolved_commitments,
timeline_events, canon_facts, scene_list, check_continuity, save_chapter.

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
