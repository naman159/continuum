from __future__ import annotations

import re
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.extraction.prompts import PASS_ORDER, build_system_prompt, build_user_prompt
from pipeline.llm import load_completion as _load_completion
from pipeline.llm import safe_json_loads as _safe_json_loads


def empty_extraction() -> dict[str, Any]:
    return {
        "summary": "",
        "new_entities": {
            "characters": [],
            "locations": [],
            "factions": [],
            "objects": [],
        },
        "state_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "relationship_updates": [],
        "dynamics_updates": [],
        "scenes": [],
        "summary_short": "",
        "summary_medium": "",
        "summary_long": "",
        "learnings": [],
        "foreshadows_introduced": [],
        "payoffs_delivered": [],
        "custom_entities": [],
        "canon_facts": [],
    }


def _dedupe_by_name(
    items: list[dict[str, Any]], *, include_owner: bool = False
) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        # Objects with different owners are distinct even when names collide.
        owner = str(item.get("owner_name") or "").strip().lower() if include_owner else ""
        key = (name.lower(), owner)
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


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (ValueError, TypeError):
        return 0.0


_VALID_DELTA_KINDS = {"possession", "location", "knowledge", "status"}
_VALID_STATUS_ATTRIBUTES = {
    "emotional_state", "goals", "physical_state", "appearance", "notes"
}


def _clean_state_delta(item: Any) -> dict[str, Any] | None:
    """Validate a single state_delta and fill in its kind-derived `change`.

    Returns the (mutated in place) dict, or None if the item is malformed.
    Shared by `_normalize_extraction` (LLM/mock output) and `merge_extractions`
    (so deltas that arrive pre-normalized, or hand-built in tests, still get
    the same kind-based `change` default).
    """
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind", "")).strip().lower()
    if kind not in _VALID_DELTA_KINDS:
        return None
    item["kind"] = kind
    if kind == "possession":
        change = str(item.get("change", "")).strip().lower()
        if change not in {"gain", "loss"}:
            return None
        item["change"] = change
    elif kind == "location":
        item["change"] = "move"
    elif kind == "knowledge":
        item["change"] = "learn"
    else:  # status
        item["change"] = "update"
        if str(item.get("attribute", "")).strip() not in _VALID_STATUS_ATTRIBUTES:
            return None
    return item


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

    state_deltas = raw.get("state_deltas", [])
    if isinstance(state_deltas, list):
        output["state_deltas"] = [
            cleaned for item in state_deltas
            if (cleaned := _clean_state_delta(item)) is not None
        ]

    events = raw.get("events", [])
    if isinstance(events, list):
        output["events"] = [item for item in events if isinstance(item, dict)]

    thread_updates = raw.get("thread_updates", [])
    if isinstance(thread_updates, list):
        output["thread_updates"] = [item for item in thread_updates if isinstance(item, dict)]

    continuity_flags = raw.get("continuity_flags", [])
    if isinstance(continuity_flags, list):
        output["continuity_flags"] = [item for item in continuity_flags if isinstance(item, dict)]

    relationship_updates = raw.get("relationship_updates", [])
    if isinstance(relationship_updates, list):
        output["relationship_updates"] = [item for item in relationship_updates if isinstance(item, dict)]

    dynamics_updates = raw.get("dynamics_updates", [])
    if isinstance(dynamics_updates, list):
        output["dynamics_updates"] = [item for item in dynamics_updates if isinstance(item, dict)]

    scenes = raw.get("scenes", [])
    if isinstance(scenes, list):
        output["scenes"] = [item for item in scenes if isinstance(item, dict)]

    for field in ("summary_short", "summary_medium", "summary_long"):
        value = raw.get(field)
        if isinstance(value, str):
            output[field] = value.strip()

    learnings = raw.get("learnings", [])
    if isinstance(learnings, list):
        output["learnings"] = [item for item in learnings if isinstance(item, dict)]

    foreshadows = raw.get("foreshadows_introduced", [])
    if isinstance(foreshadows, list):
        output["foreshadows_introduced"] = [item for item in foreshadows if isinstance(item, dict)]

    payoffs = raw.get("payoffs_delivered", [])
    if isinstance(payoffs, list):
        output["payoffs_delivered"] = [item for item in payoffs if isinstance(item, dict)]

    custom_entities = raw.get("custom_entities", [])
    if isinstance(custom_entities, list):
        output["custom_entities"] = [
            item for item in custom_entities
            if isinstance(item, dict) and item.get("name") and item.get("type")
        ]

    canon_facts = raw.get("canon_facts", [])
    if isinstance(canon_facts, list):
        output["canon_facts"] = [
            item for item in canon_facts
            if isinstance(item, dict)
            and str(item.get("subject_name", "")).strip()
            and str(item.get("predicate", "")).strip()
            and str(item.get("value", "")).strip()
        ]

    return output


def merge_extractions(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    merged = empty_extraction()

    summaries = [e.get("summary", "").strip() for e in extractions if e.get("summary")]
    merged["summary"] = "\n\n".join(_dedupe_strings(summaries))

    for entity_type in merged["new_entities"]:
        combined: list[dict[str, Any]] = []
        for extraction in extractions:
            combined.extend(extraction.get("new_entities", {}).get(entity_type, []))
        merged["new_entities"][entity_type] = _dedupe_by_name(
            combined, include_owner=(entity_type == "objects")
        )

    # State deltas: straight concatenation — chunk order across extractions IS
    # the narrative order the replay folds in, so no dedup/merge-by-key here.
    # Still route each item through _clean_state_delta so kind-derived
    # defaults (e.g. status -> change="update") apply even to deltas that
    # weren't already normalized.
    merged["state_deltas"] = [
        cleaned
        for extraction in extractions
        for delta in extraction.get("state_deltas", [])
        if (cleaned := _clean_state_delta(delta)) is not None
    ]

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

    seen_relationships: set[tuple[str, str, str, bool, str, str]] = set()
    for extraction in extractions:
        for rel in extraction.get("relationship_updates", []):
            a = str(rel.get("entity_a", "")).strip().lower()
            b = str(rel.get("entity_b", "")).strip().lower()
            rel_type = str(rel.get("rel_type", "")).strip().lower()
            if not a or not b:
                continue
            # Chunk merging must preserve the same evidence as persistence:
            # directed pairs are distinct, and an ending in a later chunk
            # must survive an earlier assertion that the relationship holds.
            mutual = rel.get("symmetric") is True
            pair = (min(a, b), max(a, b)) if mutual else (a, b)
            key = (*pair, rel_type, mutual,
                   str(rel.get("from_chapter")), str(rel.get("to_chapter")))
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            merged["relationship_updates"].append(rel)

    seen_dynamics: set[tuple[str, str]] = set()
    for extraction in extractions:
        for dyn in extraction.get("dynamics_updates", []):
            a = str(dyn.get("entity_a", "")).strip().lower()
            b = str(dyn.get("entity_b", "")).strip().lower()
            if not a or not b:
                continue
            key = (min(a, b), max(a, b))
            if key in seen_dynamics:
                continue
            seen_dynamics.add(key)
            merged["dynamics_updates"].append(dyn)

    # Scenes: dedupe across chunks by starts_at_excerpt (a scene re-reported
    # by an overlapping chunk shares its verbatim anchor; scene_index is
    # per-chunk so it cannot disambiguate) and renumber so scene_index is
    # unique and ordered across the merged result. An empty excerpt carries
    # no identity, so those scenes are always kept.
    seen_scenes: set[str] = set()
    collected_scenes: list[dict[str, Any]] = []
    for extraction in extractions:
        for scene in extraction.get("scenes", []):
            if not isinstance(scene, dict):
                continue
            excerpt = str(scene.get("starts_at_excerpt", "")).strip().lower()
            if excerpt:
                if excerpt in seen_scenes:
                    continue
                seen_scenes.add(excerpt)
            collected_scenes.append(scene)
    # Re-index sequentially based on appearance order.
    for new_idx, scene in enumerate(collected_scenes):
        scene["scene_index"] = new_idx
    merged["scenes"] = collected_scenes

    # Multi-granularity summaries: prefer the LAST chunk's non-empty value
    # for each field. This favors closing recaps which often cover the most
    # ground; if absent, fall back to earlier chunks in reverse order.
    for field in ("summary_short", "summary_medium", "summary_long"):
        for extraction in reversed(extractions):
            value = str(extraction.get(field, "") or "").strip()
            if value:
                merged[field] = value
                break

    # Learnings: dedupe by (character_name, fact_description).
    seen_learnings: set[tuple[str, str]] = set()
    for extraction in extractions:
        for learning in extraction.get("learnings", []):
            if not isinstance(learning, dict):
                continue
            character = str(learning.get("character_name", "")).strip().lower()
            fact = str(learning.get("fact_description", "")).strip().lower()
            if not character or not fact:
                continue
            key = (character, fact)
            if key in seen_learnings:
                continue
            seen_learnings.add(key)
            merged["learnings"].append(learning)

    # Foreshadows: dedupe by foreshadow_text.
    seen_foreshadows: set[str] = set()
    for extraction in extractions:
        for fs in extraction.get("foreshadows_introduced", []):
            if not isinstance(fs, dict):
                continue
            text = str(fs.get("foreshadow_text", "")).strip().lower()
            if not text or text in seen_foreshadows:
                continue
            seen_foreshadows.add(text)
            merged["foreshadows_introduced"].append(fs)

    # Payoffs: dedupe by payoff_text.
    seen_payoffs: set[str] = set()
    for extraction in extractions:
        for payoff in extraction.get("payoffs_delivered", []):
            if not isinstance(payoff, dict):
                continue
            text = str(payoff.get("payoff_text", "")).strip().lower()
            if not text or text in seen_payoffs:
                continue
            seen_payoffs.add(text)
            merged["payoffs_delivered"].append(payoff)

    # Custom entities: dedupe by (name.lower(), type).
    seen_custom: set[tuple[str, str]] = set()
    for extraction in extractions:
        for ce in extraction.get("custom_entities", []):
            if not isinstance(ce, dict):
                continue
            key = (str(ce.get("name", "")).strip().lower(), str(ce.get("type", "")).strip().lower())
            if not key[0] or not key[1] or key in seen_custom:
                continue
            seen_custom.add(key)
            merged["custom_entities"].append(ce)

    # Canon facts: dedupe by (subject, predicate); highest confidence wins.
    best_canon: dict[tuple[str, str], dict[str, Any]] = {}
    for extraction in extractions:
        for fact in extraction.get("canon_facts", []):
            if not isinstance(fact, dict):
                continue
            subj = str(fact.get("subject_name", "")).strip().lower()
            pred = str(fact.get("predicate", "")).strip().lower()
            if not subj or not pred:
                continue
            key = (subj, pred)
            current = best_canon.get(key)
            # Tie-break: strict > keeps the FIRST occurrence on equal confidence
            # (chunk order = narrative order, unlike the summaries' last-wins rule).
            if current is None or _safe_float(fact.get("confidence")) > _safe_float(current.get("confidence")):
                best_canon[key] = fact
    merged["canon_facts"] = list(best_canon.values())

    return merged


class ChapterExtractor:
    def __init__(self, *, use_mock: bool | None = None) -> None:
        completion = _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or completion is None
        else:
            self.use_mock = use_mock

    def extract_chapter(
        self,
        chunks: list[str],
        context: dict[str, Any],
        progress: Any | None = None,
        custom_entity_types: list[dict] | None = None,
    ) -> dict[str, Any]:
        if not chunks:
            return empty_extraction()
        results = [
            self.extract_chunk(chunk, context, progress=progress, custom_entity_types=custom_entity_types)
            for chunk in chunks
        ]
        return merge_extractions(results)

    def extract_chunk(
        self,
        chunk: str,
        context: dict[str, Any],
        progress: Any | None = None,
        custom_entity_types: list[dict] | None = None,
    ) -> dict[str, Any]:
        if self.use_mock:
            return self._mock_extract(chunk, context)

        pass_payload: dict[str, Any] = {}
        for pass_name in PASS_ORDER:
            if progress is not None:
                progress.on_pass_start(pass_name)
            payload = self._run_llm_pass(pass_name, chunk, context, custom_entity_types=custom_entity_types)
            if progress is not None:
                progress.on_pass_done(pass_name)
            pass_payload[pass_name] = payload

        normalized = self._compose_from_pass_payload(pass_payload)
        return _normalize_extraction(normalized)

    def _run_llm_pass(
        self,
        pass_name: str,
        chunk: str,
        context: dict[str, Any],
        custom_entity_types: list[dict] | None = None,
    ) -> dict[str, Any]:
        completion = _load_completion()
        if completion is None:
            raise RuntimeError(f"Extraction pass {pass_name!r} requires LiteLLM; it could not be loaded")

        system_prompt = build_system_prompt(pass_name, custom_entity_types=custom_entity_types)
        user_prompt = build_user_prompt(pass_name, chunk, context, custom_entity_types=custom_entity_types)

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
            if not payload:
                raise ValueError("model returned empty or invalid JSON")
            return payload
        except Exception as exc:
            # Continuing would persist an incomplete chapter, or obscure the
            # provider failure behind an empty-summary embedding error.
            raise RuntimeError(f"Extraction pass {pass_name!r} failed: {exc}") from exc

    def _compose_from_pass_payload(self, pass_payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
        chapter_summary = pass_payload.get("chapter_summary", {})
        new_entities = pass_payload.get("new_entities", {})
        events = pass_payload.get("events", {})
        thread_updates = pass_payload.get("thread_updates", {})
        continuity_flags = pass_payload.get("continuity_flags", {})
        relationship_updates = pass_payload.get("relationship_updates", {})
        dynamics_updates = pass_payload.get("dynamics_updates", {})
        scene_segmentation = pass_payload.get("scene_segmentation", {})
        multi_summaries = pass_payload.get("multi_granularity_summaries", {})
        knowledge_state = pass_payload.get("knowledge_state_deltas", {})
        commitments = pass_payload.get("commitments", {})
        canon = pass_payload.get("canon_facts", {})

        return {
            "summary": chapter_summary.get("summary", ""),
            "new_entities": new_entities,
            "state_deltas": pass_payload.get("state_deltas", {}).get("state_deltas", []),
            "events": events.get("events", []),
            "thread_updates": thread_updates.get("thread_updates", []),
            "continuity_flags": continuity_flags.get("continuity_flags", []),
            "relationship_updates": relationship_updates.get("relationship_updates", []),
            "dynamics_updates": dynamics_updates.get("dynamics_updates", []),
            "scenes": scene_segmentation.get("scenes", []),
            "summary_short": multi_summaries.get("summary_short", ""),
            "summary_medium": multi_summaries.get("summary_medium", ""),
            "summary_long": multi_summaries.get("summary_long", ""),
            "learnings": knowledge_state.get("learnings", []),
            "foreshadows_introduced": commitments.get("foreshadows_introduced", []),
            "payoffs_delivered": commitments.get("payoffs_delivered", []),
            "custom_entities": new_entities.get("custom_entities", []),
            "canon_facts": canon.get("canon_facts", []),
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
            extraction["state_deltas"].append(
                {
                    "kind": "status",
                    "character_name": name,
                    "attribute": "notes",
                    "value": f"mock delta for {name}",
                    "change": "update",
                    "quote": chunk[:40],
                }
            )
            if "took" in chunk.lower() or "picked up" in chunk.lower():
                extraction["state_deltas"].append(
                    {
                        "kind": "possession",
                        "character_name": name,
                        "object_name": "silver dagger",
                        "change": "gain",
                        "quote": chunk[:40],
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

        # Deterministic mock scene segmentation: one scene covering the chunk.
        if non_empty:
            extraction["scenes"].append(
                {
                    "scene_index": 0,
                    "pov_character_name": candidate_names[0] if candidate_names else None,
                    "location_name": None,
                    "time_anchor": None,
                    "present_character_names": candidate_names[:6],
                    "summary": " ".join(non_empty[:3])[:400],
                    "starts_at_excerpt": non_empty[0][:30],
                }
            )

        # Deterministic multi-granularity summaries derived from the existing
        # naive summary slice.
        if non_empty:
            extraction["summary_short"] = non_empty[0][:80]
            extraction["summary_medium"] = " ".join(non_empty[:3])[:1200]
            extraction["summary_long"] = " ".join(non_empty[:10])[:4000]

        # Mock knowledge-state deltas: emit nothing — knowledge extraction is
        # too speculative to fake. Tests should not assume any rows.

        # Mock foreshadow/payoff: reuse the continuity_flag heuristic so that
        # tests can assert plumbing without inventing semantics.
        for flag in extraction.get("continuity_flags", []):
            extraction["foreshadows_introduced"].append(
                {
                    "foreshadow_text": flag.get("description", ""),
                    "trigger_predicate": "unknown",
                    "weight": "medium",
                    "related_entity_names": [],
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
