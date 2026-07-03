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


def test_curly_apostrophe_in_text_still_matches():
    roster = [
        {"name": "D'Arcy", "aliases": [], "last_chapter": 1},
        {"name": "Zed", "aliases": [], "last_chapter": 9},
    ]
    text = "D’Arcy smiled."  # typographic apostrophe, as in epub/Word sources
    out = select_context_entities(text, roster, cap=1)
    assert out[0]["name"] == "D'Arcy"


def test_load_story_context_applies_caps(monkeypatch):
    import dataclasses

    from pipeline import pipeline as pipeline_mod

    # Settings is a frozen dataclass with instance-level values; patch the
    # module-global with a modified copy so call-time reads see the caps.
    patched = dataclasses.replace(pipeline_mod.settings)
    object.__setattr__(patched, "context_max_characters", 1)
    object.__setattr__(patched, "context_max_locations", 1)
    monkeypatch.setattr(pipeline_mod, "settings", patched)

    class CtxFakeDB:
        def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
            if "FROM characters" in query:
                return [
                    {"id": "c1", "name": "Alice", "aliases": [], "emotional_state": None,
                     "goals": None, "physical_state": None, "last_chapter": 1},
                    {"id": "c2", "name": "Bob", "aliases": [], "emotional_state": None,
                     "goals": None, "physical_state": None, "last_chapter": 9},
                ]
            if "FROM locations" in query:
                return [
                    {"id": "l1", "name": "Harbor", "description": None, "first_appearance_chapter": 1},
                    {"id": "l2", "name": "Castle", "description": None, "first_appearance_chapter": 5},
                ]
            return []

    ctx = pipeline_mod.load_story_context(
        CtxFakeDB(), "novel-1", 10, chapter_text="Alice sailed into the Harbor."
    )
    assert [c["name"] for c in ctx["characters"]] == ["Alice"]   # mentioned beats recency
    assert [l["name"] for l in ctx["locations"]] == ["Harbor"]
