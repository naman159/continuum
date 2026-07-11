# Write Spine Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `analyze_chapter` the single write path: typed `state_deltas` from extraction replace verb-regex inference, the materializer becomes the sole writer of `character_states`, and every chapter gets a persisted continuity critique.

**Architecture:** Spec: `docs/superpowers/specs/2026-07-11-continuum-analyzer-architecture-design.md`. This plan covers spec sequencing steps 1–5 (write spine). Plans 2 (read layer) and 3 (teardown/evals/docs) follow separately.

**Tech Stack:** Python 3.12, psycopg (via `pipeline/db/client.py` DBClient), Postgres + pgvector, LiteLLM extraction with mock mode, pytest.

## Global Constraints

- Run tests from `backend/` with the backend venv: `cd backend && .venv/bin/python -m pytest <path> -v`. Do NOT use the repo-root `.venv` (it collects DB-integration tests against an uninitialized DB).
- DB-integration tests hit a real Postgres; each test seeds its own UUID-named novel and cleans up in teardown (see `pipeline/state/tests/test_materializer.py` for the idiom).
- Breaking schema changes are allowed (spec decision); `chapters.raw_text` is ground truth.
- Every task must leave the full backend suite green: `cd backend && .venv/bin/python -m pytest`.
- Mock-LLM mode (`use_mock_llm`) must keep working for every changed pass — tests run offline.
- The docs website must be updated after every change (CLAUDE.md). Tasks that change behavior update `docs/architecture.html` inline; the full docs regeneration is Plan 3.
- One spec refinement locked in here: `state_deltas` gains an `attribute TEXT NULL` column (which `character_states` field a `status` delta updates). The spec file is updated in Task 2.

---

### Task 1: Land the in-flight phantom-character fix

The working tree already contains a complete change (resolver `create=False` reference-only resolution + tests). Verify and commit it — later tasks build on `create=False`.

**Files:**
- Modify (already modified, commit as-is): `backend/pipeline/extraction/persist_canon.py`, `backend/pipeline/extraction/persist_extras.py`, `backend/pipeline/extraction/resolver.py`, `backend/pipeline/pipeline.py`, `backend/pipeline/extraction/tests/test_canon_facts.py`, `backend/pipeline/extraction/tests/test_resolver.py`, `docs/architecture.html`
- Add: `backend/pipeline/extraction/tests/test_persist_no_phantom_characters.py`

**Interfaces:**
- Produces: `EntityResolver.resolve_character/location/faction/object(name, metadata=None, *, create=True) -> ResolvedEntity | None` and `resolve_any_entity(name, *, create=True) -> str | None`. Every later task that resolves reference-only names relies on `create=False` returning `None` for unknown names.

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all tests pass (the new `test_persist_no_phantom_characters.py` is already written and green).

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/extraction/persist_canon.py backend/pipeline/extraction/persist_extras.py backend/pipeline/extraction/resolver.py backend/pipeline/extraction/tests/ backend/pipeline/pipeline.py docs/architecture.html
git commit -m "fix: reference-only entity resolution — downstream passes never mint phantom characters"
```

---

### Task 2: Consolidated schema — drop unproduced columns, add spine tables, version stamp

**Files:**
- Modify: `backend/pipeline/db/schema.sql`
- Modify: `backend/pipeline/pipeline.py` (init_db stamps version; drop `generation_meta` param)
- Modify: `backend/pipeline/db/entity_merge.py:189-190` (delete SVO updates)
- Modify: `backend/pipeline/state/event_replay.py` (`_load_events`: drop `narrative_order` from SELECT/ORDER BY — full rewrite comes in Task 5)
- Modify: `backend/pipeline/ingestion/ingest.py` (drop `generation_meta`)
- Modify: `backend/pipeline/generation/loop.py` (stop passing `generation_meta`)
- Modify: `backend/mcp_server/queries.py:394-409` (`source="agent"`, drop `generation_meta`)
- Delete: `backend/pipeline/db/migrate_add_entity_aliases.py`, `migrate_add_entities_aliases.py`, `migrate_add_event_factions.py`, `migrate_add_ingestion_provenance.py`, `migrate_custom_entity_types.py`, `migrate_entity_refactor.py` (and any `backend/pipeline/db/tests/` test importing them — grep first: `grep -rln "migrate_" backend/pipeline/db/tests backend/pipeline/tests 2>/dev/null`)
- Modify: `docs/superpowers/specs/2026-07-11-continuum-analyzer-architecture-design.md` (add `attribute TEXT NULL` to the `state_deltas` definition)
- Test: `backend/pipeline/db/tests/test_schema_version.py` (create)

**Interfaces:**
- Produces tables later tasks write to:
  - `state_deltas(id UUID PK, chapter_id UUID NOT NULL FK chapters ON DELETE CASCADE, event_id UUID NULL FK events ON DELETE SET NULL, ordinal INTEGER NOT NULL DEFAULT 0 — narrative order within the chapter (now() is transaction-stable, so created_at cannot order rows inside one chapter), kind TEXT CHECK IN (possession|location|knowledge|status), subject_id UUID NOT NULL FK entities, object_id UUID NULL FK entities, location_id UUID NULL FK locations, change TEXT NULL CHECK IN (gain|loss|move|learn|update), attribute TEXT NULL, detail TEXT NULL, certainty FLOAT DEFAULT 1.0, created_at)`
  - `critique_reports(id UUID PK, chapter_id UUID NOT NULL UNIQUE FK chapters ON DELETE CASCADE, passed BOOLEAN NOT NULL, ran_at TIMESTAMPTZ DEFAULT now(), stats JSONB)`
  - `critique_findings(id UUID PK, report_id UUID NOT NULL FK critique_reports ON DELETE CASCADE, check_name TEXT NOT NULL, severity TEXT CHECK IN (fail|warn|info), message TEXT NOT NULL, quote TEXT NULL, evidence JSONB)`
  - `schema_version(version INTEGER NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())` + `SCHEMA_VERSION = 1` constant in `pipeline/pipeline.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/pipeline/db/tests/test_schema_version.py`:

```python
"""init_db applies the consolidated schema idempotently and stamps a version.

Hits the real branch-isolated Postgres. Running init_db twice must succeed
(idempotent DDL) and leave exactly one schema_version row at SCHEMA_VERSION.
The removed legacy surface (temporal_constraints, events SVO columns,
knows_edges.fact_id, chapters.generation_meta) must be gone afterward.
"""

from __future__ import annotations

from pipeline.db.client import DBClient
from pipeline.pipeline import SCHEMA_VERSION, init_db


def _column_exists(db: DBClient, table: str, column: str) -> bool:
    return bool(
        db.fetchval(
            """
            SELECT 1 FROM information_schema.columns
             WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
    )


def _table_exists(db: DBClient, table: str) -> bool:
    return bool(
        db.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        )
    )


def test_init_db_is_idempotent_and_stamps_version():
    init_db()
    init_db()  # second run must not raise
    with DBClient() as db:
        rows = db.fetchall("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0][0] == SCHEMA_VERSION


def test_new_spine_tables_exist_and_legacy_surface_is_gone():
    init_db()
    with DBClient() as db:
        assert _table_exists(db, "state_deltas")
        assert _table_exists(db, "critique_reports")
        assert _table_exists(db, "critique_findings")
        assert not _table_exists(db, "temporal_constraints")
        for col in ("subject_entity_id", "verb", "object_entity_id",
                    "story_time_ordinal", "narrative_order", "scene_id"):
            assert not _column_exists(db, "events", col), col
        assert not _column_exists(db, "knows_edges", "fact_id")
        assert not _column_exists(db, "chapters", "generation_meta")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest pipeline/db/tests/test_schema_version.py -v`
Expected: FAIL — `ImportError: cannot import name 'SCHEMA_VERSION'`.

- [ ] **Step 3: Edit schema.sql**

In `backend/pipeline/db/schema.sql`:

(a) DELETE these blocks entirely:
- the `-- ---- Event log enhancements ----` block (all six `ALTER TABLE events ADD COLUMN ...` lines: `subject_entity_id`, `verb`, `object_entity_id`, `story_time_ordinal`, `narrative_order`, `scene_id`, plus `CREATE INDEX ... idx_events_story_time`)
- the `DO $$ ... events_scene_id_fkey ... $$` block
- the whole `temporal_constraints` table + its two indexes
- inside `knows_edges`: the `fact_id UUID REFERENCES canon_facts(id),` line and `CREATE INDEX IF NOT EXISTS idx_knows_fact ...`
- the `ALTER TABLE chapters ADD COLUMN IF NOT EXISTS generation_meta JSONB;` line

(b) APPEND this section before the full-text-search section (idempotent drops converge existing dev DBs; new tables for the spine):

```sql
-- =====================================================================
-- Write-spine consolidation (see docs/superpowers/specs/
-- 2026-07-11-continuum-analyzer-architecture-design.md).
-- Idempotent DROPs converge databases created before the consolidation.
-- =====================================================================
DROP TABLE IF EXISTS temporal_constraints;
ALTER TABLE events DROP COLUMN IF EXISTS subject_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS verb;
ALTER TABLE events DROP COLUMN IF EXISTS object_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS story_time_ordinal;
ALTER TABLE events DROP COLUMN IF EXISTS narrative_order;
ALTER TABLE events DROP COLUMN IF EXISTS scene_id;
ALTER TABLE knows_edges DROP COLUMN IF EXISTS fact_id;
ALTER TABLE chapters DROP COLUMN IF EXISTS generation_meta;

-- ---- Typed state deltas: the extraction-time event log for state ----
-- Tier-2 rows: expensive LLM output, immutable, cascade-deleted with their
-- chapter. The materializer folds them into character_states and the
-- bitemporal edges; nothing else interprets prose for state.
CREATE TABLE IF NOT EXISTS state_deltas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    event_id UUID REFERENCES events(id) ON DELETE SET NULL,
    -- narrative order within the chapter; created_at can't order rows written
    -- in one transaction (now() is transaction-stable) and UUIDs are random.
    ordinal INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL CHECK (kind IN ('possession','location','knowledge','status')),
    subject_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    object_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    location_id UUID REFERENCES locations(id) ON DELETE CASCADE,
    change TEXT CHECK (change IN ('gain','loss','move','learn','update')),
    attribute TEXT,
    detail TEXT,
    certainty FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_state_deltas_chapter ON state_deltas(chapter_id);
CREATE INDEX IF NOT EXISTS idx_state_deltas_subject ON state_deltas(subject_id);

-- ---- Persisted continuity critique, one report per chapter ----
CREATE TABLE IF NOT EXISTS critique_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL UNIQUE REFERENCES chapters(id) ON DELETE CASCADE,
    passed BOOLEAN NOT NULL,
    ran_at TIMESTAMPTZ DEFAULT now(),
    stats JSONB
);
CREATE TABLE IF NOT EXISTS critique_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES critique_reports(id) ON DELETE CASCADE,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('fail','warn','info')),
    message TEXT NOT NULL,
    quote TEXT,
    evidence JSONB
);
CREATE INDEX IF NOT EXISTS idx_critique_findings_report ON critique_findings(report_id);

-- ---- Schema version (single row, stamped by init-db) ----
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT now()
);
```

- [ ] **Step 4: Stamp the version in init_db and drop generation_meta plumbing**

In `backend/pipeline/pipeline.py` — add the constant below the imports and stamp inside `init_db`:

```python
SCHEMA_VERSION = 1


def init_db(schema_path: str | None = None) -> None:
    if schema_path is None:
        schema_path = str(Path(__file__).parent / "db" / "schema.sql")
    sql = Path(schema_path).read_text(encoding="utf-8")
    sql = sql.replace("__EMBEDDING_DIM__", str(settings.embedding_dimensions))
    with DBClient() as db:
        with db.cursor(commit=True) as cur:
            cur.execute(sql)
            cur.execute("DELETE FROM schema_version")
            cur.execute(
                "INSERT INTO schema_version (version) VALUES (%s)",
                (SCHEMA_VERSION,),
            )
```

Also in `pipeline.py`: remove the `generation_meta: dict[str, Any] | None = None` parameter from `process_chapter` and the `generation_meta=generation_meta` argument in its `ingest_chapter(...)` call.

In `backend/pipeline/ingestion/ingest.py`: remove the `generation_meta` parameter, the `json.dumps(...)` argument, and the column from the INSERT (drop the now-unused `import json` if nothing else uses it):

```python
    chapter_id = db.fetchval(
        """
        INSERT INTO chapters (novel_id, number, title, raw_text, source)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (novel_id, chapter_number, title, raw_text, source),
        commit=True,
    )
```

In `backend/pipeline/generation/loop.py`: delete the `generation_meta={...}` argument from its `process_chapter(...)` call (keep `source="generated"` — generation dies in Plan 3).

In `backend/mcp_server/queries.py` (`save_chapter`): change `source="generated"` to `source="agent"` and delete the `generation_meta={"via": "mcp"}` argument; update the docstring line to `"""Ingest a finished draft as source='agent'. Never overwrites."""`.

- [ ] **Step 5: Remove code references to dropped columns**

- `backend/pipeline/db/entity_merge.py`: delete the two lines updating `events.subject_entity_id` / `events.object_entity_id` (lines 189–190).
- `backend/pipeline/state/event_replay.py` `_load_events`: remove `e.narrative_order,` from the SELECT list and change the ORDER BY to:

```sql
             ORDER BY c.number ASC,
                      e.created_at ASC,
                      e.id ASC
```

- Grep for stragglers and fix any hits (API references to `canon_facts.subject_entity_id` are fine — that column stays):

Run: `grep -rn "narrative_order\|story_time_ordinal\|temporal_constraints\|generation_meta\|events.subject_entity_id" backend --include="*.py" | grep -v ".venv" | grep -v temporal_check`
Expected after fixes: no hits outside `critic/checks/temporal_check.py` (deleted in Task 6) and its tests.

- [ ] **Step 6: Delete the migrate scripts**

```bash
git rm backend/pipeline/db/migrate_add_entity_aliases.py backend/pipeline/db/migrate_add_entities_aliases.py backend/pipeline/db/migrate_add_event_factions.py backend/pipeline/db/migrate_add_ingestion_provenance.py backend/pipeline/db/migrate_custom_entity_types.py backend/pipeline/db/migrate_entity_refactor.py
```

If the grep from the Files list found tests importing them, `git rm` those too. If `pyproject.toml` declares entry points for any migrate script, remove them.

- [ ] **Step 7: Update the spec with the attribute-column refinement**

In `docs/superpowers/specs/2026-07-11-continuum-analyzer-architecture-design.md`, in the `state_deltas` definition under **Added**, change `change CHECK (gain|loss|move|learn|update), detail TEXT` to `change CHECK (gain|loss|move|learn|update), attribute TEXT NULL (which character_states field a status delta updates), detail TEXT` and add `ordinal INTEGER NOT NULL (narrative order within the chapter)` after `event_id`.

- [ ] **Step 8: Run the new test, then the full suite**

Run: `cd backend && .venv/bin/python -m pytest pipeline/db/tests/test_schema_version.py -v`
Expected: PASS.
Run: `cd backend && .venv/bin/python -m pytest`
Expected: all green (materializer tests still pass — replay logic untouched, only the SELECT changed).

- [ ] **Step 9: Commit**

```bash
git add -A backend/pipeline backend/mcp_server docs/superpowers/specs
git commit -m "feat!: consolidated versioned schema — add state_deltas/critique tables, drop unproduced SVO/temporal surface and migrate scripts"
```

---

### Task 3: `state_deltas` extraction pass (replaces `entity_deltas`)

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py` (PASS_ORDER, PASS_SCHEMAS, PASS_TASK_INSTRUCTIONS)
- Modify: `backend/pipeline/extraction/extractor.py` (`empty_extraction`, `_normalize_extraction`, `merge_extractions`, `_compose_from_pass_payload`, `_mock_extract`)
- Test: `backend/pipeline/extraction/tests/test_extractor.py` (adapt entity_deltas assertions), new `backend/pipeline/extraction/tests/test_state_deltas_pass.py`

**Interfaces:**
- Produces: `extracted["state_deltas"]` — a list of dicts with keys `kind` (`possession|location|knowledge|status`), `character_name` (str), `object_name` (str|None), `location_name` (str|None), `change` (`gain|loss`|None), `fact` (str|None), `attribute` (`emotional_state|goals|physical_state|appearance|notes`|None), `value` (str|None), `quote` (str). Order within the list is narrative order (chunk order preserved by merge). `extracted["entity_deltas"]` no longer exists.
- Consumes: nothing from other tasks (pure extraction-layer change; `character_states` writes are removed in Task 4).

- [ ] **Step 1: Write the failing test**

Create `backend/pipeline/extraction/tests/test_state_deltas_pass.py`:

```python
"""The extractor emits typed state_deltas (replacing entity_deltas)."""

from __future__ import annotations

from pipeline.extraction.extractor import (
    ChapterExtractor,
    empty_extraction,
    merge_extractions,
)
from pipeline.extraction.prompts import PASS_ORDER, PASS_SCHEMAS


def test_pass_roster_swaps_entity_deltas_for_state_deltas():
    assert "state_deltas" in PASS_ORDER
    assert "entity_deltas" not in PASS_ORDER
    assert "state_deltas" in PASS_SCHEMAS
    assert "entity_deltas" not in PASS_SCHEMAS
    assert "state_deltas" in empty_extraction()
    assert "entity_deltas" not in empty_extraction()


def test_merge_preserves_chunk_order_of_deltas():
    a = empty_extraction()
    a["state_deltas"] = [
        {"kind": "possession", "character_name": "Aelric",
         "object_name": "silver dagger", "change": "gain", "quote": "q1"},
    ]
    b = empty_extraction()
    b["state_deltas"] = [
        {"kind": "possession", "character_name": "Aelric",
         "object_name": "silver dagger", "change": "loss", "quote": "q2"},
        {"kind": "status", "character_name": "Aelric",
         "attribute": "emotional_state", "value": "grieving", "quote": "q3"},
    ]
    merged = merge_extractions([a, b])
    kinds = [(d["kind"], d.get("change")) for d in merged["state_deltas"]]
    assert kinds == [("possession", "gain"), ("possession", "loss"), ("status", "update")]


def test_mock_extraction_emits_state_deltas():
    extractor = ChapterExtractor(use_mock=True)
    context = {"characters": [{"name": "Aelric"}], "locations": [], "open_threads": [],
               "recent_events": [], "custom_entities": {}}
    out = extractor.extract_chapter(
        chunks=["Aelric took the silver dagger and walked to Pellis Harbor."],
        context=context,
    )
    assert isinstance(out.get("state_deltas"), list)
    assert out["state_deltas"], "mock must emit at least one delta"
    for delta in out["state_deltas"]:
        assert delta["kind"] in {"possession", "location", "knowledge", "status"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest pipeline/extraction/tests/test_state_deltas_pass.py -v`
Expected: FAIL (`entity_deltas` still in PASS_ORDER / KeyError `state_deltas`).

- [ ] **Step 3: Update prompts.py**

In `PASS_ORDER`, replace `"entity_deltas"` with `"state_deltas"` (same position, after `new_entities`).

In `PASS_SCHEMAS`, replace the whole `"entity_deltas"` entry with:

```python
    "state_deltas": {
        "state_deltas": [
            {
                "kind": "possession|location|knowledge|status",
                "character_name": "string  # the character affected (or the entity moving, for location)",
                "object_name": "string|null  # possession only: the object gained/lost",
                "location_name": "string|null  # location only: where the character now is",
                "change": "gain|loss|null  # possession only",
                "fact": "string|null  # knowledge only: what the character now knows",
                "attribute": "emotional_state|goals|physical_state|appearance|notes|null  # status only",
                "value": "string|null  # status only: the new value of that attribute",
                "quote": "string  # short verbatim evidence from the chapter text",
            }
        ]
    },
```

In `PASS_TASK_INSTRUCTIONS`, add:

```python
    "state_deltas": dedent(
        """
        Extract every EXPLICIT state change in this chunk as a typed delta.
        These deltas are the machine-readable event log for character state —
        downstream code applies them literally and never re-reads the prose,
        so precision beats recall.

        KINDS
        - possession: a character gains or loses a physical object.
          Set character_name, object_name, change=gain|loss.
          The actor must be explicit in the text. "Aelric took the dagger"
          -> gain. "Aelric handed Mira the dagger" -> TWO deltas: loss for
          Aelric, gain for Mira.
        - location: a character (or significant object) arrives at / is
          established to be at a location. Set character_name (the mover)
          and location_name. Emit one delta per arrival, not per mention.
        - knowledge: a character learns something new. Set character_name
          and fact (one sentence). Only knowledge acquired IN THIS CHUNK.
        - status: a lasting change to a character's condition. Set
          character_name, attribute (one of emotional_state|goals|
          physical_state|appearance|notes) and value. Emit only when the
          text establishes a new state, not for momentary reactions.

        RULES
        - Failed or negated actions are NOT deltas ("tried to grab", "did
          not take", "refused the sword" -> nothing).
        - Hypotheticals, plans, and dialogue about actions are NOT deltas
          unless the chunk shows them happening.
        - Every delta needs a short verbatim quote as evidence.
        - Use canonical entity names from STORY CONTEXT when the chunk uses
          an alias.
        Return JSON only.
        """
    ).strip(),
```

- [ ] **Step 4: Update extractor.py**

- `empty_extraction()`: replace `"entity_deltas": [],` with `"state_deltas": [],`.
- `_normalize_extraction`: replace the `entity_deltas` block (currently reads `raw.get("entity_deltas", raw.get("character_deltas", []))`) with:

```python
    state_deltas = raw.get("state_deltas", [])
    if isinstance(state_deltas, list):
        valid_kinds = {"possession", "location", "knowledge", "status"}
        cleaned: list[dict[str, Any]] = []
        for item in state_deltas:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "")).strip().lower()
            if kind not in valid_kinds:
                continue
            item["kind"] = kind
            if kind == "possession":
                change = str(item.get("change", "")).strip().lower()
                if change not in {"gain", "loss"}:
                    continue
                item["change"] = change
            elif kind == "location":
                item["change"] = "move"
            elif kind == "knowledge":
                item["change"] = "learn"
            else:  # status
                item["change"] = "update"
                if str(item.get("attribute", "")).strip() not in {
                    "emotional_state", "goals", "physical_state", "appearance", "notes"
                }:
                    continue
            cleaned.append(item)
        output["state_deltas"] = cleaned
```

- `merge_extractions`: replace the `entity_deltas` merge (the `delta_index` logic keyed by character) with straight concatenation — order across chunks IS the narrative order the replay folds in:

```python
    merged["state_deltas"] = [
        delta
        for extraction in extractions
        for delta in extraction.get("state_deltas", [])
    ]
```

(Delete the old `delta_index` code and the `merged["entity_deltas"] = list(delta_index.values())` line.)

- `_compose_from_pass_payload`: replace the `entity_deltas` line with:

```python
            "state_deltas": pass_payload.get("state_deltas", {}).get("state_deltas", []),
```

- `_mock_extract`: replace the block that appends to `extraction["entity_deltas"]` with deterministic deltas derived from the same detected character names (keep it dependent only on the chunk text so tests are stable):

```python
            extraction["state_deltas"].append(
                {
                    "kind": "status",
                    "character_name": name,
                    "attribute": "notes",
                    "value": f"mock delta for {name}",
                    "change": "update",
                    "quote": chunk[:40],
                }
            )
            if "took" in chunk.lower() or "picked up" in chunk.lower():
                extraction["state_deltas"].append(
                    {
                        "kind": "possession",
                        "character_name": name,
                        "object_name": "silver dagger",
                        "change": "gain",
                        "quote": chunk[:40],
                    }
                )
```

- [ ] **Step 5: Adapt existing extractor tests**

Run: `cd backend && .venv/bin/python -m pytest pipeline/extraction/tests/test_extractor.py -v`
Fix every failure by updating assertions from `entity_deltas` to the new `state_deltas` shape (same semantic intent: normalization drops malformed rows; merge concatenates; mock emits deltas). Do not weaken assertions — port them.

- [ ] **Step 6: Run the new test and the extraction suite**

Run: `cd backend && .venv/bin/python -m pytest pipeline/extraction -v`
Expected: PASS except `test_process_chapter.py` may fail on the `entity_deltas` persistence path — if it does, that's Task 4's scope; check what it asserts. If it asserts `character_states` rows from `process_chapter`, mark the specific test with a `# moves to state_deltas in Task 4` skip ONLY if Task 4 lands in the same PR; otherwise proceed to Task 4 before committing (Tasks 3+4 may share one commit if the suite can't be green in between — prefer one commit covering both in that case).

- [ ] **Step 7: Commit (or hold for a joint commit with Task 4 — see Step 6)**

```bash
git add backend/pipeline/extraction
git commit -m "feat: typed state_deltas extraction pass replaces entity_deltas"
```

---

### Task 4: Persist deltas; extractor stops writing character_states

**Files:**
- Create: `backend/pipeline/extraction/persist_deltas.py`
- Modify: `backend/pipeline/pipeline.py` (`_persist_extraction`: delete the `entity_deltas`/`character_states` INSERT loop; `process_chapter`: call `persist_state_deltas` inside the transaction; return-dict key)
- Test: `backend/pipeline/extraction/tests/test_persist_deltas.py` (create), `backend/pipeline/extraction/tests/test_process_chapter.py` (adapt)

**Interfaces:**
- Consumes: `extracted["state_deltas"]` (Task 3 shape); `EntityResolver.resolve_*(..., create=False)` (Task 1); `state_deltas` table (Task 2).
- Produces: `persist_state_deltas(db, *, chapter_id: str, deltas: list[dict], resolver: EntityResolver) -> int` (rows written). Rows carry `subject_id`/`object_id` as **universal entity ids** (`entities.id`) and `location_id` as the typed `locations.id` — Task 5's replay depends on exactly this.

- [ ] **Step 1: Write the failing test**

Create `backend/pipeline/extraction/tests/test_persist_deltas.py`:

```python
"""persist_state_deltas resolves names reference-only and writes typed rows.

Hits the real branch-isolated Postgres (same idiom as test_materializer).
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.extraction.persist_deltas import persist_state_deltas
from pipeline.extraction.resolver import EntityResolver


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(db: DBClient):
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    chapter_id = db.fetchval(
        "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s, 1, 'x') RETURNING id",
        (novel_id,), commit=True,
    )
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=1)
    resolver.resolve_character("Aelric", {"description": "a knight"})
    resolver.resolve_object("silver dagger", {"description": "a blade"})
    resolver.resolve_location("Pellis Harbor", {"description": "a port"})
    yield {"novel_id": novel_id, "chapter_id": str(chapter_id), "resolver": resolver}
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_persists_each_kind_and_drops_unknown_names(db: DBClient, seeded):
    deltas = [
        {"kind": "possession", "character_name": "Aelric", "object_name": "silver dagger",
         "change": "gain", "quote": "he took it"},
        {"kind": "location", "character_name": "Aelric", "location_name": "Pellis Harbor",
         "change": "move", "quote": "he arrived"},
        {"kind": "knowledge", "character_name": "Aelric", "fact": "the harbor is watched",
         "change": "learn", "quote": "he realized"},
        {"kind": "status", "character_name": "Aelric", "attribute": "emotional_state",
         "value": "wary", "change": "update", "quote": "wary now"},
        # Unknown character: dropped, never minted (reference-only).
        {"kind": "status", "character_name": "Sword Skill Lv.3", "attribute": "notes",
         "value": "x", "change": "update", "quote": "q"},
        # Possession of an unknown object: dropped.
        {"kind": "possession", "character_name": "Aelric", "object_name": "unknown orb",
         "change": "gain", "quote": "q"},
    ]
    written = persist_state_deltas(
        db, chapter_id=seeded["chapter_id"], deltas=deltas, resolver=seeded["resolver"]
    )
    assert written == 4
    rows = db.fetchall(
        "SELECT kind, change, attribute, detail, ordinal FROM state_deltas WHERE chapter_id = %s ORDER BY ordinal",
        (seeded["chapter_id"],), dict_rows=True,
    )
    assert [r["kind"] for r in rows] == ["possession", "location", "knowledge", "status"]
    assert [r["ordinal"] for r in rows] == [0, 1, 2, 3]
    assert rows[2]["detail"] == "the harbor is watched"
    assert rows[3]["attribute"] == "emotional_state" and rows[3]["detail"] == "wary"
    # No phantom character was created for the game-system name.
    assert db.fetchval(
        "SELECT count(*) FROM characters WHERE novel_id = %s", (seeded["novel_id"],)
    ) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest pipeline/extraction/tests/test_persist_deltas.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.extraction.persist_deltas`.

- [ ] **Step 3: Implement persist_deltas.py**

```python
"""Persist typed state deltas — the tier-2 state log the materializer replays.

Reference-only resolution throughout: a delta naming an unknown entity is
dropped (with a warning), never minted. subject_id/object_id are universal
entity ids; location_id is the typed locations.id (matching the FK).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def persist_state_deltas(
    db: Any, *, chapter_id: str, deltas: list[dict], resolver: Any
) -> int:
    written = 0
    for delta in deltas or []:
        if not isinstance(delta, dict):
            continue
        kind = str(delta.get("kind", "")).strip().lower()
        character_name = str(delta.get("character_name", "")).strip()
        if not kind or not character_name:
            continue
        subject = resolver.resolve_character(character_name, create=False)
        if subject is None:
            logger.warning("state_delta: unknown character %r — dropped", character_name)
            continue

        object_id = location_id = None
        attribute = detail = None
        change = str(delta.get("change", "")).strip().lower() or None

        if kind == "possession":
            resolved_obj = resolver.resolve_object(
                str(delta.get("object_name", "")).strip(), create=False
            ) if str(delta.get("object_name", "")).strip() else None
            if resolved_obj is None or change not in {"gain", "loss"}:
                logger.warning("state_delta: unresolvable possession %r — dropped", delta)
                continue
            object_id = resolved_obj.universal_id
        elif kind == "location":
            resolved_loc = resolver.resolve_location(
                str(delta.get("location_name", "")).strip(), create=False
            ) if str(delta.get("location_name", "")).strip() else None
            if resolved_loc is None:
                logger.warning("state_delta: unresolvable location %r — dropped", delta)
                continue
            location_id = resolved_loc.entity_id
            change = "move"
        elif kind == "knowledge":
            detail = str(delta.get("fact", "")).strip()
            if not detail:
                continue
            change = "learn"
        elif kind == "status":
            attribute = str(delta.get("attribute", "")).strip()
            detail = str(delta.get("value", "")).strip()
            if attribute not in {"emotional_state", "goals", "physical_state", "appearance", "notes"} or not detail:
                continue
            change = "update"
        else:
            continue

        db.execute(
            """
            INSERT INTO state_deltas (
                chapter_id, ordinal, kind, subject_id, object_id, location_id,
                change, attribute, detail, certainty
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                chapter_id, written, kind, subject.universal_id, object_id,
                location_id, change, attribute, detail,
                float(delta.get("certainty") or 1.0),
            ),
        )
        written += 1
    return written


__all__ = ["persist_state_deltas"]
```

- [ ] **Step 4: Wire into the transaction; delete the character_states write**

In `backend/pipeline/pipeline.py`:

(a) Add the import: `from pipeline.extraction.persist_deltas import persist_state_deltas`.

(b) In `_persist_extraction`, DELETE the entire `for delta in extracted.get("entity_deltas", []):` loop (the block ending with the `INSERT INTO character_states ...` execute — pipeline.py lines ~460–508).

(c) In `process_chapter`, inside the `with client.session() as s:` block, after `persist_canon_facts(...)`, add:

```python
            persist_state_deltas(
                s,
                chapter_id=chapter_id,
                deltas=extracted.get("state_deltas", []),
                resolver=resolver,
            )
```

(d) In the return dict, replace nothing existing; add `"state_deltas": len(extracted.get("state_deltas", [])),`.

- [ ] **Step 5: Adapt test_process_chapter.py**

Run: `cd backend && .venv/bin/python -m pytest pipeline/extraction/tests/test_process_chapter.py -v`
Port any assertion that expected `character_states` rows written by `process_chapter`'s persistence phase: `character_states` rows now appear only via the materializer (which `process_chapter` still runs at the end — so end-to-end assertions on `character_states` should still pass AFTER Task 5; until then they reflect old replay behavior and should keep passing since the materializer still runs). Fix only what actually fails; do not delete coverage.

- [ ] **Step 6: Run the suite**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline
git commit -m "feat: persist typed state_deltas; extractor no longer writes character_states"
```

---

### Task 5: Replay folds deltas — materializer becomes the sole writer

**Files:**
- Create: `backend/pipeline/state/replay.py` (replaces `event_replay.py`)
- Delete: `backend/pipeline/state/event_replay.py`
- Modify: `backend/pipeline/state/materializer.py` (import; `_write_character_states` becomes novel-scoped delete)
- Modify: `backend/pipeline/state/types.py` (no changes to dataclasses; confirm)
- Test: `backend/pipeline/state/tests/test_materializer.py` (rewrite seeds to state_deltas), `backend/pipeline/state/tests/test_process_chapter_materializes.py` (adapt)

**Interfaces:**
- Consumes: `state_deltas` rows as written by Task 4 (`subject_id`/`object_id` universal entity ids, `location_id` typed).
- Produces: `StateReplay(db).replay(novel_id, through_chapter) -> tuple[list[StateSnapshot], list[LocationFact], list[PossessionFact]]` — same return types as the old `EventReplay.replay`, so `StateMaterializer.materialize` keeps its signature and `MaterializeResult` unchanged. `character_states` is now written ONLY by the materializer.

- [ ] **Step 1: Rewrite the materializer test seeds**

In `backend/pipeline/state/tests/test_materializer.py`, replace the event seeding in the `seeded` fixture with `state_deltas` seeding telling the same story (keep the novel/chapter/entity seeding; the typed rows for characters/locations/objects must set `entity_id` to the universal id, as the existing fixture already does):

```python
        # story as typed deltas:
        #   ch1: Aelric at Fogwood Keep (location)
        #   ch2: Aelric gains silver dagger (possession)
        #   ch3: Aelric moves to Pellis Harbor; Mira at Pellis Harbor;
        #        Aelric loses silver dagger; Aelric wary (status)
        ordinal_counter = {"n": 0}

        def add_delta(chapter_ix, kind, subject_eid, *, object_eid=None,
                      location_typed_id=None, change=None, attribute=None, detail=None):
            cur.execute(
                """
                INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                          object_id, location_id, change, attribute, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (chapter_ids[chapter_ix], ordinal_counter["n"], kind, subject_eid,
                 object_eid, location_typed_id, change, attribute, detail),
            )
            ordinal_counter["n"] += 1

        add_delta(0, "location", aelric_eid, location_typed_id=keep_lid, change="move")
        add_delta(1, "possession", aelric_eid, object_eid=dagger_eid, change="gain")
        add_delta(2, "location", aelric_eid, location_typed_id=harbor_lid, change="move")
        add_delta(2, "location", mira_eid, location_typed_id=harbor_lid, change="move")
        add_delta(2, "possession", aelric_eid, object_eid=dagger_eid, change="loss")
        add_delta(2, "status", aelric_eid, change="update",
                  attribute="emotional_state", detail="wary")
        add_delta(2, "knowledge", aelric_eid, change="learn",
                  detail="the harbor is watched")
```

(`keep_lid`/`harbor_lid` are the typed `locations.id` values the fixture already creates; `dagger_eid` is the object's universal entity id.) Keep/port the existing assertions: snapshots per (character, chapter), location edge chaining (`until_chapter` + `superseded_by_id` when Aelric moves), possession open→closed on loss, idempotent re-run. Add two new assertions:

```python
def test_status_and_knowledge_fold_into_snapshots(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], 3)
    row = db.fetchone(
        """
        SELECT cs.emotional_state, cs.knowledge FROM character_states cs
          JOIN chapters ch ON ch.id = cs.chapter_id
         WHERE cs.character_id = %s AND ch.number = 3
        """,
        (seeded["aelric_char_id"],),
    )
    assert row[0] == "wary"
    assert "the harbor is watched" in (row[1] or [])


def test_materializer_is_sole_writer_and_prunes_stale_snapshots(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], 3)
    # A snapshot for a (character, chapter) pair with no surviving deltas must
    # not survive a re-materialize (novel-scoped rebuild).
    db.execute("DELETE FROM state_deltas WHERE detail = 'wary'")
    StateMaterializer(db).materialize(seeded["novel_id"], 3)
    row = db.fetchone(
        """
        SELECT cs.emotional_state FROM character_states cs
          JOIN chapters ch ON ch.id = cs.chapter_id
         WHERE cs.character_id = %s AND ch.number = 3
        """,
        (seeded["aelric_char_id"],),
    )
    assert row is None or row[0] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest pipeline/state/tests/test_materializer.py -v`
Expected: FAIL (replay still reads `events`, ignores `state_deltas`).

- [ ] **Step 3: Write replay.py**

Create `backend/pipeline/state/replay.py`. Full replacement for `event_replay.py` — no verb lists, no `EMOTIONAL_HINTS`, no `character_states` seeding:

```python
"""Fold typed state_deltas (chapter order, insertion order) into projections.

Read-only: produces snapshots/facts; the materializer persists them.
subject_id/object_id in state_deltas are universal entity ids; this module
maps them to typed character/object ids where the projection tables need
those. Deltas naming entities that no longer resolve are skipped.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from pipeline.db.client import DBClient
from pipeline.state.types import LocationFact, PossessionFact, StateSnapshot

logger = logging.getLogger(__name__)

_STATUS_FIELDS = {"emotional_state", "goals", "physical_state", "appearance", "notes"}


class StateReplay:
    def __init__(self, db: DBClient) -> None:
        self.db = db

    def replay(
        self, novel_id: str, through_chapter: int
    ) -> tuple[list[StateSnapshot], list[LocationFact], list[PossessionFact]]:
        chapters = self.db.fetchall(
            "SELECT id, number FROM chapters WHERE novel_id = %s AND number <= %s ORDER BY number",
            (novel_id, through_chapter), dict_rows=True,
        )
        if not chapters:
            return [], [], []
        chapter_number_by_id = {str(r["id"]): r["number"] for r in chapters}
        chapter_id_by_number = {r["number"]: str(r["id"]) for r in chapters}

        char_by_entity = {
            str(r["entity_id"]): str(r["id"])
            for r in self.db.fetchall(
                "SELECT id, entity_id FROM characters WHERE novel_id = %s AND entity_id IS NOT NULL",
                (novel_id,), dict_rows=True,
            )
        }
        object_by_entity = {
            str(r["entity_id"]): str(r["id"])
            for r in self.db.fetchall(
                "SELECT id, entity_id FROM objects WHERE novel_id = %s AND entity_id IS NOT NULL",
                (novel_id,), dict_rows=True,
            )
        }

        deltas = self.db.fetchall(
            """
            SELECT d.kind, d.subject_id, d.object_id, d.location_id, d.change,
                   d.attribute, d.detail, d.certainty, d.event_id, d.chapter_id
              FROM state_deltas d
              JOIN chapters c ON c.id = d.chapter_id
             WHERE c.novel_id = %s AND c.number <= %s
             ORDER BY c.number ASC, d.ordinal ASC, d.id ASC
            """,
            (novel_id, through_chapter), dict_rows=True,
        )

        # snapshots[character_id][chapter_number] = StateSnapshot
        snapshots: dict[str, dict[int, StateSnapshot]] = {}
        active_location: dict[str, LocationFact] = {}
        location_facts: list[LocationFact] = []
        active_possession: dict[tuple[str, str], PossessionFact] = {}
        possession_facts: list[PossessionFact] = []

        def snapshot_for(character_id: str, chapter_number: int) -> StateSnapshot:
            per_char = snapshots.setdefault(character_id, {})
            if chapter_number not in per_char:
                prior = None
                for n in sorted(per_char):
                    if n < chapter_number:
                        prior = per_char[n]
                per_char[chapter_number] = StateSnapshot(
                    character_id=character_id,
                    chapter_id=chapter_id_by_number[chapter_number],
                    chapter_number=chapter_number,
                    location_id=prior.location_id if prior else None,
                    goals=prior.goals if prior else None,
                    knowledge=list(prior.knowledge) if prior else [],
                    physical_state=prior.physical_state if prior else None,
                    appearance=prior.appearance if prior else None,
                )
            return per_char[chapter_number]

        for d in deltas:
            chapter_number = chapter_number_by_id.get(str(d["chapter_id"]))
            if chapter_number is None:
                continue
            subject_entity = str(d["subject_id"])
            character_id = char_by_entity.get(subject_entity)
            kind = d["kind"]

            if kind == "location" and d["location_id"] is not None:
                location_id = str(d["location_id"])
                current = active_location.get(subject_entity)
                if current is None or current.location_id != location_id:
                    fact = LocationFact(
                        entity_id=subject_entity,
                        location_id=location_id,
                        since_chapter=chapter_number,
                        evidence_event_id=str(d["event_id"]) if d["event_id"] else None,
                        certainty=float(d["certainty"] or 1.0),
                    )
                    active_location[subject_entity] = fact
                    location_facts.append(fact)
                if character_id is not None:
                    snap = snapshot_for(character_id, chapter_number)
                    snapshots[character_id][chapter_number] = replace(
                        snap, location_id=location_id
                    )

            elif kind == "possession" and character_id is not None and d["object_id"]:
                object_id = object_by_entity.get(str(d["object_id"]))
                if object_id is None:
                    continue
                key = (character_id, object_id)
                if d["change"] == "gain":
                    if key not in active_possession:
                        fact = PossessionFact(
                            character_id=character_id,
                            object_id=object_id,
                            since_chapter=chapter_number,
                            evidence_event_id=str(d["event_id"]) if d["event_id"] else None,
                            certainty=float(d["certainty"] or 1.0),
                        )
                        active_possession[key] = fact
                        possession_facts.append(fact)
                elif d["change"] == "loss":
                    existing = active_possession.pop(key, None)
                    if existing is not None:
                        closed = replace(existing, until_chapter=chapter_number)
                        for i in range(len(possession_facts) - 1, -1, -1):
                            f = possession_facts[i]
                            if (f.character_id, f.object_id, f.since_chapter, f.until_chapter) == (
                                existing.character_id, existing.object_id,
                                existing.since_chapter, None,
                            ):
                                possession_facts[i] = closed
                                break

            elif kind == "knowledge" and character_id is not None and d["detail"]:
                snap = snapshot_for(character_id, chapter_number)
                if d["detail"] not in snap.knowledge:
                    snapshots[character_id][chapter_number] = replace(
                        snap, knowledge=list(snap.knowledge) + [d["detail"]]
                    )

            elif kind == "status" and character_id is not None:
                attribute, value = d["attribute"], d["detail"]
                if attribute in _STATUS_FIELDS and value:
                    snap = snapshot_for(character_id, chapter_number)
                    snapshots[character_id][chapter_number] = replace(
                        snap, **{attribute: value}
                    )

        flat = [s for per_char in snapshots.values() for s in per_char.values()]
        return flat, location_facts, possession_facts


__all__ = ["StateReplay"]
```

Note: `emotional_state` intentionally does NOT carry forward between chapters (matches old behavior — it's event-driven); `location/goals/knowledge/physical_state/appearance` do.

- [ ] **Step 4: Update the materializer**

In `backend/pipeline/state/materializer.py`:
- Change the import to `from pipeline.state.replay import StateReplay` and `self.replay = StateReplay(db)`.
- Replace `_write_character_states`'s pair-scoped DELETE with a novel-scoped one (sole-writer semantics — stale snapshots must not survive), so the method starts:

```python
    def _write_character_states(
        self, cur: Any, novel_id: str, snapshots: list[StateSnapshot]
    ) -> int:
        # Sole writer: rebuild the whole novel's snapshot projection so
        # snapshots whose source deltas disappeared don't survive.
        cur.execute(
            """
            DELETE FROM character_states
             WHERE character_id IN (SELECT id FROM characters WHERE novel_id = %s)
            """,
            (novel_id,),
        )
        if not snapshots:
            return 0
        for snap in snapshots:
            ...  # existing INSERT loop unchanged
```

Update its call site to pass `novel_id`: `self._write_character_states(cur, novel_id, snapshots)`.

- Delete `backend/pipeline/state/event_replay.py` (`git rm`). Update `backend/pipeline/state/cli.py` if it imports `EventReplay` (grep: `grep -rn "event_replay\|EventReplay" backend --include="*.py" | grep -v .venv`).

- [ ] **Step 5: Run state tests, adapt test_process_chapter_materializes.py**

Run: `cd backend && .venv/bin/python -m pytest pipeline/state -v`
`test_process_chapter_materializes.py` seeds via mock-LLM `process_chapter`; the mock now emits `state_deltas` (Task 3) which persist (Task 4) and materialize here — port its assertions to the delta-driven expectations (mock emits a `status` delta per detected character + a possession gain when the text contains "took").

- [ ] **Step 6: Full suite**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add -A backend/pipeline/state backend/pipeline
git commit -m "feat: replay folds typed state_deltas; materializer is the sole writer of character_states"
```

---

### Task 6: Critic — extraction adapter, drop the temporal check, relocate draft_claims

**Files:**
- Create: `backend/pipeline/critic/adapter.py` (move of `generation/draft_claims.py` + new `build_draft_from_extraction`)
- Delete: `backend/pipeline/generation/draft_claims.py`, `backend/pipeline/critic/checks/temporal_check.py`
- Modify: `backend/pipeline/critic/checks/__init__.py` (drop the temporal export), `backend/pipeline/critic/runner.py` (drop the temporal call + import), `backend/pipeline/generation/loop.py` + `backend/mcp_server/queries.py` (import from `pipeline.critic.adapter`)
- Test: `backend/pipeline/critic/tests/test_adapter.py` (create), `backend/pipeline/critic/tests/test_critic.py` (remove temporal-check tests)

**Interfaces:**
- Consumes: `extracted` dict (Task 3 shape: `state_deltas`, `learnings`, `canon_facts`, `events` keys), `lookup_typed(db, novel_id, entity_type, name) -> tuple[typed_id, universal_id] | None` from `pipeline.extraction.resolver`.
- Produces:
  - `build_draft_from_extraction(db, *, novel_id: str, chapter_number: int, text: str, extracted: dict) -> DraftChapter` — no LLM calls; reuses extraction output.
  - `extract_draft_claims` / `build_draft_chapter` re-exported unchanged from `pipeline.critic.adapter` (MCP `check_continuity` keeps using them on unsaved prose).
  - `ContinuityCritic.critique` now runs 5 checks (temporal gone).

- [ ] **Step 1: Write the failing test**

Create `backend/pipeline/critic/tests/test_adapter.py`:

```python
"""build_draft_from_extraction maps extraction output to critic claims
without LLM calls; unknown names drop claims read-only."""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic.adapter import build_draft_from_extraction
from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(db: DBClient):
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=1)
    resolver.resolve_character("Aelric", {})
    resolver.resolve_object("silver dagger", {})
    resolver.resolve_location("Pellis Harbor", {})
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_maps_extraction_to_claims(db: DBClient, seeded):
    extracted = {
        "state_deltas": [
            {"kind": "possession", "character_name": "Aelric",
             "object_name": "silver dagger", "change": "gain", "quote": "took it"},
            {"kind": "location", "character_name": "Aelric",
             "location_name": "Pellis Harbor", "change": "move", "quote": "arrived"},
            {"kind": "possession", "character_name": "Nobody",
             "object_name": "silver dagger", "change": "gain", "quote": "q"},
        ],
        "learnings": [
            {"character_name": "Aelric", "fact_description": "the harbor is watched",
             "source_type": "observation"},
        ],
        "canon_facts": [
            {"subject_name": "Aelric", "subject_type": "character",
             "predicate": "eye_color", "value": "grey", "quote": "grey eyes"},
        ],
        "events": [{"description": "Aelric arrives", "event_type": "arrival"}],
    }
    draft = build_draft_from_extraction(
        db, novel_id=seeded, chapter_number=2, text="prose", extracted=extracted
    )
    assert len(draft.possession_claims) == 1      # unknown character dropped
    assert len(draft.location_claims) == 1
    assert len(draft.knowledge_claims) == 1
    assert draft.knowledge_claims[0]["learned_this_chapter"] is True
    assert len(draft.mentions) == 1
    assert draft.mentions[0]["predicate"] == "eye_color"
    assert len(draft.events) == 1
    assert draft.planned_thread_ids == [] and draft.planned_commitment_ids == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest pipeline/critic/tests/test_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.critic.adapter`.

- [ ] **Step 3: Create adapter.py**

`git mv backend/pipeline/generation/draft_claims.py backend/pipeline/critic/adapter.py`, then append to it:

```python
def build_draft_from_extraction(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    text: str,
    extracted: dict[str, Any],
) -> DraftChapter:
    """Adapter for the spine's critique phase: reuse the chapter's already-
    extracted claims instead of paying a second claims-extraction LLM call.
    Read-only name resolution; unknown names drop the claim."""
    raw_claims = {
        "mentions": [
            {
                "entity_name": f.get("subject_name"),
                "entity_type": f.get("subject_type"),
                "predicate": f.get("predicate"),
                "claimed_value": f.get("value"),
                "quote": f.get("quote"),
            }
            for f in extracted.get("canon_facts", [])
            if isinstance(f, dict)
        ],
        "knowledge_claims": [
            {
                "character_name": l.get("character_name"),
                "fact_description": l.get("fact_description"),
                "source_type": l.get("source_type"),
                "learned_this_chapter": True,
                "quote": None,
            }
            for l in extracted.get("learnings", [])
            if isinstance(l, dict)
        ],
        "location_claims": [
            {
                "character_name": d.get("character_name"),
                "location_name": d.get("location_name"),
                "quote": d.get("quote"),
            }
            for d in extracted.get("state_deltas", [])
            if isinstance(d, dict) and d.get("kind") == "location"
        ],
        "possession_claims": [
            {
                "character_name": d.get("character_name"),
                "object_name": d.get("object_name"),
                "quote": d.get("quote"),
            }
            for d in extracted.get("state_deltas", [])
            if isinstance(d, dict)
            and d.get("kind") == "possession"
            and d.get("change") == "gain"
        ],
        "events": [
            {"description": e.get("description"), "event_type": e.get("event_type")}
            for e in extracted.get("events", [])
            if isinstance(e, dict)
        ],
    }
    return build_draft_chapter(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        text=text,
        raw_claims=raw_claims,
        planned_thread_ids=[],
        planned_commitment_ids=[],
    )
```

Add `"build_draft_from_extraction"` to `__all__`. Update the two import sites (`generation/loop.py`, `mcp_server/queries.py` — confirm with `grep -rn "draft_claims" backend --include="*.py" | grep -v .venv`) to `from pipeline.critic.adapter import ...`.

- [ ] **Step 4: Drop the temporal check**

- `git rm backend/pipeline/critic/checks/temporal_check.py`
- In `backend/pipeline/critic/checks/__init__.py`: remove the `check_temporal_consistency` import/export.
- In `backend/pipeline/critic/runner.py`: remove `check_temporal_consistency` from the import and delete the `report.findings.extend(check_temporal_consistency(...))` block; update the module/class docstrings from 6 checks to 5.
- In `backend/pipeline/critic/tests/test_critic.py`: delete tests exercising the temporal check (grep `temporal` in the file).

- [ ] **Step 5: Run critic tests + full suite**

Run: `cd backend && .venv/bin/python -m pytest pipeline/critic -v && cd backend && .venv/bin/python -m pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A backend/pipeline docs
git commit -m "feat: critic adapter builds DraftChapter from extraction output; retire hollow temporal check"
```

---

### Task 7: Persist critique + wire phase 5; rename to analyze_chapter

**Files:**
- Create: `backend/pipeline/critic/persist.py`
- Modify: `backend/pipeline/pipeline.py` (phase 5 after materialize; rename `process_chapter` → `analyze_chapter`; result keys)
- Modify callers of `process_chapter`: find with `grep -rn "process_chapter" backend --include="*.py" | grep -v .venv | grep -v test` (expected: `pipeline.py` CLI `main`, `api/jobs.py` and/or `api/routes/process.py`, `mcp_server/queries.py`, `generation/loop.py`) — plus test callers.
- Modify: `docs/architecture.html` (spine now runs critique per chapter; function rename)
- Test: `backend/pipeline/critic/tests/test_persist_critique.py` (create), `backend/pipeline/extraction/tests/test_process_chapter.py` (critique keys)

**Interfaces:**
- Consumes: `CritiqueReport`/`Finding`/`Severity` (`pipeline.critic.types`), `build_draft_from_extraction` (Task 6), `critique_reports`/`critique_findings` tables (Task 2).
- Produces:
  - `persist_critique(db, *, chapter_id: str, report: CritiqueReport) -> str` (report id; replaces any prior report for the chapter).
  - `analyze_chapter(...)` — same params as `process_chapter` (minus `generation_meta`, removed in Task 2); result dict gains `"critique": {...report.summary()...} | None` and `"materialized": bool`. Plan 2's read layer will read `critique_reports` for the wiki.

- [ ] **Step 1: Write the failing test**

Create `backend/pipeline/critic/tests/test_persist_critique.py`:

```python
"""persist_critique writes one report per chapter, replacing prior runs."""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic.persist import persist_critique
from pipeline.critic.types import CritiqueReport, Finding, Severity
from pipeline.db.client import DBClient


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def chapter(db: DBClient):
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    chapter_id = db.fetchval(
        "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s, 1, 'x') RETURNING id",
        (novel_id,), commit=True,
    )
    yield {"novel_id": novel_id, "chapter_id": str(chapter_id)}
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_persists_and_replaces(db: DBClient, chapter):
    report = CritiqueReport(novel_id=chapter["novel_id"], chapter_number=1)
    report.findings.append(Finding(
        check="knowledge_state", severity=Severity.FAIL,
        message="Aelric acts on unknown fact", quote="he knew",
        context={"fact": "the harbor is watched"},
    ))
    persist_critique(db, chapter_id=chapter["chapter_id"], report=report)

    row = db.fetchone(
        "SELECT passed FROM critique_reports WHERE chapter_id = %s", (chapter["chapter_id"],)
    )
    assert row is not None and row[0] is False
    findings = db.fetchall(
        """
        SELECT f.check_name, f.severity, f.message, f.quote
          FROM critique_findings f
          JOIN critique_reports r ON r.id = f.report_id
         WHERE r.chapter_id = %s
        """,
        (chapter["chapter_id"],), dict_rows=True,
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "fail"

    # Re-run with a clean report: old findings replaced, passed flips.
    persist_critique(
        db, chapter_id=chapter["chapter_id"],
        report=CritiqueReport(novel_id=chapter["novel_id"], chapter_number=1),
    )
    assert db.fetchval(
        "SELECT passed FROM critique_reports WHERE chapter_id = %s", (chapter["chapter_id"],)
    ) is True
    assert db.fetchval(
        """
        SELECT count(*) FROM critique_findings f
          JOIN critique_reports r ON r.id = f.report_id
         WHERE r.chapter_id = %s
        """,
        (chapter["chapter_id"],),
    ) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest pipeline/critic/tests/test_persist_critique.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.critic.persist`.

- [ ] **Step 3: Implement persist.py**

```python
"""Persist a CritiqueReport: one report row per chapter, findings replaced."""

from __future__ import annotations

import json
from typing import Any

from pipeline.critic.types import CritiqueReport


def persist_critique(db: Any, *, chapter_id: str, report: CritiqueReport) -> str:
    with db.transaction() as cur:
        cur.execute("DELETE FROM critique_reports WHERE chapter_id = %s", (chapter_id,))
        cur.execute(
            """
            INSERT INTO critique_reports (chapter_id, passed, stats)
            VALUES (%s, %s, %s::jsonb) RETURNING id
            """,
            (chapter_id, report.passed, json.dumps(report.summary())),
        )
        report_id = str(cur.fetchone()[0])
        for finding in report.findings:
            cur.execute(
                """
                INSERT INTO critique_findings (
                    report_id, check_name, severity, message, quote, evidence
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    report_id,
                    finding.check,
                    finding.severity.value.lower(),
                    finding.message,
                    finding.quote,
                    json.dumps(finding.context, default=str),
                ),
            )
    return report_id


__all__ = ["persist_critique"]
```

Note: `db` here is a DBClient (has `.transaction()`), not a session — phase 5 runs outside the persistence transaction, like the materializer.

- [ ] **Step 4: Wire phase 5 and rename**

In `backend/pipeline/pipeline.py`:

(a) Imports:

```python
from pipeline.critic.adapter import build_draft_from_extraction
from pipeline.critic.persist import persist_critique
from pipeline.critic.runner import ContinuityCritic
```

(b) Rename `def process_chapter(` to `def analyze_chapter(` and replace the materializer block at the end with phases 4+5 (derived-and-idempotent: failures don't unsave the chapter):

```python
        # ---- phase 4: MATERIALIZE / phase 5: CRITIQUE ----
        # Both derive from the committed data and are idempotent; a failure
        # here must not roll back the saved chapter. materialized/critique in
        # the result tell callers whether a manual re-run is needed.
        materialized = False
        critique_summary: dict[str, Any] | None = None
        try:
            max_chapter = client.fetchval(
                "SELECT COALESCE(MAX(number), %s) FROM chapters WHERE novel_id = %s",
                (chapter_number, novel_id),
            )
            StateMaterializer(client).materialize(novel_id, int(max_chapter))
            materialized = True
        except Exception:
            logger.exception("materialize failed for novel %s; re-run pipeline.state.cli", novel_id)
        try:
            draft = build_draft_from_extraction(
                client,
                novel_id=novel_id,
                chapter_number=chapter_number,
                text=raw_text,
                extracted=extracted,
            )
            report = ContinuityCritic(client).critique(draft)
            persist_critique(client, chapter_id=chapter_id, report=report)
            critique_summary = report.summary()
        except Exception:
            logger.exception("critique failed for chapter %s of novel %s", chapter_number, novel_id)
```

(c) Extend the return dict:

```python
            "materialized": materialized,
            "critique": critique_summary,
```

(d) Update every caller found by the grep in the Files list from `process_chapter` to `analyze_chapter` (CLI `main`, API job runner, `mcp_server/queries.py` `save_chapter`, `generation/loop.py`, and test imports). No alias is kept.

- [ ] **Step 5: End-to-end assertion**

In `backend/pipeline/extraction/tests/test_process_chapter.py`, add to an existing mock-LLM end-to-end test (or create one following the file's fixtures):

```python
    result = analyze_chapter(...)  # existing invocation, renamed
    assert result["materialized"] is True
    assert result["critique"] is not None and "passed" in result["critique"]
    # A persisted report exists for the chapter.
    assert db.fetchval(
        "SELECT count(*) FROM critique_reports WHERE chapter_id = %s",
        (result["chapter_id"],),
    ) == 1
```

- [ ] **Step 6: Full suite + docs**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all green.
Update `docs/architecture.html`: the pipeline section's phase list gains "5 · CRITIQUE — persisted per chapter"; rename `process_chapter` mentions to `analyze_chapter`.

- [ ] **Step 7: Commit**

```bash
git add -A backend docs/architecture.html
git commit -m "feat: analyze_chapter runs and persists a continuity critique for every chapter"
```

---

## Plan self-review (done at write time)

- **Spec coverage (steps 1–5):** phantom fix → Task 1; schema consolidation + version → Task 2; state_deltas pass + persistence → Tasks 3–4; sole-writer materializer + heuristic deletion → Task 5; critic in spine + persistence + adapter relocation + temporal-check removal → Tasks 6–7. Spec steps 6–10 are Plans 2–3 by design.
- **Deferred intentionally:** `save_chapter` docstring already updated in Task 2; generation loop keeps compiling until Plan 3 deletes it.
- **Type consistency:** `persist_state_deltas` writes universal ids for subject/object and typed id for location; `StateReplay` consumes exactly that (maps universal→typed via `characters/objects.entity_id`). `ResolvedEntity.entity_id` = typed-table id, `.universal_id` = `entities.id` (verified against `resolver.py`).
