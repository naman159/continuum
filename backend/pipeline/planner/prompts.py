"""Prompts for the Scene Planner agent."""

from __future__ import annotations

import json
from textwrap import dedent

from pipeline.planner.context import PlanContext


CHAPTER_PLAN_SCHEMA = {
    "title": "string|null",
    "arc_position": "rising|midpoint|climax|denouement|null",
    "chapter_goal": "string (one-paragraph statement of intent)",
    "target_word_count": "int (default 3000)",
    "scenes": [
        {
            "scene_index": "int (1-based)",
            "pov_character": "string|null (name)",
            "location": "string|null (name)",
            "time_anchor": "string|null (e.g., 'evening, third day after the council')",
            "present_characters": ["string (names)"],
            "scene_goal": "string (what this scene must accomplish)",
            "threads_to_advance": ["string (thread titles to advance)"],
            "commitments_to_plant": ["string (foreshadow text)"],
            "commitments_to_satisfy": ["string (foreshadow text of an existing pending commitment to pay off)"],
            "key_facts_to_respect": ["string (the canon facts/state that the scene must honor)"],
            "target_word_count": "int",
            "target_emotional_beat": "string|null",
            "style_notes": "string|null",
        }
    ],
    "notes": "string|null (anything the drafter should know)",
}


def build_system_prompt() -> str:
    schema_json = json.dumps(CHAPTER_PLAN_SCHEMA, indent=2)
    return dedent(
        f"""
        You are the Scene Planner for a long-form fiction continuity system.
        Your output is a typed plan that the Drafter will execute scene by
        scene and the Continuity Critic will verify against.

        Output STRICT JSON matching this schema:

        {schema_json}

        Hard rules:
          - 2 to 6 scenes per chapter unless context overwhelmingly justifies more.
          - Every scene must have a `scene_goal` written as the change it produces
            in the story state (a character learns X, gains/loses Y, decides Z).
          - `threads_to_advance` MUST reference existing plot thread titles from
            the supplied active_threads list, OR be the title of a new thread
            this chapter introduces.
          - `commitments_to_satisfy` MUST reference the foreshadow text of an
            existing pending commitment from the supplied list.
          - `key_facts_to_respect` should pull from the supplied locked_facts.
          - No prose. Plan only.
        """
    ).strip()


def build_user_prompt(ctx: PlanContext) -> str:
    payload = {
        "novel_title": ctx.novel_title,
        "next_chapter_number": ctx.chapter_number,
        "main_characters": [
            {"name": c["name"], "description": c.get("description")}
            for c in ctx.main_characters
        ],
        "locked_facts": [
            {
                "subject_entity_id": str(f["subject_entity_id"])
                if f.get("subject_entity_id") else None,
                "predicate": f["predicate"],
                "value": f["value"],
            }
            for f in ctx.locked_facts
        ],
        "active_threads": [
            {
                "title": t["title"],
                "description": t.get("description"),
                "status": t["status"],
                "thread_type": t.get("thread_type"),
                "opened_chapter": t.get("opened_chapter"),
            }
            for t in ctx.active_threads
        ],
        "pending_commitments": [
            {
                "foreshadow_text": c["foreshadow_text"],
                "foreshadow_chapter": c["foreshadow_chapter"],
                "weight": c.get("weight"),
            }
            for c in ctx.pending_commitments
        ],
        "recent_chapter_summaries": [
            {
                "number": ch["number"],
                "title": ch.get("title"),
                "summary_short": ch.get("summary_short"),
                "summary": ch.get("summary"),
            }
            for ch in ctx.recent_chapter_summaries
        ],
        "recent_events": [
            {
                "chapter_number": e["chapter_number"],
                "type": e.get("event_type"),
                "impact": e.get("impact_level"),
                "description": e["description"],
            }
            for e in ctx.recent_events
        ],
    }
    return (
        "Plan the next chapter. Context follows in JSON. "
        "Return ONLY the chapter plan JSON, no commentary.\n\n"
        f"{json.dumps(payload, indent=2, default=str)}"
    )
