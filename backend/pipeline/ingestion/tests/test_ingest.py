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
