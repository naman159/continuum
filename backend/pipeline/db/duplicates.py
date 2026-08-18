"""Find entity pairs that look like duplicates of each other.

Paired with `entity_merge`: this is the "what could be merged" half. It lives
next to the merge rather than in `reads/` because it is a maintenance query over
the entity tables, not chapter-anchored story data — it has no meaningful
`up_to_chapter`, since a duplicate introduced in chapter 40 is still a duplicate
you want to see while reviewing the novel.

Scoring reuses the canonicalizer's own `_name_pair_similarity` at its own
`LEXICAL_MATCH_RATIO`, so a pair listed here is one the canonicalizer would have
called a match had it been comparing them at all. `cross_type` marks the pairs
where it never was: `EntityCanonicalizer._load_roster` loads candidates one
entity_type at a time, so a character is never compared against an object.

These are candidates, not confirmed duplicates. "Mr. Bennet" and "Mrs. Bennet"
score 0.95 and are two people. Nothing here merges anything; the caller decides.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any
from uuid import UUID

from pipeline.entity_tables import TYPED_TABLES
from pipeline.extraction.canonicalizer import (
    LEXICAL_MATCH_RATIO,
    _name_pair_similarity,
    normalize_name,
)


def load_entity_surfaces(db: Any, novel_id: UUID | str) -> list[dict[str, Any]]:
    """Every typed entity with its surface forms, normalized the way the
    canonicalizer normalizes them so comparisons are apples-to-apples."""
    rows: list[dict[str, Any]] = []
    for entity_type, table in TYPED_TABLES.items():
        # ORDER BY id is load-bearing, not cosmetic: er_eval assigns each
        # surface to the first entity that claims it, so an unordered scan
        # makes precision/recall/F1 shift between runs on identical data
        # whenever two entities share a surface.
        for r in db.fetchall(
            f"SELECT id, entity_id, name, aliases FROM {table} "
            "WHERE novel_id = %s ORDER BY id",
            (str(novel_id),),
            dict_rows=True,
        ):
            surfaces = {normalize_name(r["name"])}
            surfaces |= {normalize_name(a) for a in (r["aliases"] or [])}
            rows.append({
                "id": str(r["id"]),
                "entity_id": str(r["entity_id"]) if r["entity_id"] else None,
                "type": entity_type,
                "name": r["name"],
                "surfaces": surfaces - {""},
            })
    return rows


def find_duplicate_candidates(db: Any, novel_id: UUID | str) -> dict[str, Any]:
    """Duplicate-looking entity pairs, split same-type vs cross-type.

    Cross-type pairs are reported first and separately because they are the ones
    nothing in the pipeline can reach on its own — and, until `entity_merge`
    learned to reclassify, could not be repaired by hand either.
    """
    entities = load_entity_surfaces(db, novel_id)
    same_type: list[dict[str, Any]] = []
    cross_type: list[dict[str, Any]] = []

    for a, b in combinations(entities, 2):
        if a["surfaces"] & b["surfaces"]:
            score, kind = 1.0, "exact"
        else:
            score = max(
                (_name_pair_similarity(x, y) for x in a["surfaces"] for y in b["surfaces"]),
                default=0.0,
            )
            if score < LEXICAL_MATCH_RATIO:
                continue
            kind = "near"
        pair = {
            "score": round(score, 4),
            "kind": kind,
            "a": {"entity_id": a["entity_id"], "type": a["type"], "name": a["name"]},
            "b": {"entity_id": b["entity_id"], "type": b["type"], "name": b["name"]},
        }
        (same_type if a["type"] == b["type"] else cross_type).append(pair)

    by_score: Any = lambda rows: sorted(rows, key=lambda p: -p["score"])  # noqa: E731
    return {
        "entity_count": len(entities),
        "cross_type_pairs": by_score(cross_type),
        "same_type_pairs": by_score(same_type),
        "cross_type_count": len(cross_type),
        "same_type_count": len(same_type),
    }


__all__ = ["find_duplicate_candidates", "load_entity_surfaces"]
