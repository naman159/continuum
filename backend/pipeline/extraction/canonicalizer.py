from __future__ import annotations

import copy
import difflib
import logging
import re
import unicodedata
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.db.client import DBClient
from pipeline.entity_tables import TYPED_TABLES
from pipeline.extraction.prompts import (
    build_canonicalization_system_prompt,
    build_canonicalization_user_prompt,
    build_intra_dedup_system_prompt,
    build_intra_dedup_user_prompt,
)

logger = logging.getLogger(__name__)

BUILTIN_ENTITY_TYPES = ("character", "location", "object", "faction")

_LEADING_ARTICLES = ("the ", "a ", "an ")


def normalize_name(name: str) -> str:
    """Aggressive-but-safe normalization for same-type, same-novel name equality.

    Two names of the same entity type that normalize identically are treated as
    the same entity without consulting an LLM (case, whitespace, surrounding
    punctuation/quotes, unicode forms, and a leading English article).
    """
    n = unicodedata.normalize("NFKC", str(name or ""))
    n = n.replace("’", "'").replace("‘", "'")
    n = n.strip().strip("\"'“”").strip()
    n = re.sub(r"\s+", " ", n).lower()
    for article in _LEADING_ARTICLES:
        if n.startswith(article) and len(n) > len(article):
            n = n[len(article):]
            break
    n = n.rstrip(".,;:!?")
    return n or str(name or "").strip().lower()


from pipeline.llm import load_completion as _load_completion
from pipeline.llm import safe_json_loads as _safe_json_loads


def _names_lexically_close(candidate: str, target: dict[str, Any]) -> bool:
    """True when the candidate is plausibly the same entity as the target's
    name or an alias on lexical evidence alone: token subset ("Empire" <->
    "Galactic Empire") or high sequence similarity ("Kings Landing" <->
    "King's Landing")."""
    nc = normalize_name(candidate)
    if not nc:
        return False
    c_tokens = set(nc.split())
    for raw in [target.get("name", ""), *(target.get("aliases") or [])]:
        nn = normalize_name(str(raw))
        if not nn:
            continue
        if nc == nn:
            return True
        t_tokens = set(nn.split())
        if c_tokens and t_tokens and (c_tokens <= t_tokens or t_tokens <= c_tokens):
            return True
        if difflib.SequenceMatcher(None, nc, nn).ratio() >= 0.85:
            return True
    return False


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
        raw_entities_by_type = _collect_entities_by_type(extracted)
        rename_map: dict[str, dict[str, str]] = {}

        for entity_type, names in by_type.items():
            if len(names) < 2:
                continue
            if entity_type == "object":
                # Send the enriched dicts (owner context), plus plain entries for
                # object names that only appear in events, never in new_entities.
                object_dicts: list = raw_entities_by_type.get("object", [])
                dict_names = {str(e.get("name", "")).strip().lower() for e in object_dicts}
                entities_input: list = list(object_dicts)
                entities_input += [{"name": n} for n in sorted(names) if n.lower() not in dict_names]
            else:
                entities_input = sorted(names)
            groups = self._call_llm(entity_type=entity_type, entities=entities_input, chapter_text=chapter_text)
            # Guard against hallucinated group members: only names actually present
            # in this extraction may participate, and the canonical form must be a
            # real input name (otherwise variants get renamed to invented strings).
            originals = {n.lower(): n for n in names}
            type_map: dict[str, str] = {}
            for group in groups:
                valid: list[str] = []
                seen_keys: set[str] = set()
                for raw in group:
                    if not isinstance(raw, str):
                        continue
                    key = raw.strip().lower()
                    if key in originals and key not in seen_keys:
                        seen_keys.add(key)
                        valid.append(originals[key])
                if len(valid) < 2:
                    continue
                canonical = max(valid, key=len)
                for variant in valid:
                    if variant.lower() != canonical.lower():
                        type_map[variant.lower()] = canonical
            rename_map[entity_type] = type_map

        return _apply_rename_map(result, rename_map)

    def _call_llm(self, *, entity_type: str, entities: list, chapter_text: str) -> list[list[str]]:
        if self._completion is None:
            return []
        system_prompt = build_intra_dedup_system_prompt(entity_type)
        user_prompt = build_intra_dedup_user_prompt(entity_type, entities, chapter_text)
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
    data: dict[str, Any],
    rename_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    char_map = rename_map.get("character", {})
    loc_map = rename_map.get("location", {})
    obj_map = rename_map.get("object", {})
    faction_map = rename_map.get("faction", {})

    def _r(name: str, m: dict[str, str]) -> str:
        return m.get(name.lower(), name) if name else name

    def _r_list(names: list, m: dict[str, str]) -> list[str]:
        """Rename every entry, then drop case-insensitive duplicates (renaming
        variants to one canonical form otherwise leaves the same name twice)."""
        seen: set[str] = set()
        out: list[str] = []
        for n in names or []:
            renamed = _r(str(n), m)
            key = renamed.lower()
            if renamed and key not in seen:
                seen.add(key)
                out.append(renamed)
        return out

    new_entities = data.get("new_entities", {}) or {}
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
        seen: set = set()
        deduped: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                n = item.get("name", "")
                if key == "objects":
                    # Objects with different owners are distinct even if names match
                    owner = str(item.get("owner_name") or "").strip().lower()
                    dedup_key = (n.lower(), owner)
                else:
                    dedup_key = n.lower()
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    deduped.append(item)
        if key in new_entities:
            new_entities[key] = deduped

    for delta in data.get("entity_deltas", []) or []:
        if not isinstance(delta, dict):
            continue
        delta["character_name"] = _r(delta.get("character_name", ""), char_map)
        if delta.get("location"):
            delta["location"] = _r(delta["location"], loc_map)

    for event in data.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        event["involved_characters"] = _r_list(event.get("involved_characters"), char_map)
        event["involved_locations"] = _r_list(event.get("involved_locations"), loc_map)
        event["involved_objects"] = _r_list(event.get("involved_objects"), obj_map)
        event["involved_factions"] = _r_list(event.get("involved_factions"), faction_map)

    for rel in data.get("relationship_updates", []) or []:
        if not isinstance(rel, dict):
            continue
        rel["entity_a"] = _r(rel.get("entity_a", ""), char_map)
        rel["entity_b"] = _r(rel.get("entity_b", ""), char_map)

    for dyn in data.get("dynamics_updates", []) or []:
        if not isinstance(dyn, dict):
            continue
        dyn["entity_a"] = _r(dyn.get("entity_a", ""), char_map)
        dyn["entity_b"] = _r(dyn.get("entity_b", ""), char_map)

    for scene in data.get("scenes", []) or []:
        if not isinstance(scene, dict):
            continue
        if scene.get("pov_character_name"):
            scene["pov_character_name"] = _r(str(scene["pov_character_name"]), char_map)
        if scene.get("location_name"):
            scene["location_name"] = _r(str(scene["location_name"]), loc_map)
        scene["present_character_names"] = _r_list(scene.get("present_character_names"), char_map)

    for learning in data.get("learnings", []) or []:
        if not isinstance(learning, dict):
            continue
        learning["character_name"] = _r(str(learning.get("character_name", "")), char_map)
        if learning.get("source_character_name"):
            learning["source_character_name"] = _r(str(learning["source_character_name"]), char_map)
        learning["shared_with_character_names"] = _r_list(
            learning.get("shared_with_character_names"), char_map
        )

    for fact in data.get("canon_facts", []) or []:
        if not isinstance(fact, dict):
            continue
        subject_type = str(fact.get("subject_type", "")).strip().lower()
        fact["subject_name"] = _r(str(fact.get("subject_name", "")), rename_map.get(subject_type, {}))

    # Custom entities: rename per their own type map, then dedupe by (type, name).
    custom_entities = data.get("custom_entities")
    if isinstance(custom_entities, list):
        seen_custom: set[tuple[str, str]] = set()
        deduped_custom: list[Any] = []
        for item in custom_entities:
            if not isinstance(item, dict):
                continue
            ce_type = str(item.get("type", "")).strip()
            item["name"] = _r(str(item.get("name", "")), rename_map.get(ce_type, {}))
            key = (ce_type.lower(), str(item.get("name", "")).strip().lower())
            if not key[1] or key in seen_custom:
                continue
            seen_custom.add(key)
            deduped_custom.append(item)
        data["custom_entities"] = deduped_custom

    return data


class EntityCanonicalizer:
    _TABLE = TYPED_TABLES

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

    def canonicalize(
        self,
        *,
        chapter_text: str,
        candidate_names_by_type: dict[str, set[str]],
    ) -> dict[str, dict[str, str]]:
        """
        For each entity type, resolves unrecognised candidate names against the
        DB roster and appends matched forms as aliases.

        Built-in types use their dedicated tables; any other key is treated as a
        custom entity type backed by the ``entities`` table.

        Returns {entity_type: {candidate_name: entity_id}} for all merges performed.
        """
        if self.use_mock:
            return {}

        all_merges: dict[str, dict[str, str]] = {}
        for entity_type, candidates in candidate_names_by_type.items():
            if not candidates:
                continue
            roster = self._load_roster(entity_type)
            if not roster:
                continue
            unresolved = self._filter_already_known(candidates, roster)
            if not unresolved:
                continue
            merges: dict[str, str] = {}
            # Free pass first: normalized-equality matches need no LLM call.
            unresolved = self._apply_deterministic_matches(entity_type, unresolved, roster, merges)
            if unresolved:
                llm_roster = _select_roster_subset(
                    unresolved, roster, settings.canonicalizer_max_roster
                )
                resolutions = self._call_llm(
                    chapter_text=chapter_text,
                    entity_type=entity_type,
                    candidates=unresolved,
                    roster=llm_roster,
                )
                merges.update(self._apply_resolutions(entity_type, resolutions, llm_roster, chapter_text))
            if merges:
                all_merges[entity_type] = merges

        return all_merges

    def _load_roster(self, entity_type: str) -> list[dict[str, Any]]:
        table = self._TABLE.get(entity_type)
        if table is None:
            # Custom entity type: roster lives in the generic entities table.
            rows = self.db.fetchall(
                """
                SELECT id, name, aliases
                FROM entities
                WHERE novel_id = %s AND entity_type = %s
                ORDER BY name
                """,
                (self.novel_id, entity_type),
                dict_rows=True,
            )
            return [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "aliases": list(row.get("aliases") or []),
                    "description": None,
                }
                for row in rows
            ]
        if entity_type == "character":
            rows = self.db.fetchall(
                """
                SELECT c.id, c.name, c.aliases, c.description,
                       ls.emotional_state, ls.goals, ls.physical_state
                FROM characters c
                LEFT JOIN LATERAL (
                    SELECT cs.emotional_state, cs.goals, cs.physical_state
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
            return [
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
                for row in rows
            ]

        rows = self.db.fetchall(
            f"""
            SELECT id, name, aliases, description
            FROM {table}
            WHERE novel_id = %s
            ORDER BY name
            """,
            (self.novel_id,),
            dict_rows=True,
        )
        return [
            {
                "id": str(row["id"]),
                "name": row["name"],
                "aliases": list(row.get("aliases") or []),
                "description": row.get("description"),
            }
            for row in rows
        ]

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
        entity_type: str,
        candidates: list[str],
        roster: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._completion is None:
            return []
        system_prompt = build_canonicalization_system_prompt(entity_type)
        user_prompt = build_canonicalization_user_prompt(chapter_text, candidates, roster, entity_type)
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
            logger.warning("entity_canonicalizer: LLM call failed for %s: %s", entity_type, exc)
            return []
        resolutions = payload.get("resolutions")
        if not isinstance(resolutions, list):
            return []
        return [r for r in resolutions if isinstance(r, dict)]

    def _apply_deterministic_matches(
        self,
        entity_type: str,
        unresolved: list[str],
        roster: list[dict[str, Any]],
        merges: dict[str, str],
    ) -> list[str]:
        """Merge candidates whose normalized form equals exactly one roster
        name/alias, without an LLM call. Returns the still-unresolved names."""
        lookup: dict[str, Any] = {}
        for entry in roster:
            for raw in [entry.get("name", ""), *(entry.get("aliases") or [])]:
                key = normalize_name(str(raw))
                if not key:
                    continue
                if key in lookup and lookup[key] is not entry:
                    lookup[key] = _AMBIGUOUS
                elif key not in lookup:
                    lookup[key] = entry

        still_unresolved: list[str] = []
        for candidate in unresolved:
            entry = lookup.get(normalize_name(candidate))
            if entry is None or entry is _AMBIGUOUS:
                still_unresolved.append(candidate)
                continue
            self._append_alias(entity_type, entry, candidate)
            merges[candidate] = str(entry["id"])
            logger.info(
                "entity_canonicalizer: deterministic merge %r -> %s (%s)",
                candidate,
                entry["id"],
                entity_type,
            )
        return still_unresolved

    def _append_alias(self, entity_type: str, target: dict[str, Any], candidate: str) -> None:
        """Record candidate as an alias of target in the DB and in-memory roster."""
        existing_aliases = [str(a) for a in (target.get("aliases") or [])]
        existing_lower = {a.lower() for a in existing_aliases}
        if candidate.lower() in existing_lower or candidate.lower() == str(target.get("name", "")).lower():
            return
        new_aliases = [*existing_aliases, candidate]
        table = self._TABLE.get(entity_type, "entities")
        self.db.execute(
            f"UPDATE {table} SET aliases = %s WHERE id = %s AND novel_id = %s",
            (new_aliases, target["id"], self.novel_id),
        )
        target["aliases"] = new_aliases

    def _apply_resolutions(
        self,
        entity_type: str,
        resolutions: list[dict[str, Any]],
        roster: list[dict[str, Any]],
        chapter_text: str,
    ) -> dict[str, str]:
        roster_by_id: dict[str, dict[str, Any]] = {str(item["id"]): item for item in roster}
        merges: dict[str, str] = {}

        for resolution in resolutions:
            candidate = str(resolution.get("candidate", "")).strip()
            if not candidate:
                continue
            if str(resolution.get("verdict", "")).strip().lower() != "existing":
                continue
            target_id = resolution.get("id")
            if not target_id or str(target_id) not in roster_by_id:
                continue
            anchor = str(resolution.get("grammatical_anchor") or "").strip()
            reasoning = str(resolution.get("reasoning") or "").strip()

            # Characters always require a verbatim anchor in the chapter text.
            # Other types tier the evidence: an anchor or lexical closeness to
            # the target's name/aliases makes the merge permanent (alias
            # written); reasoning alone — a required schema field, so its mere
            # presence proves nothing — merges for this chapter only, because
            # a hallucinated alias would reroute every future mention.
            target = roster_by_id[str(target_id)]
            anchor_valid = bool(anchor) and anchor in chapter_text
            if entity_type == "character":
                if not anchor_valid:
                    logger.info(
                        "entity_canonicalizer: rejected character merge (anchor missing or not in text): %s -> %s",
                        candidate,
                        target_id,
                    )
                    continue
                persist_alias = True
            elif anchor_valid or _names_lexically_close(candidate, target):
                persist_alias = True
            elif reasoning:
                persist_alias = False
            else:
                logger.info(
                    "entity_canonicalizer: rejected %s merge (no anchor and no reasoning): %s -> %s",
                    entity_type,
                    candidate,
                    target_id,
                )
                continue
            if persist_alias:
                self._append_alias(entity_type, target, candidate)
                logger.info(
                    "entity_canonicalizer: merged %r -> %s (%s)", candidate, target_id, entity_type
                )
            else:
                logger.info(
                    "entity_canonicalizer: semantic-only merge %r -> %s (%s); alias not persisted (reasoning: %s)",
                    candidate,
                    target_id,
                    entity_type,
                    reasoning[:120],
                )
            merges[candidate] = str(target_id)

        return merges


# Sentinel for normalized names shared by multiple roster entries — never auto-merge those.
_AMBIGUOUS = object()


def _select_roster_subset(
    candidates: list[str],
    roster: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """Bound the roster sent to the LLM. When the roster exceeds ``cap``, keep
    the entries most string-similar to the candidates (the rest are near-certain
    non-matches and only inflate the prompt)."""
    if cap <= 0 or len(roster) <= cap:
        return roster

    normalized_candidates = [normalize_name(c) for c in candidates]
    candidate_tokens = [set(nc.split()) for nc in normalized_candidates]

    def best_score(entry: dict[str, Any]) -> float:
        best = 0.0
        for raw in [entry.get("name", ""), *(entry.get("aliases") or [])]:
            nn = normalize_name(str(raw))
            tokens = set(nn.split())
            for nc, ct in zip(normalized_candidates, candidate_tokens):
                ratio = difflib.SequenceMatcher(None, nc, nn).ratio()
                if ratio > best:
                    best = ratio
                if ct and tokens:
                    overlap = len(ct & tokens) / min(len(ct), len(tokens))
                    if overlap > best:
                        best = overlap
        return best

    ranked = sorted(range(len(roster)), key=lambda i: best_score(roster[i]), reverse=True)
    kept = sorted(ranked[:cap])  # keep original roster order for prompt stability
    logger.info("entity_canonicalizer: roster capped %d -> %d entries", len(roster), cap)
    return [roster[i] for i in kept]


# Backwards-compatibility alias — existing code that imports CharacterCanonicalizer still works
CharacterCanonicalizer = EntityCanonicalizer


def _collect_entities_by_type(extracted: dict[str, Any]) -> dict[str, list[dict]]:
    """Return full entity dicts from new_entities, keyed by entity type."""
    new_entities = extracted.get("new_entities", {}) or {}
    return {
        "character": [e for e in (new_entities.get("characters") or []) if isinstance(e, dict)],
        "location": [e for e in (new_entities.get("locations") or []) if isinstance(e, dict)],
        "faction": [e for e in (new_entities.get("factions") or []) if isinstance(e, dict)],
        "object": [e for e in (new_entities.get("objects") or []) if isinstance(e, dict)],
    }


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
        for n in event.get("involved_factions", []) or []:
            _add_name(str(n), "faction", result)

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

    for scene in extracted.get("scenes", []) or []:
        if not isinstance(scene, dict):
            continue
        _add_name(str(scene.get("pov_character_name") or ""), "character", result)
        _add_name(str(scene.get("location_name") or ""), "location", result)
        for n in scene.get("present_character_names", []) or []:
            _add_name(str(n), "character", result)

    for learning in extracted.get("learnings", []) or []:
        if not isinstance(learning, dict):
            continue
        _add_name(str(learning.get("character_name") or ""), "character", result)
        _add_name(str(learning.get("source_character_name") or ""), "character", result)
        for n in learning.get("shared_with_character_names", []) or []:
            _add_name(str(n), "character", result)

    for fact in extracted.get("canon_facts", []) or []:
        if not isinstance(fact, dict):
            continue
        subject_type = str(fact.get("subject_type", "")).strip().lower()
        if subject_type in result:
            _add_name(str(fact.get("subject_name", "")), subject_type, result)

    for item in extracted.get("custom_entities", []) or []:
        if not isinstance(item, dict):
            continue
        ce_type = str(item.get("type", "")).strip()
        name = str(item.get("name", "")).strip()
        if ce_type and name:
            result.setdefault(ce_type, set()).add(name)

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
    "BUILTIN_ENTITY_TYPES",
    "CharacterCanonicalizer",       # backwards-compat alias
    "EntityCanonicalizer",
    "IntraExtractionDeduplicator",
    "collect_character_names",
    "collect_names_by_type",
    "normalize_name",
]
