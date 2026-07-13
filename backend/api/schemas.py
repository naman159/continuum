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


class NovelEntityTypeInput(BaseModel):
    name: str
    description: str | None = None


class NovelEntityType(BaseModel):
    id: UUID
    novel_id: UUID
    name: str
    description: str | None = None


class CustomEntitySummary(BaseModel):
    id: str
    name: str
    entity_type: str
    description: str | None = None


class CustomEntityRelationship(BaseModel):
    other_entity_name: str
    other_entity_type: str
    direction: str
    symmetric: bool = False
    rel_type: str | None
    from_chapter: int | None
    to_chapter: int | None
    notes: str | None


class CustomEntityDetail(BaseModel):
    id: str
    name: str
    entity_type: str
    description: str | None = None
    relationships: list[CustomEntityRelationship]


class NovelCreate(BaseModel):
    title: str
    author: str | None = None
    language: str | None = None
    custom_entity_types: list[NovelEntityTypeInput] = []


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
    physical_state: str | None
    appearance: str | None
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
    involved_factions: list[str]


class CharacterRelationshipRow(BaseModel):
    other_entity_name: str
    other_entity_type: str
    direction: str  # "from" = this character is entity_a; "to" = entity_b
    symmetric: bool = False  # rel_type is mutual (e.g. spouse_of) — direction is not meaningful
    rel_type: str | None
    from_chapter: int | None
    to_chapter: int | None
    notes: str | None


class SharedDynamicRow(BaseModel):
    id: UUID
    entity_a_name: str
    entity_b_name: str
    chapter_number: int
    description: str | None


class CharacterDynamicRow(BaseModel):
    id: UUID
    chapter_number: int
    other_entity_name: str
    other_entity_type: str
    description: str | None


class CharacterDetail(BaseModel):
    identity: CharacterSummary
    current_state: CharacterStateRow | None
    history: list[CharacterStateRow]
    relationships: list[CharacterRelationshipRow]
    dynamics: list[CharacterDynamicRow]
    events: list[CharacterEventRow]


class ChapterCritiqueSummary(BaseModel):
    passed: bool
    fails: int
    warns: int


class ChapterSummary(BaseModel):
    id: UUID
    number: int
    title: str | None
    summary: str | None
    summary_short: str | None = None
    summary_long: str | None = None
    processed_at: datetime | None
    critique: ChapterCritiqueSummary | None = None


class ChapterEvent(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    event_type: str | None
    impact_level: str | None
    involved_characters: list[str]
    involved_locations: list[str]
    involved_objects: list[str]
    involved_factions: list[str]



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
    status_at_cutoff: str
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


class CritiqueChapterRow(BaseModel):
    chapter_number: int
    passed: bool
    fails: int
    warns: int


class CritiqueFinding(BaseModel):
    check_name: str
    severity: str
    message: str
    quote: str | None = None
    evidence: dict | None = None


class CritiqueReportDetail(BaseModel):
    chapter_number: int
    passed: bool
    ran_at: datetime
    stats: dict | None = None
    findings: list[CritiqueFinding]


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
    edge_kind: str | None = None
    tooltip: str | None = None
    symmetric: bool = False  # rel_type is mutual (e.g. spouse_of) — arrow direction is not meaningful


class RelationshipGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class EntityGraphNode(BaseModel):
    id: UUID
    label: str
    entity_type: str
    native_id: UUID
    description: str | None = None


class EntityGraph(BaseModel):
    nodes: list[EntityGraphNode]
    edges: list[GraphEdge]


class ProcessRequest(BaseModel):
    number: int
    text: str
    replace: bool = False


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    current_pass: str | None = None
    passes_done: int = 0
    total_passes: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None


class LocationSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None
    first_appearance_chapter: int | None


class LocationDetail(BaseModel):
    identity: LocationSummary
    events: list[ChapterEvent]
    characters: list[str]


class ObjectSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None
    significance: str | None
    first_appearance_chapter: int | None


class ObjectRelationship(BaseModel):
    character_name: str
    rel_type: str | None
    from_chapter: int | None
    to_chapter: int | None
    notes: str | None


class ObjectDetail(BaseModel):
    identity: ObjectSummary
    events: list[ChapterEvent]
    characters: list[str]
    relationships: list[ObjectRelationship]


class FactionSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None


class FactionDetail(BaseModel):
    identity: FactionSummary
    events: list[ChapterEvent]
    characters: list[str]


# --------------------------------------------------------------------------
# SOTA-upgrade additions: scenes, knowledge graph, commitments, canon facts,
# bitemporal location/possession edges, multi-granularity chapter summaries.
# --------------------------------------------------------------------------


class SceneRow(BaseModel):
    id: UUID
    chapter_id: UUID
    chapter_number: int
    scene_index: int
    pov_character_id: UUID | None
    pov_character_name: str | None
    location_id: UUID | None
    location_name: str | None
    time_anchor: str | None
    story_time_ordinal: int | None
    present_character_names: list[str]
    summary: str | None


class CommitmentRow(BaseModel):
    id: UUID
    foreshadow_text: str
    foreshadow_chapter: int
    payoff_text: str | None
    payoff_chapter: int | None
    # JSONB column: the extractor may store a plain string, an object, or null.
    trigger_predicate: Any | None = None
    status: str
    status_at_cutoff: str
    weight: float | None
    related_entity_names: list[str]
    age_chapters: int | None


class CanonFactRow(BaseModel):
    id: UUID
    kind: str
    subject_entity_id: UUID | None
    subject_name: str | None
    predicate: str
    value: str
    source_chapter: int | None
    confidence: float | None
    locked: bool


class KnowsEdgeRow(BaseModel):
    id: UUID
    character_id: UUID
    character_name: str
    fact_description: str
    learned_chapter: int
    source_type: str | None
    source_event_id: UUID | None
    certainty: float | None
    shared_with_names: list[str]


class LocationEdgeRow(BaseModel):
    id: UUID
    entity_id: UUID
    entity_name: str | None
    entity_type: str | None
    location_id: UUID
    location_name: str | None
    since_chapter: int
    until_chapter: int | None
    certainty: float | None


class PossessionEdgeRow(BaseModel):
    id: UUID
    character_id: UUID
    character_name: str | None
    object_id: UUID
    object_name: str | None
    since_chapter: int
    until_chapter: int | None
    certainty: float | None


class CanonFactPatch(BaseModel):
    locked: bool | None = None
    value: str | None = None


class CanonFactCreate(BaseModel):
    subject_entity_id: UUID
    predicate: str
    value: str
    kind: str = "other"
    locked: bool = False


class EntityMergeRequest(BaseModel):
    source_entity_id: UUID
    target_entity_id: UUID


class SearchResultRow(BaseModel):
    kind: str
    chapter_number: int | None = None
    score: float
    snippet: str | None = None


class SearchResults(BaseModel):
    results: list[SearchResultRow]
