"""process_chapter must rebuild the state projections (character_states,
located_in_edges, possesses_edges) after persisting a chapter, so the
Knowledge page never shows stale/empty possession and location history.

Hits the real branch-isolated Postgres DB with the mock LLM extractor. The
mock extractor (pipeline/extraction/extractor.py::_mock_extract) emits, per
detected character name, a "status" state_delta (attribute="notes",
value="mock delta for {name}") always, plus a "possession" gain delta for
"silver dagger" when the chunk text contains "took"/"picked up". The
possession delta only persists as a state_deltas row if "silver dagger"
resolves as an *existing* object (persist_state_deltas resolves with
create=False) — the mock's new_entities pass never creates objects, so for
fresh novels the possession delta is dropped at persist time and no
possesses_edges row is ever materialized from it. The status delta always
resolves (the character was just created by the new_entities pass) and
folds into character_states.notes via the materializer.

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


def test_process_chapter_folds_status_delta_into_character_states(
    db: DBClient, novel_id: str
):
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

    states = db.fetchall(
        """
        SELECT c.name AS character_name, cs.notes
          FROM character_states cs
          JOIN characters c ON c.id = cs.character_id
         WHERE c.novel_id = %s
        """,
        (novel_id,),
        dict_rows=True,
    )
    aelric_states = [s for s in states if s["character_name"] == "Aelric"]
    assert aelric_states, "materializer did not fold the mock status delta into character_states"
    assert aelric_states[0]["notes"] == "mock delta for Aelric"


def test_process_chapter_drops_possession_for_unresolved_object(
    db: DBClient, novel_id: str
):
    # CHAPTER_TEXT contains "picked up", so the mock extractor emits a
    # possession-gain delta for "silver dagger". The mock's new_entities pass
    # never creates objects, so persist_state_deltas (create=False) cannot
    # resolve it — the delta is dropped before it ever reaches the
    # materializer, and no possesses_edges row is produced.
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

    assert db.fetchall(
        "SELECT id FROM objects WHERE novel_id = %s", (novel_id,), dict_rows=True
    ) == []

    edges = db.fetchall(
        """
        SELECT pe.id
          FROM possesses_edges pe
          JOIN characters c ON c.id = pe.character_id
         WHERE c.novel_id = %s
        """,
        (novel_id,),
        dict_rows=True,
    )
    assert edges == []
