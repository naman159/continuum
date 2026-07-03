from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StateSnapshot:
    character_id: str
    chapter_id: str
    chapter_number: int
    location_id: str | None = None
    emotional_state: str | None = None
    goals: str | None = None
    knowledge: list[str] = field(default_factory=list)
    physical_state: str | None = None
    appearance: str | None = None
    notes: str | None = None


@dataclass
class LocationFact:
    entity_id: str
    location_id: str
    since_chapter: int
    until_chapter: int | None = None
    evidence_event_id: str | None = None
    certainty: float = 1.0


@dataclass
class PossessionFact:
    character_id: str
    object_id: str
    since_chapter: int
    until_chapter: int | None = None
    evidence_event_id: str | None = None
    certainty: float = 1.0


@dataclass
class MaterializeResult:
    novel_id: str
    through_chapter: int
    snapshots_written: int
    location_edges_written: int
    possession_edges_written: int


__all__ = [
    "StateSnapshot",
    "LocationFact",
    "PossessionFact",
    "MaterializeResult",
]
