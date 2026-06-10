from __future__ import annotations

from pipeline.extraction.context_select import select_context_entities


def _chars(*names, **extra):
    return [{"name": n, "aliases": [], **extra} for n in names]


def test_mentioned_entities_are_always_kept():
    roster = _chars("Alice", "Bob", "Carol", "Dave")
    text = "Alice waved at Carol across the square."
    out = select_context_entities(text, roster, cap=2)
    names = [e["name"] for e in out]
    assert names == ["Alice", "Carol"]


def test_alias_mentions_count():
    roster = [
        {"name": "Elizabeth Bennet", "aliases": ["Lizzy"], "last_chapter": 1},
        {"name": "Mr. Collins", "aliases": [], "last_chapter": 2},
    ]
    out = select_context_entities("Lizzy laughed.", roster, cap=1)
    assert out[0]["name"] == "Elizabeth Bennet"


def test_backfill_by_recency():
    roster = [
        {"name": "Old Man", "aliases": [], "last_chapter": 1},
        {"name": "Recent Friend", "aliases": [], "last_chapter": 9},
        {"name": "Mid Person", "aliases": [], "last_chapter": 5},
    ]
    out = select_context_entities("Nobody from the roster appears here.", roster, cap=2)
    names = [e["name"] for e in out]
    assert names == ["Recent Friend", "Mid Person"]


def test_mentions_beat_recency_and_cap_is_respected():
    roster = [
        {"name": "Hot", "aliases": [], "last_chapter": 9},
        {"name": "Cold", "aliases": [], "last_chapter": 1},
        {"name": "Warm", "aliases": [], "last_chapter": 5},
    ]
    out = select_context_entities("Cold stood alone.", roster, cap=2)
    names = [e["name"] for e in out]
    assert names[0] == "Cold"          # mentioned
    assert names[1] == "Hot"           # most recent backfill
    assert len(names) == 2


def test_cap_zero_or_negative_disables():
    roster = _chars("A", "B", "C")
    assert len(select_context_entities("x", roster, cap=0)) == 3


def test_short_names_do_not_false_positive_inside_words():
    roster = [{"name": "Ann", "aliases": [], "last_chapter": 1},
              {"name": "Zed", "aliases": [], "last_chapter": 2}]
    # "Ann" appears only inside "cannon" — not a mention.
    out = select_context_entities("The cannon fired.", roster, cap=1)
    assert out[0]["name"] == "Zed"
