from pipeline.extraction.presets import GENRE_PRESETS, list_genres


def test_genre_presets_keys():
    assert set(GENRE_PRESETS.keys()) == {"litrpg", "high_fantasy", "xianxia", "scifi", "contemporary"}


def test_each_preset_has_name_and_description():
    for genre, types in GENRE_PRESETS.items():
        assert isinstance(types, list), genre
        for t in types:
            assert "name" in t, genre
            assert "description" in t, genre
            assert isinstance(t["name"], str) and t["name"], genre
            assert isinstance(t["description"], str) and t["description"], genre


def test_list_genres_returns_all():
    result = list_genres()
    assert len(result) == len(GENRE_PRESETS)
    keys = {r["id"] for r in result}
    assert keys == set(GENRE_PRESETS.keys())
