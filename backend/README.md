# Backend

Python backend for Continuum — a novel wiki generator. It consists of two parts:

- **Pipeline** — CLI tool that processes chapter text through an LLM extraction pipeline and stores the results in PostgreSQL.
- **API** — FastAPI server that serves extracted data to the frontend.

## Requirements

- Python 3.11
- [uv](https://docs.astral.sh/uv/) (package manager)
- PostgreSQL 14+ with the `pgvector` extension

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (default: `postgresql://localhost/novel_wiki`) |
| `DEFAULT_MODEL` | LiteLLM model string for extraction (e.g. `gemini/gemini-2.5-flash`) |
| `LLM_TEMPERATURE` | LLM sampling temperature (default: `0.1`) |
| `EMBEDDING_MODEL` | Model for vector embeddings (e.g. `gemini/gemini-embedding-2-preview`) |
| `EMBEDDING_DIMENSIONS` | Vector dimensions — must match `VECTOR(...)` in schema (default: `768`) |
| `GEMINI_API_KEY` | API key for Gemini models |
| `CHUNK_SIZE` | Characters per chunk for extraction (default: `2000`) |
| `CHUNK_OVERLAP` | Overlap between chunks (default: `200`) |
| `USE_MOCK_LLM` | Set to `true` to skip LLM calls and use deterministic mock output |

### 3. Create the database

```bash
createdb novel_wiki
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
psql -d novel_wiki -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

### 4. Initialize the schema

```bash
uv run novel-pipeline init-db
```

This reads `pipeline/db/schema.sql`, substitutes `EMBEDDING_DIMENSIONS`, and applies the schema in one transaction.

> **Note:** There is no migration system. To apply schema changes, drop and recreate the database, then re-run `init-db` and re-ingest your chapters.

## Running the API server

```bash
uv run novel-webapp --host 127.0.0.1 --port 8000 --reload
```

The server listens on `http://localhost:8000`. It serves the API under `/api/*` and, if the frontend is built, serves static files from `../frontend/dist/` with an SPA fallback.

## Pipeline CLI

All pipeline commands are available via `uv run novel-pipeline`.

### Create a novel

```bash
uv run novel-pipeline create-novel --title "My Novel" --author "Author Name"
```

Prints the novel UUID — save this for subsequent commands.

### Process a chapter

```bash
# From a file
uv run novel-pipeline process-chapter \
  --novel-id <uuid> \
  --number 1 \
  --title "Chapter One" \
  --file chapter1.txt

# From stdin
cat chapter1.txt | uv run novel-pipeline process-chapter \
  --novel-id <uuid> \
  --number 1
```

Options:

| Flag | Description |
|---|---|
| `--novel-id` | UUID of the novel (required) |
| `--number` | Chapter number (required) |
| `--title` | Chapter title (optional) |
| `--file` | Path to chapter text file (reads stdin if omitted) |
| `--mock-llm` | Use deterministic mock extractor instead of real LLM |
| `--chunk-size` | Override `CHUNK_SIZE` from env |
| `--chunk-overlap` | Override `CHUNK_OVERLAP` from env |

Processing runs six LLM passes: characters, locations, factions, objects, events, and plot threads — then canonicalizes entity names, resolves duplicates, and generates embeddings.

### Initialize the database

```bash
uv run novel-pipeline init-db [--schema path/to/schema.sql]
```

## Tests

Tests use an in-memory `FakeDB` and do not require a running database or LLM API.

```bash
# All tests
uv run pytest

# Verbose
uv run pytest -v

# Specific file
uv run pytest tests/api/test_characters.py
```

Set `USE_MOCK_LLM=true` in `.env` when running integration tests that exercise the full pipeline.

## Project structure

```
backend/
├── api/                   # FastAPI application
│   ├── app.py             # Server entry point
│   ├── queries.py         # Database queries
│   ├── schemas.py         # Pydantic response models
│   ├── jobs.py            # Async job queue
│   └── routes/            # Route handlers
├── pipeline/              # Processing pipeline
│   ├── pipeline.py        # CLI entry point
│   ├── config.py          # Config from environment
│   ├── embeddings.py      # Embedding generation
│   ├── db/
│   │   ├── client.py      # Connection pooling
│   │   └── schema.sql     # Database schema
│   ├── ingestion/         # Chapter ingestion
│   ├── extraction/        # LLM extraction passes
│   └── wiki/              # Wiki page generators
└── tests/                 # pytest test suite
```

## CLI entry points

Defined in `pyproject.toml`:

| Command | Entry point |
|---|---|
| `novel-webapp` | `api.app:run` |
| `novel-pipeline` | `pipeline.pipeline:main` |
| `novel-wiki-character` | `pipeline.wiki.character:main` |
| `novel-wiki-timeline` | `pipeline.wiki.timeline:main` |
| `novel-wiki-threads` | `pipeline.wiki.threads:main` |
| `novel-wiki-relationships` | `pipeline.wiki.relationships:main` |
