from unittest.mock import MagicMock
from pipeline.extraction.resolver import EntityResolver, ResolvedEntity


def _make_db(entities=None):
    db = MagicMock()
    db.entities = entities or []
    db.fetchone = MagicMock(return_value=None)
    db.fetchval = MagicMock(return_value="realm-uuid-123")
    db.execute = MagicMock()
    return db


def test_resolve_custom_entity_creates_in_entities_only():
    db = _make_db()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    result = resolver.resolve_custom_entity("The 93rd Universe", "realm", {"description": "A dimension."})
    assert isinstance(result, ResolvedEntity)
    # entity_id == universal_id for custom types (no dedicated table)
    assert result.entity_id == result.universal_id
    assert result.created is True
    # Only inserted into entities
    db.fetchval.assert_called_once()
    call_sql = db.fetchval.call_args[0][0]
    assert "entities" in call_sql


def test_resolve_custom_entity_cache_hit():
    db = _make_db()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    r1 = resolver.resolve_custom_entity("The 93rd Universe", "realm", {})
    r2 = resolver.resolve_custom_entity("The 93rd Universe", "realm", {})
    assert r1.entity_id == r2.entity_id
    assert db.fetchval.call_count == 1  # only created once


def test_resolve_custom_entity_finds_existing_in_fake_db():
    from uuid import uuid4
    eid = uuid4()
    db = _make_db(entities=[
        {"id": eid, "novel_id": "novel-1", "entity_type": "realm", "name": "The 93rd Universe"},
    ])
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    result = resolver.resolve_custom_entity("The 93rd Universe", "realm", {})
    assert str(result.entity_id) == str(eid)
    assert result.created is False
    db.fetchval.assert_not_called()


def test_resolve_custom_entity_empty_name_raises():
    db = _make_db()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    try:
        resolver.resolve_custom_entity("", "realm", {})
        assert False, "Should have raised"
    except ValueError:
        pass
