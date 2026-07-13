from __future__ import annotations

from evals.loader import load_answer_key, load_chapters
from evals.scoring import (
    recall_at_k,
    score_entities,
    score_knowledge,
    score_possessions,
)


def test_load_chapters_returns_ten_ordered():
    chapters = load_chapters()
    assert [n for n, _ in chapters] == list(range(1, 11))
    assert all(text.strip() for _, text in chapters)


def test_load_answer_key_shape():
    key = load_answer_key()
    assert set(key["entities"]) == {"characters", "locations", "objects", "factions"}
    assert len(key["queries"]) == 10


def test_score_entities_exact_and_partial_names():
    got = score_entities(
        ["Mira Solen", "Toren Vale"],
        ["mira solen", "Toren", "The Ash Council"],
    )
    # "Toren" matches "Toren Vale" by containment; "The Ash Council" is extra.
    assert got["recall"] == 1.0
    assert got["missing"] == []
    assert got["extra"] == ["the ash council"]
    assert got["precision"] == 2 / 3


def test_score_entities_empty_actual():
    got = score_entities(["Mira Solen"], [])
    assert got["recall"] == 0.0
    assert got["missing"] == ["mira solen"]


def test_score_possessions_with_tolerance():
    expected = [{"object": "Brass Lantern", "holder": "Mira Solen", "since": 1, "until": 3}]
    actual = [{"object": "brass lantern", "holder": "Mira", "since": 2, "until": 4}]
    got = score_possessions(expected, actual, tolerance=1)
    assert got["recall"] == 1.0
    # Outside tolerance:
    actual_far = [{"object": "brass lantern", "holder": "Mira", "since": 5, "until": None}]
    assert score_possessions(expected, actual_far, tolerance=1)["recall"] == 0.0


def test_score_possessions_open_interval():
    expected = [{"object": "Brass Lantern", "holder": "Mira Solen", "since": 8, "until": None}]
    actual = [{"object": "Brass Lantern", "holder": "Mira Solen", "since": 8, "until": None}]
    assert score_possessions(expected, actual)["recall"] == 1.0


def test_score_knowledge_word_overlap():
    expected = [{"character": "Mira Solen", "fact": "the brass lantern opens the undervault", "since": 3}]
    actual = [
        {"character": "Mira Solen", "fact": "lantern is the key that opens the undervault", "since": 3},
    ]
    assert score_knowledge(expected, actual)["recall"] == 1.0
    assert score_knowledge(expected, [])["recall"] == 0.0


def test_recall_at_k():
    assert recall_at_k([1], [3, 1, 2], k=2) == 1.0
    assert recall_at_k([1], [3, 2, 1], k=2) == 0.0
    assert recall_at_k([1, 2], [1, 5, 6], k=3) == 0.5
