"""Strict claim shapes and verbatim evidence before a continuity verdict."""
from pydantic import ValidationError

from pipeline.extraction.validation import EntityType, EventType, Record, SourceType, Text, validation_error


class Mention(Record):
    entity_name: Text
    entity_type: EntityType
    predicate: Text
    claimed_value: Text
    quote: Text


class KnowledgeClaim(Record):
    character_name: Text
    fact_description: Text
    source_type: SourceType
    learned_this_chapter: bool
    quote: Text


class PossessionClaim(Record):
    character_name: Text
    object_name: Text
    quote: Text


class DraftEvent(Record):
    description: Text
    event_type: EventType


class Claims(Record):
    mentions: list[Mention]
    knowledge_claims: list[KnowledgeClaim]
    possession_claims: list[PossessionClaim]
    events: list[DraftEvent]


def validate_claims(data: dict, text: str) -> dict:
    try:
        claims = Claims.model_validate(data).model_dump()
    except ValidationError as exc:
        raise RuntimeError(f'draft_claims: missing or malformed fields: {validation_error(exc)}') from None
    for kind in ('mentions', 'knowledge_claims', 'possession_claims'):
        for index, claim in enumerate(claims[kind]):
            if claim['quote'] not in text:
                raise RuntimeError(f'draft_claims: {kind}.{index}.quote is not verbatim draft evidence')
    return claims
