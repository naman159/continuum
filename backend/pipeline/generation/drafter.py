from __future__ import annotations

"""SceneDrafter: turns one ScenePlan into prose.

Unlike the extraction passes this produces plain text (no response_format).
Mock mode is deterministic so the loop is testable offline.
"""

import json
import logging
from typing import Any

from pipeline.config import settings
from pipeline.planner.types import ChapterPlan, ScenePlan

logger = logging.getLogger(__name__)


def _load_completion():
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return completion


_SYSTEM_PROMPT = """You are a novelist continuing an existing work. Write ONE scene.
Hard rules:
- Respect every fact in KEY FACTS and CONTEXT exactly; never contradict them.
- Only the characters listed may appear; introduce nobody new.
- Continue smoothly from PREVIOUS TEXT (do not repeat it).
- Match the VOICE profile (POV person, sentence rhythm, dialogue density).
- Aim for the target word count (+/-20%). Output prose only — no headings,
  no notes, no JSON, no scene numbers."""


class SceneDrafter:
    def __init__(
        self,
        *,
        use_mock: bool | None = None,
        completion_fn=None,
        model: str | None = None,
    ) -> None:
        self._completion = completion_fn if completion_fn is not None else _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or self._completion is None
        else:
            self.use_mock = use_mock
        self.model = model or settings.draft_model

    def draft_scene(
        self,
        *,
        scene: ScenePlan,
        plan: ChapterPlan,
        context_block: str,
        prior_text_tail: str,
        style: dict[str, Any] | None,
        revision_notes: list[str] | None = None,
    ) -> str:
        if self.use_mock:
            return self._mock_scene(scene, plan)

        user_prompt = self._build_user_prompt(
            scene=scene,
            plan=plan,
            context_block=context_block,
            prior_text_tail=prior_text_tail,
            style=style,
            revision_notes=revision_notes or [],
        )
        try:
            response = self._completion(
                model=self.model,
                temperature=settings.draft_temperature,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            if isinstance(content, list):
                content = "".join(str(p) for p in content)
            return str(content).strip()
        except Exception as exc:
            logger.warning("drafter: LLM call failed, falling back to mock: %s", exc)
            return self._mock_scene(scene, plan)

    @staticmethod
    def _build_user_prompt(
        *,
        scene: ScenePlan,
        plan: ChapterPlan,
        context_block: str,
        prior_text_tail: str,
        style: dict[str, Any] | None,
        revision_notes: list[str],
    ) -> str:
        parts = [
            f"CHAPTER GOAL\n{plan.chapter_goal}",
            f"SCENE GOAL\n{scene.scene_goal}",
            f"POV: {scene.pov_character or 'unspecified'} | LOCATION: {scene.location or 'unspecified'}"
            f" | CHARACTERS PRESENT: {', '.join(scene.present_characters) or 'unspecified'}"
            f" | TARGET WORDS: {scene.target_word_count}",
        ]
        if scene.key_facts_to_respect:
            parts.append("KEY FACTS\n" + "\n".join(f"- {f}" for f in scene.key_facts_to_respect))
        if context_block:
            parts.append(f"CONTEXT (retrieved from earlier chapters)\n{context_block}")
        if style:
            parts.append("VOICE\n" + json.dumps(style, ensure_ascii=True))
        if prior_text_tail:
            parts.append(f"PREVIOUS TEXT (continue from here)\n…{prior_text_tail[-1500:]}")
        if revision_notes:
            parts.append(
                "REVISION NOTES (a continuity critic rejected the previous draft — fix ALL of these)\n"
                + "\n".join(f"- {n}" for n in revision_notes)
            )
        return "\n\n".join(parts)

    @staticmethod
    def _mock_scene(scene: ScenePlan, plan: ChapterPlan) -> str:
        cast = ", ".join(scene.present_characters) or "The cast"
        return (
            f"[mock scene {scene.scene_index}] {cast} at "
            f"{scene.location or 'an unnamed place'}. {scene.scene_goal} "
            f"The chapter advances: {plan.chapter_goal}"
        )


__all__ = ["SceneDrafter"]
