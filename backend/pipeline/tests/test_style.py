from __future__ import annotations

from pipeline.style import compute_style_fingerprint

FIRST_PERSON = 'I walked to the door. "Hello?" I said. Nobody answered me.'
THIRD_PERSON = (
    "Jake walked to the door and knocked twice. The corridor stretched on, "
    "silent and cold. He waited for a long moment before turning away."
)


def test_fingerprint_fields_present():
    fp = compute_style_fingerprint(THIRD_PERSON)
    for key in ("avg_sentence_words", "dialogue_ratio", "pov_person", "exclamation_rate"):
        assert key in fp


def test_pov_detection():
    assert compute_style_fingerprint(FIRST_PERSON)["pov_person"] == "first"
    assert compute_style_fingerprint(THIRD_PERSON)["pov_person"] == "third"


def test_dialogue_ratio_bounds():
    fp = compute_style_fingerprint(FIRST_PERSON)
    assert 0.0 < fp["dialogue_ratio"] <= 1.0
    assert compute_style_fingerprint(THIRD_PERSON)["dialogue_ratio"] == 0.0


def test_empty_text_safe():
    fp = compute_style_fingerprint("")
    assert fp["avg_sentence_words"] == 0.0
