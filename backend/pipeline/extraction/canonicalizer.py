from __future__ import annotations

import copy
import json
import logging
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.db.client import DBClient
from pipeline.extraction.prompts import (
    build_canonicalization_system_prompt,
    build_canonicalization_user_prompt,
    build_intra_dedup_system_prompt,
    build_intra_dedup_user_prompt,
)

logger = logging.getLogger(__name__)


def _load_completion():
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return completion


def _safe_json_loads(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


class IntraExtractionDeduplicator:
    """Rewrites variant entity names within a single extraction to their canonical
    (longest) form before any DB writes, using one focused LLM call per entity type."""

    def __init__(
        self,
        *,
        use_mock: bool | None = None,
        completion_fn=None,
    ) -> None:
        self._completion = completion_fn if completion_fn is not None else _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or self._completion is None
        else:
            self.use_mock = use_mock

    def deduplicate(self, extracted: dict[str, Any], chapter_text: str) -> dict[str, Any]:
        result = copy.deepcopy(extracted)
        if self.use_mock:
            return result

        by_type = collect_names_by_type(extracted)
        rename_map: dict[str, dict[str, str]] = {}

        for entity_type, names in by_type.items():
            if len(names) < 2:
                continue
            groups = self._call_llm(entity_type=entity_type, names=sorted(names), chapter_text=chapter_text)
            type_map: dict[str, str] = {}
            for group in groups:
                valid = [n for n in group if isinstance(n, str) and n.strip()]
                if len(valid) < 2:
                    continue
                canonical = max(valid, key=len)
                for variant in valid:
                    if variant != canonical:
                        type_map[variant.lower()] = canonical
            rename_map[entity_type] = type_map

        return _apply_rename_map(result, rename_map)

    def _call_llm(self, *, entity_type: str, names: list[str], chapter_text: str) -> list[list[str]]:
        if self._completion is None:
            return []
        system_prompt = build_intra_dedup_system_prompt(entity_type)
        user_prompt = build_intra_dedup_user_prompt(entity_type, names, chapter_text)
        try:
            response = self._completion(
                model=LLM_CONFIG["model"],
                temperature=LLM_CONFIG["temperature"],
                response_format=LLM_CONFIG["response_format"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            if isinstance(content, list):
                content = "".join(str(p) for p in content)
            payload = _safe_json_loads(str(content))
        except Exception as exc:
            logger.warning("intra_dedup: LLM call failed: %s", exc)
            return []
        groups_raw = payload.get("groups")
        if not isinstance(groups_raw, list):
            return []
        return [g.get("names", []) for g in groups_raw if isinstance(g, dict)]


def _apply_rename_map(
    extracted: dict[str, Any],
    rename_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    char_map = rename_map.get("character", {})
    loc_map = rename_map.get("location", {})
    obj_map = rename_map.get("object", {})
    faction_map = rename_map.get("faction", {})

    def _r(name: str, m: dict[str, str]) -> str:
        return m.get(name.lower(), name) if name else name

    new_entities = extracted.get("new_entities", {}) or {}
    for char in new_entities.get("characters", []) or []:
        if isinstance(char, dict):
            char["name"] = _r(char.get("name", ""), char_map)
    for loc in new_entities.get("locations", []) or []:
        if isinstance(loc, dict):
            loc["name"] = _r(loc.get("name", ""), loc_map)
    for faction in new_entities.get("factions", []) or []:
        if isinstance(faction, dict):
            faction["name"] = _r(faction.get("name", ""), faction_map)
    for obj in new_entities.get("objects", []) or []:
        if isinstance(obj, dict):
            obj["name"] = _r(obj.get("name", ""), obj_map)

    # deduplicate new_entities lists by name after renaming
    type_map_pairs = [
        ("characters", char_map),
        ("locations", loc_map),
        ("factions", faction_map),
        ("objects", obj_map),
    ]
    for key, _ in type_map_pairs:
        items = new_entities.get(key) or []
        seen: set[str] = set()
        deduped: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                n = item.get("name", "")
                if n.lower() not in seen:
                    seen.add(n.lower())
                    deduped.append(item)
        if key in new_entities:
            new_entities[key] = deduped

    for delta in extracted.get("entity_deltas", []) or []:
        if not isinstance(delta, dict):
            continue
        delta["character_name"] = _r(delta.get("character_name", ""), char_map)
        if delta.get("location"):
            delta["location"] = _r(delta["location"], loc_map)

    for event in extracted.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        event["involved_characters"] = [_r(n, char_map) for n in (event.get("involved_characters") or [])]
        event["involved_locations"] = [_r(n, loc_map) for n in (event.get("involved_locations") or [])]
        event["involved_objects"] = [_r(n, obj_map) for n in (event.get("involved_objects") or [])]

    for rel in extracted.get("relationship_updates", []) or []:
        if not isinstance(rel, dict):
            continue
        rel["entity_a"] = _r(rel.get("entity_a", ""), char_map)
        rel["entity_b"] = _r(rel.get("entity_b", ""), char_map)

    for dyn in extracted.get("dynamics_updates", []) or []:
        if not isinstance(dyn, dict):
            continue
        dyn["entity_a"] = _r(dyn.get("entity_a", ""), char_map)
        dyn["entity_b"] = _r(dyn.get("entity_b", ""), char_map)

    return extracted


class CharacterCanonicalizer:
    def __init__(
        self,
        db: DBClient,
        *,
        novel_id: str,
        use_mock: bool | None = None,
        completion_fn=None,
    ) -> None:
        self.db = db
        self.novel_id = novel_id
        self._completion = completion_fn if completion_fn is not None else _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or self._completion is None
        else:
            self.use_mock = use_mock

    def canonicalize(self, *, chapter_text: str, candidate_names: set[str]) -> dict[str, str]:
        """
        Resolve candidate names against the existing roster. Appends any merged
        candidate forms to the matched character's `aliases` array.

        Returns a mapping of {candidate_name: existing_character_id} for merges
        actually performed (caller may use this for telemetry; pipeline does not
        rely on it because the strict resolver re-reads aliases from the DB).
        """
        if self.use_mock or not candidate_names:
            return {}

        roster = self._load_roster()
        unresolved = self._filter_already_known(candidate_names, roster)
        if not unresolved or not roster:
            return {}

        resolutions = self._call_llm(chapter_text=chapter_text, candidates=unresolved, roster=roster)
        if not resolutions:
            return {}

        roster_by_id: dict[str, dict[str, Any]] = {str(item["id"]): item for item in roster}

        merges: dict[str, str] = {}
        for resolution in resolutions:
            candidate = str(resolution.get("candidate", "")).strip()
            if not candidate:
                continue

            verdict = str(resolution.get("verdict", "")).strip().lower()
            if verdict != "existing":
                continue

            target_id = resolution.get("id")
            if not target_id or str(target_id) not in roster_by_id:
                continue

            anchor = str(resolution.get("grammatical_anchor") or "").strip()
            if not anchor or anchor not in chapter_text:
                logger.info(
                    "canonicalizer: rejected merge (anchor missing or not in text): %s -> %s",
                    candidate,
                    target_id,
                )
                continue

            target = roster_by_id[str(target_id)]
            existing_aliases = [str(a) for a in (target.get("aliases") or [])]
            existing_aliases_lower = {a.lower() for a in existing_aliases}
            if candidate.lower() in existing_aliases_lower:
                merges[candidate] = str(target_id)
                continue
            if candidate.lower() == str(target.get("name", "")).lower():
                merges[candidate] = str(target_id)
                continue

            new_aliases = [*existing_aliases, candidate]
            self.db.execute(
                """
                UPDATE characters
                SET aliases = %s
                WHERE id = %s AND novel_id = %s
                """,
                (new_aliases, target_id, self.novel_id),
            )
            target["aliases"] = new_aliases
            merges[candidate] = str(target_id)
            logger.info("canonicalizer: merged %r -> %s", candidate, target_id)

        return merges

    def _load_roster(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT c.id, c.name, c.aliases, c.description,
                   ls.location_id, ls.emotional_state, ls.goals, ls.physical_state
            FROM characters c
            LEFT JOIN LATERAL (
                SELECT cs.location_id, cs.emotional_state, cs.goals, cs.physical_state
                FROM character_states cs
                JOIN chapters ch ON ch.id = cs.chapter_id
                WHERE cs.character_id = c.id
                ORDER BY ch.number DESC
                LIMIT 1
            ) ls ON true
            WHERE c.novel_id = %s
            ORDER BY c.name
            """,
            (self.novel_id,),
            dict_rows=True,
        )
        roster: list[dict[str, Any]] = []
        for row in rows:
            roster.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "aliases": list(row.get("aliases") or []),
                    "description": row.get("description"),
                    "last_known": {
                        "emotional_state": row.get("emotional_state"),
                        "goals": row.get("goals"),
                        "physical_state": row.get("physical_state"),
                    },
                }
            )
        return roster

    @staticmethod
    def _filter_already_known(candidates: set[str], roster: list[dict[str, Any]]) -> list[str]:
        known: set[str] = set()
        for entry in roster:
            known.add(str(entry.get("name", "")).lower())
            for alias in entry.get("aliases") or []:
                known.add(str(alias).lower())

        unresolved: list[str] = []
        seen: set[str] = set()
        for name in candidates:
            normalized = (name or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in known or key in seen:
                continue
            seen.add(key)
            unresolved.append(normalized)
        return unresolved

    def _call_llm(
        self,
        *,
        chapter_text: str,
        candidates: list[str],
        roster: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._completion is None:
            return []

        system_prompt = build_canonicalization_system_prompt()
        user_prompt = build_canonicalization_user_prompt(chapter_text, candidates, roster)

        try:
            response = self._completion(
                model=LLM_CONFIG["model"],
                temperature=LLM_CONFIG["temperature"],
                response_format=LLM_CONFIG["response_format"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            if isinstance(content, list):
                content = "".join(str(part) for part in content)
            payload = _safe_json_loads(str(content))
        except Exception as exc:  # pragma: no cover
            logger.warning("canonicalizer: LLM call failed: %s", exc)
            return []

        resolutions = payload.get("resolutions")
        if not isinstance(resolutions, list):
            return []
        return [r for r in resolutions if isinstance(r, dict)]


def collect_names_by_type(extracted: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "character": set(),
        "location": set(),
        "object": set(),
        "faction": set(),
    }

    new_entities = extracted.get("new_entities", {}) or {}
    _collect_from_list(new_entities.get("characters"), "character", result)
    _collect_from_list(new_entities.get("locations"), "location", result)
    _collect_from_list(new_entities.get("factions"), "faction", result)
    _collect_from_list(new_entities.get("objects"), "object", result)

    for delta in extracted.get("entity_deltas", []) or []:
        if not isinstance(delta, dict):
            continue
        _add_name(str(delta.get("character_name", "")), "character", result)
        _add_name(str(delta.get("location", "")), "location", result)
        for target in (delta.get("relationships") or {}).keys():
            _add_name(str(target), "character", result)

    for event in extracted.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        for n in event.get("involved_characters", []) or []:
            _add_name(str(n), "character", result)
        for n in event.get("involved_locations", []) or []:
            _add_name(str(n), "location", result)
        for n in event.get("involved_objects", []) or []:
            _add_name(str(n), "object", result)

    for rel in extracted.get("relationship_updates", []) or []:
        if not isinstance(rel, dict):
            continue
        _add_name(str(rel.get("entity_a", "")), "character", result)
        _add_name(str(rel.get("entity_b", "")), "character", result)

    for dyn in extracted.get("dynamics_updates", []) or []:
        if not isinstance(dyn, dict):
            continue
        _add_name(str(dyn.get("entity_a", "")), "character", result)
        _add_name(str(dyn.get("entity_b", "")), "character", result)

    return result


def _collect_from_list(
    items: list[Any] | None,
    entity_type: str,
    result: dict[str, set[str]],
) -> None:
    for item in items or []:
        if isinstance(item, dict):
            _add_name(str(item.get("name", "")), entity_type, result)


def _add_name(name: str, entity_type: str, result: dict[str, set[str]]) -> None:
    normalized = name.strip()
    if normalized:
        result[entity_type].add(normalized)


def collect_character_names(extracted: dict[str, Any]) -> set[str]:
    return collect_names_by_type(extracted)["character"]


__all__ = [
    "CharacterCanonicalizer",
    "IntraExtractionDeduplicator",
    "collect_character_names",
    "collect_names_by_type",
]
