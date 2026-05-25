from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RetrievalKind = Literal["chapter", "scene", "event", "commitment"]


@dataclass
class RetrievalQuery:
    text: str
    novel_id: str
    entity_ids: list[str] | None = None
    thread_ids: list[str] | None = None
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


@dataclass
class RetrievalBundle:
    query: RetrievalQuery
    results: list[RetrievalResult]
    debug: dict[str, Any] = field(default_factory=dict)
