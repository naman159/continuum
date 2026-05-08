from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.db.client import DBClient


@dataclass
class ResolvedEntity:
    entity_id: str
    created: bool


class EntityResolver:
    def __init__(self, db: DBClient, novel_id: str, chapter_number: int) -> None:
        self.db = db
        self.novel_id = novel_id
        self.chapter_number = chapter_number
        self._cache: dict[tuple[str, str], str] = {}

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
            return ResolvedEntity(cached, created=False)

        table = _table_for(entity_type)
        name_row = self.db.fetchone(
            f"""
            SELECT id
            FROM {table}
            WHERE novel_id = %s AND lower(name) = lower(%s)
            LIMIT 1
            """,
            (self.novel_id, normalized_name),
        )
        if name_row:
            entity_id = str(name_row[0])
            self._cache[cache_key] = entity_id
            return ResolvedEntity(entity_id, created=False)

        if entity_type == "character":
            alias_row = self.db.fetchone(
                """
                SELECT id
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
                self._cache[cache_key] = entity_id
                return ResolvedEntity(entity_id, created=False)

        created_id = self._create_entity(entity_type, normalized_name, metadata)
        self._cache[cache_key] = created_id
        return ResolvedEntity(created_id, created=True)

    def _create_entity(self, entity_type: str, name: str, metadata: dict[str, Any]) -> str:
        if entity_type == "character":
            entity_id = self.db.fetchval(
                """
                INSERT INTO characters (
                    novel_id,
                    name,
                    aliases,
                    first_appearance_chapter,
                    description
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.novel_id,
                    name,
                    metadata.get("aliases") or [],
                    self.chapter_number,
                    metadata.get("description"),
                ),
                commit=True,
            )
            return str(entity_id)

        if entity_type == "location":
            entity_id = self.db.fetchval(
                """
                INSERT INTO locations (
                    novel_id,
                    name,
                    description,
                    first_appearance_chapter
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (self.novel_id, name, metadata.get("description"), self.chapter_number),
                commit=True,
            )
            return str(entity_id)

        if entity_type == "faction":
            entity_id = self.db.fetchval(
                """
                INSERT INTO factions (
                    novel_id,
                    name,
                    description
                )
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (self.novel_id, name, metadata.get("description")),
                commit=True,
            )
            return str(entity_id)

        if entity_type == "object":
            entity_id = self.db.fetchval(
                """
                INSERT INTO objects (
                    novel_id,
                    name,
                    description,
                    significance,
                    first_appearance_chapter
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.novel_id,
                    name,
                    metadata.get("description"),
                    metadata.get("significance"),
                    self.chapter_number,
                ),
                commit=True,
            )
            return str(entity_id)

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
