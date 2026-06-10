from __future__ import annotations

from types import SimpleNamespace

from pipeline.generation.drafter import SceneDrafter
from pipeline.planner.types import ChapterPlan, ScenePlan


def _scene(**over):
    base = dict(
        scene_index=1, pov_character="Jake", location="Harbor", time_anchor=None,
        present_characters=["Jake", "Sara"], scene_goal="Jake confronts Sara about the letter.",
        target_word_count=300,
    )
    base.update(over)
    return ScenePlan(**base)


def _plan():
    return ChapterPlan(
        novel_id="n1", chapter_number=5, title=None, arc_position="rising",
        chapter_goal="Confront the letter mystery.", scenes=[_scene()],
    )


def test_mock_draft_is_deterministic_and_mentions_characters():
    drafter = SceneDrafter(use_mock=True)
    a = drafter.draft_scene(scene=_scene(), plan=_plan(), context_block="", prior_text_tail="", style=None)
    b = drafter.draft_scene(scene=_scene(), plan=_plan(), context_block="", prior_text_tail="", style=None)
    assert a == b
    assert "Jake" in a and "Sara" in a


def test_real_draft_returns_completion_text_and_prompt_carries_context():
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        msg = SimpleNamespace(content="The harbor wind cut sideways.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    drafter = SceneDrafter(use_mock=False, completion_fn=fake_completion)
    text = drafter.draft_scene(
        scene=_scene(key_facts_to_respect=["Sara fears water."]),
        plan=_plan(),
        context_block="FACT: Sara fears water.",
        prior_text_tail="…the door slammed.",
        style={"pov_person": "third", "avg_sentence_words": 14.0},
        revision_notes=["Sara cannot know about the ledger."],
    )
    assert text == "The harbor wind cut sideways."
    user_prompt = captured["messages"][1]["content"]
    assert "Sara fears water" in user_prompt
    assert "door slammed" in user_prompt
    assert "cannot know about the ledger" in user_prompt
    assert "response_format" not in captured  # prose, not JSON


def test_llm_failure_falls_back_to_mock():
    def boom(**kwargs):
        raise RuntimeError("network down")

    drafter = SceneDrafter(use_mock=False, completion_fn=boom)
    text = drafter.draft_scene(
        scene=_scene(), plan=_plan(), context_block="", prior_text_tail="", style=None
    )
    assert "[mock scene 1]" in text
