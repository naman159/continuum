from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline.extraction.canonicalizer import (
    CharacterCanonicalizer,
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
    return CharacterCanonicalizer(
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
        candidate_names={"the master of Pemberley"},
    )
    assert merges == {"the master of Pemberley": "11111111-1111-1111-1111-111111111111"}
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
        candidate_names={"the master of Pemberley"},
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
        candidate_names={"the master of Pemberley"},
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
        candidate_names={"Lizzy"},
    )
    elizabeth = next(r for r in roster_rows if r["name"] == "Elizabeth Bennet")
    assert elizabeth["aliases"].count("Lizzy") == 1
    assert not any("UPDATE characters" in q for q, _ in db.executed)


def test_malformed_json_response_is_safe(roster_rows):
    db = FakeDB(roster_rows)
    canon = _make_canon(db, _make_completion("not json at all"))
    merges = canon.canonicalize(
        chapter_text=CHAPTER_TEXT,
        candidate_names={"the master of Pemberley"},
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
        candidate_names={"John Smith"},
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
        candidate_names={"Mr. Darcy", "Lizzy"},
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
    assert result["events"][0]["involved_characters"] == ["Jane Bennet", "Jane Bennet"]


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
