# Read Layer Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One cutoff-aware read package (`backend/reads/`) that both the FastAPI wiki and the MCP server call — dissolving `api/queries.py` and most of `mcp_server/queries.py` — plus `/api/search` with a wiki Search page, a wiki critique panel, and point-in-time fixes for MCP tools.

**Architecture:** Spec: `docs/superpowers/specs/2026-07-11-continuum-analyzer-architecture-design.md`, steps 6–7 (read layer + search). Every public read function takes `novel_id` and `up_to_chapter: int | None` (None = whole novel) and never returns data from later chapters; cutoff filtering happens in SQL in `reads/`, once. Routes and MCP tools contain no SQL. Admin writes (novel create/delete, canon mutations) move to `api/admin.py` — they are spine-adjacent writes, not reads.

**Tech Stack:** Python 3.12, FastAPI, psycopg via `pipeline/db/client.py` DBClient, HybridRetriever (BM25+dense+RRF+MMR), React 18 + TanStack Query + react-router (frontend), pytest with real branch-isolated Postgres.

## Global Constraints

- Tests run from `backend/` with the backend venv: `cd backend && .venv/bin/python -m pytest`. Full suite green after every task.
- **Read-layer contract (spec):** every public function in `reads/` takes `novel_id` and `up_to_chapter: int | None`; `None` means whole novel; no function returns rows attributable to chapters `> up_to_chapter`. Data with no chapter anchor (e.g. faction descriptions) passes through unfiltered but the function still accepts the parameter.
- **No SQL in surfaces:** after this plan, `api/routes/*` and `mcp_server/server.py` contain no SQL strings; `api/queries.py` and the read functions of `mcp_server/queries.py` are deleted.
- **reads/ is SQL-only:** no `hasattr(db, ...)` FakeDB duck-typing (the pattern in today's `api/queries.py`). Tests for reads are real-Postgres integration tests (idiom: `backend/api/tests/test_queries_real_sql.py` and `pipeline/state/tests/test_materializer.py` — UUID-named novel per test, cleanup in teardown).
- **HTTP compat:** existing GET endpoints keep their paths, `cap` query-param name, and response shapes (frontend depends on them). `cap` maps to `up_to_chapter` inside the route. New endpoints may add fields/paths but not change existing ones.
- **MCP:** tools keep `writing_chapter` semantics (world as of `writing_chapter - 1`). `timeline_events` gains a required `writing_chapter` param (it is broken today — queries a nonexistent `timeline` table — so this "breaking" change breaks nothing).
- The docs website must be updated after every behavior-changing task (CLAUDE.md); full regeneration is Plan 3.
- The generation loop still exists (deleted in Plan 3) and must keep compiling.
- Existing FakeDB-based route tests: each domain task migrates the tests whose fake paths die with it; the FakeDB conftest is deleted in the final task when nothing uses it.

## File Structure (end state)

```
backend/reads/
  __init__.py
  db.py            get_db() provider (patchable in tests)
  common.py        resolve_cutoff(), max_chapter_for(), merge_story_edges()
  novels.py        list_novels, get_novel
  chapters.py      list_chapters (+critique summary), list_scenes
  characters.py    list_characters, get_character_detail, get_character_page
  world.py         locations/objects/factions/custom entities/entity types
  timeline.py      list_timeline (events, cutoff-aware)
  graphs.py        relationship graph, entity graph, shared dynamics
  relationship_types.py   (moved from api/)
  threads.py       list_threads (point-in-time status)
  commitments.py   list_commitments (point-in-time status)
  knowledge.py     knows/location/possession edges, canon facts
  continuity.py    continuity flags, critique reports/findings
  search.py        search() wrapping HybridRetriever
  tests/           conftest (seed factory) + per-module real-DB tests + cutoff contract test
backend/api/admin.py      create/delete novel, canon fact mutations (writes)
backend/mcp_server/queries.py   shrinks to check_continuity + save_chapter glue
frontend/src/routes/Search.tsx  new; Continuity.tsx gains critique tab
```

---

### Task 1: `reads/` scaffolding — db provider, common helpers, test fixtures

**Files:**
- Create: `backend/reads/__init__.py` (empty), `backend/reads/db.py`, `backend/reads/common.py`
- Create: `backend/reads/tests/__init__.py`, `backend/reads/tests/conftest.py`
- Test: `backend/reads/tests/test_common.py`
- Modify: `backend/pyproject.toml` only if `reads` is not picked up as a package (check `[tool.setuptools]`/packages config; the repo installs `api`, `pipeline`, `mcp_server` from `backend/` — mirror however those are declared).

**Interfaces:**
- Produces (every later task consumes these):
  - `reads.db.get_db() -> DBClient` — module-level singleton, patchable.
  - `reads.common.max_chapter_for(db, novel_id) -> int` — `SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s`.
  - `reads.common.resolve_cutoff(db, novel_id, up_to_chapter: int | None) -> int` — `up_to_chapter` if not None else `max_chapter_for(...)`.
  - `reads.common.merge_story_edges(raw: list[dict]) -> list[dict]` — moved verbatim from `api/queries.py:16-63` (`_merge_story_edges`, made public).
  - `reads/tests/conftest.py` fixture `db` (yields DBClient, closes) and `seed_novel(db) -> dict` factory returning `{novel_id, chapter_ids, char/loc/obj ids...}` for a 3-chapter novel (copy the seeding idiom from `pipeline/state/tests/test_materializer.py`'s `seeded` fixture; include two characters, two locations, one object, a plot thread with thread_events in ch1 and ch3, a commitment foreshadowed ch1/paid off ch3, knows/possession/location edges via two `state_deltas` + materialize, one canon fact, one critique report with one finding on ch2, scenes on ch1, a relationship and a shared dynamic).

- [ ] **Step 1: Write the failing test**

`backend/reads/tests/test_common.py`:

```python
"""reads.common: cutoff resolution against real Postgres."""

from __future__ import annotations

from reads.common import max_chapter_for, resolve_cutoff


def test_resolve_cutoff_none_means_latest(db, seed_novel):
    seeded = seed_novel(db)
    assert max_chapter_for(db, seeded["novel_id"]) == 3
    assert resolve_cutoff(db, seeded["novel_id"], None) == 3
    assert resolve_cutoff(db, seeded["novel_id"], 1) == 1


def test_merge_story_edges_collapses_pairs():
    from reads.common import merge_story_edges
    raw = [
        {"from": "a", "to": "b", "edge_kind": "event", "description": "fought"},
        {"from": "b", "to": "a", "edge_kind": "dynamic", "description": "rivals"},
    ]
    merged = merge_story_edges(raw)
    assert len(merged) == 1
    assert merged[0]["edge_kind"] == "dynamic"  # dynamic outranks event
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest reads/tests/test_common.py -v`
Expected: FAIL — `ModuleNotFoundError: reads` (or collection error; if the package isn't importable, fix packaging config in this step).

- [ ] **Step 3: Implement**

`backend/reads/db.py`:

```python
"""DB provider for the read layer. Tests may patch get_db."""

from __future__ import annotations

from pipeline.db.client import DBClient

_db: DBClient | None = None


def get_db() -> DBClient:
    global _db
    if _db is None:
        _db = DBClient()
    return _db
```

`backend/reads/common.py`: move `_merge_story_edges` (verbatim body, public name `merge_story_edges`, keep the `_STORY_KIND_PRECEDENCE` constant and `uuid4` import) plus:

```python
def max_chapter_for(db: Any, novel_id: UUID | str) -> int:
    value = db.fetchval(
        "SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s",
        (novel_id,),
    )
    return int(value or 0)


def resolve_cutoff(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> int:
    return up_to_chapter if up_to_chapter is not None else max_chapter_for(db, novel_id)
```

`backend/reads/tests/conftest.py`: the `db` fixture + `seed_novel` factory fixture described in Interfaces. `seed_novel` is a function-returning fixture so tests opt in; it deletes its novel in a finalizer (`db.execute("DELETE FROM novels WHERE id = %s", ...)` — everything cascades).

- [ ] **Step 4: Run tests, then full suite**

Run: `cd backend && .venv/bin/python -m pytest reads -v && .venv/bin/python -m pytest -q`
Expected: PASS / all green.

- [ ] **Step 5: Commit**

```bash
git add backend/reads backend/pyproject.toml
git commit -m "feat: reads package scaffolding — db provider, cutoff helpers, real-DB test fixtures"
```

---

### Task 2: Novels, chapters (+critique summary), continuity reads; admin split; critique endpoint

**Files:**
- Create: `backend/reads/novels.py`, `backend/reads/chapters.py`, `backend/reads/continuity.py`, `backend/api/admin.py`
- Modify: `backend/api/routes/novels.py`, `backend/api/routes/chapters.py`, `backend/api/routes/continuity.py`, `backend/api/schemas.py`, `backend/mcp_server/server.py` (list_novels, list_chapters tools)
- Test: `backend/reads/tests/test_novels_chapters.py`, `backend/reads/tests/test_continuity.py`; migrate `backend/api/tests/test_novels.py`, `test_chapters.py`, `test_continuity.py` off FakeDB (real-DB seeding via a shared helper — copy `reads/tests/conftest.py`'s factory into `api/tests/conftest.py` as `seed_novel_real`, keeping the FakeDB fixture for not-yet-migrated tests)

**Interfaces:**
- Consumes: Task 1 (`get_db`, `resolve_cutoff`, `seed_novel`).
- Produces:
  - `reads.novels.list_novels(db) -> list[dict]` and `get_novel(db, novel_id) -> dict | None` — same output keys as today (`id,title,author,language,created_at,max_chapter`). Signature note: reads functions take `db` as first arg; ROUTES obtain it via `reads.db.get_db()`. (No novel-scoped cutoff applies to the novels list itself.)
  - `reads.chapters.list_chapters(db, novel_id, up_to_chapter) -> list[dict]` — today's keys PLUS `critique: {"passed": bool, "fails": int, "warns": int} | None`.
  - `reads.chapters.list_scenes(db, novel_id, up_to_chapter, chapter: int | None) -> list[dict]` — moved from `api/queries.py:1415` (`list_scenes`), SQL-only.
  - `reads.continuity.list_flags(db, novel_id, up_to_chapter, resolved_filter: str) -> list[dict]` — moved from `api/queries.py:832` (`list_continuity`).
  - `reads.continuity.get_chapter_critique(db, novel_id, chapter_number) -> dict | None` — `{chapter_number, passed, ran_at, stats, findings: [{check_name, severity, message, quote, evidence}]}`.
  - `reads.continuity.list_critiques(db, novel_id, up_to_chapter) -> list[dict]` — one row per chapter `{chapter_number, passed, fails, warns}`.
  - `api.admin.create_novel(...)`, `api.admin.delete_novel(...)` — moved verbatim from `api/queries.py:130-203` (they may keep using `api.queries._get_db` until Task 9 — no: move them to use `reads.db.get_db()` now so Task 9 can delete queries.py cleanly).
  - New route: `GET /api/novels/{novel_id}/continuity/critique?cap=` → `list_critiques`; `GET /api/novels/{novel_id}/continuity/critique/{chapter_number}` → `get_chapter_critique` (404 if none).
  - MCP `list_chapters` output rows now include the `critique` summary (comes free from `reads.chapters.list_chapters`).

- [ ] **Step 1: Write the failing tests**

`backend/reads/tests/test_novels_chapters.py`:

```python
from __future__ import annotations

from reads import chapters as chapters_reads
from reads import novels as novels_reads


def test_list_novels_includes_max_chapter(db, seed_novel):
    seeded = seed_novel(db)
    rows = novels_reads.list_novels(db)
    mine = next(r for r in rows if str(r["id"]) == seeded["novel_id"])
    assert mine["max_chapter"] == 3


def test_list_chapters_respects_cutoff_and_carries_critique(db, seed_novel):
    seeded = seed_novel(db)
    rows = chapters_reads.list_chapters(db, seeded["novel_id"], up_to_chapter=2)
    assert [r["number"] for r in rows] == [1, 2]
    ch2 = next(r for r in rows if r["number"] == 2)
    assert ch2["critique"] == {"passed": False, "fails": 1, "warns": 0}
    ch1 = next(r for r in rows if r["number"] == 1)
    assert ch1["critique"] is None


def test_list_scenes_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    assert chapters_reads.list_scenes(db, seeded["novel_id"], up_to_chapter=3, chapter=None)
    assert chapters_reads.list_scenes(db, seeded["novel_id"], up_to_chapter=0, chapter=None) == []
```

(The seed factory's critique report on ch2 must be seeded with `passed=False`, one finding `severity='fail'` — that is already its Task 1 definition.)

`backend/reads/tests/test_continuity.py`:

```python
from __future__ import annotations

from reads import continuity as continuity_reads


def test_get_chapter_critique_returns_findings(db, seed_novel):
    seeded = seed_novel(db)
    report = continuity_reads.get_chapter_critique(db, seeded["novel_id"], 2)
    assert report is not None and report["passed"] is False
    assert len(report["findings"]) == 1
    assert report["findings"][0]["severity"] == "fail"
    assert continuity_reads.get_chapter_critique(db, seeded["novel_id"], 1) is None


def test_list_critiques_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    rows = continuity_reads.list_critiques(db, seeded["novel_id"], up_to_chapter=1)
    assert rows == []  # ch2's report is beyond the cutoff
    rows = continuity_reads.list_critiques(db, seeded["novel_id"], up_to_chapter=None)
    assert [r["chapter_number"] for r in rows] == [2]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest reads/tests/test_novels_chapters.py reads/tests/test_continuity.py -v`
Expected: FAIL — modules don't exist.

- [ ] **Step 3: Implement the reads modules**

- `reads/novels.py`: move the `_list_novels_real`, `_get_novel_real`, `_max_chapter_real` SQL bodies from `api/queries.py:205-236` into public `list_novels(db)` / `get_novel(db, novel_id)`; delete the `hasattr` fake branches entirely; `max_chapter` comes from `reads.common.max_chapter_for`.
- `reads/chapters.py::list_chapters`: move from `api/queries.py:538` dropping fake branches; add the critique summary with one extra query (avoid N+1):

```python
    critique_by_chapter: dict[str, dict] = {
        str(r["chapter_id"]): {
            "passed": r["passed"],
            "fails": int(r["fails"]),
            "warns": int(r["warns"]),
        }
        for r in db.fetchall(
            """
            SELECT cr.chapter_id, cr.passed,
                   count(*) FILTER (WHERE f.severity = 'fail') AS fails,
                   count(*) FILTER (WHERE f.severity = 'warn') AS warns
              FROM critique_reports cr
              LEFT JOIN critique_findings f ON f.report_id = cr.id
              JOIN chapters c ON c.id = cr.chapter_id
             WHERE c.novel_id = %s AND c.number <= %s
             GROUP BY cr.chapter_id, cr.passed
            """,
            (novel_id, cutoff),
            dict_rows=True,
        )
    }
```

  and each chapter row gets `"critique": critique_by_chapter.get(str(row["id"]))`.
- `reads/chapters.py::list_scenes`: move from `api/queries.py:1415`; signature `(db, novel_id, up_to_chapter, chapter)`.
- `reads/continuity.py`: move `list_continuity` from `api/queries.py:832` as `list_flags(db, novel_id, up_to_chapter, resolved_filter)`; implement `get_chapter_critique` and `list_critiques` per Interfaces (both join `chapters` on `novel_id` + `number <= cutoff` for the list; the single-chapter variant looks up by `(novel_id, chapter_number)`).
- `api/admin.py`: move `create_novel`, `_create_novel_real`, `delete_novel`, `_delete_novel_real` from `api/queries.py:130-203`; swap `_get_db()` for `reads.db.get_db()`; keep behavior identical (including any fake-branch removal — these run real SQL only; if the moved code has `hasattr` branches, delete them and migrate their tests in this task).

- [ ] **Step 4: Re-point routes and MCP; add schemas**

- `api/routes/novels.py`: GETs call `reads.novels.*` with `reads.db.get_db()`; POST/DELETE call `api.admin.*`.
- `api/routes/chapters.py`: calls `reads.chapters.list_chapters(get_db(), novel_id, cap)`.
- `api/routes/continuity.py`: existing flags endpoint calls `reads.continuity.list_flags`; ADD the two critique endpoints per Interfaces. New schemas in `api/schemas.py`:

```python
class ChapterCritiqueSummary(BaseModel):
    passed: bool
    fails: int
    warns: int


class CritiqueChapterRow(BaseModel):
    chapter_number: int
    passed: bool
    fails: int
    warns: int


class CritiqueFinding(BaseModel):
    check_name: str
    severity: str
    message: str
    quote: str | None = None
    evidence: dict | None = None


class CritiqueReportDetail(BaseModel):
    chapter_number: int
    passed: bool
    ran_at: datetime
    stats: dict | None = None
    findings: list[CritiqueFinding]
```

  and `ChapterSummary` gains `critique: ChapterCritiqueSummary | None = None`.
- `api/routes/scenes.py`: re-point to `reads.chapters.list_scenes` (it currently calls `queries.list_scenes` — verify with grep and update).
- `mcp_server/server.py`: `list_novels` tool → `reads.novels.list_novels(reads_db.get_db())`; `list_chapters` tool → `reads.chapters.list_chapters(get_db(), UUID(novel_id), None)`; `scene_list` tool → `reads.chapters.list_scenes(get_db(), UUID(novel_id), writing_chapter - 1, chapter)`. Import as `from reads import chapters as chapters_reads` etc. — server stays SQL-free.

- [ ] **Step 5: Migrate the affected API tests**

`backend/api/tests/test_novels.py`, `test_chapters.py`, `test_continuity.py` currently patch `queries._get_db` with FakeDB. Rewrite them as thin real-DB endpoint tests: add a `seed_novel_real` fixture to `api/tests/conftest.py` (reuse the reads factory via import: `from reads.tests.conftest import ...` is fragile across pytest — instead move the factory body into a shared plain module `backend/reads/tests/seeding.py` with `seed_novel(db) -> dict` and `cleanup(db, novel_id)`, and have both conftests wrap it). Each migrated test: seed, call the endpoint via FastAPI `TestClient(app)`, assert status 200 and the semantic essentials (row counts under cap, critique summary present), cleanup. Port every behavioral assertion that still applies; delete only assertions that tested FakeDB plumbing itself.

- [ ] **Step 6: Full suite + docs**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: all green.
Update `docs/architecture.html`: the API section notes reads/ as the read layer for novels/chapters/continuity + the new critique endpoints; `docs/reference.html`: document `GET .../continuity/critique` and `.../critique/{chapter_number}`, and the `critique` field on chapter rows (both wiki API and MCP `list_chapters`).

- [ ] **Step 7: Commit**

```bash
git add -A backend docs
git commit -m "feat: reads layer for novels/chapters/continuity; critique surfaced via API and MCP list_chapters"
```

---

### Task 3: Characters reads (list, detail, merged character page)

**Files:**
- Create: `backend/reads/characters.py`
- Modify: `backend/api/routes/characters.py`, `backend/mcp_server/server.py` (get_character tool)
- Test: `backend/reads/tests/test_characters.py`; migrate `backend/api/tests/test_characters.py` to real-DB

**Interfaces:**
- Consumes: Task 1 helpers; `seeding.seed_novel`.
- Produces:
  - `reads.characters.list_characters(db, novel_id, up_to_chapter) -> list[dict]` — moved from `api/queries.py:250` (same output keys; cutoff semantics unchanged: characters with `first_appearance_chapter > cutoff` excluded, as today).
  - `reads.characters.get_character_detail(db, novel_id, character_id, up_to_chapter) -> dict | None` — moved from `api/queries.py:290` (the big one: states/relationships/events/dynamics sub-queries), fake branches deleted.
  - `reads.characters.get_character_page(db, novel_id, name: str, up_to_chapter) -> dict` — moved from `mcp_server/queries.py:23` (`build_character_page`); resolve name→character in SQL as it does today; internally reuse `get_character_detail` where the two overlap rather than duplicating sub-queries (read both implementations first; keep the MCP output shape byte-compatible).

- [ ] **Step 1: Write the failing test**

`backend/reads/tests/test_characters.py`:

```python
from __future__ import annotations

from reads import characters as characters_reads


def test_list_characters_cutoff_excludes_late_arrivals(db, seed_novel):
    seeded = seed_novel(db)  # factory: char A first appears ch1, char B ch3
    rows = characters_reads.list_characters(db, seeded["novel_id"], up_to_chapter=2)
    names = {r["name"] for r in rows}
    assert seeded["char_a_name"] in names
    assert seeded["char_b_name"] not in names


def test_character_page_by_name_matches_detail(db, seed_novel):
    seeded = seed_novel(db)
    page = characters_reads.get_character_page(
        db, seeded["novel_id"], seeded["char_a_name"], up_to_chapter=2
    )
    assert page["character"]["name"] == seeded["char_a_name"]
    detail = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=2
    )
    assert detail is not None
    # No state rows from beyond the cutoff in either shape.
    assert all(s["chapter_number"] <= 2 for s in detail["states"])
```

(Adjust key names to the actual output shapes after reading the two source functions — port, don't invent. If the factory lacks `char_a_name`/`char_b_name`/`char_a_id` keys, add them to `seeding.py`.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest reads/tests/test_characters.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement, re-point, migrate tests**

Move the three functions per Interfaces (delete fake branches; rename `cap`→`up_to_chapter`). `api/routes/characters.py` calls `reads.characters.*` via `reads.db.get_db()`. `mcp_server/server.py::get_character` calls `characters_reads.get_character_page(get_db(), novel_id, name, up_to_chapter=writing_chapter - 1)`. Migrate `api/tests/test_characters.py` to real-DB endpoint tests (same recipe as Task 2 Step 5). Check `mcp_server/tests/` for tests of `build_character_page` and update their import/monkeypatch targets.

- [ ] **Step 4: Full suite + docs**

Run: `cd backend && .venv/bin/python -m pytest -q` — all green.
`docs/reference.html`: character endpoints note the shared read layer (small wording update).

- [ ] **Step 5: Commit**

```bash
git add -A backend docs
git commit -m "feat: character reads unified — wiki detail and MCP character page share one module"
```

---

### Task 4: World reads (locations, objects, factions, custom entities, entity types)

**Files:**
- Create: `backend/reads/world.py`
- Modify: `backend/api/routes/locations.py`, `objects.py`, `factions.py`, `entity_types.py` (GETs only — the entity-merge POST stays pointing at `pipeline.db.entity_merge`)
- Test: `backend/reads/tests/test_world.py`; migrate `backend/api/tests/test_locations.py`, `test_objects.py`, `test_factions.py`, `test_entity_types.py`

**Interfaces:**
- Consumes: Task 1.
- Produces (all moved from `api/queries.py`, fake branches deleted, `cap`→`up_to_chapter`):
  - `reads.world.list_locations(db, novel_id, up_to_chapter)` / `get_location_detail(db, novel_id, location_id, up_to_chapter)` (from :904/:940)
  - `reads.world.list_objects(db, novel_id, up_to_chapter)` / `get_object_detail(db, novel_id, object_id, up_to_chapter)` (from :1050/:1087)
  - `reads.world.list_factions(db, novel_id, up_to_chapter)` / `get_faction_detail(db, novel_id, faction_id, up_to_chapter)` (from :1230/:1259 — **these gain the parameter**: factions have no chapter anchor, so the parameter is accepted and unused for the faction rows themselves, but any chapter-anchored sub-lists inside the detail (events, dynamics) must apply the cutoff; read the function to find them)
  - `reads.world.list_entity_types(db, novel_id)` (from :1771; no cutoff — type registry is config, document that exception in the module docstring)
  - `reads.world.list_custom_entities(db, novel_id, entity_type, up_to_chapter)` / `get_custom_entity_detail(db, novel_id, entity_id, up_to_chapter)` (from :1792/:1821 — same treatment as factions: accept the param, apply it to chapter-anchored sub-lists)

- [ ] **Step 1: Write the failing test**

`backend/reads/tests/test_world.py`:

```python
from __future__ import annotations

from reads import world as world_reads


def test_locations_and_objects_respect_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    locs = world_reads.list_locations(db, seeded["novel_id"], up_to_chapter=3)
    assert locs
    objs = world_reads.list_objects(db, seeded["novel_id"], up_to_chapter=3)
    assert objs


def test_faction_detail_sublists_apply_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    detail = world_reads.get_faction_detail(
        db, seeded["novel_id"], seeded["faction_id"], up_to_chapter=1
    )
    assert detail is not None
    for key, rows in detail.items():
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and "chapter_number" in row:
                    assert row["chapter_number"] <= 1, key
```

(Extend `seeding.py` with a faction + a faction-involving event in ch3 so the cutoff assertion has teeth.)

- [ ] **Step 2: Run to verify failure** — `cd backend && .venv/bin/python -m pytest reads/tests/test_world.py -v` → FAIL.

- [ ] **Step 3: Implement, re-point the four route files, migrate their tests** (same recipes as Tasks 2–3).

- [ ] **Step 4: Full suite** — `cd backend && .venv/bin/python -m pytest -q` → green. Docs: reference.html faction/custom-entity endpoints now accept `cap` (they gain the query param in their routes — add `cap: int | None = Query(default=None)` and pass through).

- [ ] **Step 5: Commit**

```bash
git add -A backend docs
git commit -m "feat: world reads — locations/objects/factions/custom entities behind the cutoff contract"
```

---

### Task 5: Timeline + graphs reads; fix MCP timeline_events (broken today) and relationships

**Files:**
- Create: `backend/reads/timeline.py`, `backend/reads/graphs.py`
- Move: `backend/api/relationship_types.py` → `backend/reads/relationship_types.py` (`git mv`; update importers — grep `relationship_types`)
- Modify: `backend/api/routes/timeline.py`, `relationships.py`, `entity_graph.py`, `dynamics.py`; `backend/mcp_server/server.py` (relationships, timeline_events tools)
- Test: `backend/reads/tests/test_timeline_graphs.py`; migrate `backend/api/tests/test_timeline.py`, `test_relationships.py`, `test_entity_graph.py`, `test_dynamics.py`, `test_relationship_types.py` (import path)

**Interfaces:**
- Consumes: Task 1 (`merge_story_edges` for graphs).
- Produces:
  - `reads.timeline.list_timeline(db, novel_id, up_to_chapter) -> list[dict]` — moved from `api/queries.py:570` (events grouped by chapter; already cutoff-aware — verify and keep).
  - `reads.graphs.relationship_graph(db, novel_id, up_to_chapter) -> dict` — SINGLE implementation replacing BOTH `api/queries.py:733` (`get_relationship_graph`) and `mcp_server/queries.py:167` (`build_relationship_graph`). Read both first: keep the API's output shape (`{nodes, edges}` matching `RelationshipGraph` schema) as the canonical one, and add whatever fields the MCP variant returned that the API one lacks (e.g. `up_to_chapter` echo) as additive keys. Both surfaces then serve the same dict.
  - `reads.graphs.entity_graph(db, novel_id, up_to_chapter) -> dict` — moved from `api/queries.py:1907`.
  - `reads.graphs.list_shared_dynamics(db, novel_id, up_to_chapter) -> list[dict]` — moved from `api/queries.py:1353`.
- **MCP `timeline_events` is replaced.** Today it queries a `timeline` TABLE that does not exist in the schema — the tool has been returning `{"error": "UndefinedTable..."}` since inception. New tool signature and docstring:

```python
@mcp.tool()
def timeline_events(novel_id: str, writing_chapter: int) -> Any:
    """Story events grouped by chapter, from chapters before writing_chapter.
    Cutoff-aware: safe against spoilers."""
    return _call(
        lambda: timeline_reads.list_timeline(
            get_db(), UUID(novel_id), writing_chapter - 1
        )
    )
```

  Delete `mcp_server/queries.py::list_timeline` (the broken one) in this task.

- [ ] **Step 1: Write the failing test**

`backend/reads/tests/test_timeline_graphs.py`:

```python
from __future__ import annotations

from reads import graphs as graphs_reads
from reads import timeline as timeline_reads


def test_timeline_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    rows = timeline_reads.list_timeline(db, seeded["novel_id"], up_to_chapter=1)
    assert rows and all(r["chapter_number"] <= 1 for r in rows)


def test_relationship_graph_single_impl_serves_both_surfaces(db, seed_novel):
    seeded = seed_novel(db)
    graph = graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=3)
    assert set(graph.keys()) >= {"nodes", "edges"}
    assert graph["nodes"]


def test_shared_dynamics_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    assert graphs_reads.list_shared_dynamics(db, seeded["novel_id"], up_to_chapter=0) == []
```

(Adjust `chapter_number` key to the actual timeline row shape after reading `api/queries.py:570` — port faithfully.)

- [ ] **Step 2: Run to verify failure** — FAIL, modules don't exist.

- [ ] **Step 3: Implement, merge the two relationship-graph variants, re-point routes + MCP, delete the broken MCP list_timeline, migrate tests.**

- [ ] **Step 4: Full suite + docs.** reference.html: `timeline_events` MCP tool now requires `writing_chapter` and is cutoff-aware (remove the old "no cutoff applies — treat late entries as potential spoilers" caveat); note the tool was previously non-functional. architecture.html: MCP section same fix.

- [ ] **Step 5: Commit**

```bash
git add -A backend docs
git commit -m "feat: timeline and graph reads; fix broken MCP timeline_events with cutoff-aware events timeline"
```

---

### Task 6: Threads, commitments, knowledge reads — true point-in-time status

**Files:**
- Create: `backend/reads/threads.py`, `backend/reads/commitments.py`, `backend/reads/knowledge.py`
- Modify: `backend/api/routes/threads.py`, `commitments.py`, `knowledge.py`, `canon.py` (GET only); `backend/mcp_server/server.py` (open_threads, unresolved_commitments, character_knowledge, canon_facts tools); `backend/api/admin.py` (canon mutations move in from `api/queries.py:1565-1638`)
- Test: `backend/reads/tests/test_threads_commitments.py`, `backend/reads/tests/test_knowledge.py`; migrate `backend/api/tests/test_threads.py`, `test_canon_mutations.py`; delete `mcp_server/queries.py::list_open_threads` (superseded)

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `reads.threads.list_threads(db, novel_id, up_to_chapter, status: str = "all") -> list[dict]` — moved from `api/queries.py:636` and upgraded to point-in-time: each row gains `status_at_cutoff`, derived in SQL:

```sql
CASE
  WHEN pt.closed_chapter IS NOT NULL AND pt.closed_chapter <= %(cutoff)s THEN 'closed'
  WHEN pt.status = 'closed' THEN 'progressing'  -- closed later than the cutoff
  ELSE pt.status
END AS status_at_cutoff
```

    The `status` filter argument filters on `status_at_cutoff`. Rows keep the raw `status` column too (the wiki shows it; the MCP tool filters by `status_at_cutoff`). Threads with `opened_chapter > cutoff` are excluded. Per-thread event lists keep their existing `ch.number <= cutoff` cap (see `mcp_server/queries.py:261-300` for the reference implementation to merge).
  - `reads.commitments.list_commitments(db, novel_id, up_to_chapter, status: str | None) -> list[dict]` — moved from `api/queries.py:1476`, upgraded: rows gain `status_at_cutoff`:

```sql
CASE
  WHEN c.status IN ('broken','abandoned') THEN c.status
  WHEN c.payoff_chapter IS NOT NULL AND c.payoff_chapter <= %(cutoff)s THEN 'satisfied'
  ELSE 'pending'
END AS status_at_cutoff
```

    filtered on `status_at_cutoff` when `status` is given; commitments with `foreshadow_chapter > cutoff` excluded.
  - `reads.knowledge.list_knows_edges(db, novel_id, up_to_chapter, character_id: UUID | None)` — from `api/queries.py:1640`.
  - `reads.knowledge.list_location_edges(db, novel_id, up_to_chapter, active_only: bool)` — from :1692.
  - `reads.knowledge.list_possession_edges(db, novel_id, up_to_chapter, active_only: bool)` — from :1732.
  - `reads.knowledge.list_canon_facts(db, novel_id, up_to_chapter, locked_only: bool)` — from :1531, **gains the cutoff**: `AND (cf.source_chapter IS NULL OR cf.source_chapter <= cutoff)`.
  - MCP re-points: `open_threads` → `threads_reads.list_threads(get_db(), UUID(novel_id), writing_chapter - 1, status="open")` filtered to non-closed `status_at_cutoff` (i.e. pass `status="all"` and filter `status_at_cutoff != 'closed'` in the tool lambda — one line, no SQL); `unresolved_commitments` → `commitments_reads.list_commitments(..., status="pending")`; `canon_facts` gains optional `writing_chapter: int | None = None` (cutoff applied when provided); `character_knowledge` re-points to the three knowledge functions. Update the `open_threads`/`unresolved_commitments` docstrings: point-in-time accurate for ANY chapter now — delete the "only when writing the next unwritten chapter" caveats.

- [ ] **Step 1: Write the failing tests**

`backend/reads/tests/test_threads_commitments.py`:

```python
from __future__ import annotations

from reads import commitments as commitments_reads
from reads import threads as threads_reads


def test_thread_closed_later_reads_open_at_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory thread: opened ch1, closed_chapter=3, status='closed'
    rows = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=2, status="all")
    mine = next(r for r in rows if str(r["id"]) == seeded["thread_id"])
    assert mine["status_at_cutoff"] == "progressing"
    assert all(e["chapter_number"] <= 2 for e in mine["events"])

    rows3 = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=3, status="all")
    assert next(r for r in rows3 if str(r["id"]) == seeded["thread_id"])["status_at_cutoff"] == "closed"


def test_commitment_paid_off_later_is_pending_at_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory commitment: foreshadow ch1, payoff_chapter=3, status='satisfied'
    rows = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=2, status="pending")
    assert any(str(r["id"]) == seeded["commitment_id"] for r in rows)
    rows3 = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=3, status="pending")
    assert not any(str(r["id"]) == seeded["commitment_id"] for r in rows3)
```

`backend/reads/tests/test_knowledge.py`:

```python
from __future__ import annotations

from reads import knowledge as knowledge_reads


def test_canon_facts_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory canon fact: source_chapter=2
    assert knowledge_reads.list_canon_facts(db, seeded["novel_id"], up_to_chapter=1, locked_only=False) == []
    assert knowledge_reads.list_canon_facts(db, seeded["novel_id"], up_to_chapter=2, locked_only=False)


def test_edges_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    for fn in (knowledge_reads.list_location_edges, knowledge_reads.list_possession_edges):
        rows = fn(db, seeded["novel_id"], up_to_chapter=1, active_only=False)
        assert all(r["since_chapter"] <= 1 for r in rows)
```

(Ensure `seeding.py` gives the thread `closed_chapter=3, status='closed'` and the commitment `payoff_chapter=3, status='satisfied'`; extend if Task 1's factory set them differently.)

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement, move canon mutations to admin, re-point routes + MCP, migrate tests.** `api/routes/threads.py` passes its existing `status` query param through (filtering now on `status_at_cutoff`; the `PlotThread` schema gains `status_at_cutoff: str`). `api/routes/canon.py` GET → `reads.knowledge.list_canon_facts` (route keeps no-cap behavior by passing `up_to_chapter=None` unless a `cap` param is supplied — add the param); PATCH/POST/DELETE → `api.admin`.

- [ ] **Step 4: Full suite + docs.** reference.html: `status_at_cutoff` on threads/commitments rows, canon facts `cap`, MCP docstring changes (delete the two point-in-time caveats — they are fixed now, this was a known follow-up from the MCP plan's final review).

- [ ] **Step 5: Commit**

```bash
git add -A backend docs
git commit -m "feat: point-in-time thread/commitment status; knowledge and canon reads behind the cutoff"
```

---

### Task 7: Search — reads/search.py, /api/search endpoint, MCP re-point

**Files:**
- Create: `backend/reads/search.py`
- Modify: `backend/api/routes/chapters.py` OR create `backend/api/routes/search.py` (new router — cleaner; register in `api/app.py`), `backend/api/schemas.py`, `backend/mcp_server/server.py` (search_story), delete `mcp_server/queries.py::search_story`
- Test: `backend/reads/tests/test_search.py`; `backend/api/tests/test_search.py` (new)

**Interfaces:**
- Consumes: `HybridRetriever` (`pipeline.retrieval.hybrid`), `RetrievalQuery` (`pipeline.retrieval.types`), `EmbeddingService` (`pipeline.embeddings`), `settings.use_mock_llm`.
- Produces:
  - `reads.search.search(db, novel_id, query_text: str, up_to_chapter: int | None, k: int = 8, retriever=None) -> dict` — adapted from `mcp_server/queries.py:303` (`search_story`): same retrieval call, but cutoff semantics follow the reads contract (`max_chapter=resolve_cutoff(db, novel_id, up_to_chapter)`), and the injectable `retriever` kwarg is kept for tests. Returns `{"results": [{"kind", "chapter_number", "score", "snippet"}]}`.
  - `GET /api/novels/{novel_id}/search?q=&cap=&k=` → `SearchResults` schema:

```python
class SearchResultRow(BaseModel):
    kind: str
    chapter_number: int | None = None
    score: float
    snippet: str | None = None


class SearchResults(BaseModel):
    results: list[SearchResultRow]
```

  - MCP `search_story` tool body becomes `reads.search.search(get_db(), UUID(novel_id), query, writing_chapter - 1, k=k)` — note the MCP tool passes `writing_chapter - 1` explicitly (its `writing_chapter` semantics), while the API route passes `cap` straight through.

- [ ] **Step 1: Write the failing test**

`backend/reads/tests/test_search.py` (fake retriever — no embeddings needed):

```python
from __future__ import annotations

from dataclasses import dataclass

from reads import search as search_reads


@dataclass
class _R:
    kind: str = "chapter"
    chapter_number: int = 1
    score: float = 0.9
    snippet: str = "Aelric took the dagger"


class FakeRetriever:
    def __init__(self):
        self.last_query = None

    def retrieve(self, query, use_rerank=False):
        self.last_query = query

        class Bundle:
            results = [_R()]

        return Bundle()


def test_search_applies_cutoff_and_shapes_results(db, seed_novel):
    seeded = seed_novel(db)
    fake = FakeRetriever()
    out = search_reads.search(
        db, seeded["novel_id"], "dagger", up_to_chapter=2, k=5, retriever=fake
    )
    assert out["results"][0]["snippet"] == "Aelric took the dagger"
    assert fake.last_query.max_chapter == 2
    assert fake.last_query.k == 5

    # None resolves to the novel's latest chapter (3 in the fixture).
    search_reads.search(db, seeded["novel_id"], "dagger", up_to_chapter=None, retriever=fake)
    assert fake.last_query.max_chapter == 3
```

`backend/api/tests/test_search.py`: TestClient GET `/api/novels/{id}/search?q=dagger&cap=2` with the retriever patched (monkeypatch `reads.search._build_retriever` — implement retriever construction behind that seam) → 200, results list shape.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** (module with `_build_retriever(db)` seam constructing `HybridRetriever(db, EmbeddingService(use_mock=settings.use_mock_llm))`; `search()` per Interfaces), register the new router, re-point MCP, delete `mcp_server/queries.py::search_story`, update `mcp_server` tests that referenced it.

- [ ] **Step 4: Full suite + docs.** reference.html: document `GET .../search`; architecture.html: retriever now serves three consumers (wiki search, MCP search_story, generation until Plan 3).

- [ ] **Step 5: Commit**

```bash
git add -A backend docs
git commit -m "feat: /api/search backed by reads/search; MCP search_story shares the implementation"
```

---

### Task 8: Frontend — Search page + Continuity critique panel

**Files:**
- Create: `frontend/src/routes/Search.tsx`
- Modify: `frontend/src/App.tsx` (route), `frontend/src/components/Sidebar.tsx` (nav entry), `frontend/src/routes/Continuity.tsx` (tabbed: Critique | Flags), `frontend/src/api.ts` (types + endpoints)
- Test: `cd frontend && npm run build` (the repo has no frontend unit-test rig; type-checking via the build is the gate — verify with `ls frontend/src/**/*.test.*` first and use the test runner instead if one exists)

**Interfaces:**
- Consumes: `GET /api/novels/{id}/search?q=&cap=&k=` (Task 7), `GET /api/novels/{id}/continuity/critique?cap=` and `/critique/{chapter}` (Task 2), existing `cap` context used by other pages (read how Continuity.tsx obtains the chapter-cap slider value and follow the same pattern).
- Produces: `api.ts` additions:

```typescript
export type SearchResultRow = {
  kind: string;
  chapter_number: number | null;
  score: number;
  snippet: string | null;
};
export type CritiqueChapterRow = {
  chapter_number: number;
  passed: boolean;
  fails: number;
  warns: number;
};
export type CritiqueFinding = {
  check_name: string;
  severity: "fail" | "warn" | "info";
  message: string;
  quote: string | null;
  evidence: Record<string, unknown> | null;
};
export type CritiqueReportDetail = {
  chapter_number: number;
  passed: boolean;
  ran_at: string;
  stats: Record<string, unknown> | null;
  findings: CritiqueFinding[];
};
```

- [ ] **Step 1: Read the neighboring pages first** — `Knowledge.tsx` (tabs/data-fetch pattern), `Timeline.tsx` (cap usage), `Sidebar.tsx` (nav structure). Follow their exact idioms (TanStack Query hooks, dark-theme classes).

- [ ] **Step 2: Implement Search.tsx** — search input (submit on Enter), calls `/search?q=...&cap=<current cap>`, renders result rows: kind badge, chapter number, score (2 decimals), snippet. Empty-query → no fetch; loading and "no results" states per the codebase's existing patterns. Route `/novels/:novelId/search`; Sidebar entry "Search" near the top.

- [ ] **Step 3: Implement the Continuity critique tab** — Continuity.tsx becomes two tabs: **Critique** (default): table of `CritiqueChapterRow` (chapter, pass/fail badge, fails/warns counts); clicking a row expands (fetch detail) to the findings list (check name, severity badge, message, quote in monospace). **Flags**: the existing page content unchanged.

- [ ] **Step 4: Build** — `cd frontend && npm run build` → succeeds with no type errors. Manually verify the two pages compile into the bundle (build output).

- [ ] **Step 5: Docs + commit** — architecture.html/reference.html: wiki gains Search page and Continuity critique panel.

```bash
git add frontend docs
git commit -m "feat: wiki Search page and per-chapter critique panel"
```

---

### Task 9: Dissolution — delete api/queries.py, slim mcp_server/queries.py, enforce the contract

**Files:**
- Delete: `backend/api/queries.py`, `backend/api/tests/conftest.py` FakeDB parts (whole fixture if unused), any remaining FakeDB-based tests (must be zero by now — migrate stragglers instead of deleting coverage)
- Modify: `backend/mcp_server/queries.py` — keep ONLY `check_continuity`, `save_chapter`, `_finding_dict` (its read functions were deleted in Tasks 3/5/6/7); module docstring updated
- Test: `backend/reads/tests/test_contract.py` (new)
- Modify: `docs/architecture.html`, `docs/reference.html`, `docs/state-of-the-system.html` (read-layer reality)

**Interfaces:**
- Consumes: everything prior.
- Produces: the enforced end state — grep-clean surfaces.

- [ ] **Step 1: Write the contract tests**

`backend/reads/tests/test_contract.py`:

```python
"""The read-layer contract, enforced structurally.

1. No SQL in surfaces: api/routes/* and mcp_server/server.py contain no SQL.
2. Every public reads function (except documented exceptions) accepts
   up_to_chapter.
"""

from __future__ import annotations

import inspect
import pkgutil
import re
from importlib import import_module
from pathlib import Path

import reads

SQL_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\bFROM\b|\bINSERT INTO\b", re.I | re.S)

# Functions that legitimately take no cutoff (registry/config reads).
CUTOFF_EXEMPT = {
    ("reads.novels", "list_novels"),
    ("reads.novels", "get_novel"),
    ("reads.world", "list_entity_types"),
    ("reads.continuity", "get_chapter_critique"),  # keyed by explicit chapter
}


def test_no_sql_in_surfaces():
    backend = Path(__file__).resolve().parents[2]
    surface_files = list((backend / "api" / "routes").glob("*.py"))
    surface_files.append(backend / "mcp_server" / "server.py")
    offenders = [
        str(f) for f in surface_files if SQL_PATTERN.search(f.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"SQL found in surface files: {offenders}"


def test_reads_functions_take_up_to_chapter():
    missing = []
    for mod_info in pkgutil.iter_modules(reads.__path__):
        if mod_info.name in {"db", "common", "tests"}:
            continue
        module = import_module(f"reads.{mod_info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            if (module.__name__, name) in CUTOFF_EXEMPT:
                continue
            if "up_to_chapter" not in inspect.signature(fn).parameters:
                missing.append(f"{module.__name__}.{name}")
    assert missing == [], f"reads functions missing up_to_chapter: {missing}"
```

- [ ] **Step 2: Run** — likely PASS already if Tasks 2–7 were faithful; any failure is a real straggler: fix the straggler, not the test.

- [ ] **Step 3: Delete `api/queries.py`** — first `grep -rn "from api import queries\|api\.queries\|from api.queries" backend --include="*.py" | grep -v .venv`; every hit must be re-pointed (reads/admin) or a dead test to migrate. Then `git rm backend/api/queries.py`. Slim `mcp_server/queries.py` per Interfaces. Remove the FakeDB fixture from `api/tests/conftest.py` if no test uses it (grep `FakeDB`); `git rm` `test_queries_real_sql.py` ONLY if its coverage was ported into reads tests — otherwise move it to `reads/tests/`.

- [ ] **Step 4: Full suite** — `cd backend && .venv/bin/python -m pytest -q` → green.

- [ ] **Step 5: Docs** — architecture.html: read-layer section now states "routes and MCP tools contain no SQL; reads/ is the only presentation reader" with the module list; state-of-the-system.html: mark the dual read-layer / spoiler-leak items resolved (dated 2026-07-12+); reference.html sweep for `api/queries` mentions.

- [ ] **Step 6: Commit**

```bash
git add -A backend docs
git commit -m "feat!: api/queries dissolved into reads/; contract tests enforce SQL-free surfaces"
```

---

## Plan self-review (done at write time)

- **Spec coverage (steps 6–7):** shared read layer (Tasks 1–6, 9), `up_to_chapter` contract + SQL-once cutoff (Tasks 1–6 + contract test Task 9), API routes re-pointed / queries.py dissolved (2–6, 9), MCP re-pointed + `timeline_events` spoiler fix + thread/commitment point-in-time status (5, 6), `/api/search` + wiki Search page + MCP shares impl (7, 8), critic surfaced in wiki + MCP `list_chapters` summary (2, 8 — spec's "Critic as a product feature" read-side items). Writer-side queries stay in the pipeline (untouched).
- **Discovered-in-survey items folded in:** MCP `timeline_events` queries a nonexistent `timeline` table (Task 5 replaces it); `api/queries.py` FakeDB duck-typing dies with the SQL-only rule (each domain task migrates its tests; Task 9 removes the fixture).
- **Type consistency:** reads functions uniformly `(db, novel_id, up_to_chapter, ...)`; routes translate `cap`; MCP translates `writing_chapter - 1`. `status_at_cutoff` naming consistent across threads/commitments (Task 6) and schemas/frontend types (Tasks 6, 8).
- **Known deliberate exceptions** are encoded in `CUTOFF_EXEMPT` (Task 9) and module docstrings (Task 4).
