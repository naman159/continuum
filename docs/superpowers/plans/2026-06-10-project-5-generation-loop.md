# Project 5: The Generation Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `generate_chapter(novel_id, n)` plans, drafts scene-by-scene with retrieval context, extracts claims from the draft, critiques, revises up to N times, and (optionally) ingests the passing chapter back with `source='generated'`.

**Architecture:** Four new modules under `pipeline/generation/`: `style.py` (deterministic per-chapter style fingerprint, persisted at ingest and averaged for drafting), `drafter.py` (`SceneDrafter` — prose, not JSON; mock mode is deterministic), `draft_claims.py` (one focused LLM pass extracting the critic's claim shapes from draft prose, then read-only name→id resolution into a `DraftChapter`), and `loop.py` (the orchestrator wiring planner → `HybridRetriever` → drafter → claims → `ContinuityCritic` → revision → `process_chapter(source="generated")`). CLI subcommand, API job, and a minimal Generate page complete the loop.

**Prerequisites:** Project 1 (atomic ingestion: `process_chapter(db=, source=, generation_meta=)`) is **required**. Project 2 (canon facts) strongly recommended — without it the entity-mention check is vacuous.

**Tech Stack:** Python 3.11, LiteLLM (mocked in tests), FastAPI, React.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/pipeline/generation/__init__.py` | package |
| Create | `backend/pipeline/generation/style.py` | style fingerprint |
| Create | `backend/pipeline/generation/drafter.py` | SceneDrafter |
| Create | `backend/pipeline/generation/draft_claims.py` | draft → DraftChapter |
| Create | `backend/pipeline/generation/loop.py` | orchestrator |
| Create | `backend/pipeline/generation/tests/__init__.py` | tests package |
| Create | `backend/pipeline/generation/tests/test_style.py` | tests |
| Create | `backend/pipeline/generation/tests/test_drafter.py` | tests |
| Create | `backend/pipeline/generation/tests/test_draft_claims.py` | tests |
| Create | `backend/pipeline/generation/tests/test_loop.py` | tests |
| Modify | `backend/pipeline/pipeline.py` | persist style fingerprint; `generate-chapter` CLI |
| Modify | `backend/pipeline/config.py` | `draft_model`, `draft_temperature`, `generation_max_revisions` |
| Modify | `backend/api/jobs.py` | `submit_generation_job` |
| Modify | `backend/api/routes/process.py` | generate endpoint |
| Modify | `backend/api/schemas.py` | `GenerateRequest` |
| Create | `backend/api/tests/test_generate_api.py` | API tests |
| Modify | `frontend/src/api.ts`, `frontend/src/routes/Process.tsx` | Generate UI |
| Modify | `docs/architecture.html`, `docs/reference.html`, `docs/state-of-the-system.html` | docs |

---

## Task 1: Config knobs

**Files:**
- Modify: `backend/pipeline/config.py`

- [ ] **Step 1: Add settings**

In `Settings`, after the context caps (or after `canonicalizer_max_roster` if
Project 4 hasn't landed):

```python
    # Generation loop
    draft_model: str = field(default_factory=lambda: os.getenv("DRAFT_MODEL", os.getenv("DEFAULT_MODEL", "gpt-4o-mini")))
    draft_temperature: float = field(default_factory=lambda: float(os.getenv("DRAFT_TEMPERATURE", "0.8")))
    generation_max_revisions: int = field(
        default_factory=lambda: int(os.getenv("GENERATION_MAX_REVISIONS", "2"))
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/config.py
git commit -m "feat(config): generation loop settings"
```

---

## Task 2: Style fingerprint

**Files:**
- Create: `backend/pipeline/generation/__init__.py` (empty), `backend/pipeline/generation/tests/__init__.py` (empty)
- Create: `backend/pipeline/generation/style.py`
- Modify: `backend/pipeline/pipeline.py` (persist at ingest)
- Test: `backend/pipeline/generation/tests/test_style.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/generation/tests/test_style.py`:

```python
from __future__ import annotations

from pipeline.generation.style import compute_style_fingerprint

FIRST_PERSON = 'I walked to the door. "Hello?" I said. Nobody answered me.'
THIRD_PERSON = (
    "Jake walked to the door and knocked twice. The corridor stretched on, "
    "silent and cold. He waited for a long moment before turning away."
)


def test_fingerprint_fields_present():
    fp = compute_style_fingerprint(THIRD_PERSON)
    for key in ("avg_sentence_words", "dialogue_ratio", "pov_person", "exclamation_rate"):
        assert key in fp


def test_pov_detection():
    assert compute_style_fingerprint(FIRST_PERSON)["pov_person"] == "first"
    assert compute_style_fingerprint(THIRD_PERSON)["pov_person"] == "third"


def test_dialogue_ratio_bounds():
    fp = compute_style_fingerprint(FIRST_PERSON)
    assert 0.0 < fp["dialogue_ratio"] <= 1.0
    assert compute_style_fingerprint(THIRD_PERSON)["dialogue_ratio"] == 0.0


def test_empty_text_safe():
    fp = compute_style_fingerprint("")
    assert fp["avg_sentence_words"] == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_style.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement**

Create `backend/pipeline/generation/style.py`:

```python
from __future__ import annotations

"""Deterministic, LLM-free style fingerprint of a chapter's prose.

Stored in chapters.style_fingerprint at ingest; the drafter averages recent
chapters' fingerprints to match the novel's voice.
"""

import re
from typing import Any

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DIALOGUE = re.compile(r"[\"“”][^\"“”]+[\"“”]")
_FIRST_PERSON = re.compile(r"\b(I|me|my|mine|we|our)\b")
_THIRD_PERSON = re.compile(r"\b(he|she|they|his|her|their|him|them)\b", re.IGNORECASE)


def compute_style_fingerprint(text: str) -> dict[str, Any]:
    text = text or ""
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    word_counts = [len(s.split()) for s in sentences]
    avg_sentence_words = round(sum(word_counts) / len(word_counts), 2) if word_counts else 0.0

    dialogue_chars = sum(len(m.group(0)) for m in _DIALOGUE.finditer(text))
    dialogue_ratio = round(dialogue_chars / len(text), 3) if text else 0.0

    first = len(_FIRST_PERSON.findall(text))
    third = len(_THIRD_PERSON.findall(text))
    pov_person = "first" if first > third else "third"

    exclamations = text.count("!")
    exclamation_rate = round(exclamations / max(1, len(sentences)), 3)

    return {
        "avg_sentence_words": avg_sentence_words,
        "dialogue_ratio": dialogue_ratio,
        "pov_person": pov_person,
        "exclamation_rate": exclamation_rate,
        "sentence_count": len(sentences),
    }


def average_fingerprints(fingerprints: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [f for f in fingerprints if isinstance(f, dict) and f.get("sentence_count")]
    if not usable:
        return None
    numeric = ("avg_sentence_words", "dialogue_ratio", "exclamation_rate")
    out: dict[str, Any] = {
        k: round(sum(float(f.get(k) or 0.0) for f in usable) / len(usable), 3) for k in numeric
    }
    povs = [f.get("pov_person") for f in usable]
    out["pov_person"] = max(set(povs), key=povs.count)
    return out


__all__ = ["compute_style_fingerprint", "average_fingerprints"]
```

- [ ] **Step 4: Persist at ingest**

In `backend/pipeline/pipeline.py`, inside the persistence block of
`process_chapter` (right after the `UPDATE chapters SET summary ...` statement),
add:

```python
            s.execute(
                "UPDATE chapters SET style_fingerprint = %s::jsonb WHERE id = %s",
                (json.dumps(compute_style_fingerprint(raw_text)), chapter_id),
            )
```

with the import `from pipeline.generation.style import compute_style_fingerprint`.
(`json` is already imported in `pipeline.py`. If Project 1 hasn't landed, use
`db.execute` instead of `s.execute`.)

- [ ] **Step 5: Run tests + suite, commit**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_style.py -v && .venv/bin/pytest -q`
Expected: all pass.

```bash
git add backend/pipeline/generation/ backend/pipeline/pipeline.py
git commit -m "feat(generation): deterministic style fingerprint, persisted at ingest"
```

---

## Task 3: SceneDrafter

**Files:**
- Create: `backend/pipeline/generation/drafter.py`
- Test: `backend/pipeline/generation/tests/test_drafter.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/generation/tests/test_drafter.py`:

```python
from __future__ import annotations

import json
from types import SimpleNamespace

from pipeline.generation.drafter import SceneDrafter
from pipeline.planner.types import ChapterPlan, ScenePlan


def _scene(**over):
    base = dict(
        scene_index=1, pov_character="Jake", location="Harbor", time_anchor=None,
        present_characters=["Jake", "Sara"], scene_goal="Jake confronts Sara about the letter.",
        target_word_count=300,
    )
    base.update(over)
    return ScenePlan(**base)


def _plan():
    return ChapterPlan(
        novel_id="n1", chapter_number=5, title=None, arc_position="rising",
        chapter_goal="Confront the letter mystery.", scenes=[_scene()],
    )


def test_mock_draft_is_deterministic_and_mentions_characters():
    drafter = SceneDrafter(use_mock=True)
    a = drafter.draft_scene(scene=_scene(), plan=_plan(), context_block="", prior_text_tail="", style=None)
    b = drafter.draft_scene(scene=_scene(), plan=_plan(), context_block="", prior_text_tail="", style=None)
    assert a == b
    assert "Jake" in a and "Sara" in a


def test_real_draft_returns_completion_text_and_prompt_carries_context():
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        msg = SimpleNamespace(content="The harbor wind cut sideways.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    drafter = SceneDrafter(use_mock=False, completion_fn=fake_completion)
    text = drafter.draft_scene(
        scene=_scene(), plan=_plan(),
        context_block="FACT: Sara fears water.",
        prior_text_tail="…the door slammed.",
        style={"pov_person": "third", "avg_sentence_words": 14.0},
        revision_notes=["Sara cannot know about the ledger."],
    )
    assert text == "The harbor wind cut sideways."
    user_prompt = captured["messages"][1]["content"]
    assert "Sara fears water" in user_prompt
    assert "door slammed" in user_prompt
    assert "cannot know about the ledger" in user_prompt
    assert "response_format" not in captured  # prose, not JSON
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_drafter.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement**

Create `backend/pipeline/generation/drafter.py`:

```python
from __future__ import annotations

"""SceneDrafter: turns one ScenePlan into prose.

Unlike the extraction passes this produces plain text (no response_format).
Mock mode is deterministic so the loop is testable offline.
"""

import json
import logging
from typing import Any

from pipeline.config import settings
from pipeline.planner.types import ChapterPlan, ScenePlan

logger = logging.getLogger(__name__)


def _load_completion():
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return completion


_SYSTEM_PROMPT = """You are a novelist continuing an existing work. Write ONE scene.
Hard rules:
- Respect every fact in KEY FACTS and CONTEXT exactly; never contradict them.
- Only the characters listed may appear; introduce nobody new.
- Continue smoothly from PREVIOUS TEXT (do not repeat it).
- Match the VOICE profile (POV person, sentence rhythm, dialogue density).
- Aim for the target word count (+/-20%). Output prose only — no headings,
  no notes, no JSON, no scene numbers."""


class SceneDrafter:
    def __init__(
        self,
        *,
        use_mock: bool | None = None,
        completion_fn=None,
        model: str | None = None,
    ) -> None:
        self._completion = completion_fn if completion_fn is not None else _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or self._completion is None
        else:
            self.use_mock = use_mock
        self.model = model or settings.draft_model

    def draft_scene(
        self,
        *,
        scene: ScenePlan,
        plan: ChapterPlan,
        context_block: str,
        prior_text_tail: str,
        style: dict[str, Any] | None,
        revision_notes: list[str] | None = None,
    ) -> str:
        if self.use_mock:
            return self._mock_scene(scene, plan)

        user_prompt = self._build_user_prompt(
            scene=scene, plan=plan, context_block=context_block,
            prior_text_tail=prior_text_tail, style=style,
            revision_notes=revision_notes or [],
        )
        try:
            response = self._completion(
                model=self.model,
                temperature=settings.draft_temperature,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            if isinstance(content, list):
                content = "".join(str(p) for p in content)
            return str(content).strip()
        except Exception as exc:
            logger.warning("drafter: LLM call failed, falling back to mock: %s", exc)
            return self._mock_scene(scene, plan)

    @staticmethod
    def _build_user_prompt(
        *,
        scene: ScenePlan,
        plan: ChapterPlan,
        context_block: str,
        prior_text_tail: str,
        style: dict[str, Any] | None,
        revision_notes: list[str],
    ) -> str:
        parts = [
            f"CHAPTER GOAL\n{plan.chapter_goal}",
            f"SCENE GOAL\n{scene.scene_goal}",
            f"POV: {scene.pov_character or 'unspecified'} | LOCATION: {scene.location or 'unspecified'}"
            f" | CHARACTERS PRESENT: {', '.join(scene.present_characters) or 'unspecified'}"
            f" | TARGET WORDS: {scene.target_word_count}",
        ]
        if scene.key_facts_to_respect:
            parts.append("KEY FACTS\n" + "\n".join(f"- {f}" for f in scene.key_facts_to_respect))
        if context_block:
            parts.append(f"CONTEXT (retrieved from earlier chapters)\n{context_block}")
        if style:
            parts.append("VOICE\n" + json.dumps(style, ensure_ascii=True))
        if prior_text_tail:
            parts.append(f"PREVIOUS TEXT (continue from here)\n…{prior_text_tail[-1500:]}")
        if revision_notes:
            parts.append(
                "REVISION NOTES (a continuity critic rejected the previous draft — fix ALL of these)\n"
                + "\n".join(f"- {n}" for n in revision_notes)
            )
        return "\n\n".join(parts)

    @staticmethod
    def _mock_scene(scene: ScenePlan, plan: ChapterPlan) -> str:
        cast = ", ".join(scene.present_characters) or "The cast"
        return (
            f"[mock scene {scene.scene_index}] {cast} at "
            f"{scene.location or 'an unnamed place'}. {scene.scene_goal} "
            f"The chapter advances: {plan.chapter_goal}"
        )


__all__ = ["SceneDrafter"]
```

- [ ] **Step 4: Run tests, commit**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_drafter.py -v`
Expected: 2 passed

```bash
git add backend/pipeline/generation/drafter.py backend/pipeline/generation/tests/test_drafter.py
git commit -m "feat(generation): SceneDrafter with mock mode and revision notes"
```

---

## Task 4: Draft claims → `DraftChapter`

**Files:**
- Create: `backend/pipeline/generation/draft_claims.py`
- Test: `backend/pipeline/generation/tests/test_draft_claims.py`

### Background — exact claim shapes the critic consumes

- `mentions`: `{entity_id (entities.id), predicate, claimed_value, quote}`
- `knowledge_claims`: `{character_id (characters.id), fact_description, source_type, quote}`
- `location_claims`: `{character_id (characters.id), location_id (locations.id), quote}`
- `possession_claims`: `{character_id, object_id (objects.id), quote}`
- `events`: free dicts with `description` (used by commitment/thread/temporal checks)

The LLM emits **names**; resolution to ids is read-only (unknown names drop the
claim — a generated draft must never create entities as a side effect of being
critiqued).

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/generation/tests/test_draft_claims.py`:

```python
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from pipeline.generation.draft_claims import build_draft_chapter, extract_draft_claims

CHAR_ID = str(uuid.uuid4())
CHAR_ENTITY_ID = str(uuid.uuid4())
LOC_ID = str(uuid.uuid4())
OBJ_ID = str(uuid.uuid4())


class ClaimsFakeDB:
    """Read-only name lookups: knows Jake (character), Harbor (location), Knife (object)."""

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        name = str(params[1]).lower() if params and len(params) > 1 else ""
        if "FROM characters" in query and name == "jake":
            return (CHAR_ID, CHAR_ENTITY_ID)
        if "FROM locations" in query and name == "harbor":
            return (LOC_ID, str(uuid.uuid4()))
        if "FROM objects" in query and name == "knife":
            return (OBJ_ID, str(uuid.uuid4()))
        return None


RAW = {
    "mentions": [
        {"entity_name": "Jake", "entity_type": "character", "predicate": "eye_color",
         "claimed_value": "green", "quote": "his green eyes"},
        {"entity_name": "Nobody", "entity_type": "character", "predicate": "x",
         "claimed_value": "y", "quote": "z"},  # unknown — dropped
    ],
    "knowledge_claims": [
        {"character_name": "Jake", "fact_description": "the ledger is forged",
         "source_type": "inference", "quote": "Jake knew the ledger was forged"},
    ],
    "location_claims": [{"character_name": "Jake", "location_name": "Harbor", "quote": "at the harbor"}],
    "possession_claims": [{"character_name": "Jake", "object_name": "Knife", "quote": "his knife"}],
    "events": [{"description": "Jake confronts Sara", "event_type": "conflict"}],
}


def test_build_draft_chapter_resolves_names_read_only():
    draft = build_draft_chapter(
        ClaimsFakeDB(), novel_id="n1", chapter_number=5, text="prose",
        raw_claims=RAW, planned_thread_ids=["t1"], planned_commitment_ids=["c1"],
    )
    assert draft.mentions == [
        {"entity_id": CHAR_ENTITY_ID, "predicate": "eye_color",
         "claimed_value": "green", "quote": "his green eyes"}
    ]
    assert draft.knowledge_claims[0]["character_id"] == CHAR_ID
    assert draft.location_claims[0] == {"character_id": CHAR_ID, "location_id": LOC_ID, "quote": "at the harbor"}
    assert draft.possession_claims[0]["object_id"] == OBJ_ID
    assert draft.events[0]["description"] == "Jake confronts Sara"
    assert draft.planned_thread_ids == ["t1"]


def test_extract_draft_claims_parses_llm_json():
    def fake_completion(**kwargs):
        msg = SimpleNamespace(content=json.dumps(RAW))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    out = extract_draft_claims("prose", use_mock=False, completion_fn=fake_completion)
    assert out["mentions"][0]["entity_name"] == "Jake"


def test_extract_draft_claims_mock_is_empty():
    out = extract_draft_claims("prose", use_mock=True)
    assert out == {"mentions": [], "knowledge_claims": [], "location_claims": [],
                   "possession_claims": [], "events": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_draft_claims.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement**

Create `backend/pipeline/generation/draft_claims.py`:

```python
from __future__ import annotations

"""Extract the critic's claim shapes from draft prose, then resolve names to
ids READ-ONLY (unknown names drop the claim; critiquing a draft must never
create entities)."""

import json
import logging
from textwrap import dedent
from typing import Any

from pipeline.config import LLM_CONFIG, settings
from pipeline.critic.types import DraftChapter

logger = logging.getLogger(__name__)


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
    mock = settings.use_mock_llm or completion is None if use_mock is None else use_mock
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
        logger.warning("draft_claims: extraction failed: %s", exc)
        return {k: [] for k in _EMPTY}
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
        knowledge_claims.append({
            "character_id": cid,
            "fact_description": str(k.get("fact_description", "")).strip(),
            "source_type": k.get("source_type"),
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
```

- [ ] **Step 4: Run tests, commit**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_draft_claims.py -v`
Expected: 3 passed

```bash
git add backend/pipeline/generation/draft_claims.py backend/pipeline/generation/tests/test_draft_claims.py
git commit -m "feat(generation): draft claims extraction and read-only DraftChapter resolution"
```

---

## Task 5: Orchestrator (`loop.py`)

**Files:**
- Create: `backend/pipeline/generation/loop.py`
- Test: `backend/pipeline/generation/tests/test_loop.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/generation/tests/test_loop.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import patch

from pipeline.generation.loop import GeneratedChapter, generate_chapter
from pipeline.planner.types import ChapterPlan, ScenePlan


class LoopFakeDB:
    """Empty-result DB: style lookup and critic queries all return nothing.
    The plan itself is stubbed via the _mock_plan/gather_plan_context seam,
    so no planner context queries are exercised here."""

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        return None

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        return []

    def fetchval(self, query, params=None, *, commit=False):
        return None

    def close(self):
        pass


def _fixed_plan() -> ChapterPlan:
    return ChapterPlan(
        novel_id="novel-1",
        chapter_number=7,
        title=None,
        arc_position="rising",
        chapter_goal="Advance the conflict by one beat.",
        scenes=[
            ScenePlan(
                scene_index=1, pov_character="Jake", location=None, time_anchor=None,
                present_characters=["Jake"], scene_goal="Jake reads the letter.",
            ),
        ],
    )


def _plan_seam():
    """Patch the mock-planning seam so the test controls the plan exactly."""
    return (
        patch("pipeline.generation.loop.gather_plan_context", lambda db, n, c: object()),
        patch("pipeline.generation.loop._mock_plan", lambda ctx: _fixed_plan()),
    )


def test_generate_chapter_mock_end_to_end_no_ingest():
    p1, p2 = _plan_seam()
    with p1, p2:
        result = generate_chapter(
            "novel-1", 7, db=LoopFakeDB(), ingest=False, use_mock=True, max_revisions=2
        )
    assert isinstance(result, GeneratedChapter)
    assert result.chapter_number == 7
    assert result.text.strip()                 # mock drafter produced prose
    assert result.report is not None
    assert result.report.passed                # empty claims -> no findings
    assert result.ingested is False


def test_generate_chapter_ingests_when_passing():
    captured: dict = {}

    def fake_process_chapter(**kwargs):
        captured.update(kwargs)
        return {"chapter_id": str(uuid.uuid4())}

    p1, p2 = _plan_seam()
    with p1, p2, patch("pipeline.generation.loop.process_chapter", fake_process_chapter):
        result = generate_chapter(
            "novel-1", 7, db=LoopFakeDB(), ingest=True, use_mock=True, max_revisions=2
        )

    assert result.ingested is True
    assert captured["source"] == "generated"
    assert captured["replace"] is False
    assert captured["generation_meta"]["chapter_goal"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_loop.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement**

Create `backend/pipeline/generation/loop.py`:

```python
from __future__ import annotations

"""generate_chapter: plan -> retrieve -> draft -> claims -> critique -> revise
-> (optionally) ingest with source='generated'.

Requires Project 1 (process_chapter(db=, source=, generation_meta=, replace=)).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pipeline.config import settings
from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import CritiqueReport
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService
from pipeline.generation.draft_claims import build_draft_chapter, extract_draft_claims
from pipeline.generation.drafter import SceneDrafter
from pipeline.generation.style import average_fingerprints
from pipeline.pipeline import process_chapter
from pipeline.planner.context import gather_plan_context
from pipeline.planner.planner import ScenePlanner, _mock_plan
from pipeline.planner.types import ChapterPlan
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery

logger = logging.getLogger(__name__)


@dataclass
class GeneratedChapter:
    novel_id: str
    chapter_number: int
    text: str
    plan: ChapterPlan
    report: CritiqueReport
    iterations: int = 1
    ingested: bool = False
    chapter_id: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)


def _load_recent_style(db: Any, novel_id: str) -> dict[str, Any] | None:
    rows = db.fetchall(
        """
        SELECT style_fingerprint FROM chapters
         WHERE novel_id = %s AND style_fingerprint IS NOT NULL
         ORDER BY number DESC LIMIT 3
        """,
        (novel_id,),
        dict_rows=True,
    )
    fingerprints = []
    for r in rows or []:
        fp = r.get("style_fingerprint")
        if isinstance(fp, str):
            try:
                fp = json.loads(fp)
            except Exception:
                continue
        if isinstance(fp, dict):
            fingerprints.append(fp)
    return average_fingerprints(fingerprints)


def _context_block_for_scene(retriever: HybridRetriever | None, novel_id: str,
                             chapter_number: int, scene) -> str:
    if retriever is None:
        return ""
    query_text = f"{scene.scene_goal} {' '.join(scene.present_characters)}"
    try:
        bundle = retriever.retrieve(
            RetrievalQuery(
                text=query_text, novel_id=novel_id,
                max_chapter=chapter_number - 1, k=6,
            ),
            use_rerank=False,
        )
    except Exception as exc:
        logger.warning("generation: retrieval failed, drafting without it: %s", exc)
        return ""
    lines = [
        f"[{r.kind} ch{r.chapter_number}] {r.snippet}" for r in bundle.results if r.snippet
    ]
    return "\n".join(lines[:6])


def generate_chapter(
    novel_id: str,
    chapter_number: int,
    *,
    db: DBClient | None = None,
    ingest: bool = False,
    use_mock: bool | None = None,
    max_revisions: int | None = None,
    progress: Any | None = None,
) -> GeneratedChapter:
    owned = db is None
    client = db if db is not None else DBClient()
    mock = settings.use_mock_llm if use_mock is None else use_mock
    revisions_allowed = (
        settings.generation_max_revisions if max_revisions is None else max_revisions
    )

    def _tick(name: str) -> None:
        if progress is not None:
            progress.on_pass_start(name)
            progress.on_pass_done(name)

    try:
        _tick("planning")
        if mock:
            # Force the mock plan: the planner consults the global
            # settings.use_mock_llm itself, so with an API key configured a
            # use_mock=True loop run would otherwise still hit the LLM.
            plan = _mock_plan(gather_plan_context(client, novel_id, chapter_number))
        else:
            plan = ScenePlanner(client).plan(novel_id, chapter_number)

        retriever = None
        if not mock:
            retriever = HybridRetriever(client, EmbeddingService(use_mock=mock))
        style = _load_recent_style(client, novel_id)
        drafter = SceneDrafter(use_mock=mock)
        critic = ContinuityCritic(client)

        revision_notes: list[str] = []
        text = ""
        report: CritiqueReport | None = None
        iterations = 0

        while iterations <= revisions_allowed:
            iterations += 1
            scene_texts: list[str] = []
            for scene in plan.scenes:
                _tick(f"drafting scene {scene.scene_index}/{len(plan.scenes)}")
                context_block = _context_block_for_scene(
                    retriever, novel_id, chapter_number, scene
                )
                scene_texts.append(
                    drafter.draft_scene(
                        scene=scene,
                        plan=plan,
                        context_block=context_block,
                        prior_text_tail="\n\n".join(scene_texts)[-1500:],
                        style=style,
                        revision_notes=revision_notes or None,
                    )
                )
            text = "\n\n".join(scene_texts)

            _tick("critique")
            raw_claims = extract_draft_claims(text, use_mock=mock)
            planned_commitments = [
                c for s in plan.scenes for c in (s.commitments_to_plant + s.commitments_to_satisfy)
            ]
            draft = build_draft_chapter(
                client,
                novel_id=novel_id,
                chapter_number=chapter_number,
                text=text,
                raw_claims=raw_claims,
                planned_thread_ids=[t for s in plan.scenes for t in s.threads_to_advance],
                planned_commitment_ids=planned_commitments,
            )
            report = critic.critique(draft)
            if report.passed:
                break
            revision_notes = [
                f"{f.message}" + (f" (offending text: {f.quote})" if f.quote else "")
                for f in report.fails
            ]
            logger.info(
                "generation: draft failed critique (%d fails), revising (iteration %d)",
                len(report.fails), iterations,
            )

        assert report is not None
        result = GeneratedChapter(
            novel_id=novel_id,
            chapter_number=chapter_number,
            text=text,
            plan=plan,
            report=report,
            iterations=iterations,
        )

        if ingest and report.passed:
            _tick("ingest")
            outcome = process_chapter(
                novel_id=novel_id,
                chapter_number=chapter_number,
                raw_text=text,
                chapter_title=plan.title,
                use_mock_llm=mock or None,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
                progress=progress,
                db=client,
                replace=False,
                source="generated",
                generation_meta={
                    "model": settings.draft_model,
                    "chapter_goal": plan.chapter_goal,
                    "critic": report.summary(),
                    "iterations": iterations,
                },
            )
            result.ingested = True
            result.chapter_id = str(outcome.get("chapter_id"))
        elif ingest:
            logger.warning(
                "generation: NOT ingesting chapter %s — critique failed after %d iterations",
                chapter_number, iterations,
            )

        return result
    finally:
        if owned:
            client.close()


__all__ = ["generate_chapter", "GeneratedChapter"]
```

- [ ] **Step 4: Run tests, commit**

Run: `cd backend && .venv/bin/pytest pipeline/generation/tests/test_loop.py -v && .venv/bin/pytest -q`
Expected: all pass.

```bash
git add backend/pipeline/generation/loop.py backend/pipeline/generation/tests/test_loop.py
git commit -m "feat(generation): generate_chapter orchestrator with revision loop and gated ingest"
```

---

## Task 6: CLI subcommand

**Files:**
- Modify: `backend/pipeline/pipeline.py` (`build_parser`, `main`)

- [ ] **Step 1: Add the subcommand**

In `build_parser()`:

```python
    generate_parser = subparsers.add_parser("generate-chapter", help="Plan, draft, critique, and optionally ingest the next chapter")
    generate_parser.add_argument("--novel-id", required=True)
    generate_parser.add_argument("--number", required=True, type=int)
    generate_parser.add_argument("--ingest", action="store_true", help="Ingest the chapter if the critic passes")
    generate_parser.add_argument("--mock-llm", action="store_true")
    generate_parser.add_argument("--out", help="Write the draft prose to this file")
```

In `main()`:

```python
    if args.command == "generate-chapter":
        from pipeline.generation.loop import generate_chapter

        result = generate_chapter(
            args.novel_id,
            args.number,
            ingest=args.ingest,
            use_mock=True if args.mock_llm else None,
        )
        if args.out:
            Path(args.out).write_text(result.text, encoding="utf-8")
        print(json.dumps({
            "chapter_number": result.chapter_number,
            "iterations": result.iterations,
            "critic": result.report.summary(),
            "ingested": result.ingested,
            "chapter_id": result.chapter_id,
            "words": len(result.text.split()),
            "out": args.out,
        }, indent=2, default=str))
        return
```

- [ ] **Step 2: Smoke-test with mock**

Run: `cd backend && .venv/bin/python -m pipeline.pipeline generate-chapter --novel-id <any-id-from-list-novels> --number 998 --mock-llm --out /tmp/gen.txt`
Expected: JSON summary with `"ingested": false`, `/tmp/gen.txt` contains mock prose.

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/pipeline.py
git commit -m "feat(cli): generate-chapter subcommand"
```

---

## Task 7: API job + endpoint

**Files:**
- Modify: `backend/api/jobs.py`
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/routes/process.py`
- Create: `backend/api/tests/test_generate_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/api/tests/test_generate_api.py`:

```python
from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_novel


def test_generate_endpoint_submits_job(fake_db_factory, client, monkeypatch):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[])
    captured: dict = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return "gen-job-1"

    monkeypatch.setattr("api.routes.process.submit_generation_job", fake_submit)
    response = client.post(
        f"/api/novels/{novel['id']}/chapters/generate",
        json={"number": 4, "ingest": True},
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == "gen-job-1"
    assert captured["chapter_number"] == 4
    assert captured["ingest"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest api/tests/test_generate_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Implement**

In `backend/api/schemas.py`:

```python
class GenerateRequest(BaseModel):
    number: int
    ingest: bool = False
```

In `backend/api/jobs.py` add:

```python
def submit_generation_job(*, novel_id: str, chapter_number: int, ingest: bool) -> str:
    job_id = _job_store.create(total_passes=0)  # label-only progress
    tracker = ProgressTracker(job_id=job_id, store=_job_store)

    def _run() -> None:
        try:
            from pipeline.generation.loop import generate_chapter

            result = generate_chapter(
                novel_id, chapter_number, ingest=ingest, progress=tracker
            )
            _job_store.mark_done(job_id, {
                "chapter_number": result.chapter_number,
                "iterations": result.iterations,
                "passed": result.report.passed,
                "fails": len(result.report.fails),
                "warns": len(result.report.warns),
                "ingested": result.ingested,
                "chapter_id": result.chapter_id,
                "text": result.text,
            })
        except Exception as exc:
            _job_store.mark_error(job_id, str(exc))

    _executor.submit(_run)
    return job_id
```

and export it in `__all__`.

In `backend/api/routes/process.py`:

```python
from api.jobs import get_job, submit_generation_job, submit_job
from api.schemas import GenerateRequest, JobStatusResponse, ProcessRequest


@router.post(
    "/api/novels/{novel_id}/chapters/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict,
)
def generate_chapter_endpoint(novel_id: UUID, body: GenerateRequest) -> dict:
    novel = queries.get_novel(novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="Novel not found")
    job_id = submit_generation_job(
        novel_id=str(novel_id), chapter_number=body.number, ingest=body.ingest
    )
    return {"job_id": job_id}
```

- [ ] **Step 4: Run tests, commit**

Run: `cd backend && .venv/bin/pytest api/tests -q`
Expected: all pass.

```bash
git add backend/api
git commit -m "feat(api): chapter generation job endpoint"
```

---

## Task 8: Frontend — Generate tab on the Process page

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/routes/Process.tsx`

- [ ] **Step 1: Add API call**

In `frontend/src/api.ts`:

```typescript
export type GenerateResult = {
  chapter_number: number;
  iterations: number;
  passed: boolean;
  fails: number;
  warns: number;
  ingested: boolean;
  chapter_id: string | null;
  text: string;
};
```

and in the `api` object:

```typescript
  generateChapter: async (novelId: string, number: number, ingest: boolean) => {
    const res = await fetch(`/api/novels/${novelId}/chapters/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ number, ingest }),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json() as Promise<{ job_id: string }>;
  },
```

- [ ] **Step 2: Add a mode toggle to `Process.tsx`**

Add state and a toggle at the top of the component:

```tsx
const [mode, setMode] = useState<"process" | "generate">("process");
const [ingestGenerated, setIngestGenerated] = useState(false);

const generateMutation = useMutation({
  mutationFn: ({ number, ingest }: { number: number; ingest: boolean }) =>
    api.generateChapter(novelId!, number, ingest),
  onSuccess: (data) => setJobId(data.job_id),
});
```

Render the toggle above the form:

```tsx
<div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
  <button onClick={() => setMode("process")} disabled={mode === "process"}>Ingest text</button>
  <button onClick={() => setMode("generate")} disabled={mode === "generate"}>Generate chapter</button>
</div>
```

When `mode === "generate"` (and no `jobId`), render instead of the textarea form:

```tsx
<form
  onSubmit={(e) => {
    e.preventDefault();
    generateMutation.mutate({ number: chapterNumber, ingest: ingestGenerated });
  }}
  style={{ maxWidth: 480 }}
>
  <div className="form-group">
    <label className="form-label" htmlFor="gen-number">Chapter number</label>
    <input id="gen-number" type="number" min={1} value={chapterNumber}
      onChange={(e) => setChapterNumber(Number(e.target.value))} style={{ width: 100 }} />
  </div>
  <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
    <input type="checkbox" checked={ingestGenerated}
      onChange={(e) => setIngestGenerated(e.target.checked)} />
    Ingest into Continuum if the continuity critic passes
  </label>
  {generateMutation.isError && (
    <p style={{ color: "var(--red-text)", fontSize: 13 }}>
      Error: {(generateMutation.error as Error).message}
    </p>
  )}
  <button type="submit" className="btn-primary" disabled={generateMutation.isPending}>
    <ZapIcon /> {generateMutation.isPending ? "Submitting…" : "Generate chapter"}
  </button>
</form>
```

In the `job?.status === "done"` block, when the result contains generated text,
render it (the job result for generation jobs has `text`, `passed`, `fails`):

```tsx
{typeof result?.text === "string" && (
  <div style={{ marginTop: 16 }}>
    <div className="job-pass-count" style={{ marginBottom: 8 }}>
      Critic: {result.passed ? "passed" : `failed (${result.fails} blocking)`} ·
      {" "}{result.iterations} iteration(s) · {result.ingested ? "ingested" : "not ingested"}
    </div>
    <pre style={{ whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>
      {result.text as unknown as string}
    </pre>
  </div>
)}
```

(Loosen the `result` typing to `Record<string, unknown>` accesses as the file
already does; cast where needed to satisfy tsc.)

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: success, no TS errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): generate-chapter mode on the Process page"
```

---

## Task 9: End-to-end verification + docs

- [ ] **Step 1: Mock end-to-end against the dev DB**

```bash
cd backend
.venv/bin/python -m pipeline.pipeline generate-chapter --novel-id <ID> --number 998 --mock-llm --ingest
# expect: ingested=true, then verify and clean up:
.venv/bin/python -c "
from pipeline.db.client import DBClient
from pipeline.ingestion.ingest import delete_chapter_data
with DBClient() as db:
    row = db.fetchone(\"SELECT source FROM chapters WHERE number = 998\", dict_rows=True)
    print('source =', row and row['source'])
    with db.session() as s:
        delete_chapter_data(s, novel_id='<ID>', chapter_number=998)
print('cleaned up')
"
```

Expected: `source = generated`.

- [ ] **Step 2: Update docs**

- `docs/architecture.html`: new "Generation Loop" section (plan → retrieve →
  draft → claims → critique → revise → ingest diagram + module table for
  `pipeline/generation/`); note `chapters.source` filtering.
- `docs/reference.html`: `generate-chapter` CLI, generate endpoint, new env
  vars (`DRAFT_MODEL`, `DRAFT_TEMPERATURE`, `GENERATION_MAX_REVISIONS`),
  `pipeline/generation/` module reference.
- `docs/state-of-the-system.html`: update the "missing drafter/orchestrator"
  gaps; the loop now exists.

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: generation loop"
```
