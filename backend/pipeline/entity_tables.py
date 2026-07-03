"""Single source of truth for the built-in entity type -> typed table map.

Custom entity types live only in the ``entities`` table and are not listed
here; callers that support them fall back to ``entities`` explicitly.
"""

from __future__ import annotations

TYPED_TABLES: dict[str, str] = {
    "character": "characters",
    "location": "locations",
    "faction": "factions",
    "object": "objects",
}


def table_for(entity_type: str) -> str:
    if entity_type not in TYPED_TABLES:
        raise ValueError(f"Unsupported entity type: {entity_type}")
    return TYPED_TABLES[entity_type]


__all__ = ["TYPED_TABLES", "table_for"]
