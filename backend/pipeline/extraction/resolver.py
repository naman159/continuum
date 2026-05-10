from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.db.client import DBClient


@dataclass
class ResolvedEntity:
    entity_id: str       # type-specific ID (e.g. characters.id)
    universal_id: str    # entities.id — use for relationships / shared_dynamics
    created: bool


class EntityResolver:
    def __init__(self, db: DBClient, novel_id: str, chapter_number: int) -> None:
        self.db = db
        self.novel_id = novel_id
        self.chapter_number = chapter_number
        # (entity_type, lower_name) -> (entity_id, universal_id)
        self._cache: dict[tuple[str, str], tuple[str, str]] = {}

    def resolve_character(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("character", name, metadata or {})

    def resolve_location(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("location", name, metadata or {})

    def resolve_faction(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("faction", name, metadata or {})

    def resolve_object(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("object", name, metadata or {})

    def _resolve(self, entity_type: str, name: str, metadata: dict[str, Any]) -> ResolvedEntity:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError(f"Cannot resolve empty {entity_type} name")

        cache_key = (entity_type, normalized_name.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return ResolvedEntity(cached[0], cached[1], created=False)

        table = _table_for(entity_type)
        name_row = self.db.fetchone(
            f"""
            SELECT id, entity_id
            FROM {table}
            WHERE novel_id = %s AND lower(name) = lower(%s)
            LIMIT 1
            """,
            (self.novel_id, normalized_name),
        )
        if name_row:
            entity_id = str(name_row[0])
            universal_id = str(name_row[1]) if name_row[1] else entity_id
            self._cache[cache_key] = (entity_id, universal_id)
            return ResolvedEntity(entity_id, universal_id, created=False)

        if entity_type == "character":
            alias_row = self.db.fetchone(
                """
                SELECT id, entity_id
                FROM characters
                WHERE novel_id = %s
                  AND EXISTS (
                      SELECT 1 FROM unnest(aliases) AS a WHERE lower(a) = lower(%s)
                  )
                LIMIT 1
                """,
                (self.novel_id, normalized_name),
            )
            if alias_row:
                entity_id = str(alias_row[0])
                universal_id = str(alias_row[1]) if alias_row[1] else entity_id
                self._cache[cache_key] = (entity_id, universal_id)
                return ResolvedEntity(entity_id, universal_id, created=False)

            # Partial-name match: "Jane" <-> "Jane Bennet" (one name is a word-boundary
            # prefix of the other). The shorter form becomes an alias of the longer one.
            partial_row = self.db.fetchone(
                """
                SELECT id, entity_id, name, aliases
                FROM characters
                WHERE novel_id = %s
                  AND (
                      lower(name) LIKE lower(%s || ' %%')
                      OR lower(%s) LIKE lower(name || ' %%')
                  )
                LIMIT 1
                """,
                (self.novel_id, normalized_name, normalized_name),
            )
            if partial_row:
                entity_id = str(partial_row[0])
                universal_id = str(partial_row[1]) if partial_row[1] else entity_id
                existing_name = str(partial_row[2])
                existing_aliases = list(partial_row[3] or [])
                # Add whichever form is shorter as an alias (the short form may
                # not be in aliases yet if this is the first time it appears).
                short_form = normalized_name if len(normalized_name) < len(existing_name) else existing_name
                if short_form.lower() not in {a.lower() for a in existing_aliases} and short_form.lower() != existing_name.lower():
                    self.db.execute(
                        "UPDATE characters SET aliases = %s WHERE id = %s",
                        (existing_aliases + [short_form], entity_id),
                    )
                self._cache[cache_key] = (entity_id, universal_id)
                return ResolvedEntity(entity_id, universal_id, created=False)

        entity_id, universal_id = self._create_entity(entity_type, normalized_name, metadata)
        self._cache[cache_key] = (entity_id, universal_id)
        return ResolvedEntity(entity_id, universal_id, created=True)

    def _create_entity(self, entity_type: str, name: str, metadata: dict[str, Any]) -> tuple[str, str]:
        universal_id = str(self.db.fetchval(
            """
            INSERT INTO entities (novel_id, entity_type, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, entity_type, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (self.novel_id, entity_type, name),
            commit=True,
        ))

        if entity_type == "character":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO characters (novel_id, entity_id, name, aliases, first_appearance_chapter, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.novel_id,
                    universal_id,
                    name,
                    metadata.get("aliases") or [],
                    self.chapter_number,
                    metadata.get("description"),
                ),
                commit=True,
            ))
            return entity_id, universal_id

        if entity_type == "location":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO locations (novel_id, entity_id, name, description, first_appearance_chapter)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (self.novel_id, universal_id, name, metadata.get("description"), self.chapter_number),
                commit=True,
            ))
            return entity_id, universal_id

        if entity_type == "faction":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO factions (novel_id, entity_id, name, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (self.novel_id, universal_id, name, metadata.get("description")),
                commit=True,
            ))
            return entity_id, universal_id

        if entity_type == "object":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO objects (novel_id, entity_id, name, description, significance, first_appearance_chapter)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.novel_id,
                    universal_id,
                    name,
                    metadata.get("description"),
                    metadata.get("significance"),
                    self.chapter_number,
                ),
                commit=True,
            ))
            return entity_id, universal_id

        raise ValueError(f"Unsupported entity type: {entity_type}")


def _table_for(entity_type: str) -> str:
    mapping = {
        "character": "characters",
        "location": "locations",
        "faction": "factions",
        "object": "objects",
    }
    if entity_type not in mapping:
        raise ValueError(f"Unsupported entity type: {entity_type}")
    return mapping[entity_type]


__all__ = ["EntityResolver", "ResolvedEntity"]
