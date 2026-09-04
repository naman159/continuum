from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RetrievalKind = Literal["chapter", "scene", "event"]


@dataclass
class RetrievalQuery:
    text: str
    novel_id: str
    max_chapter: int | None = None
    k: int = 10


@dataclass
class RetrievalResult:
    item_id: str
    kind: RetrievalKind
    score: float
    snippet: str
    chapter_id: str | None = None
    chapter_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
