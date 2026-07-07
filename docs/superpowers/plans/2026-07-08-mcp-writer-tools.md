# MCP Writer Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `backend/cli/` with `backend/mcp_server/` — a FastMCP stdio server exposing ~13 cutoff-aware lookup/critique/save tools so external writing agents can query the novel data layer.

**Architecture:** `mcp_server/queries.py` holds composite query functions (moved from `cli/`, plus new `search_story` / `check_continuity` / `save_chapter` glue). `mcp_server/server.py` holds only FastMCP tool definitions over those functions and `api.queries` — zero SQL. `cli/` is deleted in the same change. Spec: `docs/superpowers/specs/2026-07-07-mcp-writer-tools-design.md`.

**Tech Stack:** Python 3.11+, FastMCP from the official `mcp` package (stdio transport), psycopg via existing `DBClient`, pytest.

## Global Constraints

- Run all tests from `backend/` using `backend/.venv` (e.g. `.venv/bin/pytest`). Do NOT use the repo-root `.venv` — it collects DB-integration tests against an uninitialized DB.
- Nothing may import from `cli/` (it is deleted in Task 4).
- `mcp_server/server.py` owns zero SQL. All SQL lives in `mcp_server/queries.py` or `api/queries.py`.
- Cutoff semantics: MCP tools take `writing_chapter`; internally they pass the **inclusive** cap `up_to_chapter = writing_chapter - 1` (agent writing chapter N sees only chapters ≤ N−1).
- Package directory is `mcp_server`, never `mcp` (a local `mcp/` would shadow the `mcp` pip package).
- New dependency: `mcp>=1.2.0`. Existing deps and `novel-pipeline` / `novel-webapp` scripts stay.
- `save_chapter` ingests with `source="generated"`, `replace=False` — never overwrites.
- CLAUDE.md requires the docs website to be updated with the change (Task 6).

---

### Task 1: `mcp_server/queries.py` — moved lookup functions

**Files:**
- Create: `backend/mcp_server/__init__.py` (empty)
- Create: `backend/mcp_server/queries.py`
- Create: `backend/mcp_server/tests/__init__.py` (empty)
- Test: `backend/mcp_server/tests/test_queries.py`

**Interfaces:**
- Consumes: `pipeline.db.client.DBClient` (context manager; `fetchone/fetchall/fetchval(query, params, *, dict_rows=False)`), source functions in `backend/cli/` (copied verbatim, cli left in place until Task 4).
- Produces (used by Task 3):
  - `build_character_page(novel_id: str, name: str, up_to_chapter: int | None = None) -> dict[str, Any]`
  - `build_relationship_graph(novel_id: str, *, up_to_chapter: int | None = None) -> dict[str, Any]`
  - `list_timeline(novel_id: str) -> list[dict[str, Any]]`
  - `list_open_threads(novel_id: str, up_to_chapter: int, *, db: DBClient | None = None) -> list[dict[str, Any]]`
  - Test helper `FakeDB` in `tests/test_queries.py`.

- [ ] **Step 1: Scaffold package and write the failing tests**

Create empty `backend/mcp_server/__init__.py` and `backend/mcp_server/tests/__init__.py`.

Write `backend/mcp_server/tests/test_queries.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from mcp_server import queries


class FakeDB:
    """Records every call; returns canned results in order (empty when exhausted)."""

    def __init__(self, fetchall_results=None, fetchone_results=None, fetchval_result=None):
        self.calls: list[tuple[str, str, Any]] = []
        self._fetchall = list(fetchall_results or [])
        self._fetchone = list(fetchone_results or [])
        self._fetchval = fetchval_result

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append(("fetchall", query, params))
        return self._fetchall.pop(0) if self._fetchall else []

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append(("fetchone", query, params))
        return self._fetchone.pop(0) if self._fetchone else None

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append(("fetchval", query, params))
        return self._fetchval

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_build_character_page_not_found_suggests_close_names(monkeypatch):
    fake = FakeDB(
        fetchone_results=[None],
        fetchall_results=[[{"name": "Marla"}, {"name": "Jake"}]],
    )
    monkeypatch.setattr(queries, "DBClient", lambda: fake)
    with pytest.raises(ValueError) as exc:
        queries.build_character_page("novel-1", "Mara")
    assert "Marla" in str(exc.value)
    assert "closest names" in str(exc.value)


def test_list_open_threads_applies_cutoff_to_threads_and_events():
    thread_row = {
        "id": "t-1", "title": "The letter", "description": None, "status": "open",
        "thread_type": "mystery", "opened_chapter": 2, "closed_chapter": None,
    }
    fake = FakeDB(fetchall_results=[[thread_row], []])
    result = queries.list_open_threads("novel-1", 5, db=fake)

    assert result[0]["title"] == "The letter"
    thread_call = fake.calls[0]
    assert thread_call[2] == ("novel-1", 5, 5)          # opened<=5 AND (closed IS NULL OR closed>5)
    events_call = fake.calls[1]
    assert events_call[2] == ("t-1", 5)                  # events capped at chapter 5


def test_list_open_threads_owned_db_is_closed(monkeypatch):
    closed = []
    fake = FakeDB(fetchall_results=[[]])
    fake.close = lambda: closed.append(True)
    monkeypatch.setattr(queries, "DBClient", lambda: fake)
    queries.list_open_threads("novel-1", 3)
    assert closed == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest mcp_server/tests/test_queries.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'mcp_server.queries'` (or `ImportError`).

- [ ] **Step 3: Write `backend/mcp_server/queries.py` (moved functions)**

Create the file with this skeleton, then copy the function bodies verbatim from `cli/` as instructed below:

```python
"""Composite, cutoff-aware lookups for the MCP writer tools.

Query functions moved from the deleted cli/ package plus MCP-specific glue.
`up_to_chapter` is always an INCLUSIVE cap; server.py converts the agent-facing
`writing_chapter` to `writing_chapter - 1` before calling in here.
"""

from __future__ import annotations

import difflib
from typing import Any

from pipeline.db.client import DBClient
```

1. Copy `build_character_page` from `backend/cli/character.py:10-144` **verbatim**, with one change — replace the two lines:

```python
        if character is None:
            raise ValueError(f"Character not found: {name}")
```

with:

```python
        if character is None:
            rows = db.fetchall(
                "SELECT name FROM characters WHERE novel_id = %s",
                (novel_id,),
                dict_rows=True,
            )
            close = difflib.get_close_matches(
                name, [r["name"] for r in rows], n=3, cutoff=0.5
            )
            hint = f"; closest names: {', '.join(close)}" if close else ""
            raise ValueError(f"Character not found: {name}{hint}")
```

2. Copy `build_relationship_graph` from `backend/cli/relationships.py:10-67` verbatim.
3. Copy `list_timeline` from `backend/cli/timeline.py:10-42` verbatim.
4. Add `list_open_threads` (derived from `cli/threads.py`'s `build_thread_tracker`, but filtered to threads still open as of the cap, with capped events, and without continuity flags):

```python
def list_open_threads(
    novel_id: str, up_to_chapter: int, *, db: DBClient | None = None
) -> list[dict[str, Any]]:
    """Plot threads opened by `up_to_chapter` and not yet closed at that point."""
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        threads = client.fetchall(
            """
            SELECT pt.id, pt.title, pt.description, pt.status, pt.thread_type,
                   pt.opened_chapter, pt.closed_chapter
            FROM plot_threads pt
            WHERE pt.novel_id = %s
              AND (pt.opened_chapter IS NULL OR pt.opened_chapter <= %s)
              AND (pt.closed_chapter IS NULL OR pt.closed_chapter > %s)
            ORDER BY pt.opened_chapter NULLS LAST, pt.title
            """,
            (novel_id, up_to_chapter, up_to_chapter),
            dict_rows=True,
        )
        payload: list[dict[str, Any]] = []
        for thread in threads:
            events = client.fetchall(
                """
                SELECT te.impact, e.description, e.event_type, e.impact_level,
                       ch.number AS chapter_number
                FROM thread_events te
                JOIN events e ON e.id = te.event_id
                JOIN chapters ch ON ch.id = e.chapter_id
                WHERE te.thread_id = %s AND ch.number <= %s
                ORDER BY ch.number ASC, e.created_at ASC
                """,
                (thread["id"], up_to_chapter),
                dict_rows=True,
            )
            payload.append({**dict(thread), "events": [dict(e) for e in events]})
        return payload
    finally:
        if owned:
            client.close()
```

Do NOT copy any `main()` or argparse code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest mcp_server/tests/test_queries.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_server
git commit -m "feat: mcp_server package with cutoff-aware lookup queries moved from cli"
```

---

### Task 2: `search_story`, `check_continuity`, `save_chapter` glue

**Files:**
- Modify: `backend/mcp_server/queries.py` (append)
- Test: `backend/mcp_server/tests/test_queries.py` (append)

**Interfaces:**
- Consumes: `HybridRetriever(db, embedding_service).retrieve(RetrievalQuery, use_rerank=False) -> RetrievalBundle`; `RetrievalQuery(text, novel_id, max_chapter, k)`; `EmbeddingService(use_mock=...)`; `extract_draft_claims(text, *, use_mock=None) -> dict[str, list]`; `build_draft_chapter(db, *, novel_id, chapter_number, text, raw_claims, planned_thread_ids, planned_commitment_ids) -> DraftChapter`; `ContinuityCritic(db).critique(draft) -> CritiqueReport` (`.passed`, `.fails`, `.warns`; `Finding` has `.check/.message/.quote/.suggested_fix`); `process_chapter(*, novel_id, chapter_number, raw_text, chapter_title, use_mock_llm, chunk_size, chunk_overlap, db, replace, source, generation_meta) -> dict` (raises on duplicate chapter); `pipeline.config.settings` (`.use_mock_llm`, `.chunk_size`, `.chunk_overlap`).
- Produces (used by Task 3):
  - `search_story(novel_id: str, query_text: str, writing_chapter: int, *, k: int = 8, retriever=None) -> dict[str, Any]`
  - `check_continuity(novel_id: str, chapter_number: int, draft_text: str, *, db=None, use_mock: bool | None = None) -> dict[str, Any]`
  - `save_chapter(novel_id: str, chapter_number: int, text: str, title: str | None = None, *, db=None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests (append to `test_queries.py`)**

```python
class FakeRetriever:
    def __init__(self, results=None):
        self.seen_query = None
        self._results = results or []

    def retrieve(self, query, use_rerank=False):
        from pipeline.retrieval.types import RetrievalBundle

        self.seen_query = query
        return RetrievalBundle(query=query, results=self._results)


def test_search_story_caps_max_chapter_and_serializes():
    from pipeline.retrieval.types import RetrievalResult

    retriever = FakeRetriever(
        results=[
            RetrievalResult(
                item_id="i1", kind="chunk", score=0.9,
                snippet="Jake read the letter.", chapter_number=3,
            )
        ]
    )
    out = queries.search_story("novel-1", "the letter", 12, retriever=retriever)
    assert retriever.seen_query.max_chapter == 11
    assert retriever.seen_query.novel_id == "novel-1"
    assert out["results"][0]["snippet"] == "Jake read the letter."
    assert out["results"][0]["chapter_number"] == 3


def test_check_continuity_serializes_critic_report(monkeypatch):
    from pipeline.critic.types import CritiqueReport, Finding, Severity

    report = CritiqueReport(novel_id="novel-1", chapter_number=4)
    report.findings.append(
        Finding(check="knowledge_state", severity=Severity.FAIL,
                message="Mara cannot know about the sword yet", quote="the sword")
    )

    class StubCritic:
        def __init__(self, db):
            pass

        def critique(self, draft):
            return report

    monkeypatch.setattr(queries, "extract_draft_claims", lambda text, use_mock=None: {})
    monkeypatch.setattr(queries, "build_draft_chapter", lambda db, **kw: object())
    monkeypatch.setattr(queries, "ContinuityCritic", StubCritic)

    out = queries.check_continuity("novel-1", 4, "draft text", db=FakeDB(), use_mock=True)
    assert out["passed"] is False
    assert out["fails"][0]["check"] == "knowledge_state"
    assert out["fails"][0]["quote"] == "the sword"
    assert out["warns"] == []


def test_save_chapter_reports_duplicate_as_error(monkeypatch):
    def boom(**kwargs):
        raise ValueError("chapter 3 already ingested for this novel")

    monkeypatch.setattr(queries, "process_chapter", boom)
    out = queries.save_chapter("novel-1", 3, "some prose", db=FakeDB())
    assert "error" in out
    assert "already ingested" in out["error"]


def test_save_chapter_success_and_flags(monkeypatch):
    seen = {}

    def fake_process(**kwargs):
        seen.update(kwargs)
        return {"chapter_id": "abc-123"}

    monkeypatch.setattr(queries, "process_chapter", fake_process)
    out = queries.save_chapter("novel-1", 9, "some prose", title="The Gate", db=FakeDB())
    assert out == {"ingested": True, "chapter_id": "abc-123"}
    assert seen["source"] == "generated"
    assert seen["replace"] is False
    assert seen["chapter_title"] == "The Gate"


def test_save_chapter_rejects_empty_text():
    out = queries.save_chapter("novel-1", 9, "   ")
    assert "error" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest mcp_server/tests/test_queries.py -v`
Expected: new tests FAIL with `AttributeError: module 'mcp_server.queries' has no attribute 'search_story'` (etc.); Task 1 tests still pass.

- [ ] **Step 3: Append implementations to `queries.py`**

Add imports at the top of the file:

```python
from pipeline.config import settings
from pipeline.critic.runner import ContinuityCritic
from pipeline.embeddings import EmbeddingService
from pipeline.generation.draft_claims import build_draft_chapter, extract_draft_claims
from pipeline.pipeline import process_chapter
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery
```

Append:

```python
def search_story(
    novel_id: str,
    query_text: str,
    writing_chapter: int,
    *,
    k: int = 8,
    retriever: HybridRetriever | None = None,
) -> dict[str, Any]:
    """Hybrid semantic+keyword search over chapters <= writing_chapter - 1."""
    owned_db: DBClient | None = None
    if retriever is None:
        owned_db = DBClient()
        retriever = HybridRetriever(
            owned_db, EmbeddingService(use_mock=settings.use_mock_llm)
        )
    try:
        bundle = retriever.retrieve(
            RetrievalQuery(
                text=query_text,
                novel_id=novel_id,
                max_chapter=writing_chapter - 1,
                k=k,
            ),
            use_rerank=False,
        )
        return {
            "results": [
                {
                    "kind": str(r.kind),
                    "chapter_number": r.chapter_number,
                    "score": r.score,
                    "snippet": r.snippet,
                }
                for r in bundle.results
            ]
        }
    finally:
        if owned_db is not None:
            owned_db.close()


def _finding_dict(finding: Any) -> dict[str, Any]:
    return {
        "check": finding.check,
        "message": finding.message,
        "quote": finding.quote,
        "suggested_fix": finding.suggested_fix,
    }


def check_continuity(
    novel_id: str,
    chapter_number: int,
    draft_text: str,
    *,
    db: DBClient | None = None,
    use_mock: bool | None = None,
) -> dict[str, Any]:
    """Run claim extraction + the ContinuityCritic on a draft (not saved)."""
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        raw_claims = extract_draft_claims(draft_text, use_mock=use_mock)
        draft = build_draft_chapter(
            client,
            novel_id=novel_id,
            chapter_number=chapter_number,
            text=draft_text,
            raw_claims=raw_claims,
            planned_thread_ids=[],
            planned_commitment_ids=[],
        )
        report = ContinuityCritic(client).critique(draft)
        return {
            "passed": report.passed,
            "fails": [_finding_dict(f) for f in report.fails],
            "warns": [_finding_dict(f) for f in report.warns],
        }
    finally:
        if owned:
            client.close()


def save_chapter(
    novel_id: str,
    chapter_number: int,
    text: str,
    title: str | None = None,
    *,
    db: DBClient | None = None,
) -> dict[str, Any]:
    """Ingest a finished draft as source='generated'. Never overwrites."""
    if not text or not text.strip():
        return {"error": "text must not be empty"}
    try:
        outcome = process_chapter(
            novel_id=novel_id,
            chapter_number=chapter_number,
            raw_text=text,
            chapter_title=title,
            use_mock_llm=None,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            db=db,
            replace=False,
            source="generated",
            generation_meta={"via": "mcp"},
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"ingested": True, "chapter_id": str(outcome.get("chapter_id"))}
```

Note: `build_draft_chapter` is called with empty `planned_thread_ids` / `planned_commitment_ids` — the external agent has no pre-registered plan, so plan-adherence checks have nothing to assert against and the factual checks still run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest mcp_server/tests/test_queries.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_server
git commit -m "feat: search_story, check_continuity, save_chapter glue for MCP tools"
```

---

### Task 3: FastMCP server, `mcp` dependency, `novel-mcp` script

**Files:**
- Create: `backend/mcp_server/server.py`
- Modify: `backend/pyproject.toml` (add dependency + script)
- Test: `backend/mcp_server/tests/test_server.py`

**Interfaces:**
- Consumes: everything Tasks 1–2 produce; `api.queries` functions: `list_novels()`, `list_chapters(novel_id: UUID, cap: int | None)`, `list_knows_edges(novel_id: UUID, cap, character_id: UUID | None)`, `list_location_edges(novel_id: UUID, cap, only_active: bool)`, `list_possession_edges(novel_id: UUID, cap, only_active: bool)`, `list_commitments(novel_id: UUID, up_to_chapter, status: str)`, `list_canon_facts(novel_id: UUID, locked_only: bool)`, `list_scenes(novel_id: UUID, up_to_chapter, chapter)`.
- Produces: module-level `mcp` (FastMCP instance named `"continuum"`), `main()` entry point, console script `novel-mcp`. Exactly these 13 tools: `list_novels, list_chapters, search_story, get_character, character_knowledge, relationships, open_threads, unresolved_commitments, timeline_events, canon_facts, scene_list, check_continuity, save_chapter`.

- [ ] **Step 1: Add the `mcp` dependency**

Run `cd backend && uv add "mcp>=1.2.0"` — this appends the dependency to `pyproject.toml`, updates `uv.lock`, and syncs `.venv`. (If `uv` is unavailable: add `"mcp>=1.2.0",` to `dependencies` in `backend/pyproject.toml` by hand, then `.venv/bin/pip install "mcp>=1.2.0"`.)

Then add to `[project.scripts]` in `backend/pyproject.toml` by hand:

```toml
novel-mcp = "mcp_server.server:main"
```

and reinstall entry points: `cd backend && uv sync` (or `.venv/bin/pip install -e . --no-deps`).
Verify: `cd backend && .venv/bin/python -c "from mcp.server.fastmcp import FastMCP; print('ok')"` → prints `ok`.

- [ ] **Step 2: Write the failing tests**

Write `backend/mcp_server/tests/test_server.py`:

```python
from __future__ import annotations

import asyncio

from mcp_server import server


EXPECTED_TOOLS = {
    "list_novels", "list_chapters", "search_story", "get_character",
    "character_knowledge", "relationships", "open_threads",
    "unresolved_commitments", "timeline_events", "canon_facts",
    "scene_list", "check_continuity", "save_chapter",
}


def test_server_exposes_exactly_the_expected_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_has_a_docstring_description():
    tools = asyncio.run(server.mcp.list_tools())
    for tool in tools:
        assert tool.description, f"tool {tool.name} has no description"


def test_tool_errors_come_back_as_error_dicts(monkeypatch):
    def boom(novel_id, name, up_to_chapter=None):
        raise ValueError("Character not found: Mara; closest names: Marla")

    monkeypatch.setattr(server.queries, "build_character_page", boom)
    out = server.get_character("novel-1", "Mara", 5)
    assert out["error"].endswith("closest names: Marla")


def test_writing_chapter_converts_to_inclusive_cap(monkeypatch):
    seen = {}

    def fake_page(novel_id, name, up_to_chapter=None):
        seen["cap"] = up_to_chapter
        return {"identity": {"name": name}}

    monkeypatch.setattr(server.queries, "build_character_page", fake_page)
    server.get_character("novel-1", "Jake", 12)
    assert seen["cap"] == 11


def test_bad_uuid_is_an_error_dict_not_an_exception():
    out = server.canon_facts("not-a-uuid")
    assert "error" in out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest mcp_server/tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.server'`.

- [ ] **Step 4: Write `backend/mcp_server/server.py`**

```python
"""Continuum MCP server: cutoff-aware novel-data tools for writing agents.

Tool definitions only — zero SQL. Lookups take `writing_chapter` and show the
world as of the chapter BEFORE it: while writing chapter N you see <= N-1.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from api import queries as api_queries
from mcp_server import queries

mcp = FastMCP("continuum")


def _call(fn: Callable[[], Any]) -> Any:
    """Never raise into the transport; agents self-correct from error dicts."""
    try:
        return fn()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_novels() -> Any:
    """List all novels: id, title, author, and highest chapter number."""
    return _call(api_queries.list_novels)


@mcp.tool()
def list_chapters(novel_id: str) -> Any:
    """List every chapter of a novel (number, title, summary) for orientation."""
    return _call(lambda: api_queries.list_chapters(UUID(novel_id), None))


@mcp.tool()
def search_story(novel_id: str, query: str, writing_chapter: int, k: int = 8) -> Any:
    """Semantic + keyword search over prose and extracted facts from chapters
    before writing_chapter. Use for 'has X happened yet?' style questions."""
    return _call(lambda: queries.search_story(novel_id, query, writing_chapter, k=k))


@mcp.tool()
def get_character(novel_id: str, name: str, writing_chapter: int) -> Any:
    """A character's identity, latest state, state history, relationships, and
    events as of the chapter before writing_chapter."""
    return _call(
        lambda: queries.build_character_page(
            novel_id, name, up_to_chapter=writing_chapter - 1
        )
    )


@mcp.tool()
def character_knowledge(
    novel_id: str, writing_chapter: int, character_id: str | None = None
) -> Any:
    """Who knows what (theory of mind), plus location and possession history,
    before writing_chapter. Optionally restrict 'knows' to one character id."""

    def run() -> Any:
        cap = writing_chapter - 1
        nid = UUID(novel_id)
        cid = UUID(character_id) if character_id else None
        return {
            "knows": api_queries.list_knows_edges(nid, cap, cid),
            "locations_history": api_queries.list_location_edges(nid, cap, False),
            "possessions": api_queries.list_possession_edges(nid, cap, False),
        }

    return _call(run)


@mcp.tool()
def relationships(novel_id: str, writing_chapter: int) -> Any:
    """Character relationship graph (nodes + typed edges) established before
    writing_chapter."""
    return _call(
        lambda: queries.build_relationship_graph(
            novel_id, up_to_chapter=writing_chapter - 1
        )
    )


@mcp.tool()
def open_threads(novel_id: str, writing_chapter: int) -> Any:
    """Plot threads opened before writing_chapter and not yet closed at that
    point, each with its capped event history."""
    return _call(
        lambda: queries.list_open_threads(novel_id, up_to_chapter=writing_chapter - 1)
    )


@mcp.tool()
def unresolved_commitments(novel_id: str, writing_chapter: int) -> Any:
    """Foreshadowing planted before writing_chapter that still awaits payoff."""
    return _call(
        lambda: api_queries.list_commitments(
            UUID(novel_id), writing_chapter - 1, "pending"
        )
    )


@mcp.tool()
def timeline_events(novel_id: str) -> Any:
    """Chronological story timeline. NOTE: timeline entries are not
    chapter-anchored, so no writing_chapter cutoff applies — treat late
    entries as potential spoilers."""
    return _call(lambda: queries.list_timeline(novel_id))


@mcp.tool()
def canon_facts(novel_id: str, locked_only: bool = False) -> Any:
    """Established world facts. locked_only=True limits to facts whose
    contradiction is a hard continuity failure."""
    return _call(lambda: api_queries.list_canon_facts(UUID(novel_id), locked_only))


@mcp.tool()
def scene_list(novel_id: str, writing_chapter: int, chapter: int | None = None) -> Any:
    """Scene segmentation (POV, location, present characters, summary) for
    chapters before writing_chapter; optionally one specific chapter."""
    return _call(
        lambda: api_queries.list_scenes(UUID(novel_id), writing_chapter - 1, chapter)
    )


@mcp.tool()
def check_continuity(novel_id: str, chapter_number: int, draft_text: str) -> Any:
    """Run the continuity critic on a draft WITHOUT saving it. Returns
    passed/fails/warns with quotes and suggested fixes. Always run this
    before save_chapter."""
    return _call(lambda: queries.check_continuity(novel_id, chapter_number, draft_text))


@mcp.tool()
def save_chapter(
    novel_id: str, chapter_number: int, text: str, title: str | None = None
) -> Any:
    """Ingest a finished draft into the novel as a generated chapter. Runs the
    full extraction pipeline. Refuses to overwrite an existing chapter."""
    return _call(lambda: queries.save_chapter(novel_id, chapter_number, text, title))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest mcp_server/tests/ -v`
Expected: 13 passed.

- [ ] **Step 6: Manual smoke — server boots on stdio**

Run: `cd backend && (echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'; sleep 2) | .venv/bin/novel-mcp 2>/dev/null | head -c 400`
Expected: a JSON-RPC `initialize` result naming the `continuum` server (DB not required for boot).

- [ ] **Step 7: Commit**

```bash
git add backend/mcp_server backend/pyproject.toml backend/uv.lock
git commit -m "feat: FastMCP server exposing 13 writer tools; add novel-mcp entry point"
```

---

### Task 4: Delete `cli/` and its console scripts

**Files:**
- Delete: `backend/cli/` (entire directory: `__init__.py`, `canon.py`, `character.py`, `commitments.py`, `knowledge.py`, `merge.py`, `relationships.py`, `scenes.py`, `threads.py`, `timeline.py`)
- Modify: `backend/pyproject.toml:21-29` (remove the nine `novel-wiki-*` script lines; keep `novel-pipeline`, `novel-webapp`, `novel-mcp`)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing — pure deletion. Entity merge stays available via `pipeline/db/entity_merge.py` and `POST /api/novels/{id}/entities/merge` (verified: nothing outside `cli/` imports `cli`).

- [ ] **Step 1: Verify nothing imports cli**

Run: `grep -rn "from cli\|import cli" backend --include="*.py" | grep -v "backend/cli/" | grep -v __pycache__`
Expected: no output.

- [ ] **Step 2: Delete**

```bash
git rm -r backend/cli
```

Remove these nine lines from `[project.scripts]` in `backend/pyproject.toml`:

```toml
novel-wiki-character = "cli.character:main"
novel-wiki-timeline = "cli.timeline:main"
novel-wiki-threads = "cli.threads:main"
novel-wiki-relationships = "cli.relationships:main"
novel-wiki-scenes = "cli.scenes:main"
novel-wiki-commitments = "cli.commitments:main"
novel-wiki-canon = "cli.canon:main"
novel-wiki-knowledge = "cli.knowledge:main"
novel-wiki-merge-entity = "cli.merge:main"
```

Then reinstall entry points: `cd backend && uv sync` (or `.venv/bin/pip install -e . --no-deps`).

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: everything passes (same pass/skip counts as before this task, plus the 13 mcp_server tests).

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat!: delete cli/ — MCP server replaces the novel-wiki commands"
```

---

### Task 5: `.mcp.json` registration + README

**Files:**
- Create: `.mcp.json` (repo root)
- Modify: `README.md` (repo root — add a "Writing agents (MCP)" section)

**Interfaces:**
- Consumes: `novel-mcp` console script from Task 3.
- Produces: automatic server availability in Claude Code sessions in this repo.

- [ ] **Step 1: Create `.mcp.json`**

```json
{
  "mcpServers": {
    "continuum": {
      "command": "uv",
      "args": ["run", "--directory", "backend", "novel-mcp"]
    }
  }
}
```

(`--directory backend` also makes `backend/.env` the working-dir dotenv, so `DBClient` finds Postgres.)

- [ ] **Step 2: Add README section**

Append to root `README.md`:

```markdown
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
```

- [ ] **Step 3: Verify Claude Code sees the server**

Run: `claude mcp list` from the repo root (or restart the session and check the MCP list).
Expected: `continuum` listed with its 13 tools.

- [ ] **Step 4: Commit**

```bash
git add .mcp.json README.md
git commit -m "feat: register continuum MCP server for Claude Code and document setup"
```

---

### Task 6: Update the docs website

**Files:**
- Modify: `docs/reference.html`, `docs/architecture.html`, `docs/state-of-the-system.html` (every section that documents the `novel-wiki-*` CLI)

**Interfaces:**
- Consumes: tool list and semantics from Task 3.
- Produces: docs site consistent with the codebase (CLAUDE.md requirement).

- [ ] **Step 1: Find all CLI references**

Run: `grep -n "novel-wiki\|cli/" docs/reference.html docs/architecture.html docs/state-of-the-system.html`
Expected: a handful of hits per file — these are the sections to rewrite.

- [ ] **Step 2: Rewrite each CLI section as MCP documentation**

Replace CLI command documentation with equivalent MCP content, keeping each page's existing HTML structure, classes, and styling. The content to convey (adapt markup to match each page):

> **MCP server (`backend/mcp_server/`)** — replaces the retired `novel-wiki-*` CLI.
> A FastMCP stdio server (`novel-mcp`) exposes the data layer to writing agents.
> All lookups take `writing_chapter` and return only facts from chapters before
> it. Tools: `list_novels`, `list_chapters` (orientation); `search_story`
> (hybrid retrieval); `get_character`, `character_knowledge`, `relationships`
> (who/what state); `open_threads`, `unresolved_commitments` (what's pending);
> `timeline_events`, `canon_facts`, `scene_list` (world reference);
> `check_continuity` (critic on a draft, pre-save); `save_chapter` (ingest as
> `source="generated"`, never overwrites). Registered for Claude Code via
> `.mcp.json`; see README for Claude Desktop setup. Entity merge is no longer
> a CLI command — use `POST /api/novels/{id}/entities/merge`.

In `architecture.html`, also update any component diagram/list that names `cli/` to name `mcp_server/` instead.

- [ ] **Step 3: Verify no stale references remain**

Run: `grep -rn "novel-wiki" docs/*.html`
Expected: no output (plan/spec markdown files under `docs/superpowers/` may still mention it; that's history, leave them).

- [ ] **Step 4: Visual check**

Open the three pages (e.g. `open docs/reference.html`) and confirm the new sections render correctly with the page's styling.

- [ ] **Step 5: Commit**

```bash
git add docs/reference.html docs/architecture.html docs/state-of-the-system.html
git commit -m "docs: document MCP writer tools; retire novel-wiki CLI docs"
```
