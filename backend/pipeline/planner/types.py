"""Typed structures for the Scene Planner.

A ChapterPlan is the contract the Drafter must satisfy and the Critic will
verify against. Keep it small and concrete — vague plans produce vague drafts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenePlan:
    """One scene within a chapter.

    Names here are the *names* the planner produced. They are resolved to
    UUIDs by the orchestrator before the plan is persisted.
    """

    scene_index: int
    pov_character: str | None
    location: str | None
    time_anchor: str | None
    present_characters: list[str]
    scene_goal: str
    threads_to_advance: list[str] = field(default_factory=list)
    commitments_to_plant: list[str] = field(default_factory=list)
    commitments_to_satisfy: list[str] = field(default_factory=list)
    key_facts_to_respect: list[str] = field(default_factory=list)
    target_word_count: int = 800
    target_emotional_beat: str | None = None
    style_notes: str | None = None


@dataclass
class ChapterPlan:
    """Top-level plan for one chapter."""

    novel_id: str
    chapter_number: int
    title: str | None
    arc_position: str | None        # "rising", "midpoint", "climax", "denouement"
    chapter_goal: str               # one-paragraph statement of intent
    scenes: list[ScenePlan]
    target_word_count: int = 3000
    notes: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)
