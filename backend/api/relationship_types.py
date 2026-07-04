"""Classification of relationship-type labels as symmetric or directional.

`relationships.rel_type` is freeform text produced by the LLM extractor, not
a fixed enum, so this is a best-effort lookup rather than an exhaustive
mapping. A relationship is symmetric when the label reads the same from
either entity's side (e.g. "spouse_of"); it is directional when swapping
entity_a/entity_b would change the meaning (e.g. "mentor_of").
"""

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
    """Prefer the extractor's per-instance judgment; fall back to the static label lookup.

    The extractor sees the actual chapter text and can tell a one-sided "friend_of"
    (A considers B a friend; B doesn't) from a genuinely mutual one, which a label-only
    lookup never can. `stored_symmetric` is the `relationships.symmetric` column, which
    is NULL for rows written before this field existed or when the extractor left it unset.
    """
    if stored_symmetric is not None:
        return bool(stored_symmetric)
    return is_symmetric(rel_type)
