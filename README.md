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

## Wiki Outputs

Character page:

```bash
uv run novel-wiki-character --novel-id <uuid> --name "Protagonist"
```

Timeline:

```bash
uv run novel-wiki-timeline --novel-id <uuid> --up-to-chapter 5
```

Thread tracker:

```bash
uv run novel-wiki-threads --novel-id <uuid> --status open
```

Relationship graph JSON:

```bash
uv run novel-wiki-relationships --novel-id <uuid> --up-to-chapter 5
```
