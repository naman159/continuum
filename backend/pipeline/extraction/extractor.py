from __future__ import annotations

import json
import logging
import re
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.extraction.prompts import PASS_ORDER, build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)


def _load_completion():
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return completion


def empty_extraction() -> dict[str, Any]:
    return {
        "summary": "",
        "new_entities": {
            "characters": [],
            "locations": [],
            "factions": [],
            "objects": [],
        },
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
    }


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


def _dedupe_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        key = name.lower()
        if key not in deduped:
            deduped[key] = item
            continue

        existing = deduped[key]
        for field, value in item.items():
            if field not in existing or not existing[field]:
                existing[field] = value
            elif isinstance(existing[field], list) and isinstance(value, list):
                merged = list(dict.fromkeys([*existing[field], *value]))
                existing[field] = merged
    return list(deduped.values())


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _normalize_extraction(raw: dict[str, Any]) -> dict[str, Any]:
    output = empty_extraction()

    output["summary"] = str(raw.get("summary", "")).strip()

    new_entities = raw.get("new_entities", raw)
    if isinstance(new_entities, dict):
        for entity_type in ("characters", "locations", "factions", "objects"):
            entity_items = new_entities.get(entity_type, [])
            if isinstance(entity_items, list):
                output["new_entities"][entity_type] = [
                    item for item in entity_items if isinstance(item, dict)
                ]

    entity_deltas = raw.get("entity_deltas", raw.get("character_deltas", []))
    if isinstance(entity_deltas, list):
        output["entity_deltas"] = [item for item in entity_deltas if isinstance(item, dict)]

    events = raw.get("events", [])
    if isinstance(events, list):
        output["events"] = [item for item in events if isinstance(item, dict)]

    thread_updates = raw.get("thread_updates", [])
    if isinstance(thread_updates, list):
        output["thread_updates"] = [item for item in thread_updates if isinstance(item, dict)]

    continuity_flags = raw.get("continuity_flags", [])
    if isinstance(continuity_flags, list):
        output["continuity_flags"] = [item for item in continuity_flags if isinstance(item, dict)]

    return output


def merge_extractions(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    merged = empty_extraction()

    summaries = [e.get("summary", "").strip() for e in extractions if e.get("summary")]
    merged["summary"] = "\n\n".join(_dedupe_strings(summaries))

    for entity_type in merged["new_entities"]:
        combined: list[dict[str, Any]] = []
        for extraction in extractions:
            combined.extend(extraction.get("new_entities", {}).get(entity_type, []))
        merged["new_entities"][entity_type] = _dedupe_by_name(combined)

    # Merge deltas by character name, keeping latest non-empty values.
    delta_index: dict[str, dict[str, Any]] = {}
    for extraction in extractions:
        for delta in extraction.get("entity_deltas", []):
            character_name = str(delta.get("character_name", "")).strip()
            if not character_name:
                continue
            key = character_name.lower()
            if key not in delta_index:
                delta_index[key] = {
                    "character_name": character_name,
                    "location": None,
                    "emotional_state": None,
                    "goals": None,
                    "knowledge": [],
                    "relationships": {},
                    "physical_state": None,
                    "notes": None,
                }
            existing = delta_index[key]
            for field in ("location", "emotional_state", "goals", "physical_state", "notes"):
                value = delta.get(field)
                if value:
                    existing[field] = value

            knowledge = delta.get("knowledge")
            if isinstance(knowledge, list):
                existing["knowledge"] = _dedupe_strings([
                    *existing.get("knowledge", []),
                    *(str(item) for item in knowledge),
                ])

            relationships = delta.get("relationships")
            if isinstance(relationships, dict):
                existing_rels = existing.get("relationships", {})
                existing_rels.update(relationships)
                existing["relationships"] = existing_rels

    merged["entity_deltas"] = list(delta_index.values())

    seen_events: set[str] = set()
    for extraction in extractions:
        for event in extraction.get("events", []):
            description = str(event.get("description", "")).strip()
            if not description:
                continue
            key = description.lower()
            if key in seen_events:
                continue
            seen_events.add(key)
            merged["events"].append(event)

    seen_threads: set[tuple[str, str]] = set()
    for extraction in extractions:
        for update in extraction.get("thread_updates", []):
            title = str(update.get("title", "")).strip()
            impact = str(update.get("impact", "")).strip()
            if not title:
                continue
            key = (title.lower(), impact.lower())
            if key in seen_threads:
                continue
            seen_threads.add(key)
            merged["thread_updates"].append(update)

    seen_flags: set[str] = set()
    for extraction in extractions:
        for flag in extraction.get("continuity_flags", []):
            description = str(flag.get("description", "")).strip()
            if not description:
                continue
            key = description.lower()
            if key in seen_flags:
                continue
            seen_flags.add(key)
            merged["continuity_flags"].append(flag)

    return merged


class ChapterExtractor:
    def __init__(self, *, use_mock: bool | None = None) -> None:
        completion = _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or completion is None
        else:
            self.use_mock = use_mock

    def extract_chapter(
        self, chunks: list[str], context: dict[str, Any], progress: Any | None = None
    ) -> dict[str, Any]:
        if not chunks:
            return empty_extraction()

        results = [self.extract_chunk(chunk, context, progress=progress) for chunk in chunks]
        return merge_extractions(results)

    def extract_chunk(
        self, chunk: str, context: dict[str, Any], progress: Any | None = None
    ) -> dict[str, Any]:
        if self.use_mock:
            return self._mock_extract(chunk, context)

        pass_payload: dict[str, Any] = {}
        for pass_name in PASS_ORDER:
            if progress is not None:
                progress.on_pass_start(pass_name)
            payload = self._run_llm_pass(pass_name, chunk, context)
            if progress is not None:
                progress.on_pass_done(pass_name)
            pass_payload[pass_name] = payload

        normalized = self._compose_from_pass_payload(pass_payload)
        return _normalize_extraction(normalized)

    def _run_llm_pass(self, pass_name: str, chunk: str, context: dict[str, Any]) -> dict[str, Any]:
        completion = _load_completion()
        if completion is None:
            return {}

        system_prompt = build_system_prompt(pass_name)
        user_prompt = build_user_prompt(pass_name, chunk, context)

        try:
            response = completion(
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
            return payload
        except Exception as exc:  # pragma: no cover
            logger.warning("LLM pass failed (%s): %s", pass_name, exc)
            return {}

    def _compose_from_pass_payload(self, pass_payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
        chapter_summary = pass_payload.get("chapter_summary", {})
        new_entities = pass_payload.get("new_entities", {})
        entity_deltas = pass_payload.get("entity_deltas", {})
        events = pass_payload.get("events", {})
        thread_updates = pass_payload.get("thread_updates", {})
        continuity_flags = pass_payload.get("continuity_flags", {})

        return {
            "summary": chapter_summary.get("summary", ""),
            "new_entities": new_entities,
            "entity_deltas": entity_deltas.get("character_deltas", entity_deltas.get("entity_deltas", [])),
            "events": events.get("events", []),
            "thread_updates": thread_updates.get("thread_updates", []),
            "continuity_flags": continuity_flags.get("continuity_flags", []),
        }

    def _mock_extract(self, chunk: str, context: dict[str, Any]) -> dict[str, Any]:
        extraction = empty_extraction()
        sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
        non_empty = [s.strip() for s in sentences if s.strip()]

        extraction["summary"] = " ".join(non_empty[:3])[:1200]

        known_characters = {
            str(item.get("name", "")).lower()
            for item in context.get("characters", [])
            if isinstance(item, dict)
        }

        candidate_names = self._extract_candidate_names(chunk)
        for name in candidate_names:
            if name.lower() in known_characters:
                continue
            extraction["new_entities"]["characters"].append(
                {
                    "name": name,
                    "aliases": [],
                    "description": "Auto-detected from chapter text (mock extractor).",
                }
            )
            if len(extraction["new_entities"]["characters"]) >= 10:
                break

        for sentence in non_empty[:20]:
            event = {
                "description": sentence,
                "event_type": "action",
                "impact_level": "medium",
                "involved_characters": [
                    name for name in candidate_names if name in sentence
                ][:4],
                "involved_locations": [],
                "involved_objects": [],
            }
            extraction["events"].append(event)

        for name in candidate_names[:10]:
            extraction["entity_deltas"].append(
                {
                    "character_name": name,
                    "location": None,
                    "emotional_state": None,
                    "goals": None,
                    "knowledge": [],
                    "relationships": {},
                    "physical_state": None,
                    "notes": "Generated by mock extraction.",
                }
            )

        if extraction["events"]:
            extraction["thread_updates"].append(
                {
                    "title": "Chapter progression",
                    "description": "Auto-generated progression thread.",
                    "status": "progressing",
                    "impact": "advances",
                    "thread_type": "other",
                    "event_description": extraction["events"][0]["description"],
                }
            )

        for sentence in non_empty:
            if any(token in sentence.lower() for token in ("promise", "prophecy", "someday", "if only")):
                extraction["continuity_flags"].append(
                    {
                        "description": sentence,
                        "flag_type": "foreshadowing",
                    }
                )

        return _normalize_extraction(extraction)

    @staticmethod
    def _extract_candidate_names(chunk: str) -> list[str]:
        blocked = {
            "The",
            "A",
            "An",
            "Chapter",
            "He",
            "She",
            "They",
            "It",
            "His",
            "Her",
            "Their",
            "When",
            "After",
            "Before",
            "Meanwhile",
            "But",
            "And",
            "Then",
        }
        candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", chunk)

        names: list[str] = []
        for candidate in candidates:
            first = candidate.split()[0]
            if first in blocked:
                continue
            if candidate not in names:
                names.append(candidate)
        return names


__all__ = ["ChapterExtractor", "merge_extractions", "empty_extraction"]
