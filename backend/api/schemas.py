from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NovelSummary(BaseModel):
    id: UUID
    title: str
    author: str | None = None
    language: str | None = None
    created_at: datetime
    max_chapter: int


class CharacterSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None
    first_appearance_chapter: int | None


class CharacterStateRow(BaseModel):
    chapter_number: int
    location: str | None
    emotional_state: str | None
    goals: str | None
    knowledge: list[str]
    relationships: dict[str, Any]
    physical_state: str | None
    notes: str | None


class CharacterEventRow(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    event_type: str | None
    impact_level: str | None
    involved_characters: list[str]
    involved_locations: list[str]
    involved_objects: list[str]


class CharacterRelationshipRow(BaseModel):
    chapter_number: int | None
    other_character_id: UUID
    other_character_name: str
    direction: str  # "from" if this character is entity_a; "to" if entity_b
    rel_type: str | None
    status: str | None
    notes: str | None


class CharacterDetail(BaseModel):
    identity: CharacterSummary
    current_state: CharacterStateRow | None
    history: list[CharacterStateRow]
    relationships: list[CharacterRelationshipRow]
    events: list[CharacterEventRow]


class ChapterSummary(BaseModel):
    id: UUID
    number: int
    title: str | None
    summary: str | None
    processed_at: datetime | None


class TimelineEvent(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    event_type: str | None
    impact_level: str | None
    involved_characters: list[str]
    involved_locations: list[str]
    involved_objects: list[str]


class ThreadEventLink(BaseModel):
    event_id: UUID
    description: str
    chapter_number: int
    impact: str | None


class PlotThread(BaseModel):
    id: UUID
    title: str
    description: str | None
    status: str
    thread_type: str | None
    opened_chapter: int | None
    closed_chapter: int | None
    events: list[ThreadEventLink]


class ContinuityFlag(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    flag_type: str | None
    resolved: bool
    resolved_chapter_number: int | None


class GraphNode(BaseModel):
    id: UUID
    label: str
    description: str | None


class GraphEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    from_: UUID = Field(alias="from")
    to: UUID
    label: str | None
    chapter_number: int | None


class RelationshipGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
