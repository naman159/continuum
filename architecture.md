# Continuum - Architecture

Novel knowledge pipeline and wiki generator. Processes chapter text through LLM-powered extraction to build a structured knowledge graph in PostgreSQL, then exposes wiki-style query views.

## High-Level Data Flow

```
Chapter text (file/stdin)
    |
    v
[Ingestion] -- upsert into `chapters` table
    |
    v
[Chunking] -- sliding window tokenizer (tiktoken/whitespace fallback)
    |
    v
[Extraction] -- 6-pass LLM extraction per chunk (or mock extractor)
    |   Passes: chapter_summary, new_entities, entity_deltas,
    |           events, thread_updates, continuity_flags
    |
    v
[Entity Resolution] -- fuzzy-match + create entities (characters, locations, factions, objects)
    |
    v
[Persistence] -- character_states, events, relationships, plot_threads, continuity_flags
    |
    v
[Embeddings] -- embed chapter summaries + event descriptions (pgvector)
    |
    v
[Wiki Views] -- character pages, timeline, thread tracker, relationship graph
```

## Module Map

### Entry Points

| Module | Purpose |
|--------|---------|
| `main.py` | Thin wrapper: imports and calls `pipeline.main()` |
| `pipeline.py` | CLI entry point (`novel-pipeline`). Subcommands: `init-db`, `create-novel`, `list-novels`, `process-chapter`. Orchestrates the full chapter processing flow. |

### Config

| Module | Purpose |
|--------|---------|
| `config.py` | Loads `.env`, exposes frozen `Settings` dataclass and `LLM_CONFIG` dict. Settings: `database_url`, `default_model`, `embedding_model`, `embedding_dimensions`, `llm_temperature`, `chunk_size`, `chunk_overlap`, `use_mock_llm`. |

### Database (`db/`)

| Module | Purpose |
|--------|---------|
| `db/schema.sql` | PostgreSQL DDL. Extensions: `pgcrypto` (UUIDs), `vector` (pgvector). Tables: `novels`, `chapters`, `characters`, `locations`, `factions`, `objects`, `character_states`, `events`, `relationships`, `plot_threads`, `thread_events`, `continuity_flags`. IVFFlat indexes on embedding columns. |
| `db/client.py` | `DBClient` wrapping `psycopg_pool.ConnectionPool`. Methods: `execute`, `fetchone`, `fetchall`, `fetchval`, `execute_many`, `cursor`, `transaction`, `connection`. Each fetch/execute call borrows a connection from the pool and operates in its own transaction. |

### Ingestion (`ingestion/`)

| Module | Purpose |
|--------|---------|
| `ingestion/ingest.py` | `ingest_chapter()` -- upserts chapter text into `chapters` table. On conflict (same novel + chapter number), replaces text and resets `processed_at`. |

### Extraction (`extraction/`)

| Module | Purpose |
|--------|---------|
| `extraction/chunker.py` | `sliding_window_chunks()` -- tokenizes text via tiktoken (falls back to whitespace split) and produces overlapping chunks. `tokenize()` / `detokenize()` helpers. |
| `extraction/prompts.py` | Prompt templates for the 6 extraction passes. `PASS_ORDER` defines execution order. `PASS_SCHEMAS` defines expected JSON output per pass. Builds system + user prompts with story context. |
| `extraction/extractor.py` | `ChapterExtractor` -- runs 6 LLM passes per chunk via LiteLLM, normalizes and merges results across chunks. Mock extractor uses regex name detection + sentence splitting for deterministic testing. `merge_extractions()` deduplicates entities, events, threads, and flags. |
| `extraction/resolver.py` | `EntityResolver` -- resolves entity names to database IDs. Lookup order: exact name match -> alias match (all entity types) -> word-boundary partial-name match (characters only) -> create new entity. In-memory cache avoids redundant queries within a session. |

### Embeddings

| Module | Purpose |
|--------|---------|
| `embeddings.py` | `EmbeddingService` -- calls LiteLLM embedding API or falls back to deterministic SHA-256 hash embeddings. `embed_chapter_and_events()` writes vector embeddings to `chapters.embedding` and `events.embedding` columns. |

### Wiki Views (`wiki/`)

| Module | Purpose |
|--------|---------|
| `wiki/character.py` | `build_character_page()` -- assembles identity, current state, state history, events, and relationships for a character. Supports spoiler cap (`--up-to-chapter`). CLI: `novel-wiki-character`. |
| `wiki/timeline.py` | `build_timeline()` -- queries events with optional filters: chapter cap, character, location, event type, impact level. CLI: `novel-wiki-timeline`. |
| `wiki/threads.py` | `build_thread_tracker()` -- lists plot threads with linked events and unresolved continuity flags. Filterable by status and thread type. CLI: `novel-wiki-threads`. |
| `wiki/relationships.py` | `build_relationship_graph()` -- produces a node+edge graph of all entities and their relationships. Nodes: characters, locations, factions, objects. CLI: `novel-wiki-relationships`. |

## Database Schema (ERD summary)

```
novels 1--* chapters
novels 1--* characters
novels 1--* locations
novels 1--* factions
novels 1--* objects
novels 1--* plot_threads

chapters 1--* events
chapters 1--* character_states
chapters 1--* continuity_flags

characters 1--* character_states
locations  ?--* character_states (optional FK)

plot_threads *--* events (via thread_events, with impact column)

relationships (polymorphic: entity_a/b_id + entity_a/b_type)
    -> references characters, locations, factions, objects by UUID + type tag
```

Key design choices:
- UUIDs everywhere (via `pgcrypto.gen_random_uuid()`)
- `pgvector` VECTOR(1536) columns on `chapters` and `events` for semantic search
- Array columns (`involved_characters UUID[]`, etc.) on `events` for denormalized lookups
- JSONB `relationships` column on `character_states` for flexible state snapshots
- UNIQUE constraints: `(novel_id, number)` on chapters, `(novel_id, name)` on all entity tables, `(novel_id, title)` on plot_threads

## Extraction Passes

Each chunk goes through 6 sequential LLM calls:

1. **chapter_summary** -- produces a <= 300 token summary
2. **new_entities** -- identifies new characters, locations, factions, objects
3. **entity_deltas** -- tracks state changes: location, emotions, goals, knowledge, relationships, physical state
4. **events** -- extracts narrative events with type/impact and involved entities
5. **thread_updates** -- tracks plot thread creation, progression, and closure
6. **continuity_flags** -- detects foreshadowing, planted details, setups, callbacks

Results from multiple chunks are merged via `merge_extractions()` which deduplicates by name/description.

## Dependencies

- **psycopg[binary,pool]** -- PostgreSQL driver + connection pooling
- **litellm** -- LLM abstraction (supports OpenAI, Anthropic, etc.)
- **tiktoken** -- tokenizer for chunk sizing
- **python-dotenv** -- environment config

## CLI Commands

| Command | Entry point |
|---------|-------------|
| `novel-pipeline init-db` | `pipeline:main` |
| `novel-pipeline create-novel` | `pipeline:main` |
| `novel-pipeline list-novels` | `pipeline:main` |
| `novel-pipeline process-chapter` | `pipeline:main` |
| `novel-wiki-character` | `wiki.character:main` |
| `novel-wiki-timeline` | `wiki.timeline:main` |
| `novel-wiki-threads` | `wiki.threads:main` |
| `novel-wiki-relationships` | `wiki.relationships:main` |

## Configuration

Via `.env` or environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://localhost/novel_wiki` | PostgreSQL connection string |
| `DEFAULT_MODEL` | `gpt-4o-mini` | LLM model for extraction |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `EMBEDDING_DIMENSIONS` | `1536` | Embedding vector size |
| `LLM_TEMPERATURE` | `0.1` | LLM temperature |
| `CHUNK_SIZE` | `2000` | Tokens per chunk |
| `CHUNK_OVERLAP` | `200` | Overlap tokens between chunks |
| `USE_MOCK_LLM` | `false` | Use deterministic mock extraction |
