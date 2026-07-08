"""process_chapter must rebuild the state projections (character_states,
located_in_edges, possesses_edges) after persisting a chapter, so the
Knowledge page never shows stale/empty possession and location history.

Hits the real branch-isolated Postgres DB with the mock LLM extractor.
Each test owns its own novel and deletes it in teardown.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.pipeline import process_chapter

CHAPTER_TEXT = """
Aelric walked the halls of Fogwood Keep. He picked up the silver dagger
from the armory and tucked it into his belt before heading to the gates.
"""


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


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


def test_process_chapter_records_materialization_run(db: DBClient, novel_id: str):
    process_chapter(
        novel_id=novel_id,
        chapter_number=1,
        raw_text=CHAPTER_TEXT,
        chapter_title="The Armory",
        use_mock_llm=True,
        chunk_size=2000,
        chunk_overlap=200,
        db=db,
    )

    runs = db.fetchall(
        "SELECT through_chapter FROM materialized_state_runs WHERE novel_id = %s",
        (novel_id,),
        dict_rows=True,
    )
    assert runs, "process_chapter did not run the state materializer"
    assert runs[-1]["through_chapter"] == 1
