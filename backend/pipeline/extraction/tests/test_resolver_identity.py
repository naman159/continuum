"""Entity identity must never corrupt itself silently.

For a memory layer, a wrong merge is worse than a missing answer: the wrong
one is unrecoverable without manual repair and poisons every later chapter,
while an unresolved name simply fails to link.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.extraction.resolver import EntityResolver


@pytest.fixture
def novel(db):
    novel_id = str(db.fetchval(
        "INSERT INTO novels (title) VALUES (%s) RETURNING id",
        (f"identity-{uuid.uuid4()}",), commit=True,
    ))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def _aliases(db, novel_id, name):
    return db.fetchval(
        "SELECT aliases FROM characters WHERE novel_id = %s AND name = %s",
        (novel_id, name),
    ) or []


def test_ambiguous_partial_name_refuses_to_guess(db, novel):
    """Two characters sharing a first name: a bare 'Elizabeth' binds to neither.

    This used to LIMIT 1 with no ORDER BY, so it bound to whichever row the
    planner returned and then persisted that guess as an alias — permanently
    routing every later bare 'Elizabeth' to the wrong character.
    """
    r = EntityResolver(db, novel_id=novel, chapter_number=1)
    r.resolve_character("Elizabeth Bennet", {})
    r.resolve_character("Elizabeth Elliot", {})

    assert r.resolve_character("Elizabeth", create=False) is None
    assert _aliases(db, novel, "Elizabeth Bennet") == []
    assert _aliases(db, novel, "Elizabeth Elliot") == []


def test_unambiguous_partial_name_still_resolves(db, novel):
    """The useful half of the behaviour is preserved when there's one candidate."""
    r = EntityResolver(db, novel_id=novel, chapter_number=1)
    created = r.resolve_character("Jane Bennet", {})

    found = r.resolve_character("Jane", create=False)
    assert found is not None
    assert found.entity_id == created.entity_id


def test_reference_only_pass_never_writes_an_alias(db, novel):
    """create=False means 'look this up', not 'decide who this is'.

    Scene POV lists, knows-edges and event actors all resolve this way; a
    stray name there is far weaker evidence than the canonicalizer demands
    before it will persist an alias.
    """
    r = EntityResolver(db, novel_id=novel, chapter_number=1)
    r.resolve_character("Jane Bennet", {})

    r.resolve_character("Jane", create=False)
    assert _aliases(db, novel, "Jane Bennet") == [], (
        "a reference-only lookup must not mutate identity"
    )


def test_extractor_aliases_merge_into_an_existing_character(db, novel):
    """A nickname established in a later chapter must not be discarded.

    _create_entity writes metadata['aliases'] only on create, so the resolver
    used to drop them for a character that already existed — and the next bare
    use of that nickname minted a duplicate character row.
    """
    r1 = EntityResolver(db, novel_id=novel, chapter_number=1)
    created = r1.resolve_character("Elizabeth Bennet", {"aliases": []})

    r2 = EntityResolver(db, novel_id=novel, chapter_number=3)
    r2.resolve_character("Elizabeth Bennet", {"aliases": ["Lizzy", "Eliza"]})

    assert sorted(_aliases(db, novel, "Elizabeth Bennet")) == ["Eliza", "Lizzy"]

    # And the payoff: the nickname now resolves instead of minting a duplicate.
    r3 = EntityResolver(db, novel_id=novel, chapter_number=4)
    resolved = r3.resolve_character("Lizzy", create=False)
    assert resolved is not None and resolved.entity_id == created.entity_id
    assert db.fetchval(
        "SELECT count(*) FROM characters WHERE novel_id = %s", (novel,)
    ) == 1


def test_alias_that_belongs_to_another_character_is_not_stolen(db, novel):
    """Merging aliases is additive, never a repoint of an existing identity."""
    r = EntityResolver(db, novel_id=novel, chapter_number=1)
    r.resolve_character("Mary Crawford", {})
    r.resolve_character("Henry Crawford", {})

    # Extraction wrongly claims "Mary Crawford" is an alias of Henry.
    r2 = EntityResolver(db, novel_id=novel, chapter_number=2)
    r2.resolve_character("Henry Crawford", {"aliases": ["Mary Crawford"]})

    assert _aliases(db, novel, "Henry Crawford") == []
