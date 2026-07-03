# Project 1: Atomic & Replayable Ingestion + Provenance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chapter persistence becomes all-or-nothing, chapters can be re-processed with `--replace`, and every chapter row carries provenance (`source`, `generation_meta`).

**Architecture:** A new `DBSession` exposes the same query API as `DBClient` but pins a single pooled connection so all persistence statements share one transaction (LLM extraction stays outside it). `process_chapter` gains `replace` and `db` parameters; replace deletes the chapter's derived rows inside the same transaction before re-inserting. A migration adds `chapters.source`/`chapters.generation_meta` and `relationships.chapter_id` (which makes relationship rows traceable and cascade-deletable).

**Tech Stack:** Python 3.11, psycopg3 + psycopg_pool, pytest (fakes, no live DB needed except one manual verification step).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/pipeline/db/client.py` | Add `DBSession` + `DBClient.session()` |
| Create | `backend/pipeline/db/tests/__init__.py` | test package |
| Create | `backend/pipeline/db/tests/test_session.py` | session unit tests |
| Create | `backend/pipeline/db/migrate_add_ingestion_provenance.py` | migration |
| Modify | `backend/pipeline/db/schema.sql` | new columns |
| Modify | `backend/pipeline/ingestion/ingest.py` | provenance params, `delete_chapter_data` |
| Create | `backend/pipeline/ingestion/tests/__init__.py` | test package |
| Create | `backend/pipeline/ingestion/tests/test_ingest.py` | ingest/delete tests |
| Modify | `backend/pipeline/pipeline.py` | transactional `process_chapter`, `replace`, `db`, `source` params |
| Modify | `backend/api/schemas.py` | `ProcessRequest.replace` |
| Modify | `backend/api/routes/process.py` | pass `replace` |
| Modify | `backend/api/jobs.py` | thread `replace` through `submit_job` |
| Modify | `docs/architecture.html`, `docs/reference.html` | docs |

---

## Task 1: `DBSession` and `DBClient.session()`

**Files:**
- Modify: `backend/pipeline/db/client.py`
- Create: `backend/pipeline/db/tests/__init__.py` (empty)
- Test: `backend/pipeline/db/tests/test_session.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/db/tests/__init__.py` (empty file), then `backend/pipeline/db/tests/test_session.py`:

```python
from __future__ import annotations

from contextlib import contextmanager

import pytest

from pipeline.db.client import DBClient, DBSession


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        self._conn.statements.append((query, params))

    def fetchone(self):
        return self._conn.next_row

    def fetchall(self):
        return list(self._conn.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class FakeConn:
    def __init__(self):
        self.statements: list = []
        self.committed = 0
        self.rolled_back = 0
        self.next_row = ("value",)
        self.rows: list = []

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def connection(self):
        yield self._conn


def _client_with(conn: FakeConn) -> DBClient:
    client = DBClient.__new__(DBClient)  # skip __init__ (no real pool)
    client._pool = FakePool(conn)
    return client


def test_session_shares_one_connection_and_commits_once():
    conn = FakeConn()
    client = _client_with(conn)
    with client.session() as s:
        s.execute("INSERT 1", (1,))
        s.execute("INSERT 2", (2,))
        assert s.fetchval("SELECT x") == "value"
    assert [q for q, _ in conn.statements] == ["INSERT 1", "INSERT 2", "SELECT x"]
    assert conn.committed == 1
    assert conn.rolled_back == 0


def test_session_rolls_back_on_exception():
    conn = FakeConn()
    client = _client_with(conn)
    with pytest.raises(RuntimeError):
        with client.session() as s:
            s.execute("INSERT 1", None)
            raise RuntimeError("boom")
    assert conn.committed == 0
    assert conn.rolled_back == 1


def test_session_accepts_and_ignores_commit_kwarg():
    """Existing persistence helpers pass commit=True; sessions must tolerate it."""
    conn = FakeConn()
    s = DBSession(conn)
    assert s.fetchval("SELECT 1", commit=True) == "value"
    s.fetchone("SELECT 1", dict_rows=False, commit=True)
    s.fetchall("SELECT 1", dict_rows=False, commit=True)
    assert conn.committed == 0  # session never commits on its own
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/db/tests/test_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'DBSession'`

- [ ] **Step 3: Implement `DBSession` and `DBClient.session()`**

In `backend/pipeline/db/client.py`, after the imports add nothing; after the `DBClient` class closing (before `__all__`) add:

```python
class DBSession:
    """Single-connection view of the DBClient query API.

    Every statement issued through a session runs on one pooled connection and
    therefore inside one transaction; commit/rollback is owned by
    ``DBClient.session()``. The ``commit`` kwargs accepted by DBClient methods
    are accepted here and ignored so existing persistence helpers work
    unchanged when handed a session instead of a client.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(query, params)

    def fetchone(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        dict_rows: bool = False,
        commit: bool = False,
    ) -> Any:
        factory = dict_row if dict_rows else None
        with self._conn.cursor(row_factory=factory) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def fetchall(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        dict_rows: bool = False,
        commit: bool = False,
    ) -> list[Any]:
        factory = dict_row if dict_rows else None
        with self._conn.cursor(row_factory=factory) as cur:
            cur.execute(query, params)
            return list(cur.fetchall())

    def fetchval(
        self, query: str, params: Sequence[Any] | None = None, *, commit: bool = False
    ) -> Any:
        row = self.fetchone(query, params)
        if row is None:
            return None
        return row[0]
```

Inside the `DBClient` class, after the `transaction()` method, add:

```python
    @contextmanager
    def session(self):
        """Yield a DBSession bound to one connection; commit on success,
        roll back on exception."""
        with self._pool.connection() as conn:
            try:
                yield DBSession(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
```

Also add `Any` to the `typing` import if missing, and update the module exports:

```python
__all__ = ["DBClient", "DBSession"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest pipeline/db/tests/test_session.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/db/client.py backend/pipeline/db/tests/
git commit -m "feat(db): DBSession + DBClient.session() for single-transaction persistence"
```

---

## Task 2: Schema + migration for provenance and relationship traceability

**Files:**
- Modify: `backend/pipeline/db/schema.sql`
- Create: `backend/pipeline/db/migrate_add_ingestion_provenance.py`

- [ ] **Step 1: Add idempotent ALTERs to `schema.sql`**

In `backend/pipeline/db/schema.sql`, in the SOTA section (after the
`ALTER TABLE relationships ...` block around line 224), add:

```sql
-- ---- Ingestion provenance + replayability ----
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'human';
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS generation_meta JSONB;
-- relationships become traceable to the chapter that asserted them, and are
-- cascade-deleted when that chapter is replaced.
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS chapter_id UUID REFERENCES chapters(id) ON DELETE CASCADE;
```

- [ ] **Step 2: Write the migration script**

Create `backend/pipeline/db/migrate_add_ingestion_provenance.py`:

```python
from __future__ import annotations

from pipeline.db.client import DBClient


def run() -> None:
    with DBClient() as db:
        db.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'human'")
        db.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS generation_meta JSONB")
        db.execute(
            "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS chapter_id "
            "UUID REFERENCES chapters(id) ON DELETE CASCADE"
        )
    print("Migration complete: chapters.source/generation_meta + relationships.chapter_id added.")


if __name__ == "__main__":
    run()
```

- [ ] **Step 3: Run the migration against the dev DB**

Run: `cd backend && .venv/bin/python -m pipeline.db.migrate_add_ingestion_provenance`
Expected: `Migration complete: ...`

- [ ] **Step 4: Commit**

```bash
git add backend/pipeline/db/schema.sql backend/pipeline/db/migrate_add_ingestion_provenance.py
git commit -m "feat(db): chapters provenance columns and relationships.chapter_id"
```

---

## Task 3: `ingest_chapter` provenance + `delete_chapter_data`

**Files:**
- Modify: `backend/pipeline/ingestion/ingest.py`
- Create: `backend/pipeline/ingestion/tests/__init__.py` (empty)
- Test: `backend/pipeline/ingestion/tests/test_ingest.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/ingestion/tests/__init__.py` (empty), then
`backend/pipeline/ingestion/tests/test_ingest.py`:

```python
from __future__ import annotations

import uuid

import pytest

from pipeline.ingestion.ingest import delete_chapter_data, ingest_chapter


class RecorderDB:
    """Records every statement; scripts fetchval responses in order."""

    def __init__(self, fetchval_results=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchval_results = list(fetchval_results or [])

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append((query, tuple(params or ())))
        if self._fetchval_results:
            return self._fetchval_results.pop(0)
        return None

    def execute(self, query, params=None):
        self.calls.append((query, tuple(params or ())))


def test_ingest_chapter_writes_source_and_meta():
    new_id = uuid.uuid4()
    db = RecorderDB(fetchval_results=[None, new_id])  # no duplicate, then insert
    chapter_id = ingest_chapter(
        db,
        novel_id="novel-1",
        chapter_number=3,
        raw_text="text",
        title="T",
        source="generated",
        generation_meta={"model": "gpt-x"},
    )
    assert chapter_id == str(new_id)
    insert_q, insert_p = db.calls[-1]
    assert "INSERT INTO chapters" in insert_q
    assert "source" in insert_q and "generation_meta" in insert_q
    assert "generated" in insert_p


def test_ingest_chapter_defaults_to_human_source():
    db = RecorderDB(fetchval_results=[None, uuid.uuid4()])
    ingest_chapter(db, novel_id="n", chapter_number=1, raw_text="x")
    _, insert_p = db.calls[-1]
    assert "human" in insert_p


def test_ingest_chapter_still_rejects_duplicates():
    db = RecorderDB(fetchval_results=[uuid.uuid4()])  # duplicate found
    with pytest.raises(ValueError):
        ingest_chapter(db, novel_id="n", chapter_number=1, raw_text="x")


def test_delete_chapter_data_covers_non_cascading_tables():
    db = RecorderDB()
    delete_chapter_data(db, novel_id="novel-1", chapter_number=4)
    queries = [q for q, _ in db.calls]
    assert any("DELETE FROM knows_edges" in q for q in queries)
    assert any("UPDATE commitments" in q and "'pending'" in q for q in queries)
    assert any("DELETE FROM commitments" in q for q in queries)
    assert any("DELETE FROM chapters" in q for q in queries)
    # chapter delete must come last (everything else references it or its scope)
    assert "DELETE FROM chapters" in queries[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/ingestion/tests/test_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name 'delete_chapter_data'` (and signature errors)

- [ ] **Step 3: Implement**

Replace the body of `backend/pipeline/ingestion/ingest.py` with:

```python
from __future__ import annotations

import json
from typing import Any

from pipeline.db.client import DBClient


def ingest_chapter(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    title: str | None = None,
    source: str = "human",
    generation_meta: dict[str, Any] | None = None,
) -> str:
    existing = db.fetchval(
        """
        SELECT id FROM chapters
        WHERE novel_id = %s AND number = %s
        """,
        (novel_id, chapter_number),
    )
    if existing is not None:
        raise ValueError(
            f"Chapter {chapter_number} already exists for novel {novel_id}. "
            "Use replace=True to re-process it."
        )

    chapter_id = db.fetchval(
        """
        INSERT INTO chapters (novel_id, number, title, raw_text, source, generation_meta)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id
        """,
        (
            novel_id,
            chapter_number,
            title,
            raw_text,
            source,
            json.dumps(generation_meta) if generation_meta is not None else None,
        ),
        commit=True,
    )
    return str(chapter_id)


def delete_chapter_data(db: DBClient, *, novel_id: str, chapter_number: int) -> None:
    """Delete one chapter and every derived row, enabling re-processing.

    The chapters FK cascades cover events (and thread_events), scenes,
    character_states, continuity_flags, shared_dynamics, and (after the
    provenance migration) relationships. Tables keyed by chapter *number*
    instead of a FK need explicit handling. Known non-undoable residue:
    plot_threads upserts and entity rows created by this chapter remain —
    re-processing resolves back onto them.
    """
    # knows_edges has no chapter FK — keyed by learned_chapter int.
    db.execute(
        """
        DELETE FROM knows_edges
         WHERE learned_chapter = %s
           AND character_id IN (SELECT id FROM characters WHERE novel_id = %s)
        """,
        (chapter_number, novel_id),
    )
    # Payoffs this chapter satisfied go back to pending.
    db.execute(
        """
        UPDATE commitments
           SET status = 'pending', payoff_chapter = NULL, payoff_text = NULL,
               updated_at = now()
         WHERE novel_id = %s AND payoff_chapter = %s
        """,
        (novel_id, chapter_number),
    )
    # Foreshadows this chapter introduced disappear with it.
    db.execute(
        "DELETE FROM commitments WHERE novel_id = %s AND foreshadow_chapter = %s",
        (novel_id, chapter_number),
    )
    # Legacy relationships rows (pre-migration, chapter_id IS NULL) cannot be
    # attributed; rows written after the migration cascade with the chapter.
    db.execute(
        "DELETE FROM chapters WHERE novel_id = %s AND number = %s",
        (novel_id, chapter_number),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest pipeline/ingestion/tests/test_ingest.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/ingestion/
git commit -m "feat(ingestion): provenance params and delete_chapter_data for replace mode"
```

---

## Task 4: Transactional, replayable `process_chapter`

**Files:**
- Modify: `backend/pipeline/pipeline.py`
- Test: `backend/pipeline/extraction/tests/test_process_chapter.py` (create)

### Background

Today `process_chapter` opens `with DBClient() as db:` and every helper commits
statement-by-statement. The new flow:

1. duplicate check (fail fast, read-only) — honors `replace`
2. context load + LLM extraction + dedup + canonicalization (outside the
   transaction; canonicalizer alias writes are additive and safe to keep)
3. `with client.session() as s:` → optional `delete_chapter_data` →
   `ingest_chapter` → all persistence helpers receive `s`

`relationships` inserts also gain `chapter_id`.

- [ ] **Step 1: Write the failing test**

Create `backend/pipeline/extraction/tests/test_process_chapter.py`:

```python
from __future__ import annotations

import inspect

from pipeline import pipeline as pipeline_mod


def test_process_chapter_accepts_db_replace_and_source():
    sig = inspect.signature(pipeline_mod.process_chapter)
    for param in ("db", "replace", "source", "generation_meta"):
        assert param in sig.parameters, f"process_chapter missing {param!r} param"


def test_persist_extraction_inserts_relationships_with_chapter_id():
    src = inspect.getsource(pipeline_mod._persist_extraction)
    assert "chapter_id" in src.split("INSERT INTO relationships")[1].split(")")[0], (
        "relationships INSERT must include chapter_id"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_process_chapter.py -v`
Expected: FAIL (params missing)

- [ ] **Step 3: Restructure `process_chapter`**

In `backend/pipeline/pipeline.py`, change the `process_chapter` signature and body.
New signature:

```python
def process_chapter(
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    chapter_title: str | None,
    use_mock_llm: bool | None,
    chunk_size: int,
    chunk_overlap: int,
    progress: Any | None = None,
    db: DBClient | None = None,
    replace: bool = False,
    source: str = "human",
    generation_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

New body structure (the extraction/dedup/canonicalization block is unchanged —
only the surrounding plumbing moves):

```python
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        # Fail fast on duplicates before paying for LLM extraction.
        existing = client.fetchval(
            "SELECT id FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, chapter_number),
        )
        if existing is not None and not replace:
            raise ValueError(
                f"Chapter {chapter_number} already exists for novel {novel_id}. "
                "Pass replace=True to re-process it."
            )

        custom_entity_types = [
            dict(r)
            for r in client.fetchall(
                "SELECT name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
                (novel_id,),
                dict_rows=True,
            )
        ]

        context = load_story_context(client, novel_id, chapter_number, custom_entity_types=custom_entity_types)
        chunks = sliding_window_chunks(raw_text, chunk_size=chunk_size, overlap=chunk_overlap)
        extractor = ChapterExtractor(use_mock=use_mock_llm)
        extracted = extractor.extract_chapter(
            chunks=chunks,
            context=context,
            progress=progress,
            custom_entity_types=custom_entity_types or None,
        )
        extracted["custom_entities"] = _normalize_custom_entities(
            extracted.get("custom_entities", []), custom_entity_types
        )

        if progress is not None:
            progress.on_pass_start("intra_dedup")
        deduplicator = IntraExtractionDeduplicator(use_mock=use_mock_llm)
        extracted = deduplicator.deduplicate(extracted, raw_text)
        if progress is not None:
            progress.on_pass_done("intra_dedup")

        if progress is not None:
            progress.on_pass_start("canonicalization")
        canonicalizer = EntityCanonicalizer(client, novel_id=novel_id, use_mock=use_mock_llm)
        canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names_by_type=collect_names_by_type(extracted),
        )
        if progress is not None:
            progress.on_pass_done("canonicalization")

        # ---- everything below is one transaction ----
        with client.session() as s:
            if replace:
                delete_chapter_data(s, novel_id=novel_id, chapter_number=chapter_number)
            chapter_id = ingest_chapter(
                s,
                novel_id=novel_id,
                chapter_number=chapter_number,
                title=chapter_title,
                raw_text=raw_text,
                source=source,
                generation_meta=generation_meta,
            )
            resolver = EntityResolver(s, novel_id=novel_id, chapter_number=chapter_number)
            event_rows = _persist_extraction(
                s,
                resolver=resolver,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                extracted=extracted,
            )

            embedding_service = EmbeddingService(use_mock=use_mock_llm)
            embed_chapter_and_events(
                s,
                chapter_id=chapter_id,
                chapter_summary=extracted.get("summary", ""),
                event_rows=event_rows,
                service=embedding_service,
                embed_chapter=not (extracted.get("summary_medium") or "").strip(),
            )

            s.execute(
                """
                UPDATE chapters
                SET summary = %s,
                    processed_at = now()
                WHERE id = %s
                """,
                (extracted.get("summary", ""), chapter_id),
            )

            persist_multi_summaries(
                s,
                chapter_id=chapter_id,
                summary_short=extracted.get("summary_short", ""),
                summary_medium=extracted.get("summary_medium", ""),
                summary_long=extracted.get("summary_long", ""),
                embedder=embedding_service,
            )
            persist_scenes(
                s,
                chapter_id=chapter_id,
                scenes_data=extracted.get("scenes", []),
                resolver=resolver,
                embedder=embedding_service,
            )
            persist_knows_edges(
                s,
                chapter_number=chapter_number,
                learnings=extracted.get("learnings", []),
                resolver=resolver,
            )
            persist_commitments(
                s,
                novel_id=novel_id,
                chapter_number=chapter_number,
                foreshadows=extracted.get("foreshadows_introduced", []),
                payoffs=extracted.get("payoffs_delivered", []),
                resolver=resolver,
                embedder=embedding_service,
            )

        return {
            "chapter_id": chapter_id,
            "chunks": len(chunks),
            "summary_preview": extracted.get("summary", "")[:200],
            "new_characters": len(extracted.get("new_entities", {}).get("characters", [])),
            "new_locations": len(extracted.get("new_entities", {}).get("locations", [])),
            "events": len(extracted.get("events", [])),
            "thread_updates": len(extracted.get("thread_updates", [])),
            "continuity_flags": len(extracted.get("continuity_flags", [])),
        }
    finally:
        if owned:
            client.close()
```

Update the imports at the top of `pipeline.py`:

```python
from pipeline.ingestion.ingest import delete_chapter_data, ingest_chapter
```

- [ ] **Step 4: Add `chapter_id` to the relationships INSERT**

Still in `pipeline.py`, in `_persist_extraction`, change the relationships insert to:

```python
        db.execute(
            """
            INSERT INTO relationships (
                entity_a_id, entity_b_id, rel_type, from_chapter, to_chapter, notes, chapter_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                a_universal,
                b_universal,
                rel.get("rel_type"),
                rel.get("from_chapter"),
                rel.get("to_chapter"),
                rel.get("notes"),
                chapter_id,
            ),
        )
```

- [ ] **Step 5: Add `--replace` to the CLI**

In `build_parser()` (same file), under the `process-chapter` subparser, add:

```python
    process_parser.add_argument(
        "--replace", action="store_true",
        help="Delete this chapter's previously extracted data and re-process it",
    )
```

and in `main()` pass it through:

```python
        result = process_chapter(
            novel_id=args.novel_id,
            chapter_number=args.number,
            raw_text=chapter_text,
            chapter_title=args.title,
            use_mock_llm=True if args.mock_llm else None,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            replace=args.replace,
        )
```

- [ ] **Step 6: Run the tests + full suite**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_process_chapter.py -v && .venv/bin/pytest -q`
Expected: new tests pass; full suite green.

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline/pipeline.py backend/pipeline/extraction/tests/test_process_chapter.py
git commit -m "feat(pipeline): transactional persistence, replace mode, provenance, injectable db"
```

---

## Task 5: API surface (`replace` flag)

**Files:**
- Modify: `backend/api/schemas.py` (the `ProcessRequest` model)
- Modify: `backend/api/jobs.py:82-113`
- Modify: `backend/api/routes/process.py`
- Test: `backend/api/tests/test_process.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/api/tests/test_process.py`:

```python
def test_process_accepts_replace_flag(fake_db_factory, client, monkeypatch):
    from api.tests.conftest import make_novel
    from api import routes

    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[])

    captured: dict = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return "job-123"

    monkeypatch.setattr("api.routes.process.submit_job", fake_submit)
    response = client.post(
        f"/api/novels/{novel['id']}/chapters/process",
        json={"number": 2, "text": "chapter text", "replace": True},
    )
    assert response.status_code == 202
    assert captured["replace"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest api/tests/test_process.py -v -k replace`
Expected: FAIL (unexpected keyword / validation error)

- [ ] **Step 3: Implement**

In `backend/api/schemas.py`, find `class ProcessRequest` and add the field:

```python
class ProcessRequest(BaseModel):
    number: int
    text: str
    replace: bool = False
```

In `backend/api/jobs.py`, change `submit_job`:

```python
def submit_job(*, novel_id: str, chapter_number: int, text: str, replace: bool = False) -> str:
```

and inside `_run()` pass `replace=replace` to `process_chapter(...)`.

In `backend/api/routes/process.py`, pass it through:

```python
    job_id = submit_job(
        novel_id=str(novel_id),
        chapter_number=body.number,
        text=body.text,
        replace=body.replace,
    )
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest api/tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/schemas.py backend/api/jobs.py backend/api/routes/process.py backend/api/tests/test_process.py
git commit -m "feat(api): replace flag for chapter re-processing"
```

---

## Task 6: Manual verification + docs

- [ ] **Step 1: Manual end-to-end verification against the dev DB**

```bash
cd backend
# pick any existing test novel id from: .venv/bin/python -m pipeline.pipeline list-novels
echo "A short test chapter. Alice met Bob." | .venv/bin/python -m pipeline.pipeline process-chapter --novel-id <ID> --number 999 --mock-llm
echo "A short test chapter, revised. Alice met Bob again." | .venv/bin/python -m pipeline.pipeline process-chapter --novel-id <ID> --number 999 --mock-llm --replace
# cleanup
echo "ok"
```

Expected: first run succeeds, second run succeeds (no duplicate error), and
`SELECT count(*) FROM chapters WHERE number = 999` is 1. Then delete chapter 999
via `delete_chapter_data` or SQL.

- [ ] **Step 2: Update docs**

- `docs/architecture.html`: in the chapter-processing steps list, note that
  persistence runs in a single transaction and that `--replace` re-processes a
  chapter; in the idempotency callout, replace the "don't process twice"
  warning with the new `replace` semantics. Add `source`/`generation_meta` to
  the chapters table row in the schema table.
- `docs/reference.html`: add `replace` to the process endpoint docs; add
  `source`, `generation_meta` to the chapters schema section; document
  `DBClient.session()` under the DB client section.

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.html docs/reference.html
git commit -m "docs: atomic ingestion, replace mode, chapter provenance"
```
