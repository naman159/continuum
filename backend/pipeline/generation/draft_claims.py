from __future__ import annotations

"""Extract the critic's claim shapes from draft prose, then resolve names to
ids READ-ONLY (unknown names drop the claim; critiquing a draft must never
create entities)."""

import json
from textwrap import dedent
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.critic.types import DraftChapter

def _load_completion():
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return completion


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
        data = json.loads(str(content))
    except Exception as exc:
        # Empty claims would make the critic pass vacuously and let an
        # unchecked draft through the ingest gate — fail loudly instead.
        raise RuntimeError(f"draft_claims: extraction failed: {exc}") from exc
    return {k: data.get(k) if isinstance(data.get(k), list) else [] for k in _EMPTY}


_TYPED_TABLE = {"character": "characters", "location": "locations",
                "object": "objects", "faction": "factions"}


def _lookup(db: Any, novel_id: str, table: str, name: str) -> tuple[str, str] | None:
    """(typed_id, entity_id) by exact name or alias. Read-only."""
    name = (name or "").strip()
    if not name:
        return None
    row = db.fetchone(
        f"""
        SELECT id, entity_id FROM {table}
         WHERE novel_id = %s
           AND (lower(name) = lower(%s)
                OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(a) = lower(%s)))
         LIMIT 1
        """,
        (novel_id, name, name),
    )
    if row is None:
        return None
    return str(row[0]), str(row[1]) if row[1] else str(row[0])


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
    mentions: list[dict] = []
    for m in raw_claims.get("mentions", []):
        if not isinstance(m, dict):
            continue
        table = _TYPED_TABLE.get(str(m.get("entity_type", "")).strip().lower())
        if table is None:
            continue
        found = _lookup(db, novel_id, table, str(m.get("entity_name", "")))
        if found is None:
            continue
        mentions.append({
            "entity_id": found[1],
            "predicate": str(m.get("predicate", "")).strip().lower(),
            "claimed_value": str(m.get("claimed_value", "")).strip(),
            "quote": m.get("quote"),
        })

    def _char(name: str) -> str | None:
        found = _lookup(db, novel_id, "characters", name)
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
        loc = _lookup(db, novel_id, "locations", str(c.get("location_name", "")))
        if cid is None or loc is None:
            continue
        location_claims.append({"character_id": cid, "location_id": loc[0], "quote": c.get("quote")})

    possession_claims: list[dict] = []
    for c in raw_claims.get("possession_claims", []):
        if not isinstance(c, dict):
            continue
        cid = _char(str(c.get("character_name", "")))
        obj = _lookup(db, novel_id, "objects", str(c.get("object_name", "")))
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


__all__ = ["extract_draft_claims", "build_draft_chapter"]
