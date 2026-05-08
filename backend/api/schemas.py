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
