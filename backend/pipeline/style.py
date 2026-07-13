"""Deterministic, LLM-free style fingerprint of a chapter's prose.

Computed at ingest and stored in chapters.style_fingerprint as chapter
voice metadata for the wiki.
"""

from __future__ import annotations

import re
from typing import Any

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DIALOGUE = re.compile(r"[\"“”][^\"“”]+[\"“”]")
_FIRST_PERSON = re.compile(r"\b(I|me|my|mine|we|our)\b", re.IGNORECASE)
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


__all__ = ["compute_style_fingerprint"]
