"""Tests for the Scene Planner.

Uses USE_MOCK_LLM=true so we test the data-gathering + plan-shape logic
without depending on a live LLM. The mock planner derives its plan from
PlanContext, which is what we want to verify.
"""

from __future__ import annotations

import os
import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.planner import ScenePlanner, gather_plan_context, plan_chapter
from pipeline.planner.types import ChapterPlan, ScenePlan


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    # The settings dataclass is frozen and read at import time, so monkeypatch
    # the .use_mock_llm attribute directly via object.__setattr__.
    from pipeline.config import settings
    object.__setattr__(settings, "use_mock_llm", True)
    yield
    object.__setattr__(settings, "use_mock_llm", False)


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
    title = f"PlanTest-{novel_id[:8]}"
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, title)
        )

        cur.execute(
            "INSERT INTO chapters (novel_id, number, raw_text, summary, summary_short) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (novel_id, 1, "chapter 1 text", "Aelric arrives.", "Arrival."),
        )
        ch1_id = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO chapters (novel_id, number, raw_text, summary) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (novel_id, 2, "chapter 2 text", "Aelric meets Mira."),
        )
        ch2_id = str(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s, 'character', %s) RETURNING id",
            (novel_id, "Aelric"),
        )
        aelric_eid = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name, description) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (novel_id, aelric_eid, "Aelric", "the protagonist"),
        )
        aelric_id = str(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s, 'character', %s) RETURNING id",
            (novel_id, "Mira"),
        )
        mira_eid = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name, description) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (novel_id, mira_eid, "Mira", "the navigator"),
        )
        mira_id = str(cur.fetchone()[0])

        # character_states rows so they show up as main characters
        cur.execute(
            "INSERT INTO character_states (character_id, chapter_id, emotional_state) "
            "VALUES (%s, %s, %s)",
            (aelric_id, ch1_id, "determined"),
        )
        cur.execute(
            "INSERT INTO character_states (character_id, chapter_id, emotional_state) "
            "VALUES (%s, %s, %s)",
            (mira_id, ch2_id, "cautious"),
        )

        # locked canon fact
        cur.execute(
            """
            INSERT INTO canon_facts
                (novel_id, kind, subject_entity_id, predicate, value, locked, source_chapter)
            VALUES (%s, 'identity', %s, 'eye_color', 'green', true, 1)
            """,
            (novel_id, aelric_eid),
        )

        # active plot thread
        cur.execute(
            """
            INSERT INTO plot_threads (novel_id, title, description, status, opened_chapter, thread_type)
            VALUES (%s, 'The lost crown', 'find the missing crown of Pellis', 'open', 1, 'mystery')
            """,
            (novel_id,),
        )

        # pending commitment
        cur.execute(
            """
            INSERT INTO commitments
                (novel_id, foreshadow_text, foreshadow_chapter, weight, status)
            VALUES (%s, %s, %s, %s, 'pending')
            """,
            (novel_id, "a silver dagger glints in the firelight", 1, 0.8),
        )

        # an event for recent_events
        cur.execute(
            """
            INSERT INTO events (chapter_id, description, event_type, impact_level)
            VALUES (%s, %s, %s, %s)
            """,
            (ch2_id, "Mira warns Aelric about the gate.", "revelation", "medium"),
        )

    yield {"novel_id": novel_id, "ch1_id": ch1_id, "ch2_id": ch2_id}

    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_gather_plan_context_pulls_relevant_state(db, seeded):
    ctx = gather_plan_context(db, seeded["novel_id"], chapter_number=3)
    assert ctx.novel_title.startswith("PlanTest-")
    assert any(f["predicate"] == "eye_color" for f in ctx.locked_facts)
    assert any(t["title"] == "The lost crown" for t in ctx.active_threads)
    assert len(ctx.pending_commitments) == 1
    assert ctx.pending_commitments[0]["foreshadow_text"].startswith("a silver dagger")
    assert len(ctx.recent_chapter_summaries) == 2
    assert any(e["event_type"] == "revelation" for e in ctx.recent_events)
    char_names = {c["name"] for c in ctx.main_characters}
    assert "Aelric" in char_names
    assert "Mira" in char_names


def test_mock_planner_produces_valid_plan(db, seeded):
    plan = ScenePlanner(db).plan(seeded["novel_id"], chapter_number=3)
    assert isinstance(plan, ChapterPlan)
    assert plan.chapter_number == 3
    assert plan.novel_id == seeded["novel_id"]
    assert len(plan.scenes) >= 1
    assert all(isinstance(s, ScenePlan) for s in plan.scenes)
    # At least one scene should reference the pending commitment, since the mock
    # planner pulls from pending_commitments.
    sat = [c for s in plan.scenes for c in s.commitments_to_satisfy]
    assert any("silver dagger" in s for s in sat)
    # And should mention the active thread.
    advanced = [t for s in plan.scenes for t in s.threads_to_advance]
    assert "The lost crown" in advanced


def test_plan_chapter_convenience_function(db, seeded):
    plan = plan_chapter(seeded["novel_id"], chapter_number=3, db=db)
    assert plan.chapter_number == 3
    assert plan.scenes
