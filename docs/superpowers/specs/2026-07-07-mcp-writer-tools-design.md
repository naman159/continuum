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

Expose the data layer to writing agents via a single **MCP server**, built
in its own directory. The existing `cli/` package is **left untouched** —
the user will archive it separately; nothing new may import from it. Query
logic the MCP tools need is ported from the CLI functions (which already
implement the cutoff-aware composite lookups) into the MCP package, so the
MCP package is self-contained and survives the CLI's removal.

The writing agent itself lives outside Continuum (Claude Code, Claude
Desktop, or an Agent SDK app — all speak MCP). Wiring these tools into the
built-in `generate_chapter` pipeline is explicitly out of scope for now.

## Architecture

```
backend/
  mcp_server/       # NEW: self-contained MCP package
    __init__.py
    server.py       # FastMCP (official `mcp` package), stdio transport; tool defs only
    queries.py      # composite lookup functions ported from cli/ (cutoff-aware)
  cli/              # unchanged; user archives it separately
  api/              # unchanged (serves the frontend)
  pipeline/         # unchanged (ingestion, generation loop)
.mcp.json           # project-level registration for Claude Code
```

- The directory is named `mcp_server`, not `mcp`, because `package-dir`
  is the backend root and a local `mcp/` package would shadow the `mcp`
  pip package the server imports.
- `mcp_server/queries.py` holds the composite query functions ported from
  `cli/` (`build_character_page`, thread / commitment / knowledge /
  timeline / canon / scene / relationship queries), adapted only as needed
  for tool output. `mcp_server/` imports from `pipeline/` and `api/` where
  useful but **never from `cli/`**.
- `server.py` owns zero SQL; it only defines tools over `queries.py`,
  `HybridRetriever`, `ContinuityCritic`, and `process_chapter`. It connects
  to Postgres via `DBClient` directly — the FastAPI server does not need
  to be running.
- `pyproject.toml`: add one console script `novel-mcp =
  "mcp_server.server:main"`; existing scripts are left alone (they go away
  when the CLI is archived).

## Tools

All lookup tools take a `writing_chapter` parameter and return only facts
from chapters **strictly before** it (the agent writing chapter N sees the
world as of N−1). This reuses the existing `up_to_chapter` / `max_chapter`
support in the ported queries and retriever.

| Tool | Source | Notes |
|---|---|---|
| `list_novels()` | existing queries | orientation; returns ids + titles |
| `list_chapters(novel_id)` | existing queries | numbers, titles, summaries |
| `search_story(novel_id, query, writing_chapter)` | **new** query fn | wraps `HybridRetriever` with `max_chapter` cutoff |
| `get_character(novel_id, name, writing_chapter)` | ported `build_character_page` | state, goals, knowledge, relationships |
| `character_knowledge(...)` | ported from cli | what a character knows / doesn't know |
| `relationships(...)` | ported from cli | pairwise + dynamics |
| `open_threads(novel_id, writing_chapter)` | ported from cli | unresolved plot threads |
| `unresolved_commitments(novel_id, writing_chapter)` | ported from cli | planted foreshadowing awaiting payoff |
| `timeline_events(...)` | ported from cli | chronological events |
| `canon_facts(...)` | ported from cli | established world facts |
| `scene_list(...)` | ported from cli | scene breakdowns per chapter |
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

- Pytest from `backend/` using `backend/.venv` (per project convention),
  tests in `backend/mcp_server/tests/`.
- Unit tests for the query functions: `search_story` (cutoff respected),
  `get_character` cutoff, `check_continuity` (fails on a known violation,
  passes on a clean draft, mock LLM), `save_chapter` guard (existing
  chapter refused).
- One smoke test: the MCP server starts and lists the expected tool names.
- `cli/` tests (if any) are untouched.

## Docs

The docs website documents the `novel-wiki-*` CLI today. Update
`docs/reference.html`, `docs/architecture.html`, and
`docs/state-of-the-system.html` to describe the MCP server and its tools
instead (CLAUDE.md requires docs updates with every change).

## Out of scope

- Archiving/deleting `cli/` and its console scripts — the user handles this
  separately.
- Wiring the MCP query functions into the built-in `generate_chapter` loop.
- HTTP/streamable transport (mounting MCP on the FastAPI app). The stdio
  design can grow into this later without rework.
- Prompt input for the built-in generation endpoint.
- Consolidating `api/queries.py` SQL with `mcp_server/queries.py`.
