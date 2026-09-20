from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pipeline.db.client import DBClient
from pipeline.db.history import metadata_table
from pipeline.entity_tables import table_for

logger = logging.getLogger(__name__)


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

    def resolve_character(
        self, name: str, metadata: dict[str, Any] | None = None, *, create: bool = True
    ) -> ResolvedEntity | None:
        """Resolve a character by name.

        With create=True (default, used by the authoritative new_entities pass)
        an unknown name is created. With create=False (reference-only passes —
        events, scenes, deltas, knowledge) an unknown name resolves to None and
        NO character row is minted; this is the guard that keeps game-system
        elements and other non-characters out of the characters table when they
        are merely referenced by a downstream pass.
        """
        return self._resolve("character", name, metadata or {}, create=create)

    def resolve_location(
        self, name: str, metadata: dict[str, Any] | None = None, *, create: bool = True
    ) -> ResolvedEntity | None:
        meta = metadata or {}
        parent_name = str(meta.get("parent_location") or "").strip()
        normalized = (name or "").strip()
        if parent_name and parent_name.lower() != normalized.lower():
            # This is a sub-location — always resolve (or create) the parent
            # instead. The description belongs to the sub-location, so a newly
            # created parent must not inherit it; and the sub-location name
            # becomes an alias of the parent so metadata-less callers (event
            # and scene resolution) fold to the same row instead of creating a
            # standalone duplicate location.
            parent = self._resolve("location", parent_name, {}, create=create)
            if parent is None:
                return None
            self._append_location_alias(parent.entity_id, normalized)
            self._cache[("location", normalized.lower())] = (
                parent.entity_id,
                parent.universal_id,
            )
            return parent
        return self._resolve("location", normalized, meta, create=create)

    def _merge_aliases(
        self, entity_type: str, typed_id: str, metadata: dict[str, Any]
    ) -> None:
        """Add extractor-supplied aliases to an entity that already exists.

        Only additive — an alias is never removed, and the canonical name is
        never changed, so this cannot merge two distinct entities. Names that
        already resolve elsewhere are skipped rather than stolen.
        """
        incoming = [
            str(a).strip()
            for a in (metadata or {}).get("aliases", []) or []
            if str(a).strip()
        ]
        if not incoming:
            return
        table = table_for(entity_type)
        if table is None:
            return
        row = self.db.fetchone(
            f"SELECT name, aliases FROM {table} WHERE id = %s LIMIT 1", (typed_id,)
        )
        if not row:
            return
        name = str(row[0] or "")
        aliases = list(row[1] or [])
        known = {name.lower()} | {a.lower() for a in aliases}
        added = []
        for alias in incoming:
            if alias.lower() in known:
                continue
            # Don't claim a surface form that already identifies a different
            # entity of this type — that would silently repoint it.
            clash = self._lookup_typed(entity_type, alias)
            if clash is not None and str(clash[0]) != str(typed_id):
                logger.warning(
                    "alias %r for %s %r already resolves to a different entity; skipping",
                    alias, entity_type, name,
                )
                continue
            added.append(alias)
            known.add(alias.lower())
        if added:
            self.db.execute(
                f"UPDATE {table} SET aliases = %s WHERE id = %s",
                (aliases + added, typed_id),
            )

    def _append_location_alias(self, location_id: str, alias: str) -> None:
        row = self.db.fetchone(
            "SELECT name, aliases FROM locations WHERE id = %s LIMIT 1",
            (location_id,),
        )
        if not row:
            return
        name = str(row[0] or "")
        aliases = list(row[1] or [])
        if alias.lower() == name.lower() or alias.lower() in {a.lower() for a in aliases}:
            return
        self.db.execute(
            "UPDATE locations SET aliases = %s WHERE id = %s",
            (aliases + [alias], location_id),
        )

    def resolve_faction(
        self, name: str, metadata: dict[str, Any] | None = None, *, create: bool = True
    ) -> ResolvedEntity | None:
        return self._resolve("faction", name, metadata or {}, create=create)

    def resolve_object(
        self, name: str, metadata: dict[str, Any] | None = None, *, create: bool = True
    ) -> ResolvedEntity | None:
        return self._resolve("object", name, metadata or {}, create=create)

    def resolve_custom_entity(
        self, name: str, entity_type: str, metadata: dict[str, Any] | None = None
    ) -> ResolvedEntity:
        """Resolve or create a custom-typed entity (entities table only, no dedicated table)."""
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError(f"Cannot resolve empty {entity_type} name")

        cache_key = (entity_type, normalized_name.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return ResolvedEntity(cached[0], cached[1], created=False)

        # Check existing entity in entities table directly (by name, then alias).
        row = self.db.fetchone(
            """
            SELECT id FROM entities
            WHERE novel_id = %s AND entity_type = %s
              AND (
                  lower(name) = lower(%s)
                  OR EXISTS (
                      SELECT 1 FROM unnest(aliases) AS a WHERE lower(a) = lower(%s)
                  )
              )
            LIMIT 1
            """,
            (self.novel_id, entity_type, normalized_name, normalized_name),
        )
        if row:
            uid = str(row[0])
            self._cache[cache_key] = (uid, uid)
            return ResolvedEntity(uid, uid, created=False)

        # Create: insert into entities only.
        universal_id = str(self.db.fetchval(
            """
            INSERT INTO entities (novel_id, entity_type, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, entity_type, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (self.novel_id, entity_type, normalized_name),
            commit=True,
        ))
        self._cache[cache_key] = (universal_id, universal_id)
        return ResolvedEntity(universal_id, universal_id, created=True)

    def resolve_any_entity(self, name: str, *, create: bool = True) -> str | None:
        """Return the universal entity ID for any entity type.

        Lookup order: entities table by name or alias, then each typed table by
        name or alias (catches entities referenced by an alias that only the
        typed table knows about). Only if nothing matches anywhere does it fall
        back to creating a character — previously an aliased faction/location/
        object reference would silently create a phantom character here.

        With create=False (reference-only passes — relationships, dynamics,
        commitment links) an unresolvable name returns None instead of minting a
        phantom character, so a link is simply dropped rather than inventing an
        entity for it.
        """
        normalized = (name or "").strip()
        if not normalized:
            raise ValueError("Cannot resolve empty entity name")

        cache_key = ("any", normalized.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return cached[1]

        row = self.db.fetchone(
            """
            SELECT id FROM entities
            WHERE novel_id = %s
              AND (
                  lower(name) = lower(%s)
                  OR EXISTS (
                      SELECT 1 FROM unnest(aliases) AS a WHERE lower(a) = lower(%s)
                  )
              )
            LIMIT 1
            """,
            (self.novel_id, normalized, normalized),
        )
        if row:
            uid = str(row[0])
            self._cache[cache_key] = (uid, uid)
            return uid

        for entity_type in ("character", "faction", "location", "object"):
            found = self._lookup_typed(entity_type, normalized)
            if found:
                self._cache[cache_key] = found
                return found[1]

        if not create:
            return None

        resolved = self.resolve_character(normalized)
        assert resolved is not None  # create defaults True — always resolves
        self._cache[cache_key] = (resolved.entity_id, resolved.universal_id)
        return resolved.universal_id

    def _resolve(
        self, entity_type: str, name: str, metadata: dict[str, Any], *, create: bool = True
    ) -> ResolvedEntity | None:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError(f"Cannot resolve empty {entity_type} name")

        cache_key = (entity_type, normalized_name.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return ResolvedEntity(cached[0], cached[1], created=False)

        found = self._lookup_typed(entity_type, normalized_name)
        if found:
            self._cache[cache_key] = found
            # Fold in aliases the extractor supplied for an entity that already
            # exists. _create_entity writes metadata["aliases"], but only on
            # create — so a nickname first established in a later chapter used
            # to be dropped on the floor, and the next bare use of it minted a
            # duplicate entity. `create` gates this for the same reason as the
            # partial-match branch: only authoritative passes touch identity.
            if create:
                self._merge_aliases(entity_type, found[0], metadata)
            return ResolvedEntity(found[0], found[1], created=False)

        if entity_type == "character":
            # Partial-name match: "Jane" <-> "Jane Bennet" (one name is a word-boundary
            # prefix of the other). The shorter form becomes an alias of the longer one.
            like_safe = (
                normalized_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            # Deliberately NOT `LIMIT 1`: with two candidates the match is
            # ambiguous, and picking one meant binding "Elizabeth" to whichever
            # of Elizabeth Bennet / Elizabeth Elliot the planner happened to
            # return — then persisting that guess as an alias, permanently. An
            # unresolvable name is recoverable; a wrong merge is not.
            partial_rows = self.db.fetchall(
                r"""
                SELECT id, entity_id, name, aliases
                FROM characters
                WHERE novel_id = %s
                  AND (
                      lower(name) LIKE lower(%s || ' %%')
                      OR lower(%s) LIKE lower(
                          replace(replace(replace(name, '\', '\\'), '%%', '\%%'), '_', '\_') || ' %%'
                      )
                  )
                ORDER BY id
                """,
                (self.novel_id, like_safe, normalized_name),
            )
            if len(partial_rows) > 1:
                logger.warning(
                    "character name %r partially matches %d existing characters (%s); "
                    "refusing to guess — resolve it explicitly or add an alias",
                    normalized_name,
                    len(partial_rows),
                    ", ".join(str(r[2]) for r in partial_rows[:5]),
                )
            elif partial_rows:
                partial_row = partial_rows[0]
                entity_id = str(partial_row[0])
                universal_id = str(partial_row[1]) if partial_row[1] else entity_id
                existing_name = str(partial_row[2])
                existing_aliases = list(partial_row[3] or [])
                # Add whichever form is shorter as an alias (the short form may
                # not be in aliases yet if this is the first time it appears).
                short_form = normalized_name if len(normalized_name) < len(existing_name) else existing_name
                # Only an authoritative pass may persist an alias. Reference-only
                # passes (scene POV, knows-edges, event actors, state deltas)
                # resolve for lookup and must not mutate identity: a stray name
                # in a scene list is far weaker evidence than the canonicalizer
                # demands before it will write one.
                if (
                    create
                    and short_form.lower() not in {a.lower() for a in existing_aliases}
                    and short_form.lower() != existing_name.lower()
                ):
                    self.db.execute(
                        "UPDATE characters SET aliases = %s WHERE id = %s",
                        (existing_aliases + [short_form], entity_id),
                    )
                self._cache[cache_key] = (entity_id, universal_id)
                return ResolvedEntity(entity_id, universal_id, created=False)

        if not create:
            # Reference-only resolution: the entity doesn't exist and we are not
            # allowed to mint one. Don't cache the miss — a later authoritative
            # pass (create=True) may still create it.
            return None

        entity_id, universal_id = self._create_entity(entity_type, normalized_name, metadata)
        self._cache[cache_key] = (entity_id, universal_id)
        return ResolvedEntity(entity_id, universal_id, created=True)

    def _lookup_typed(self, entity_type: str, name: str) -> tuple[str, str] | None:
        return lookup_typed(self.db, self.novel_id, entity_type, name)

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


def lookup_typed(
    db: Any, novel_id: str, entity_type: str, name: str, *, cutoff: int | None = None
) -> tuple[str, str] | None:
    """Find an existing row in the typed table by exact name or alias, name
    matches taking priority, in a single query.

    Returns (typed_id, universal_id) or None. Never creates anything — safe
    for read-only callers like the draft-claims critic bridge.
    """
    table = table_for(entity_type)
    relation = table if cutoff is None else metadata_table(table, novel_id, cutoff)
    row = db.fetchone(
        f"""
        SELECT id, entity_id
        FROM {relation} AS typed
        WHERE novel_id = %s
          AND (
              lower(name) = lower(%s)
              OR EXISTS (SELECT 1 FROM unnest(aliases) AS a WHERE lower(a) = lower(%s))
          )
        ORDER BY (lower(name) = lower(%s)) DESC
        LIMIT 1
        """,
        (novel_id, name, name, name),
    )
    if row:
        entity_id = str(row[0])
        universal_id = str(row[1]) if row[1] else entity_id
        return entity_id, universal_id
    return None


__all__ = ["EntityResolver", "ResolvedEntity", "lookup_typed"]
