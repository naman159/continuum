# MCP Writer Tools — Design

**Date:** 2026-07-07
**Status:** Approved (brainstorming session)

## Problem

Continuum's data layer (characters, knowledge, relationships, plot threads,
commitments, timeline, canon) is the source of truth for a novel's past, but
there is no way for an external writing agent to query it interactively. The
built-in `generate_chapter` loop is a closed pipeline: it takes no user
prompt, and its retrieval is a single fixed hybrid-search call per scene —
the drafter cannot ask follow-up questions mid-draft.

Meanwhile the project has accumulated overlapping surfaces: a `novel-wiki-*`
CLI, a REST API, and the pipeline, each carrying its own query code. Adding
an agent surface must not add a fourth copy.

## Decision

Expose the data layer to writing agents via a single **MCP server**, and
**retire the CLI** as a user-facing surface. The CLI's query functions are
kept — they already implement the cutoff-aware composite lookups the agent
needs — but the argparse wrappers and console-script entry points are
deleted.

The writing agent itself lives outside Continuum (Claude Code, Claude
Desktop, or an Agent SDK app — all speak MCP). Wiring these tools into the
built-in `generate_chapter` pipeline is explicitly out of scope for now.

## Architecture

```
backend/
  toolkit/          # renamed from cli/: plain query functions, no argparse
  mcp_server.py     # FastMCP (official `mcp` package), stdio transport
  api/              # unchanged (serves the frontend)
  pipeline/         # unchanged (ingestion, generation loop)
.mcp.json           # project-level registration for Claude Code
```

- `backend/cli/` → `backend/toolkit/`. Every `main()` and argparse block is
  removed; the composite query functions (`build_character_page`, thread /
  commitment / knowledge / timeline / canon / scene / relationship queries)
  remain and keep their signatures.
- `pyproject.toml`: remove the ten `novel-wiki-*` and `novel-pipeline`
  console scripts (`novel-webapp` stays).
- `backend/mcp_server.py` decorates toolkit functions as MCP tools. It owns
  **zero SQL**; anything it needs that doesn't exist yet goes into the
  toolkit. It connects to Postgres via `DBClient` directly — the FastAPI
  server does not need to be running.

After this change each surface has exactly one consumer: pipeline →
processing/generation, API → frontend, MCP → agents.

## Tools

All lookup tools take a `writing_chapter` parameter and return only facts
from chapters **strictly before** it (the agent writing chapter N sees the
world as of N−1). This reuses the existing `up_to_chapter` / `max_chapter`
support in the toolkit queries and retriever.

| Tool | Source | Notes |
|---|---|---|
| `list_novels()` | existing queries | orientation; returns ids + titles |
| `list_chapters(novel_id)` | existing queries | numbers, titles, summaries |
| `search_story(novel_id, query, writing_chapter)` | **new** toolkit fn | wraps `HybridRetriever` with `max_chapter` cutoff |
| `get_character(novel_id, name, writing_chapter)` | `build_character_page` | state, goals, knowledge, relationships |
| `character_knowledge(...)` | toolkit | what a character knows / doesn't know |
| `relationships(...)` | toolkit | pairwise + dynamics |
| `open_threads(novel_id, writing_chapter)` | toolkit | unresolved plot threads |
| `unresolved_commitments(novel_id, writing_chapter)` | toolkit | planted foreshadowing awaiting payoff |
| `timeline_events(...)` | toolkit | chronological events |
| `canon_facts(...)` | toolkit | established world facts |
| `scene_list(...)` | toolkit | scene breakdowns per chapter |
| `check_continuity(novel_id, chapter_number, draft_text)` | **new** glue | claim extraction + `ContinuityCritic` on a draft; returns pass/fail + specific violations |
| `save_chapter(novel_id, chapter_number, text, title)` | `process_chapter` | `source="generated"`, `replace=False`; refuses to overwrite existing chapters (same guard as the API) |

## Registration

- Project-level `.mcp.json` (committed) so Claude Code sessions in this repo
  get the server automatically, launched via `uv run` against `backend/`.
- README snippet documenting `claude mcp add continuum ...` and Claude
  Desktop config for use outside the repo.

## Error handling

Tools never raise to the transport. Failures return structured, actionable
strings the agent can self-correct from, e.g. `Character 'Mara' not found;
closest names: Marla, Maren`, `Novel id X does not exist`, `Chapter 12
already exists; save_chapter does not overwrite`.

## Testing

- Pytest from `backend/` using `backend/.venv` (per project convention).
- Unit tests for the new toolkit functions: `search_story` (cutoff
  respected), `check_continuity` (fails on a known violation, passes on a
  clean draft, mock LLM), `save_chapter` guard (existing chapter refused).
- Existing toolkit behavior keeps whatever tests it has; the rename must not
  change function behavior.
- One smoke test: the MCP server starts and lists the expected tool names.

## Docs

The docs website documents the `novel-wiki-*` CLI today. Update
`docs/reference.html`, `docs/architecture.html`, and
`docs/state-of-the-system.html` to describe the MCP server and its tools
instead (CLAUDE.md requires docs updates with every change).

## Out of scope

- Wiring toolkit tools into the built-in `generate_chapter` loop.
- HTTP/streamable transport (mounting MCP on the FastAPI app). The stdio
  toolkit design can grow into this later without rework.
- Prompt input for the built-in generation endpoint.
- Consolidating `api/queries.py` SQL with the toolkit.
