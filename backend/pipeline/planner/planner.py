"""Scene Planner.

Produces a ChapterPlan for the next chapter from a PlanContext. In real
mode it calls LiteLLM with the prompts in `prompts.py`. In mock mode it
emits a deterministic plan derived from the context, so tests run without
network calls and `USE_MOCK_LLM=true` still produces valid plan shapes.
"""

from __future__ import annotations

import logging

from pipeline.config import LLM_CONFIG, settings
from pipeline.db.client import DBClient
from pipeline.llm import load_completion, safe_json_loads
from pipeline.planner.context import PlanContext, gather_plan_context
from pipeline.planner.prompts import build_system_prompt, build_user_prompt
from pipeline.planner.types import ChapterPlan, ScenePlan

logger = logging.getLogger(__name__)


def _mock_plan(ctx: PlanContext) -> ChapterPlan:
    """Deterministic plan for USE_MOCK_LLM=true and tests.

    Builds a 2-scene plan that exercises commitments and threads if any are
    available in the context.
    """
    main_names = [c["name"] for c in ctx.main_characters] or ["Protagonist"]
    pov = main_names[0]
    second = main_names[1] if len(main_names) > 1 else pov
    threads = [t["title"] for t in ctx.active_threads]
    pending_fs = [c["foreshadow_text"] for c in ctx.pending_commitments]
    locked_facts = [
        f"{f['predicate']}={f['value']}" for f in ctx.locked_facts
    ][:3]

    scene_1 = ScenePlan(
        scene_index=1,
        pov_character=pov,
        location=None,
        time_anchor=None,
        present_characters=main_names[:3],
        scene_goal=(
            f"{pov} confronts the latest development in the story; "
            f"introduce one new beat and respect existing canon."
        ),
        threads_to_advance=threads[:1],
        commitments_to_plant=[],
        commitments_to_satisfy=[],
        key_facts_to_respect=locked_facts,
        target_word_count=1200,
        target_emotional_beat="tension rising",
    )
    scene_2 = ScenePlan(
        scene_index=2,
        pov_character=second,
        location=None,
        time_anchor="later",
        present_characters=[second],
        scene_goal=(
            "Move at least one pending commitment toward payoff "
            "without resolving the chapter's central question."
        ),
        threads_to_advance=threads[:2],
        commitments_to_plant=[],
        commitments_to_satisfy=pending_fs[:1],
        key_facts_to_respect=locked_facts,
        target_word_count=1600,
        target_emotional_beat="quiet revelation",
    )
    return ChapterPlan(
        novel_id=ctx.novel_id,
        chapter_number=ctx.chapter_number,
        title=None,
        arc_position="rising",
        chapter_goal=(
            f"Advance the central conflict for chapter {ctx.chapter_number} "
            f"by one beat. Satisfy {len(pending_fs[:1])} pending commitment(s). "
            f"Respect {len(locked_facts)} locked canon fact(s)."
        ),
        scenes=[scene_1, scene_2],
        target_word_count=2800,
        notes="mock plan (USE_MOCK_LLM=true)",
        debug={"mock": True, "main_characters": main_names},
    )


def _plan_from_json(novel_id: str, chapter_number: int, raw: dict) -> ChapterPlan:
    scenes_raw = raw.get("scenes") or []
    scenes: list[ScenePlan] = []
    for i, s in enumerate(scenes_raw, start=1):
        scenes.append(
            ScenePlan(
                scene_index=int(s.get("scene_index") or i),
                pov_character=s.get("pov_character"),
                location=s.get("location"),
                time_anchor=s.get("time_anchor"),
                present_characters=list(s.get("present_characters") or []),
                scene_goal=str(s.get("scene_goal") or ""),
                threads_to_advance=list(s.get("threads_to_advance") or []),
                commitments_to_plant=list(s.get("commitments_to_plant") or []),
                commitments_to_satisfy=list(s.get("commitments_to_satisfy") or []),
                key_facts_to_respect=list(s.get("key_facts_to_respect") or []),
                target_word_count=int(s.get("target_word_count") or 800),
                target_emotional_beat=s.get("target_emotional_beat"),
                style_notes=s.get("style_notes"),
            )
        )
    return ChapterPlan(
        novel_id=novel_id,
        chapter_number=chapter_number,
        title=raw.get("title"),
        arc_position=raw.get("arc_position"),
        chapter_goal=str(raw.get("chapter_goal") or ""),
        scenes=scenes,
        target_word_count=int(raw.get("target_word_count") or 3000),
        notes=raw.get("notes"),
    )


class ScenePlanner:
    def __init__(self, db: DBClient, model: str | None = None) -> None:
        self.db = db
        self.model = model or settings.default_model

    def plan(self, novel_id: str, chapter_number: int) -> ChapterPlan:
        ctx = gather_plan_context(self.db, novel_id, chapter_number)

        if settings.use_mock_llm:
            return _mock_plan(ctx)

        # Real mode fails loudly: a silently substituted mock plan would let a
        # placeholder-planned chapter be drafted and ingested as canon.
        completion = load_completion()
        if completion is None:
            raise RuntimeError("planner: litellm unavailable in real mode")

        system = build_system_prompt()
        user = build_user_prompt(ctx)
        try:
            response = completion(
                **{**LLM_CONFIG, "model": self.model},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            raw_text = response["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"planner: LLM call failed: {exc}") from exc

        data = safe_json_loads(raw_text)
        if not data:
            raise RuntimeError("planner: LLM returned unparseable JSON")
        plan = _plan_from_json(novel_id, chapter_number, data)
        plan.debug["model"] = self.model
        return plan


def plan_chapter(
    novel_id: str,
    chapter_number: int,
    db: DBClient | None = None,
    model: str | None = None,
) -> ChapterPlan:
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        return ScenePlanner(client, model=model).plan(novel_id, chapter_number)
    finally:
        if owned:
            client.close()
