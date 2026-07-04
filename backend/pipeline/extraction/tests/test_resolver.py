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
            # The combined name-or-alias query matches via the alias arm
            # (an exact-name-only query would return nothing).
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


# ---------------------------------------------------------------------------
# Parent-location folding
# ---------------------------------------------------------------------------

def test_resolve_location_folds_into_parent_when_parent_location_set():
    """
    When metadata contains parent_location and that parent exists in the DB,
    resolve_location should return the parent's entity rather than creating a
    new sub-location row.
    """
    import uuid

    parent_loc_id = str(uuid.uuid4())
    parent_entity_id = str(uuid.uuid4())

    class ParentDB:
        def __init__(self):
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            # Exact name lookup for "Corporate Office" → return parent row
            if "lower(name) = lower" in query and params and str(params[1]).lower() == "corporate office":
                return (parent_loc_id, parent_entity_id)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = ParentDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=5)
    result = resolver.resolve_location(
        "Corporate office (14th floor)",
        {"parent_location": "Corporate Office", "description": "The 14th floor."},
    )
    assert result.entity_id == parent_loc_id
    assert result.universal_id == parent_entity_id
    assert result.created is False
    # The sub-location name must NOT have been inserted
    assert not any("14th floor" in str(params) for _, params in db.executed if params)


def test_resolve_location_creates_parent_if_missing():
    """
    When metadata has parent_location but the parent doesn't exist yet,
    resolve_location creates the parent (not the sub-location).
    """
    import uuid

    class MissingParentDB:
        def __init__(self):
            self.inserted_names: list[str] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None  # nothing exists yet

        def fetchval(self, query, params=None, *, commit=False):
            new_id = uuid.uuid4()
            if params and len(params) >= 3:
                self.inserted_names.append(str(params[2]))  # name arg in INSERT INTO entities
            return new_id

        def execute(self, query, params=None):
            pass

    db = MissingParentDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    resolver.resolve_location(
        "Elevator in corporate building",
        {"parent_location": "Corporate Building", "description": "The elevator."},
    )
    # The entity created should be for "Corporate Building", not the elevator
    assert "Corporate Building" in db.inserted_names
    assert "Elevator in corporate building" not in db.inserted_names


def test_resolve_location_without_parent_location_creates_exact_name():
    """When no parent_location is in metadata, behaviour is unchanged."""
    import uuid

    class EmptyDB:
        def __init__(self):
            self.inserted_names: list[str] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None

        def fetchval(self, query, params=None, *, commit=False):
            if params and len(params) >= 3:
                self.inserted_names.append(str(params[2]))
            return uuid.uuid4()

        def execute(self, query, params=None):
            pass

    db = EmptyDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    resolver.resolve_location("Pemberley", {"description": "Grand estate."})
    assert "Pemberley" in db.inserted_names


# ---------------------------------------------------------------------------
# resolve_any_entity: aliased non-character entities must not create characters
# ---------------------------------------------------------------------------

def test_resolve_any_entity_finds_faction_by_alias_no_phantom_character():
    """An alias known only to the factions table must resolve to the faction,
    not fall through to creating a character named after the alias."""
    import uuid

    faction_id = str(uuid.uuid4())
    faction_entity_id = str(uuid.uuid4())

    class FactionAliasDB:
        def __init__(self):
            self.inserts: list[tuple[str, tuple]] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            if "FROM factions" in query and "unnest(aliases)" in query:
                return (faction_id, faction_entity_id)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            import uuid as _uuid
            self.inserts.append((query, tuple(params or ())))
            return _uuid.uuid4()

        def execute(self, query, params=None):
            pass

    db = FactionAliasDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=3)
    uid = resolver.resolve_any_entity("the Empire")
    assert uid == faction_entity_id
    assert db.inserts == [], "no character must be created for an aliased faction"


def test_resolve_any_entity_creates_character_only_as_last_resort():
    """When nothing matches anywhere, the fallback still creates a character."""
    import uuid

    class NothingDB:
        def __init__(self):
            self.inserts: list[str] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None

        def fetchval(self, query, params=None, *, commit=False):
            self.inserts.append(query)
            return uuid.uuid4()

        def execute(self, query, params=None):
            pass

    db = NothingDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    uid = resolver.resolve_any_entity("Brand New Person")
    assert uid
    assert any("INSERT INTO characters" in q for q in db.inserts)


def test_resolve_custom_entity_finds_by_alias():
    """Custom entities resolve through entities.aliases (no duplicate row)."""
    import uuid

    realm_id = str(uuid.uuid4())

    class CustomAliasDB:
        def __init__(self):
            self.inserts: list[str] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            if "FROM entities" in query and "unnest(aliases)" in query:
                return (realm_id,)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            self.inserts.append(query)
            return uuid.uuid4()

        def execute(self, query, params=None):
            pass

    db = CustomAliasDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=2)
    result = resolver.resolve_custom_entity("Ninety-Third Universe", "realm")
    assert result.entity_id == realm_id
    assert result.created is False
    assert db.inserts == []
