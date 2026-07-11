from __future__ import annotations

"""Reference-only resolution skip paths for the Phase 3 extras persistence.

persist_scenes / persist_knows_edges / persist_commitments all resolve names
with create=False (see resolver.EntityResolver docstring): an unknown name
must not mint a phantom entity. This module exercises the "drop when
unresolvable" branches directly against persist_extras.py, complementing
test_persist_no_phantom_characters.py which covers events via
pipeline._persist_extraction instead.
"""

import uuid

from pipeline.embeddings import EmbeddingService
from pipeline.extraction.persist_extras import (
    persist_commitments,
    persist_knows_edges,
    persist_scenes,
)
from pipeline.extraction.resolver import ResolvedEntity


class FakeResolver:
    """Resolves a fixed set of known names to a stable uid; any other name
    resolves to None when create=False, matching EntityResolver's
    reference-only contract without touching a real DB."""

    def __init__(self, known_names: dict[str, str] | None = None):
        self.known = {name.lower(): uid for name, uid in (known_names or {}).items()}

    def _lookup(self, name, create):
        key = (name or "").strip().lower()
        if key in self.known:
            uid = self.known[key]
            return ResolvedEntity(entity_id=uid, universal_id=uid, created=False)
        if create:
            uid = str(uuid.uuid4())
            self.known[key] = uid
            return ResolvedEntity(entity_id=uid, universal_id=uid, created=True)
        return None

    def resolve_character(self, name, metadata=None, *, create=True):
        return self._lookup(name, create)

    def resolve_location(self, name, metadata=None, *, create=True):
        return self._lookup(name, create)

    def resolve_faction(self, name, metadata=None, *, create=True):
        return self._lookup(name, create)

    def resolve_object(self, name, metadata=None, *, create=True):
        return self._lookup(name, create)

    def resolve_any_entity(self, name, *, create=True):
        resolved = self._lookup(name, create)
        return resolved.universal_id if resolved else None


class FakeDB:
    """Records every fetchval/execute call; fetchval returns a fresh uuid
    (standing in for RETURNING id)."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append((query, tuple(params or ())))
        return uuid.uuid4()

    def fetchall(self, query, params=None, *, dict_rows=False):
        return []

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        return None

    def execute(self, query, params=None):
        self.calls.append((query, tuple(params or ())))


def _embedder() -> EmbeddingService:
    return EmbeddingService(use_mock=True)


def _find(db: FakeDB, needle: str):
    return next(c for c in db.calls if needle in c[0])


# ---------------------------------------------------------------------------
# Scenes: POV / present character resolution
# ---------------------------------------------------------------------------


def test_scene_pov_unresolvable_is_dropped_but_scene_still_persists():
    db = FakeDB()
    resolver = FakeResolver({"Alice": "alice-uid"})
    inserted = persist_scenes(
        db,
        chapter_id="ch1",
        scenes_data=[{
            "scene_index": 0,
            "pov_character_name": "Ghost",
            "present_character_names": [],
            "summary": "A quiet moment.",
        }],
        resolver=resolver,
        embedder=_embedder(),
    )
    assert len(inserted) == 1
    _, params = _find(db, "INSERT INTO scenes")
    pov_character_id = params[2]
    assert pov_character_id is None


def test_scene_present_character_unresolvable_is_dropped_from_present_ids():
    db = FakeDB()
    resolver = FakeResolver({"Alice": "alice-uid"})
    inserted = persist_scenes(
        db,
        chapter_id="ch1",
        scenes_data=[{
            "scene_index": 0,
            "present_character_names": ["Alice", "Ghost"],
            "summary": "A crowded room.",
        }],
        resolver=resolver,
        embedder=_embedder(),
    )
    assert len(inserted) == 1
    _, params = _find(db, "INSERT INTO scenes")
    present_characters = params[5]
    assert present_characters == ["alice-uid"]


# ---------------------------------------------------------------------------
# knows_edges
# ---------------------------------------------------------------------------


def test_knows_edge_with_unresolvable_knower_is_dropped_entirely():
    db = FakeDB()
    resolver = FakeResolver({"Alice": "alice-uid"})
    inserted = persist_knows_edges(
        db,
        chapter_number=1,
        learnings=[{"character_name": "Ghost", "fact_description": "A secret."}],
        resolver=resolver,
    )
    assert inserted == []
    assert not any("INSERT INTO knows_edges" in q for q, _ in db.calls)


def test_knows_edge_shared_with_unresolvable_name_dropped_edge_still_persists():
    db = FakeDB()
    resolver = FakeResolver({"Alice": "alice-uid", "Bob": "bob-uid"})
    inserted = persist_knows_edges(
        db,
        chapter_number=1,
        learnings=[{
            "character_name": "Alice",
            "fact_description": "A secret.",
            "shared_with_character_names": ["Bob", "Ghost"],
        }],
        resolver=resolver,
    )
    assert len(inserted) == 1
    _, params = _find(db, "INSERT INTO knows_edges")
    shared_with = params[5]
    assert shared_with == ["bob-uid"]


# ---------------------------------------------------------------------------
# Commitments
# ---------------------------------------------------------------------------


def test_commitment_unresolvable_related_entity_dropped_commitment_still_persists():
    db = FakeDB()
    resolver = FakeResolver({"Alice": "alice-uid"})
    result = persist_commitments(
        db,
        novel_id="n1",
        chapter_number=1,
        foreshadows=[{
            "foreshadow_text": "The sword will return.",
            "related_entity_names": ["Alice", "Ghost"],
        }],
        payoffs=[],
        resolver=resolver,
        embedder=_embedder(),
    )
    assert len(result["inserted"]) == 1
    _, params = _find(db, "INSERT INTO commitments")
    related_entity_ids = params[6]
    assert related_entity_ids == ["alice-uid"]
