"""Runtime contracts for model output; JSON validity alone is insufficient.

Validate before normalization/persistence. Optional story values must be explicit
nulls; empty finding lists are valid. Errors describe fields, never manuscript text.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, create_model, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
SourceType = Literal['dialogue', 'observation', 'inference', 'witnessed', 'told', 'assumed']
EventType = Literal['action', 'revelation', 'death', 'arrival', 'conflict', 'other']
EntityType = Literal['character', 'location', 'object', 'faction']


class Record(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class Named(Record):
    name: Text
    description: str


class Character(Named):
    aliases: list[Text]


class Location(Named):
    parent_location: str | None


class Object(Named):
    owner_name: str | None
    distinguishing_properties: str | None
    significance: str | None


class CustomEntity(Named):
    type: Text


class NewEntities(Record):
    characters: list[Character]
    locations: list[Location]
    factions: list[Named]
    objects: list[Object]
    custom_entities: list[CustomEntity] = Field(default_factory=list)


class StateDelta(Record):
    kind: Literal['possession', 'location', 'status']
    character_name: Text
    object_name: str | None
    location_name: str | None
    change: Literal['gain', 'loss'] | None
    attribute: Literal['emotional_state', 'goals', 'physical_state', 'appearance', 'notes'] | None
    value: str | None
    quote: Text

    @model_validator(mode='after')
    def required_by_kind(self):
        fields = {'possession': ('object_name', 'change'), 'location': ('location_name',),
                  'status': ('attribute', 'value')}[self.kind]
        if any(not (getattr(self, key) or '').strip() for key in fields):
            raise ValueError('missing fields required by delta kind')
        return self


class Event(Record):
    description: Text
    event_type: EventType
    impact_level: Literal['low', 'medium', 'high', 'critical']
    involved_characters: list[Text]
    involved_locations: list[Text]
    involved_objects: list[Text]
    involved_factions: list[Text]


class ThreadUpdate(Record):
    title: Text
    description: str
    status: Literal['open', 'progressing', 'closed']
    impact: Literal['opens', 'advances', 'closes']
    thread_type: Literal['mystery', 'conflict', 'goal', 'prophecy', 'secret', 'other']
    event_description: str


class ContinuityFlag(Record):
    description: Text
    flag_type: Literal['foreshadowing', 'planted_detail', 'setup', 'callback', 'other']


class Relationship(Record):
    entity_a: Text
    entity_b: Text
    rel_type: Text
    symmetric: bool | None
    from_chapter: Annotated[int, Field(ge=1)] | None
    to_chapter: Annotated[int, Field(ge=1)] | None
    notes: str | None


class Dynamic(Record):
    entity_a: Text
    entity_b: Text
    description: Text


class Scene(Record):
    scene_index: Annotated[int, Field(ge=0)]
    pov_character_name: str | None
    location_name: str | None
    time_anchor: str | None
    present_character_names: list[Text]
    summary: Text
    starts_at_excerpt: Text


class Learning(Record):
    character_name: Text
    fact_description: Text
    source_type: SourceType
    source_character_name: str | None
    certainty: Literal['high', 'medium', 'low']
    shared_with_character_names: list[Text]


class Foreshadow(Record):
    foreshadow_text: Text
    trigger_predicate: Text
    weight: Literal['low', 'medium', 'high']
    related_entity_names: list[Text]


class Payoff(Record):
    payoff_text: Text
    matches_foreshadow: str


class CanonFact(Record):
    subject_name: Text
    subject_type: EntityType
    predicate: Text
    value: Text
    kind: Literal['physical', 'relational', 'world_rule', 'backstory', 'other']
    confidence: Probability
    quote: Text


def _pass_model(name: str, **fields):
    return create_model(name, __base__=Record, **{k: (v, ...) for k, v in fields.items()})


PASS_MODELS = {
    'chapter_summary': _pass_model('ChapterSummary', summary=Text),
    'new_entities': NewEntities,
    'state_deltas': _pass_model('StateDeltas', state_deltas=list[StateDelta]),
    'events': _pass_model('Events', events=list[Event]),
    'thread_updates': _pass_model('Threads', thread_updates=list[ThreadUpdate]),
    'continuity_flags': _pass_model('Flags', continuity_flags=list[ContinuityFlag]),
    'relationship_updates': _pass_model('Relationships', relationship_updates=list[Relationship]),
    'dynamics_updates': _pass_model('Dynamics', dynamics_updates=list[Dynamic]),
    'scene_segmentation': _pass_model('Scenes', scenes=list[Scene]),
    'multi_granularity_summaries': _pass_model('Summaries', summary_short=Text, summary_medium=Text, summary_long=Text),
    'knowledge_state_deltas': _pass_model('Learnings', learnings=list[Learning]),
    'commitments': _pass_model('Commitments', foreshadows_introduced=list[Foreshadow], payoffs_delivered=list[Payoff]),
    'canon_facts': _pass_model('CanonFacts', canon_facts=list[CanonFact]),
}


def validation_error(exc: ValidationError) -> str:
    # Pydantic's default str(exc) includes raw input; do not log private prose.
    return '; '.join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors(include_input=False))


def validate_pass(pass_name: str, payload: dict, custom_entity_types: list[dict] | None = None) -> dict:
    model = PASS_MODELS[pass_name]
    try:
        validated = model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(validation_error(exc)) from None
    if pass_name == 'new_entities':
        registered = {t['name'].strip().lower() for t in custom_entity_types or []}
        if registered and 'custom_entities' not in payload:
            raise ValueError('custom_entities: missing')
        if any(e.type.lower() not in registered for e in validated.custom_entities):
            raise ValueError('custom_entities.type: unregistered')
    return validated.model_dump(exclude_unset=True)
