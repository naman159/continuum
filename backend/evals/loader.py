"""Load the golden fixture novel and its answer key."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

GOLDEN_DIR = Path(__file__).parent / "golden"
_CHAPTER_FILE = re.compile(r"^ch(\d+)\.txt$")


def load_chapters() -> list[tuple[int, str]]:
    """Return (chapter_number, text) for every golden chapter, ascending."""
    chapters: list[tuple[int, str]] = []
    for path in GOLDEN_DIR.iterdir():
        m = _CHAPTER_FILE.match(path.name)
        if m:
            chapters.append((int(m.group(1)), path.read_text(encoding="utf-8")))
    chapters.sort(key=lambda pair: pair[0])
    return chapters


def load_answer_key() -> dict[str, Any]:
    with (GOLDEN_DIR / "answer_key.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
