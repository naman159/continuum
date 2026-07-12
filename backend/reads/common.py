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


# ---------------------------------------------------------------------------
# Relationship symmetry classification (duplicated from api/relationship_types.py
# rather than imported, so reads/ has no dependency on api/ — api/queries.py
# keeps its own copy for the callers it still owns).

SYMMETRIC_REL_TYPES = frozenset({
    "spouse_of",
    "spouse",
    "married_to",
    "marriage",
    "husband_of",
    "wife_of",
    "sibling_of",
    "sibling",
    "brother_of",
    "sister_of",
    "twin_of",
    "cousin_of",
    "cousin",
    "friend_of",
    "friend",
    "best_friend_of",
    "colleague_of",
    "coworker_of",
    "co-worker_of",
    "partner_of",
    "business_partner_of",
    "ally_of",
    "allied_with",
    "rival_of",
    "rival",
    "enemy_of",
    "nemesis_of",
    "neighbor_of",
    "roommate_of",
    "classmate_of",
    "engaged_to",
    "in-law_of",
    "related_to",
})


def is_symmetric(rel_type: str | None) -> bool:
    if rel_type is None:
        return False
    return rel_type.strip().lower() in SYMMETRIC_REL_TYPES


def resolve_symmetric(rel_type: str | None, stored_symmetric: bool | None) -> bool:
    """Prefer the extractor's per-instance judgment; fall back to the static label lookup."""
    if stored_symmetric is not None:
        return bool(stored_symmetric)
    return is_symmetric(rel_type)
