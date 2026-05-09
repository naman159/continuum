from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline.extraction.canonicalizer import CharacterCanonicalizer, collect_character_names


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
