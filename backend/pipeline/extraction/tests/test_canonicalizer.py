from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline.extraction.canonicalizer import (
    CharacterCanonicalizer,
    EntityCanonicalizer,
    IntraExtractionDeduplicator,
    collect_character_names,
    collect_names_by_type,
)


class FakeDB:
    def __init__(self, roster_rows: list[dict]) -> None:
        self.roster_rows = roster_rows
        self.executed: list[tuple[str, tuple]] = []

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        return [dict(row) for row in self.roster_rows]

    def execute(self, query, params=None):
        self.executed.append((query, tuple(params or ())))
        if "UPDATE characters" in query and "aliases = %s" in query:
            new_aliases, char_id, _novel_id = params
            for row in self.roster_rows:
                if str(row["id"]) == str(char_id):
                    row["aliases"] = list(new_aliases)


def _make_completion(payload: dict | str):
    body = payload if isinstance(payload, str) else json.dumps(payload)

    def fake_completion(**_kwargs):
        message = SimpleNamespace(content=body)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])

    return fake_completion


CHAPTER_TEXT = (
    "Mr. Darcy, the master of Pemberley, walked into the room. "
    "He nodded curtly to Elizabeth's father. "
    "Eliza watched him with quiet amusement."
)


@pytest.fixture
def roster_rows():
    return [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Mr. Darcy",
            "aliases": [],
            "description": "A wealthy gentleman, owner of Pemberley.",
            "location_id": None,
            "emotional_state": None,
            "goals": None,
            "physical_state": None,
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "Mr. Bennet",
            "aliases": [],
            "description": "Father of the Bennet daughters.",
            "location_id": None,
            "emotional_state": None,
            "goals": None,
            "physical_state": None,
        },
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "name": "Elizabeth Bennet",
            "aliases": ["Lizzy"],
            "description": "Second daughter of the Bennets.",
            "location_id": None,
            "emotional_state": None,
            "goals": None,
            "physical_state": None,
        },
    ]


def _make_canon(db, completion_fn):
    return EntityCanonicalizer(
        db,
        novel_id="novel-1",
        use_mock=False,
        completion_fn=completion_fn,
    )


def test_existing_with_valid_anchor_appends_alias(roster_rows):
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "the master of Pemberley",
                "verdict": "existing",
                "id": "11111111-1111-1111-1111-111111111111",
                "grammatical_anchor": "Mr. Darcy, the master of Pemberley",
                "reasoning": "apposition",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    merges = canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"the master of Pemberley"}, "location": set(), "object": set(), "faction": set()},
    )
    assert merges == {"character": {"the master of Pemberley": "11111111-1111-1111-1111-111111111111"}}
    darcy = next(r for r in roster_rows if r["name"] == "Mr. Darcy")
    assert "the master of Pemberley" in darcy["aliases"]


def test_existing_with_missing_anchor_is_rejected(roster_rows):
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "the master of Pemberley",
                "verdict": "existing",
                "id": "11111111-1111-1111-1111-111111111111",
                "grammatical_anchor": "",
                "reasoning": "guessing",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    merges = canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"the master of Pemberley"}, "location": set(), "object": set(), "faction": set()},
    )
    assert merges == {}
    darcy = next(r for r in roster_rows if r["name"] == "Mr. Darcy")
    assert darcy["aliases"] == []
    assert not any("UPDATE characters" in q for q, _ in db.executed)


def test_existing_with_anchor_not_in_text_is_rejected(roster_rows):
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "the master of Pemberley",
                "verdict": "existing",
                "id": "11111111-1111-1111-1111-111111111111",
                "grammatical_anchor": "Mr. Darcy of Pemberley estate, lord of the lake",
                "reasoning": "hallucinated",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    merges = canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"the master of Pemberley"}, "location": set(), "object": set(), "faction": set()},
    )
    assert merges == {}
    darcy = next(r for r in roster_rows if r["name"] == "Mr. Darcy")
    assert darcy["aliases"] == []


def test_candidate_already_in_aliases_is_noop(roster_rows):
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "Lizzy",
                "verdict": "existing",
                "id": "33333333-3333-3333-3333-333333333333",
                "grammatical_anchor": "Eliza watched him with quiet amusement.",
                "reasoning": "alias",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"Lizzy"}, "location": set(), "object": set(), "faction": set()},
    )
    elizabeth = next(r for r in roster_rows if r["name"] == "Elizabeth Bennet")
    assert elizabeth["aliases"].count("Lizzy") == 1
    assert not any("UPDATE characters" in q for q, _ in db.executed)


def test_malformed_json_response_is_safe(roster_rows):
    db = FakeDB(roster_rows)
    canon = _make_canon(db, _make_completion("not json at all"))
    merges = canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"the master of Pemberley"}, "location": set(), "object": set(), "faction": set()},
    )
    assert merges == {}
    assert all("UPDATE characters" not in q for q, _ in db.executed)


def test_new_verdict_does_nothing_to_roster(roster_rows):
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "John Smith",
                "verdict": "new",
                "id": None,
                "grammatical_anchor": None,
                "reasoning": "introduced fresh",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"John Smith"}, "location": set(), "object": set(), "faction": set()},
    )
    assert all("UPDATE characters" not in q for q, _ in db.executed)
    for row in roster_rows:
        assert row["aliases"] == ([] if row["name"] != "Elizabeth Bennet" else ["Lizzy"])


def test_already_known_candidates_are_not_sent_to_llm(roster_rows):
    db = FakeDB(roster_rows)
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"resolutions": []})()

    canon = _make_canon(db, recording_completion)
    canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names_by_type={"character": {"Mr. Darcy", "Lizzy"}, "location": set(), "object": set(), "faction": set()},
    )
    assert calls == []


def test_collect_character_names_pulls_from_all_sources():
    extracted = {
        "new_entities": {
            "characters": [{"name": "John"}, {"name": ""}, {"name": "  "}],
        },
        "entity_deltas": [
            {"character_name": "Eliza", "relationships": {"the master of Pemberley": "lover"}},
            {"character_name": "  "},
        ],
        "events": [
            {"involved_characters": ["John", "Mr. Bennet"]},
            {"involved_characters": []},
        ],
    }
    names = collect_character_names(extracted)
    assert names == {"John", "Eliza", "the master of Pemberley", "Mr. Bennet"}


def test_collect_names_by_type_characters():
    extracted = {
        "new_entities": {"characters": [{"name": "Jane Bennet"}, {"name": ""}, {"name": "  "}]},
        "entity_deltas": [{"character_name": "Jane", "location": "Netherfield Park"}],
        "events": [
            {
                "involved_characters": ["Jane Bennet"],
                "involved_locations": ["Longbourn"],
                "involved_objects": ["letter"],
            }
        ],
        "relationship_updates": [{"entity_a": "Jane Bennet", "entity_b": "Mr. Bingley"}],
        "dynamics_updates": [{"entity_a": "Jane Bennet", "entity_b": "Mr. Bingley"}],
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["character"] == {"Jane Bennet", "Jane", "Mr. Bingley"}
    assert by_type["location"] == {"Netherfield Park", "Longbourn"}
    assert by_type["object"] == {"letter"}
    assert by_type["faction"] == set()


def test_collect_names_by_type_locations_and_objects():
    extracted = {
        "new_entities": {
            "locations": [{"name": "Pemberley"}],
            "objects": [{"name": "the ring"}],
            "factions": [{"name": "The Order"}],
        },
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["location"] == {"Pemberley"}
    assert by_type["object"] == {"the ring"}
    assert by_type["faction"] == {"The Order"}
    assert by_type["character"] == set()


def test_collect_character_names_is_still_correct():
    extracted = {
        "new_entities": {"characters": [{"name": "Eliza"}]},
        "entity_deltas": [{"character_name": "Mr. Darcy"}],
        "events": [{"involved_characters": ["Eliza"]}],
    }
    assert collect_character_names(extracted) == {"Eliza", "Mr. Darcy"}


def _make_deduplicator(payload: dict):
    return IntraExtractionDeduplicator(use_mock=False, completion_fn=_make_completion(payload))


def test_intra_dedup_renames_character_variant():
    extracted = {
        "new_entities": {
            "characters": [
                {"name": "Jane Bennet", "aliases": [], "description": "eldest Bennet"},
                {"name": "Jane", "aliases": [], "description": ""},
            ],
        },
        "entity_deltas": [{"character_name": "Jane", "location": None}],
        "events": [{"involved_characters": ["Jane", "Jane Bennet"], "involved_locations": [], "involved_objects": []}],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = _make_deduplicator({"groups": [{"names": ["Jane", "Jane Bennet"], "reasoning": "same person"}]})
    result = dedup.deduplicate(extracted, "Jane walked in. Jane Bennet smiled.")
    chars = [c["name"] for c in result["new_entities"]["characters"]]
    assert "Jane" not in chars
    assert chars.count("Jane Bennet") == 1
    assert result["entity_deltas"][0]["character_name"] == "Jane Bennet"
    # Renaming variants to one canonical form also drops the resulting duplicates.
    assert result["events"][0]["involved_characters"] == ["Jane Bennet"]


def test_intra_dedup_renames_location_variant():
    extracted = {
        "new_entities": {"locations": [{"name": "Netherfield Park"}, {"name": "Netherfield"}]},
        "events": [{"involved_characters": [], "involved_locations": ["Netherfield"], "involved_objects": []}],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = _make_deduplicator({"groups": [{"names": ["Netherfield", "Netherfield Park"], "reasoning": "shorthand"}]})
    result = dedup.deduplicate(extracted, "They arrived at Netherfield, also called Netherfield Park.")
    locs = [l["name"] for l in result["new_entities"]["locations"]]
    assert "Netherfield" not in locs
    assert locs.count("Netherfield Park") == 1
    assert result["events"][0]["involved_locations"] == ["Netherfield Park"]


def test_intra_dedup_mock_mode_returns_unchanged():
    extracted = {"new_entities": {"characters": [{"name": "Jane"}, {"name": "Jane Bennet"}]}}
    dedup = IntraExtractionDeduplicator(use_mock=True)
    result = dedup.deduplicate(extracted, "chapter text")
    assert result is not extracted  # deep copy
    assert [c["name"] for c in result["new_entities"]["characters"]] == ["Jane", "Jane Bennet"]


def test_intra_dedup_skips_llm_when_fewer_than_two_names():
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"groups": []})()

    extracted = {"new_entities": {"characters": [{"name": "Jane Bennet"}]}}
    dedup = IntraExtractionDeduplicator(use_mock=False, completion_fn=recording_completion)
    dedup.deduplicate(extracted, "text")
    assert calls == []


def test_intra_dedup_does_not_mutate_original():
    extracted = {
        "new_entities": {
            "characters": [
                {"name": "Jane Bennet", "aliases": [], "description": "eldest"},
                {"name": "Jane", "aliases": [], "description": ""},
            ],
        },
        "entity_deltas": [{"character_name": "Jane", "location": None}],
        "events": [],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = _make_deduplicator({"groups": [{"names": ["Jane", "Jane Bennet"], "reasoning": "same"}]})
    dedup.deduplicate(extracted, "Jane walked. Jane Bennet smiled.")
    # Original must be unchanged
    assert extracted["entity_deltas"][0]["character_name"] == "Jane"
    assert extracted["new_entities"]["characters"][1]["name"] == "Jane"


def test_intra_dedup_renames_relationship_and_dynamics():
    extracted = {
        "new_entities": {"characters": [{"name": "Jane Bennet"}, {"name": "Jane"}]},
        "entity_deltas": [],
        "events": [],
        "relationship_updates": [{"entity_a": "Jane", "entity_b": "Mr. Bingley"}],
        "dynamics_updates": [{"entity_a": "Mr. Bingley", "entity_b": "Jane"}],
    }
    dedup = _make_deduplicator({"groups": [{"names": ["Jane", "Jane Bennet"], "reasoning": "same"}]})
    result = dedup.deduplicate(extracted, "Jane, i.e. Jane Bennet, met Mr. Bingley.")
    assert result["relationship_updates"][0]["entity_a"] == "Jane Bennet"
    assert result["dynamics_updates"][0]["entity_b"] == "Jane Bennet"


def test_intra_dedup_renames_object_and_faction_variants():
    extracted = {
        "new_entities": {
            "objects": [{"name": "the One Ring"}, {"name": "the Ring"}],
            "factions": [{"name": "The Fellowship of the Ring"}, {"name": "The Fellowship"}],
        },
        "events": [
            {
                "involved_characters": [],
                "involved_locations": [],
                "involved_objects": ["the Ring"],
            }
        ],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = IntraExtractionDeduplicator(
        use_mock=False,
        completion_fn=_make_completion({
            "groups": [
                {"names": ["the One Ring", "the Ring"], "reasoning": "same object"},
                {"names": ["The Fellowship of the Ring", "The Fellowship"], "reasoning": "shorthand"},
            ]
        }),
    )
    result = dedup.deduplicate(extracted, "Frodo bore the Ring, also called the One Ring.")
    objs = [o["name"] for o in result["new_entities"]["objects"]]
    assert "the Ring" not in objs
    assert objs.count("the One Ring") == 1
    factions = [f["name"] for f in result["new_entities"]["factions"]]
    assert "The Fellowship" not in factions
    assert factions.count("The Fellowship of the Ring") == 1
    assert result["events"][0]["involved_objects"] == ["the One Ring"]


class FakeDBMultiType(FakeDB):
    """Extends FakeDB to support location roster rows."""

    def __init__(self, roster_by_type: dict[str, list[dict]]) -> None:
        super().__init__(roster_by_type.get("character", []))
        self._roster_by_type = roster_by_type

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        for entity_type, rows in self._roster_by_type.items():
            table = {
                "character": "characters",
                "location": "locations",
                "object": "objects",
                "faction": "factions",
            }[entity_type]
            if f"FROM {table}" in query:
                return [dict(r) for r in rows]
        return []

    def execute(self, query, params=None):
        self.executed.append((query, tuple(params or ())))
        for entity_type, rows in self._roster_by_type.items():
            table = {
                "character": "characters",
                "location": "locations",
                "object": "objects",
                "faction": "factions",
            }[entity_type]
            if f"UPDATE {table}" in query and "aliases = %s" in query:
                new_aliases, row_id, _novel_id = params
                for row in rows:
                    if str(row["id"]) == str(row_id):
                        row["aliases"] = list(new_aliases)


def test_entity_canonicalizer_handles_location_type():
    location_roster = [
        {
            "id": "aaaa0000-0000-0000-0000-000000000001",
            "name": "Netherfield Park",
            "aliases": [],
            "description": "A grand estate.",
        }
    ]
    db = FakeDBMultiType({"location": location_roster})
    response = {
        "resolutions": [
            {
                "candidate": "Netherfield",
                "verdict": "existing",
                "id": "aaaa0000-0000-0000-0000-000000000001",
                "grammatical_anchor": "Netherfield, the great park",
                "reasoning": "shorthand",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    chapter_text = "They arrived at Netherfield, the great park nearby."
    merges = canon.canonicalize(
        chapter_text=chapter_text,
        candidate_names_by_type={"character": set(), "location": {"Netherfield"}, "object": set(), "faction": set()},
    )
    assert merges.get("location", {}).get("Netherfield") == "aaaa0000-0000-0000-0000-000000000001"
    assert "Netherfield" in location_roster[0]["aliases"]


def test_entity_canonicalizer_skips_type_with_no_candidates():
    db = FakeDBMultiType({"character": []})
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"resolutions": []})()

    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=recording_completion)
    canon.canonicalize(
        chapter_text="text",
        candidate_names_by_type={"character": set(), "location": set(), "object": set(), "faction": set()},
    )
    assert calls == []


# ---------------------------------------------------------------------------
# Type-specific intra-dedup prompt content
# ---------------------------------------------------------------------------

from pipeline.extraction.prompts import build_intra_dedup_system_prompt, build_intra_dedup_user_prompt


def test_intra_dedup_prompt_character_requires_explicit_evidence():
    prompt = build_intra_dedup_system_prompt("character")
    assert "when in doubt" in prompt.lower() or "do not group" in prompt.lower()


def test_intra_dedup_prompt_location_mentions_sub_location():
    prompt = build_intra_dedup_system_prompt("location")
    assert "sub-location" in prompt.lower() or "parent" in prompt.lower()


def test_intra_dedup_prompt_object_mentions_owner():
    prompt = build_intra_dedup_system_prompt("object")
    assert "owner" in prompt.lower()


def test_intra_dedup_prompt_faction_mentions_abbreviation():
    prompt = build_intra_dedup_system_prompt("faction")
    assert "abbreviation" in prompt.lower() or "alias" in prompt.lower()


def test_intra_dedup_user_prompt_objects_include_owner_context():
    """Object user prompt should include owner_name alongside the entity name."""
    entities = [
        {"name": "black sedan", "owner_name": "Jake"},
        {"name": "black sedan", "owner_name": "Sarah"},
    ]
    prompt = build_intra_dedup_user_prompt("object", entities, "Jake drove his black sedan. Sarah drove hers.")
    assert "Jake" in prompt
    assert "Sarah" in prompt


def test_intra_dedup_user_prompt_non_objects_accept_plain_names():
    """Non-object user prompt still works with a plain list of strings."""
    prompt = build_intra_dedup_user_prompt("character", ["Jane", "Jane Bennet"], "text")
    assert "Jane" in prompt
    assert "Jane Bennet" in prompt


def test_intra_dedup_objects_different_owners_not_merged():
    """Objects with different owners must not be merged even if names match."""
    extracted = {
        "new_entities": {
            "objects": [
                {"name": "black sedan", "owner_name": "Jake", "description": "Jake's car"},
                {"name": "black sedan", "owner_name": "Sarah", "description": "Sarah's car"},
            ],
        },
        "entity_deltas": [],
        "events": [],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    # LLM returns NO groups (correctly deciding not to merge)
    dedup = _make_deduplicator({"groups": []})
    result = dedup.deduplicate(extracted, "Jake drove his black sedan. Sarah had one too.")
    objs = result["new_entities"]["objects"]
    assert len(objs) == 2, "Different-owner objects must not be merged"


# ---------------------------------------------------------------------------
# Type-specific cross-chapter canonicalization (relax anchor for non-chars)
# ---------------------------------------------------------------------------

def test_location_merges_without_anchor():
    """Locations should merge on semantic reasoning even without a verbatim anchor."""
    location_roster = [
        {
            "id": "loc-0001-0000-0000-0000-000000000001",
            "name": "Corporate Office",
            "aliases": [],
            "description": "A glass office building where Jake works.",
        }
    ]
    db = FakeDBMultiType({"location": location_roster})
    response = {
        "resolutions": [
            {
                "candidate": "Jake's office building",
                "verdict": "existing",
                "id": "loc-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "The chapter describes Jake going to his office; 'Jake's office building' is the same building as 'Corporate Office'.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="Jake walked to his office building and swiped his badge.",
        candidate_names_by_type={"character": set(), "location": {"Jake's office building"}, "object": set(), "faction": set()},
    )
    assert merges.get("location", {}).get("Jake's office building") == "loc-0001-0000-0000-0000-000000000001"
    # Reasoning-only evidence merges for this chapter but must NOT persist the
    # alias: a hallucinated alias would reroute every future mention.
    assert "Jake's office building" not in location_roster[0]["aliases"]


def test_lexically_close_merge_persists_alias_without_anchor():
    """Token-subset name variants ("the Empire" ⊂ "The Galactic Empire") are
    strong evidence on their own — the alias is written even with no anchor."""
    faction_roster = [
        {
            "id": "fac-0001-0000-0000-0000-000000000001",
            "name": "The Galactic Empire",
            "aliases": [],
            "description": "Authoritarian ruling faction.",
        }
    ]
    db = FakeDBMultiType({"faction": faction_roster})
    response = {
        "resolutions": [
            {
                "candidate": "the Empire",
                "verdict": "existing",
                "id": "fac-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "Common shorthand.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="The Empire tightened its grip.",
        candidate_names_by_type={"character": set(), "location": set(), "object": set(), "faction": {"the Empire"}},
    )
    assert merges.get("faction", {}).get("the Empire") == "fac-0001-0000-0000-0000-000000000001"
    assert "the Empire" in faction_roster[0]["aliases"]


def test_reasoning_only_merge_rejects_alias_for_dissimilar_names():
    """A lexically unrelated candidate merged on reasoning alone must not
    poison the roster: merge honored this chapter, alias not written."""
    location_roster = [
        {
            "id": "loc-0002-0000-0000-0000-000000000002",
            "name": "South Tower",
            "aliases": [],
            "description": "The castle's south tower.",
        }
    ]
    db = FakeDBMultiType({"location": location_roster})
    response = {
        "resolutions": [
            {
                "candidate": "North Tower",
                "verdict": "existing",
                "id": "loc-0002-0000-0000-0000-000000000002",
                "grammatical_anchor": "",
                "reasoning": "Both are towers of the same castle.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    canon.canonicalize(
        chapter_text="She climbed the North Tower at dawn.",
        candidate_names_by_type={"character": set(), "location": {"North Tower"}, "object": set(), "faction": set()},
    )
    assert location_roster[0]["aliases"] == []


def test_object_merges_without_anchor():
    """Objects should merge on semantic reasoning without a verbatim anchor."""
    obj_roster = [
        {
            "id": "obj-0001-0000-0000-0000-000000000001",
            "name": "Jake's sword",
            "aliases": [],
            "description": "A katana owned by Jake.",
        }
    ]
    db = FakeDBMultiType({"object": obj_roster})
    response = {
        "resolutions": [
            {
                "candidate": "the blade",
                "verdict": "existing",
                "id": "obj-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "Jake wields 'the blade' in this chapter; he only has one sword in the roster.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="Jake raised the blade and cut through the air.",
        candidate_names_by_type={"character": set(), "location": set(), "object": {"the blade"}, "faction": set()},
    )
    assert merges.get("object", {}).get("the blade") == "obj-0001-0000-0000-0000-000000000001"


def test_faction_merges_without_anchor():
    """Factions should merge on semantic reasoning without a verbatim anchor."""
    faction_roster = [
        {
            "id": "fac-0001-0000-0000-0000-000000000001",
            "name": "The Galactic Empire",
            "aliases": [],
            "description": "Authoritarian ruling faction.",
        }
    ]
    db = FakeDBMultiType({"faction": faction_roster})
    response = {
        "resolutions": [
            {
                "candidate": "the Empire",
                "verdict": "existing",
                "id": "fac-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "The Empire is the common shorthand for The Galactic Empire used throughout.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="The Empire tightened its grip on the outer systems.",
        candidate_names_by_type={"character": set(), "location": set(), "object": set(), "faction": {"the Empire"}},
    )
    assert merges.get("faction", {}).get("the Empire") == "fac-0001-0000-0000-0000-000000000001"


def test_character_still_requires_anchor_when_absent(roster_rows):
    """Characters must still be rejected when no grammatical anchor is provided."""
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "the tall man",
                "verdict": "existing",
                "id": "11111111-1111-1111-1111-111111111111",
                "grammatical_anchor": "",
                "reasoning": "Probably Darcy because he is tall.",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    merges = canon.canonicalize(
        chapter_text="A tall man entered the room.",
        candidate_names_by_type={"character": {"the tall man"}, "location": set(), "object": set(), "faction": set()},
    )
    assert merges == {}


def test_canonicalization_system_prompt_character_mentions_anchor():
    from pipeline.extraction.prompts import build_canonicalization_system_prompt
    prompt = build_canonicalization_system_prompt("character")
    assert "grammatical_anchor" in prompt or "anchor" in prompt.lower()


def test_canonicalization_system_prompt_location_relaxed():
    from pipeline.extraction.prompts import build_canonicalization_system_prompt
    prompt = build_canonicalization_system_prompt("location")
    assert "reasoning" in prompt.lower() or "semantic" in prompt.lower()


# ---------------------------------------------------------------------------
# Dedup coverage gaps: event factions, scenes, learnings, custom entities
# ---------------------------------------------------------------------------

from pipeline.extraction.canonicalizer import normalize_name, _select_roster_subset


def test_collect_names_by_type_includes_event_factions():
    extracted = {
        "events": [{"involved_factions": ["the Empire", "Rebel Alliance"]}],
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["faction"] == {"the Empire", "Rebel Alliance"}


def test_collect_names_by_type_includes_scenes_and_learnings():
    extracted = {
        "scenes": [
            {
                "pov_character_name": "Jake",
                "location_name": "Corporate Office",
                "present_character_names": ["Sarah", "Jake"],
            }
        ],
        "learnings": [
            {
                "character_name": "Sarah",
                "source_character_name": "Mr. Kim",
                "shared_with_character_names": ["Jake"],
            }
        ],
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["character"] == {"Jake", "Sarah", "Mr. Kim"}
    assert by_type["location"] == {"Corporate Office"}


def test_collect_names_by_type_includes_custom_entities():
    extracted = {
        "custom_entities": [
            {"name": "The 93rd Universe", "type": "realm"},
            {"name": "The System", "type": "power_system"},
        ],
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["realm"] == {"The 93rd Universe"}
    assert by_type["power_system"] == {"The System"}


def test_intra_dedup_renames_scenes_learnings_and_event_factions():
    extracted = {
        "new_entities": {
            "characters": [{"name": "Jane Bennet"}, {"name": "Jane"}],
            "factions": [{"name": "The Galactic Empire"}, {"name": "the Empire"}],
        },
        "entity_deltas": [],
        "events": [
            {
                "involved_characters": [],
                "involved_locations": [],
                "involved_objects": [],
                "involved_factions": ["the Empire"],
            }
        ],
        "relationship_updates": [],
        "dynamics_updates": [],
        "scenes": [
            {
                "pov_character_name": "Jane",
                "location_name": None,
                "present_character_names": ["Jane", "Jane Bennet"],
            }
        ],
        "learnings": [
            {
                "character_name": "Jane",
                "source_character_name": "Jane",
                "shared_with_character_names": ["Jane"],
            }
        ],
    }

    def completion(**kwargs):
        user = kwargs["messages"][1]["content"]
        if "CHARACTER" in user:
            return _make_completion({"groups": [{"names": ["Jane", "Jane Bennet"], "reasoning": "same"}]})()
        return _make_completion({"groups": [{"names": ["the Empire", "The Galactic Empire"], "reasoning": "shorthand"}]})()

    dedup = IntraExtractionDeduplicator(use_mock=False, completion_fn=completion)
    result = dedup.deduplicate(extracted, "Jane Bennet faced The Galactic Empire. Jane and the Empire.")
    assert result["scenes"][0]["pov_character_name"] == "Jane Bennet"
    assert result["scenes"][0]["present_character_names"] == ["Jane Bennet"]
    assert result["learnings"][0]["character_name"] == "Jane Bennet"
    assert result["learnings"][0]["source_character_name"] == "Jane Bennet"
    assert result["learnings"][0]["shared_with_character_names"] == ["Jane Bennet"]
    assert result["events"][0]["involved_factions"] == ["The Galactic Empire"]


def test_intra_dedup_custom_entities_renamed_and_deduped():
    extracted = {
        "new_entities": {},
        "custom_entities": [
            {"name": "The 93rd Universe", "type": "realm", "description": "A dimension."},
            {"name": "93rd Universe", "type": "realm", "description": "Same place."},
        ],
        "entity_deltas": [],
        "events": [],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = _make_deduplicator(
        {"groups": [{"names": ["93rd Universe", "The 93rd Universe"], "reasoning": "same realm"}]}
    )
    result = dedup.deduplicate(extracted, "They entered the 93rd Universe.")
    assert len(result["custom_entities"]) == 1
    assert result["custom_entities"][0]["name"] == "The 93rd Universe"


def test_intra_dedup_ignores_hallucinated_group_members():
    """A canonical name invented by the LLM must never replace real input names."""
    extracted = {
        "new_entities": {
            "characters": [{"name": "Jane"}, {"name": "Mr. Bingley"}],
        },
        "entity_deltas": [],
        "events": [],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    # "Jane Bennet of Longbourn Estate" is not an input name — hallucinated.
    dedup = _make_deduplicator(
        {"groups": [{"names": ["Jane", "Jane Bennet of Longbourn Estate"], "reasoning": "same"}]}
    )
    result = dedup.deduplicate(extracted, "Jane met Mr. Bingley.")
    chars = [c["name"] for c in result["new_entities"]["characters"]]
    assert chars == ["Jane", "Mr. Bingley"]


# ---------------------------------------------------------------------------
# Deterministic normalized-equality merges (no LLM call)
# ---------------------------------------------------------------------------


def test_normalize_name_basics():
    assert normalize_name("The Galactic Empire") == normalize_name("Galactic  Empire")
    assert normalize_name("“Jake’s Sword”") == normalize_name("jake's sword")
    assert normalize_name("Pemberley.") == normalize_name("pemberley")
    assert normalize_name("A") != ""  # short names survive article stripping


def test_canonicalizer_deterministic_merge_skips_llm():
    faction_roster = [
        {
            "id": "fac-0001-0000-0000-0000-000000000001",
            "name": "Galactic Empire",
            "aliases": [],
            "description": "Authoritarian ruling faction.",
        }
    ]
    db = FakeDBMultiType({"faction": faction_roster})
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"resolutions": []})()

    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=recording_completion)
    merges = canon.canonicalize(
        chapter_text="The Galactic Empire advanced.",
        candidate_names_by_type={"faction": {"The Galactic Empire"}},
    )
    assert calls == [], "normalized-equal candidate must not reach the LLM"
    assert merges["faction"]["The Galactic Empire"] == "fac-0001-0000-0000-0000-000000000001"
    assert "The Galactic Empire" in faction_roster[0]["aliases"]


def test_canonicalizer_ambiguous_normalized_match_goes_to_llm():
    """Two roster entries with the same normalized name must not auto-merge."""
    faction_roster = [
        {"id": "fac-1", "name": "The Order", "aliases": [], "description": ""},
        {"id": "fac-2", "name": "Order", "aliases": [], "description": ""},
    ]
    db = FakeDBMultiType({"faction": faction_roster})
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"resolutions": []})()

    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=recording_completion)
    merges = canon.canonicalize(
        chapter_text="An order was given.",
        candidate_names_by_type={"faction": {"the order!"}},
    )
    assert len(calls) == 1, "ambiguous candidates must be deferred to the LLM"
    assert merges == {}


# ---------------------------------------------------------------------------
# Custom entity types in the cross-chapter canonicalizer
# ---------------------------------------------------------------------------


class FakeDBCustomEntities(FakeDB):
    def __init__(self, entity_rows: list[dict]) -> None:
        super().__init__([])
        self.entity_rows = entity_rows

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        if "FROM entities" in query:
            return [dict(r) for r in self.entity_rows]
        return []

    def execute(self, query, params=None):
        self.executed.append((query, tuple(params or ())))
        if "UPDATE entities" in query and "aliases = %s" in query:
            new_aliases, row_id, _novel_id = params
            for row in self.entity_rows:
                if str(row["id"]) == str(row_id):
                    row["aliases"] = list(new_aliases)


def test_canonicalizer_custom_type_merges_via_entities_table():
    entity_rows = [
        {"id": "ent-0001", "name": "The 93rd Universe", "aliases": []},
    ]
    db = FakeDBCustomEntities(entity_rows)
    response = {
        "resolutions": [
            {
                "candidate": "the Ninety-Third Universe",
                "verdict": "existing",
                "id": "ent-0001",
                "grammatical_anchor": "crossed into the Ninety-Third Universe",
                "reasoning": "Spelled-out form of the same realm.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="They crossed into the Ninety-Third Universe.",
        candidate_names_by_type={"realm": {"the Ninety-Third Universe"}},
    )
    assert merges["realm"]["the Ninety-Third Universe"] == "ent-0001"
    assert "the Ninety-Third Universe" in entity_rows[0]["aliases"]
    assert any("UPDATE entities" in q for q, _ in db.executed)


# ---------------------------------------------------------------------------
# Roster cap for canonicalization LLM calls
# ---------------------------------------------------------------------------


def test_select_roster_subset_keeps_similar_entries():
    roster = [
        {"id": f"id-{i}", "name": f"Background Person {i}", "aliases": []}
        for i in range(120)
    ]
    roster.append({"id": "id-target", "name": "Galactic Empire", "aliases": []})
    subset = _select_roster_subset(["the Empire"], roster, cap=10)
    assert len(subset) == 10
    assert any(e["id"] == "id-target" for e in subset)


def test_select_roster_subset_noop_when_under_cap():
    roster = [{"id": "1", "name": "Jake", "aliases": []}]
    assert _select_roster_subset(["anything"], roster, cap=80) is roster


def test_canonicalization_system_prompt_custom_type_uses_generic_rules():
    from pipeline.extraction.prompts import build_canonicalization_system_prompt
    prompt = build_canonicalization_system_prompt("realm")
    assert "NOT required" in prompt
    assert "realm" in prompt


# ---------------------------------------------------------------------------
# rename_map_for_merges — merges must take effect for the current chapter
# ---------------------------------------------------------------------------

def test_rename_map_for_merges_maps_candidates_to_canonical_names():
    from pipeline.extraction.canonicalizer import rename_map_for_merges

    class FakeDB:
        def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
            assert "FROM locations" in query
            return [{"id": "loc-1", "name": "Halcyon Tower"}]

    out = rename_map_for_merges(
        FakeDB(), {"location": {"Jake's office building": "loc-1"}}
    )
    assert out == {"location": {"jake's office building": "Halcyon Tower"}}


def test_rename_map_for_merges_skips_identity_renames():
    from pipeline.extraction.canonicalizer import rename_map_for_merges

    class FakeDB:
        def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
            return [{"id": "loc-1", "name": "Halcyon Tower"}]

    out = rename_map_for_merges(FakeDB(), {"location": {"halcyon tower": "loc-1"}})
    assert out == {}


def test_lexically_close_but_ambiguous_does_not_persist_alias():
    """A candidate close to TWO roster entries (\"the Empire\" fits both
    \"The Galactic Empire\" and \"Empire of the Sun\") must not be permanently
    welded to whichever one the LLM picked — merge honored, alias withheld."""
    faction_roster = [
        {
            "id": "fac-0001-0000-0000-0000-000000000001",
            "name": "The Galactic Empire",
            "aliases": [],
            "description": "Authoritarian ruling faction.",
        },
        {
            "id": "fac-0002-0000-0000-0000-000000000002",
            "name": "Empire of the Sun",
            "aliases": [],
            "description": "Rival empire in the east.",
        },
    ]
    db = FakeDBMultiType({"faction": faction_roster})
    response = {
        "resolutions": [
            {
                "candidate": "the Empire",
                "verdict": "existing",
                "id": "fac-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "Shorthand used in this chapter.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="The Empire tightened its grip.",
        candidate_names_by_type={"character": set(), "location": set(), "object": set(), "faction": {"the Empire"}},
    )
    # Merge still honored for this chapter…
    assert merges.get("faction", {}).get("the Empire") == "fac-0001-0000-0000-0000-000000000001"
    # …but neither roster row gains the ambiguous alias.
    assert faction_roster[0]["aliases"] == []
    assert faction_roster[1]["aliases"] == []
