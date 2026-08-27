from __future__ import annotations

import inspect
import uuid

import pytest

from pipeline import pipeline as pipeline_mod
from pipeline.db.client import DBClient
from pipeline.pipeline import analyze_chapter

CHAPTER_TEXT = """
Aelric walked the halls of Fogwood Keep. He picked up the silver dagger
from the armory and tucked it into his belt before heading to the gates.
"""


def test_process_chapter_accepts_db_replace_and_source():
    sig = inspect.signature(pipeline_mod.analyze_chapter)
    for param in ("db", "replace", "source"):
        assert param in sig.parameters, f"analyze_chapter missing {param!r} param"


def test_persist_extraction_inserts_relationships_with_chapter_id():
    src = inspect.getsource(pipeline_mod._persist_extraction)
    assert "chapter_id" in src.split("INSERT INTO relationships")[1].split(")")[0], (
        "relationships INSERT must include chapter_id"
    )


@pytest.fixture
def novel_id(db: DBClient):
    nid = str(uuid.uuid4())
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO novels (id, title) VALUES (%s, %s)",
            (nid, f"TestNovel-{nid[:8]}"),
        )
    yield nid
    with db.transaction() as cur:
        cur.execute("DELETE FROM novels WHERE id = %s", (nid,))


def test_analyze_chapter_materializes_and_persists_critique(db: DBClient, novel_id: str):
    result = analyze_chapter(
        novel_id=novel_id,
        chapter_number=1,
        raw_text=CHAPTER_TEXT,
        chapter_title="The Armory",
        use_mock_llm=True,
        chunk_size=2000,
        chunk_overlap=200,
        db=db,
    )

    assert result["materialized"] is True
    assert result["critique"] is not None and "passed" in result["critique"]
    # A persisted report exists for the chapter.
    assert db.fetchval(
        "SELECT count(*) FROM critique_reports WHERE chapter_id = %s",
        (result["chapter_id"],),
    ) == 1
