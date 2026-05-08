from __future__ import annotations

import json
import logging
from typing import Any

from config import LLM_CONFIG, settings
from db.client import DBClient
from extraction.prompts import (
    build_canonicalization_system_prompt,
    build_canonicalization_user_prompt,
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


def collect_character_names(extracted: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    new_entities = extracted.get("new_entities", {}) or {}
    for char in new_entities.get("characters", []) or []:
        if isinstance(char, dict):
            name = str(char.get("name", "")).strip()
            if name:
                names.add(name)

    for delta in extracted.get("entity_deltas", []) or []:
        if not isinstance(delta, dict):
            continue
        name = str(delta.get("character_name", "")).strip()
        if name:
            names.add(name)
        relationships = delta.get("relationships")
        if isinstance(relationships, dict):
            for target in relationships.keys():
                target_name = str(target).strip()
                if target_name:
                    names.add(target_name)

    for event in extracted.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        for character in event.get("involved_characters", []) or []:
            character_name = str(character).strip()
            if character_name:
                names.add(character_name)

    return names


__all__ = ["CharacterCanonicalizer", "collect_character_names"]
