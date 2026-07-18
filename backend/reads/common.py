"""Shared helpers for the cutoff-aware read layer."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

_STORY_KIND_PRECEDENCE = ["dynamic", "event", "possession", "location"]


def merge_story_edges(raw: list[dict]) -> list[dict]:
    """Collapse multiple raw story records between the same entity pair into one edge.

    Each item in raw must have: from (str), to (str), edge_kind (str), description (str|None).
    Returns one dict per canonical pair with keys: id, from, to, label, chapter_number,
    edge_kind, tooltip.
    """
    grouped: dict[tuple[str, str], dict] = {}
    for item in raw:
        a, b = item["from"], item["to"]
        pair = (min(a, b), max(a, b))
        desc = item.get("description") or ""
        if pair not in grouped:
            grouped[pair] = {
                "from": a,
                "to": b,
                "edge_kind": item["edge_kind"],
                "descriptions": [desc] if desc else [],
            }
        else:
            existing = grouped[pair]
            cur_prec = _STORY_KIND_PRECEDENCE.index(existing["edge_kind"])
            new_prec = _STORY_KIND_PRECEDENCE.index(item["edge_kind"])
            if new_prec < cur_prec:
                existing["edge_kind"] = item["edge_kind"]
            if desc:
                existing["descriptions"].append(desc)

    result = []
    for data in grouped.values():
        n = len(data["descriptions"])
        kind = data["edge_kind"]
        kind_plural = {
            "dynamic": "dynamics", "event": "events",
            "possession": "possessions", "location": "locations",
        }.get(kind, kind)
        label = data["descriptions"][0] if n == 1 else (f"{n} {kind_plural}" if n > 0 else None)
        tooltip = "\n".join(data["descriptions"]) or None
        result.append({
            "id": str(uuid4()),
            "from": data["from"],
            "to": data["to"],
            "label": label,
            "chapter_number": None,
            "edge_kind": kind,
            "tooltip": tooltip,
        })
    return result


def max_chapter_for(db: Any, novel_id: UUID | str) -> int:
    value = db.fetchval(
        "SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s",
        (novel_id,),
    )
    return int(value or 0)


def resolve_cutoff(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> int:
    return up_to_chapter if up_to_chapter is not None else max_chapter_for(db, novel_id)


def resolve_cutoff_and_uncapped(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None
) -> tuple[int, bool]:
    """Cutoff plus whether the view is effectively uncapped (cutoff >= last chapter).

    Terminal statuses without a chapter anchor (a thread closed with no
    closed_chapter, a commitment marked broken, a flag resolved with no
    resolved_chapter_id) cannot be dated, so point-in-time views must not
    show them; only an uncapped view — where nothing lies in the future —
    may report them as terminal.
    """
    max_ch = max_chapter_for(db, novel_id)
    if up_to_chapter is None:
        return max_ch, True
    return up_to_chapter, up_to_chapter >= max_ch
