from __future__ import annotations

"""Extract the critic's claim shapes from draft prose, then resolve names to
ids READ-ONLY (unknown names drop the claim; critiquing a draft must never
create entities)."""

import json
from textwrap import dedent
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.critic.types import DraftChapter
from pipeline.entity_tables import TYPED_TABLES
from pipeline.extraction.resolver import lookup_typed
from pipeline.llm import load_completion as _load_completion
from pipeline.llm import safe_json_loads


_EMPTY: dict[str, list] = {
    "mentions": [], "knowledge_claims": [], "location_claims": [],
    "possession_claims": [], "events": [],
}

_CLAIMS_SCHEMA = {
    "mentions": [{"entity_name": "string", "entity_type": "character|location|object|faction",
                  "predicate": "string snake_case", "claimed_value": "string", "quote": "string"}],
    "knowledge_claims": [{"character_name": "string", "fact_description": "string",
                          "source_type": "dialogue|observation|inference|witnessed|told|assumed",
                          "learned_this_chapter": "boolean — true only if the character acquires this fact within this draft; false if they act on knowledge from before",
                          "quote": "string"}],
    "location_claims": [{"character_name": "string", "location_name": "string", "quote": "string"}],
    "possession_claims": [{"character_name": "string", "object_name": "string", "quote": "string"}],
    "events": [{"description": "string", "event_type": "action|revelation|death|arrival|conflict|other"}],
}

_SYSTEM = dedent(
    f"""
    You audit a draft chapter for a continuity system. Extract every checkable
    claim the draft makes. Quotes must be verbatim substrings of the draft.
    Return ONLY strict JSON matching:
    {json.dumps(_CLAIMS_SCHEMA, ensure_ascii=True, indent=2)}
    """
).strip()


def extract_draft_claims(
    text: str, *, use_mock: bool | None = None, completion_fn=None
) -> dict[str, list]:
    completion = completion_fn if completion_fn is not None else _load_completion()
    mock = (settings.use_mock_llm or completion is None) if use_mock is None else use_mock
    if mock:
        return {k: [] for k in _EMPTY}
    try:
        response = completion(
            model=LLM_CONFIG["model"],
            temperature=LLM_CONFIG["temperature"],
            response_format=LLM_CONFIG["response_format"],
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"DRAFT CHAPTER\n{text}\n\nReturn JSON only."},
            ],
        )
        content = response.choices[0].message.content
        if isinstance(content, list):
            content = "".join(str(p) for p in content)
        data = safe_json_loads(str(content))
    except Exception as exc:
        # Empty claims would make the critic pass vacuously and let an
        # unchecked draft through the ingest gate — fail loudly instead.
        raise RuntimeError(f"draft_claims: extraction failed: {exc}") from exc
    if not data:
        raise RuntimeError("draft_claims: extraction returned unparseable JSON")
    return {k: data.get(k) if isinstance(data.get(k), list) else [] for k in _EMPTY}


def build_draft_chapter(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    text: str,
    raw_claims: dict[str, list],
    planned_thread_ids: list[str],
    planned_commitment_ids: list[str],
) -> DraftChapter:
    # The same names repeat across a draft's claims; memoize the read-only
    # lookups so one draft costs one query per distinct (type, name).
    cache: dict[tuple[str, str], tuple[str, str] | None] = {}

    def _find(entity_type: str, name: str) -> tuple[str, str] | None:
        name = (name or "").strip()
        if not name:
            return None
        key = (entity_type, name.lower())
        if key not in cache:
            cache[key] = lookup_typed(db, novel_id, entity_type, name)
        return cache[key]

    mentions: list[dict] = []
    for m in raw_claims.get("mentions", []):
        if not isinstance(m, dict):
            continue
        entity_type = str(m.get("entity_type", "")).strip().lower()
        if entity_type not in TYPED_TABLES:
            continue
        found = _find(entity_type, str(m.get("entity_name", "")))
        if found is None:
            continue
        mentions.append({
            "entity_id": found[1],
            "predicate": str(m.get("predicate", "")).strip().lower(),
            "claimed_value": str(m.get("claimed_value", "")).strip(),
            "quote": m.get("quote"),
        })

    def _char(name: str) -> str | None:
        found = _find("character", name)
        return found[0] if found else None

    knowledge_claims: list[dict] = []
    for k in raw_claims.get("knowledge_claims", []):
        if not isinstance(k, dict):
            continue
        cid = _char(str(k.get("character_name", "")))
        if cid is None:
            continue
        learned = k.get("learned_this_chapter")
        knowledge_claims.append({
            "character_id": cid,
            "fact_description": str(k.get("fact_description", "")).strip(),
            "source_type": k.get("source_type"),
            # Lenient when the model omitted the flag; a non-bool must not
            # silently disable the knowledge check via truthiness.
            "learned_this_chapter": learned if isinstance(learned, bool) else True,
            "quote": k.get("quote"),
        })

    location_claims: list[dict] = []
    for c in raw_claims.get("location_claims", []):
        if not isinstance(c, dict):
            continue
        cid = _char(str(c.get("character_name", "")))
        loc = _find("location", str(c.get("location_name", "")))
        if cid is None or loc is None:
            continue
        location_claims.append({"character_id": cid, "location_id": loc[0], "quote": c.get("quote")})

    possession_claims: list[dict] = []
    for c in raw_claims.get("possession_claims", []):
        if not isinstance(c, dict):
            continue
        cid = _char(str(c.get("character_name", "")))
        obj = _find("object", str(c.get("object_name", "")))
        if cid is None or obj is None:
            continue
        possession_claims.append({"character_id": cid, "object_id": obj[0], "quote": c.get("quote")})

    events = [e for e in raw_claims.get("events", []) if isinstance(e, dict) and e.get("description")]

    return DraftChapter(
        novel_id=novel_id,
        chapter_number=chapter_number,
        text=text,
        mentions=mentions,
        knowledge_claims=knowledge_claims,
        location_claims=location_claims,
        possession_claims=possession_claims,
        events=events,
        planned_thread_ids=planned_thread_ids,
        planned_commitment_ids=planned_commitment_ids,
    )


def build_draft_from_extraction(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    text: str,
    extracted: dict[str, Any],
) -> DraftChapter:
    """Adapter for the spine's critique phase: reuse the chapter's already-
    extracted claims instead of paying a second claims-extraction LLM call.
    Read-only name resolution; unknown names drop the claim."""
    raw_claims = {
        "mentions": [
            {
                "entity_name": f.get("subject_name"),
                "entity_type": f.get("subject_type"),
                "predicate": f.get("predicate"),
                "claimed_value": f.get("value"),
                "quote": f.get("quote"),
            }
            for f in extracted.get("canon_facts", [])
            if isinstance(f, dict)
        ],
        "knowledge_claims": [
            {
                "character_name": l.get("character_name"),
                "fact_description": l.get("fact_description"),
                "source_type": l.get("source_type"),
                "learned_this_chapter": True,
                "quote": None,
            }
            for l in extracted.get("learnings", [])
            if isinstance(l, dict)
        ],
        "location_claims": [
            {
                "character_name": d.get("character_name"),
                "location_name": d.get("location_name"),
                "quote": d.get("quote"),
            }
            for d in extracted.get("state_deltas", [])
            if isinstance(d, dict) and d.get("kind") == "location"
        ],
        "possession_claims": [
            {
                "character_name": d.get("character_name"),
                "object_name": d.get("object_name"),
                "quote": d.get("quote"),
            }
            for d in extracted.get("state_deltas", [])
            if isinstance(d, dict)
            and d.get("kind") == "possession"
            and d.get("change") == "gain"
        ],
        "events": [
            {"description": e.get("description"), "event_type": e.get("event_type")}
            for e in extracted.get("events", [])
            if isinstance(e, dict)
        ],
    }
    return build_draft_chapter(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        text=text,
        raw_claims=raw_claims,
        planned_thread_ids=[],
        planned_commitment_ids=[],
    )


__all__ = ["extract_draft_claims", "build_draft_chapter", "build_draft_from_extraction"]
