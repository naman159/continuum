from __future__ import annotations

from pipeline.extraction.resolver import EntityResolver, ResolvedEntity


class FakeDBForResolver:
    """Minimal fake DB for resolver tests."""

    def __init__(self) -> None:
        self._entities: list[dict] = []
        self._characters: list[dict] = []

    def fetchone(self, sql: str, params=(), dict_rows: bool = False):
        sql_lower = sql.lower()
        if "from entities" in sql_lower:
            name = params[1] if len(params) > 1 else None
            novel_id = params[0]
            for e in self._entities:
                if str(e["novel_id"]) == str(novel_id) and e["name"].lower() == str(name).lower():
                    return (e["id"],) if not dict_rows else e
            return None
        if "from characters" in sql_lower and "entity_id" in sql_lower:
            char_id = params[0]
            for c in self._characters:
                if str(c["id"]) == str(char_id):
                    row = {"entity_id": c.get("entity_id")}
                    return (c.get("entity_id"),) if not dict_rows else row
            return None
        if "from characters" in sql_lower:
            novel_id = params[0]
            name = params[1] if len(params) > 1 else None
            for c in self._characters:
                if str(c["novel_id"]) == str(novel_id) and c["name"].lower() == str(name).lower():
                    return (c["id"], c.get("entity_id")) if not dict_rows else c
            return None
        return None

    def fetchval(self, sql: str, params=(), commit: bool = False):
        import uuid
        new_id = uuid.uuid4()
        sql_lower = sql.lower()
        if "insert into entities" in sql_lower:
            entity = {"id": new_id, "novel_id": params[0], "entity_type": params[1], "name": params[2]}
            self._entities.append(entity)
            return new_id
        if "insert into characters" in sql_lower:
            char = {"id": new_id, "novel_id": params[0], "name": params[1], "entity_id": params[2]}
            self._characters.append(char)
            return new_id
        return new_id

    def execute(self, sql: str, params=(), commit: bool = False):
        pass


def test_resolve_character_creates_entity_record():
    db = FakeDBForResolver()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    result = resolver.resolve_character("Alice", {"description": "Hero"})

    assert result.entity_id is not None
    assert result.universal_id is not None
    assert result.created is True
    assert any(e["name"] == "Alice" and e["entity_type"] == "character" for e in db._entities)


def test_resolve_character_returns_same_ids_on_second_call():
    db = FakeDBForResolver()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    first = resolver.resolve_character("Alice")
    second = resolver.resolve_character("Alice")

    assert first.entity_id == second.entity_id
    assert first.universal_id == second.universal_id
    assert second.created is False


def test_resolve_location_by_alias_returns_existing():
    """Resolver finds a location by alias instead of creating a new one."""
    import uuid
    from pipeline.extraction.resolver import EntityResolver

    class AliasDB:
        def __init__(self):
            self.loc_id = str(uuid.uuid4())
            self.entity_id = str(uuid.uuid4())
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            # Exact name lookup returns None (no exact match for alias form)
            if "lower(name) = lower" in query:
                return None
            # Alias lookup — match when the query uses unnest(aliases)
            if "unnest(aliases)" in query:
                return (self.loc_id, self.entity_id)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = AliasDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    resolved = resolver.resolve_location("Netherfield")
    assert resolved.entity_id == db.loc_id
    assert resolved.created is False


def test_resolve_any_entity_finds_entity_by_name():
    """resolve_any_entity returns the entity's universal ID from the entities table."""
    import uuid
    from pipeline.extraction.resolver import EntityResolver

    obj_entity_id = str(uuid.uuid4())

    class EntityDB:
        def __init__(self):
            self.entities = [
                {"id": obj_entity_id, "novel_id": "novel-1", "entity_type": "object", "name": "the One Ring"}
            ]
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None  # no exact name / alias hit via characters table

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = EntityDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    uid = resolver.resolve_any_entity("the One Ring")
    assert uid == obj_entity_id


def test_resolve_any_entity_falls_back_to_character():
    """resolve_any_entity falls back to resolve_character when entity not in entities table."""
    import uuid
    from pipeline.extraction.resolver import EntityResolver

    char_entity_id = str(uuid.uuid4())
    char_id = str(uuid.uuid4())

    class FallbackDB:
        def __init__(self):
            self.entities = []  # empty — will fall through to resolve_character
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            # exact name match in characters table
            if "lower(name) = lower" in query and "characters" in query:
                return (char_id, char_entity_id)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = FallbackDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    uid = resolver.resolve_any_entity("Frodo")
    assert uid == char_entity_id
