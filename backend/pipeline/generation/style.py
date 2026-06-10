from __future__ import annotations

"""Deterministic, LLM-free style fingerprint of a chapter's prose.

Stored in chapters.style_fingerprint at ingest; the drafter averages recent
chapters' fingerprints to match the novel's voice.
"""

import re
from typing import Any

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DIALOGUE = re.compile(r"[\"“”][^\"“”]+[\"“”]")
_FIRST_PERSON = re.compile(r"\b(I|me|my|mine|we|our)\b")
_THIRD_PERSON = re.compile(r"\b(he|she|they|his|her|their|him|them)\b", re.IGNORECASE)


def compute_style_fingerprint(text: str) -> dict[str, Any]:
    text = text or ""
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    word_counts = [len(s.split()) for s in sentences]
    avg_sentence_words = round(sum(word_counts) / len(word_counts), 2) if word_counts else 0.0

    dialogue_chars = sum(len(m.group(0)) for m in _DIALOGUE.finditer(text))
    dialogue_ratio = round(dialogue_chars / len(text), 3) if text else 0.0

    first = len(_FIRST_PERSON.findall(text))
    third = len(_THIRD_PERSON.findall(text))
    pov_person = "first" if first > third else "third"

    exclamations = text.count("!")
    exclamation_rate = round(exclamations / max(1, len(sentences)), 3)

    return {
        "avg_sentence_words": avg_sentence_words,
        "dialogue_ratio": dialogue_ratio,
        "pov_person": pov_person,
        "exclamation_rate": exclamation_rate,
        "sentence_count": len(sentences),
    }


def average_fingerprints(fingerprints: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [f for f in fingerprints if isinstance(f, dict) and f.get("sentence_count")]
    if not usable:
        return None
    numeric = ("avg_sentence_words", "dialogue_ratio", "exclamation_rate")
    out: dict[str, Any] = {
        k: round(sum(float(f.get(k) or 0.0) for f in usable) / len(usable), 3) for k in numeric
    }
    povs = [f.get("pov_person") for f in usable]
    out["pov_person"] = max(set(povs), key=povs.count)
    return out


__all__ = ["compute_style_fingerprint", "average_fingerprints"]
