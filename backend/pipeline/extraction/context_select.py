from __future__ import annotations

"""Select which roster entities to inject into extraction prompts.

Mentioned-in-text entities always make the cut; the remainder back-fills by
recency (``last_chapter`` / ``first_appearance_chapter``, falling back to 0).
Pure function — DB access stays in load_story_context.
"""

import re
from typing import Any

from pipeline.extraction.canonicalizer import normalize_name


def _mentioned(text_lower: str, entity: dict[str, Any]) -> bool:
    for raw in [entity.get("name", ""), *(entity.get("aliases") or [])]:
        token = normalize_name(str(raw))
        if len(token) < 2:
            continue
        # word-boundary containment; escape regex metacharacters in names
        if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text_lower):
            return True
    return False


def _recency(entity: dict[str, Any]) -> int:
    for key in ("last_chapter", "first_appearance_chapter"):
        value = entity.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def select_context_entities(
    chapter_text: str,
    roster: list[dict[str, Any]],
    *,
    cap: int,
) -> list[dict[str, Any]]:
    if cap <= 0 or len(roster) <= cap:
        return list(roster)

    text_lower = (chapter_text or "").lower()
    mentioned = [e for e in roster if _mentioned(text_lower, e)]
    if len(mentioned) >= cap:
        return mentioned[:cap]

    mentioned_ids = {id(e) for e in mentioned}
    rest = sorted(
        (e for e in roster if id(e) not in mentioned_ids),
        key=_recency,
        reverse=True,
    )
    return [*mentioned, *rest[: cap - len(mentioned)]]


__all__ = ["select_context_entities"]
