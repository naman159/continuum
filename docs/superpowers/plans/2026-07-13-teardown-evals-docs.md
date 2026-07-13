# Teardown / Evals / Docs Implementation Plan (Plan 3 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the in-repo chapter-generation loop (spec decision: Continuum is an analyzer + agent memory, not a generator), build the offline eval harness in `backend/evals/`, and regenerate the docs site + README to describe the final two-spine system.

**Architecture:** Continuum after Plans 1–2 has one write spine (`analyze_chapter`: INGEST → EXTRACT → PERSIST → MATERIALIZE → CRITIQUE) and one read layer (`backend/reads/`, cutoff-aware, SQL-only). This plan removes the last pre-redesign component (the planner/drafter/generation loop, which nothing in the spines depends on), adds a pytest-runnable eval harness measuring extraction fidelity, retrieval recall@k, and critic precision/recall against a hand-written golden fixture novel, and finishes the docs.

**Tech Stack:** Python 3.11 (`backend/.venv`), FastAPI, psycopg3 + Postgres (with pgvector), pytest, PyYAML (new declared dep), React + TypeScript frontend, hand-maintained HTML docs site in `docs/`.

**Spec:** `docs/superpowers/specs/2026-07-11-continuum-analyzer-architecture-design.md` (steps 8–10, "Eval harness", "Hygiene, docs, testing", "Success criteria").

## Global Constraints

- Work on branch `teardown-evals-docs` off `main`.
- Backend tests: **always** `cd backend && .venv/bin/python -m pytest` (repo-root `.venv` collects DB-integration tests against an uninitialized DB — never use it). Tests need the dev Postgres from `.env`, same as the existing `reads/` and `api/` suites.
- Frontend gate: `cd frontend && npm run build` after any frontend change.
- CLAUDE.md mandate: "Ensure the docs website is updated after every change" — every task that changes behavior updates `docs/*.html` in the same commit. Tasks 1–2 only *remove* statements the change makes false; Task 6 is the full regeneration (don't write final prose twice).
- **Never delete, edit, or commit `BLOG_OUTLINE.md`.** The spec lists it as a stale file to remove, but it is the user's untracked scratch file — this constraint overrides the spec. Leave it exactly as-is.
- Never push to origin; commits stay local.
- Id-space reminder for any SQL you write: `events.involved_*` arrays hold TYPED ids (`characters.id`, `locations.id`, `objects.id`, `factions.id`); `relationships`/`shared_dynamics` `entity_a/b_id` and `state_deltas.subject_id/object_id` hold universal `entities.id`; `state_deltas.location_id` is typed `locations.id`.

---

### Task 1: Remove the generation path from API and frontend

The generation HTTP endpoint, its job runner, and the "Generate chapter" mode of the Process page go away. The `pipeline/generation/` package itself is deleted in Task 2 (this order keeps the repo green: `api/jobs.py` imports `pipeline.generation.loop` lazily inside the function being deleted here).

**Files:**
- Modify: `backend/api/routes/process.py`
- Modify: `backend/api/jobs.py`
- Modify: `backend/api/schemas.py` (delete `GenerateRequest`, ~line 417)
- Delete: `backend/api/tests/test_generate_api.py`
- Modify: `frontend/src/api.ts` (delete `generateChapter`, ~line 489)
- Modify: `frontend/src/routes/Process.tsx`
- Modify: `docs/reference.html` (delete the `/chapters/generate` endpoint row, ~line 1720; fix the `source` values note, ~line 943)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `api/jobs.py` exports only `JobRecord`, `JobStore`, `ProgressTracker`, `get_job`, `submit_job`. `POST /api/novels/{id}/chapters/generate` no longer exists. Task 2 relies on `api/` having no reference to `pipeline.generation`.

- [ ] **Step 1: Delete the generation endpoint tests**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git rm backend/api/tests/test_generate_api.py
```

- [ ] **Step 2: Remove the endpoint from `backend/api/routes/process.py`**

Delete the entire `generate_chapter_endpoint` function (the `@router.post("/api/novels/{novel_id}/chapters/generate", ...)` block, lines 38–61) and fix the imports at the top:

```python
from api.jobs import get_job, submit_job
from api.schemas import JobStatusResponse, ProcessRequest
```

(`submit_generation_job` and `GenerateRequest` drop out; `reads.chapters` import drops too — it was only used by the 409 duplicate check in the deleted endpoint. Verify with `grep -n chapters_reads backend/api/routes/process.py` that nothing else uses it before removing the import.)

- [ ] **Step 3: Remove `submit_generation_job` from `backend/api/jobs.py`**

Delete the whole `submit_generation_job` function (lines 128–153) and remove `"submit_generation_job",` from `__all__`.

- [ ] **Step 4: Delete `GenerateRequest` from `backend/api/schemas.py`**

Delete this class (nothing else references it — verify with `grep -rn GenerateRequest backend frontend`):

```python
class GenerateRequest(BaseModel):
    number: int
    ingest: bool = False
```

- [ ] **Step 5: Run backend tests**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all pass (360 - the 3 deleted generate-api tests = 357, exact count may vary).

- [ ] **Step 6: Remove `generateChapter` from `frontend/src/api.ts`**

Delete these two lines from the `api` object (~line 489):

```typescript
  generateChapter: (novelId: string, number: number, ingest: boolean) =>
    postJson<{ job_id: string }>(`/api/novels/${novelId}/chapters/generate`, { number, ingest }),
```

- [ ] **Step 7: Strip generation mode from `frontend/src/routes/Process.tsx`**

Six precise edits (the file is ~300 lines; everything else stays):

7a. Delete the state + mutation for generation (lines 49–50 and 58–62):

```typescript
  const [mode, setMode] = useState<"process" | "generate">("process");
  const [ingestGenerated, setIngestGenerated] = useState(false);
```
and
```typescript
  const generateMutation = useMutation({
    mutationFn: ({ number, ingest }: { number: number; ingest: boolean }) =>
      api.generateChapter(novelId!, number, ingest),
    onSuccess: (data) => setJobId(data.job_id),
  });
```

7b. Delete `const isGenerationResult = typeof result?.text === "string";` (line 78).

7c. Simplify the `<h1>` (line 83):

```typescript
        <h1 style={{ margin: 0 }}>Process Chapter</h1>
```

7d. Delete the mode-toggle button row (the `{!jobId && (<div style={{ display: "flex", gap: 8 ...` block, lines 86–95) and the entire generate form (`{!jobId && mode === "generate" && (<form ...` block, lines 97–134). Change the process form's guard from `{!jobId && mode === "process" && (` to `{!jobId && (`.

7e. In the done-state header (line 228), replace
`{isGenerationResult ? "Chapter generated" : "Chapter processed"}` with `Chapter processed`.
Change the results guard `{result && !isGenerationResult && (` to `{result && (`, and delete the whole `{result && isGenerationResult && (...)}` block (lines 248–259).

7f. The `api` import: check whether `api` is still used in the file after 7a (`grep -n "api\." frontend/src/routes/Process.tsx`); if not, change the import to `import { fetchJson, postJson } from "../api";`.

Ride-along fix while in this file: `PASS_LABELS` still has the stale key `entity_deltas` (line 29) — the extraction pass was renamed in Plan 1. Rename the key to `state_deltas` (keep the label "Tracking character changes").

- [ ] **Step 8: Frontend gate**

Run: `cd frontend && npm run build`
Expected: clean build (pre-existing chunk-size warning is fine). Also `grep -rn "generate" frontend/src/routes/Process.tsx frontend/src/api.ts` returns nothing.

- [ ] **Step 9: Update docs (removal only)**

In `docs/reference.html`:
- Delete the API-table row for `POST /api/novels/{novel_id}/chapters/generate` (~line 1720).
- Fix the `chapters.source` note (~line 943): it currently says `'generated'` is written "for the self-generation loop's own ingest". Rewrite that sentence so the documented values are `'human'` (Process page / CLI ingest) and `'agent'` (MCP `save_chapter`), with no mention of a generation loop.

Grep-check: `grep -in "chapters/generate" docs/*.html` returns nothing.

- [ ] **Step 10: Commit**

```bash
git add -A backend/api frontend/src docs/reference.html
git commit -m "feat: remove chapter-generation API endpoint and Process-page generate mode"
```

---

### Task 2: Delete `pipeline/generation/` + `pipeline/planner/`; move the style fingerprint to `pipeline/style.py`; remove the `generate-chapter` CLI

The style fingerprint survives as wiki chapter metadata (written at ingest into `chapters.style_fingerprint`); `average_fingerprints` does NOT survive — its only consumer was the deleted drafter loop.

**Files:**
- Create: `backend/pipeline/style.py`
- Create: `backend/pipeline/tests/__init__.py`, `backend/pipeline/tests/test_style.py`
- Modify: `backend/pipeline/pipeline.py` (import line 34; `generate-chapter` parser block ~lines 790–802; `if args.command == "generate-chapter":` handler block ~lines 841–861)
- Modify: `backend/pipeline/config.py` (delete generation settings, lines 55–64)
- Modify: `backend/pipeline/llm.py` (docstring line 1)
- Modify: `backend/pipeline/critic/types.py` (docstrings lines 86–88), `backend/pipeline/critic/checks/thread_coverage_check.py` (docstring line 3)
- Delete: `backend/pipeline/generation/` (whole package incl. tests), `backend/pipeline/planner/` (whole package incl. tests)
- Modify: `docs/reference.html` (CLI row ~line 398; env rows `DRAFT_MODEL` ~358, `DRAFT_TEMPERATURE` ~363, `GENERATION_MAX_REVISIONS` ~368), `docs/architecture.html` (any generation-loop mentions — removal only)

**Interfaces:**
- Consumes: Task 1 removed all `api/` references to generation.
- Produces: `pipeline.style.compute_style_fingerprint(text: str) -> dict[str, Any]` — imported by `pipeline/pipeline.py`. Tasks 6–7 rely on `grep -rn "generation\|planner" backend --include="*.py"` matching only prose/docstrings, never imports.

- [ ] **Step 1: Write the failing test at its new home**

Create `backend/pipeline/tests/__init__.py` (empty) and `backend/pipeline/tests/test_style.py`:

```python
from __future__ import annotations

from pipeline.style import compute_style_fingerprint

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

(These are the surviving tests from `pipeline/generation/tests/test_style.py`; `test_average_fingerprints` dies with `average_fingerprints`.)

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd backend && .venv/bin/python -m pytest pipeline/tests/test_style.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.style'`.

- [ ] **Step 3: Create `backend/pipeline/style.py`**

```python
"""Deterministic, LLM-free style fingerprint of a chapter's prose.

Computed at ingest and stored in chapters.style_fingerprint as chapter
voice metadata for the wiki.
"""

from __future__ import annotations

import re
from typing import Any

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DIALOGUE = re.compile(r"[\"“”][^\"“”]+[\"“”]")
_FIRST_PERSON = re.compile(r"\b(I|me|my|mine|we|our)\b", re.IGNORECASE)
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


__all__ = ["compute_style_fingerprint"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest pipeline/tests/test_style.py -v`
Expected: 4 passed.

- [ ] **Step 5: Re-point `pipeline/pipeline.py` and remove the CLI command**

- Line 34: `from pipeline.generation.style import compute_style_fingerprint` → `from pipeline.style import compute_style_fingerprint`
- Delete the parser block in `build_parser()`:

```python
    generate_parser = subparsers.add_parser(
        "generate-chapter",
        help="Plan, draft, critique, and optionally ingest the next chapter",
    )
    generate_parser.add_argument("--novel-id", required=True)
    generate_parser.add_argument("--number", required=True, type=int)
    generate_parser.add_argument(
        "--ingest", action="store_true", help="Ingest the chapter if the critic passes"
    )
    generate_parser.add_argument("--mock-llm", action="store_true")
    generate_parser.add_argument("--out", help="Write the draft prose to this file")
```

- Delete the whole `if args.command == "generate-chapter":` handler in `main()` (from that line through its `return`, including the lazy `from pipeline.generation.loop import generate_chapter` import).

- [ ] **Step 6: Delete the packages and the generation settings**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git rm -r backend/pipeline/generation backend/pipeline/planner
```

In `backend/pipeline/config.py`, delete the block (lines 55–64):

```python
    # Generation loop
    draft_model: str = field(
        default_factory=lambda: os.getenv("DRAFT_MODEL", os.getenv("DEFAULT_MODEL", "gpt-4o-mini"))
    )
    draft_temperature: float = field(
        default_factory=lambda: float(os.getenv("DRAFT_TEMPERATURE", "0.8"))
    )
    generation_max_revisions: int = field(
        default_factory=lambda: int(os.getenv("GENERATION_MAX_REVISIONS", "2"))
    )
```

Verify no survivors consume them: `grep -rn "draft_model\|draft_temperature\|generation_max_revisions" backend --include="*.py"` returns nothing.

- [ ] **Step 7: Fix stale docstrings**

- `backend/pipeline/llm.py` line 1: `"""Shared LLM plumbing used across extraction, generation, planning, and` → rewrite the sentence to name the surviving consumers: extraction and the continuity critic's claim extraction.
- `backend/pipeline/critic/types.py` (~lines 86–88): `# Plot threads the planner said this chapter would advance.` → `# Plot threads the caller declared this chapter would advance.` and same for the commitments comment below it.
- `backend/pipeline/critic/checks/thread_coverage_check.py` line 3: replace "the planner said it would" with "the caller said it would".

- [ ] **Step 8: Full backend suite + import check**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all pass.
Run: `grep -rn "from pipeline.generation\|from pipeline.planner\|import pipeline.generation\|import pipeline.planner" backend --include="*.py"`
Expected: no output.

- [ ] **Step 9: Update docs (removal only)**

In `docs/reference.html`: delete the `novel-pipeline generate-chapter` CLI table row (~line 398) and the `DRAFT_MODEL`, `DRAFT_TEMPERATURE`, `GENERATION_MAX_REVISIONS` env-var rows (~lines 358–370). In `docs/architecture.html`: `grep -in "generation\|planner\|drafter" docs/architecture.html` and delete/reword any sentence that describes the generation loop as an existing component (full regeneration happens in Task 6 — here just make nothing false).

- [ ] **Step 10: Commit**

```bash
git add -A backend/pipeline docs/
git commit -m "feat: delete generation loop and planner; style fingerprint moves to pipeline/style.py"
```

---

### Task 3: Eval golden set + scoring library (offline, no DB)

The golden fixture is a hand-written 10-chapter mini-novel with a YAML answer key, plus pure scoring functions. Nothing in this task touches Postgres or an LLM.

**Files:**
- Create: `backend/evals/__init__.py` (empty), `backend/evals/tests/__init__.py` (empty)
- Create: `backend/evals/golden/ch01.txt` … `ch10.txt`
- Create: `backend/evals/golden/answer_key.yaml`
- Create: `backend/evals/loader.py`
- Create: `backend/evals/scoring.py`
- Test: `backend/evals/tests/test_scoring.py`
- Modify: `backend/pyproject.toml` (add `"pyyaml>=6.0",` to `dependencies`)

**Interfaces:**
- Consumes: nothing.
- Produces (used by Tasks 4–5):
  - `evals.loader.load_chapters() -> list[tuple[int, str]]` — `(chapter_number, text)` sorted ascending.
  - `evals.loader.load_answer_key() -> dict` — parsed YAML.
  - `evals.scoring.score_entities(expected: list[str], actual: list[str]) -> dict` — keys `precision`, `recall`, `missing`, `extra`.
  - `evals.scoring.score_possessions(expected: list[dict], actual: list[dict], tolerance: int = 1) -> dict` — keys `recall`, `matched`, `missing`.
  - `evals.scoring.score_knowledge(expected: list[dict], actual: list[dict]) -> dict` — keys `recall`, `missing`.
  - `evals.scoring.recall_at_k(expected_chapters: list[int], result_chapters: list[int], k: int) -> float`.

- [ ] **Step 1: Declare pyyaml**

In `backend/pyproject.toml` add `"pyyaml>=6.0",` to the `dependencies` list (it is already present transitively; this declares the direct use), then `cd backend && uv sync 2>/dev/null || .venv/bin/pip install "pyyaml>=6.0"`.

- [ ] **Step 2: Write the golden fixture chapters**

Create `backend/evals/golden/ch01.txt` through `ch10.txt` with exactly this content (facts are load-bearing — the answer key and both DB evals depend on them; keep names and events verbatim):

`ch01.txt`:
```
Mira Solen had worked the tide-tables at the Harbor of Veyra since she was twelve, and she knew every crate that came off the ferries. The one that split open on the quay that morning was not on any manifest. Inside, wrapped in oilcloth, lay a brass lantern, cold to the touch and heavier than it had any right to be. Mira looked around the empty quay, then tucked the Brass Lantern into her satchel and walked home along the seawall.
```

`ch02.txt`:
```
The lantern would not light, no matter what oil Mira fed it. So she took the coach inland to the Glass Archive, where it was said the archivists could name any made thing. The archivist on duty was a stooped, careful man called Toren Vale. He turned the Brass Lantern over twice in his gloved hands and went very still. "Where did you get this?" Toren asked. "The harbor," Mira said. "It washed in with the ferries."
```

`ch03.txt`:
```
Toren Vale locked the reading-room door before he spoke. "This is a vault-key," he told Mira. "The Brass Lantern opens the Undervault beneath the old city. Light it at the sealed gate and the gate will open. Every door down there answers to that flame." Mira stared at the lantern on the table between them. She had carried the key to the Undervault in her satchel for a week without knowing it.
```

`ch04.txt`:
```
Mira did not trust herself to keep such a thing under a harbor-house floorboard. "Study it," she told Toren Vale, and she gave the Brass Lantern into his keeping at the Glass Archive. Toren signed for it in the acquisitions ledger and set it in the iron cabinet behind his desk. "Three weeks," he promised. "Then it goes back to you, with everything I can learn about it."
```

`ch05.txt`:
```
The woman who called herself Kessa Dray had been a reader at the Glass Archive for a month, always polite, always early. On the night of the spring audit she stayed late, forced the iron cabinet behind Toren Vale's desk, and stole the Brass Lantern. A porter saw her cross the courtyard with a satchel and swore her shadow moved wrong. By dawn Kessa Dray was gone, and the cabinet stood open and empty.
```

`ch06.txt`:
```
Mira Solen and Toren Vale followed the thief's trail south out of the city and into the Saltmarsh, where the causeways sank underfoot and the mist ate every landmark. A charcoal-burner had seen a woman matching Kessa Dray's description buying passage toward the ferry crossing. They pushed on through the reeds, Toren coughing in the damp, Mira counting the channels so they would not be lost when dark came.
```

`ch07.txt`:
```
The causeway plank snapped under Kessa Dray at the ferry crossing, and she went into the black water of the Saltmarsh. She came up swearing — alive, but the satchel gaped open, and the Brass Lantern was gone into the reeds. It was Odo Bram, the old ferryman, who fished it out of the channel the next morning on his mooring-hook. He wiped the mud from the brass and hung the strange lantern in his boathouse.
```

`ch08.txt`:
```
Word travels along a marsh faster than a coach on a road. Odo Bram the ferryman had heard a harbor-girl was asking after a brass lantern, and when Mira Solen reached his boathouse he handed it over without ceremony. "Take it, and take this too," Odo said. "The woman who lost it — she wore the grey ring of the Ash Council. They pay for vault-keys, and they do not stop." So Mira learned who Kessa Dray truly served: the Ash Council.
```

`ch09.txt`:
```
The sealed gate stood at the bottom of the old city's cistern stair, just as Toren Vale's maps promised. Mira Solen lit the Brass Lantern with a steady hand. The flame burned white, the seal drew back like a tide, and the gate of the Undervault swung open onto cold, dry dark. Mira stepped through with the lantern held high, and the vault woke around her, shelf upon shelf of things the old city had chosen to forget.
```

`ch10.txt`:
```
They were waiting for her in the deep gallery of the Undervault: three speakers of the Ash Council in grey, with Kessa Dray standing silent behind them. "The key, harbor-girl," the eldest said. Mira Solen set the Brass Lantern on the floor between them and did not step back. "It opens every door down here," she said. "Including the ones you sealed on your own dead. Shall we open those first?" The Council speakers looked at one another, and for the first time the Undervault heard them bargain.
```

- [ ] **Step 3: Write the answer key**

Create `backend/evals/golden/answer_key.yaml`:

```yaml
# Answer key for the golden fixture novel "The Lantern of Veyra".
# Interval semantics match possesses_edges: since_chapter/until_chapter are
# inclusive chapter numbers; until: null means "still held at end of novel".
novel:
  title: The Lantern of Veyra

entities:
  characters: [Mira Solen, Toren Vale, Kessa Dray, Odo Bram]
  locations: [Harbor of Veyra, Glass Archive, Saltmarsh, Undervault]
  objects: [Brass Lantern]
  factions: [Ash Council]

possessions:
  - {object: Brass Lantern, holder: Mira Solen, since: 1, until: 3}
  - {object: Brass Lantern, holder: Toren Vale, since: 4, until: 4}
  - {object: Brass Lantern, holder: Kessa Dray, since: 5, until: 6}
  - {object: Brass Lantern, holder: Odo Bram, since: 7, until: 7}
  - {object: Brass Lantern, holder: Mira Solen, since: 8, until: null}

locations_at:
  - {character: Mira Solen, location: Harbor of Veyra, chapter: 1}
  - {character: Mira Solen, location: Glass Archive, chapter: 2}
  - {character: Kessa Dray, location: Glass Archive, chapter: 5}
  - {character: Mira Solen, location: Saltmarsh, chapter: 6}
  - {character: Mira Solen, location: Undervault, chapter: 9}

knowledge:
  - {character: Mira Solen, fact: the brass lantern opens the undervault, since: 3}
  - {character: Mira Solen, fact: kessa dray serves the ash council, since: 8}

queries:
  - {text: who found the brass lantern at the harbor, expect_chapters: [1]}
  - {text: archivist at the Glass Archive examines the lantern, expect_chapters: [2]}
  - {text: what does the Brass Lantern open, expect_chapters: [3]}
  - {text: Mira gives the lantern to Toren for study, expect_chapters: [4]}
  - {text: Kessa Dray steals the lantern from the iron cabinet, expect_chapters: [5]}
  - {text: pursuit through the Saltmarsh causeways, expect_chapters: [6]}
  - {text: ferryman fishes the lantern out of the channel, expect_chapters: [7]}
  - {text: Odo Bram returns the lantern and warns about the grey ring, expect_chapters: [8]}
  - {text: lighting the lantern at the sealed gate of the Undervault, expect_chapters: [9]}
  - {text: bargaining with the Ash Council speakers, expect_chapters: [10]}
```

- [ ] **Step 4: Write the failing scoring tests**

Create `backend/evals/tests/test_scoring.py`:

```python
from __future__ import annotations

from evals.loader import load_answer_key, load_chapters
from evals.scoring import (
    recall_at_k,
    score_entities,
    score_knowledge,
    score_possessions,
)


def test_load_chapters_returns_ten_ordered():
    chapters = load_chapters()
    assert [n for n, _ in chapters] == list(range(1, 11))
    assert all(text.strip() for _, text in chapters)


def test_load_answer_key_shape():
    key = load_answer_key()
    assert set(key["entities"]) == {"characters", "locations", "objects", "factions"}
    assert len(key["queries"]) == 10


def test_score_entities_exact_and_partial_names():
    got = score_entities(
        ["Mira Solen", "Toren Vale"],
        ["mira solen", "Toren", "The Ash Council"],
    )
    # "Toren" matches "Toren Vale" by containment; "The Ash Council" is extra.
    assert got["recall"] == 1.0
    assert got["missing"] == []
    assert got["extra"] == ["the ash council"]
    assert got["precision"] == 2 / 3


def test_score_entities_empty_actual():
    got = score_entities(["Mira Solen"], [])
    assert got["recall"] == 0.0
    assert got["missing"] == ["mira solen"]


def test_score_possessions_with_tolerance():
    expected = [{"object": "Brass Lantern", "holder": "Mira Solen", "since": 1, "until": 3}]
    actual = [{"object": "brass lantern", "holder": "Mira", "since": 2, "until": 4}]
    got = score_possessions(expected, actual, tolerance=1)
    assert got["recall"] == 1.0
    # Outside tolerance:
    actual_far = [{"object": "brass lantern", "holder": "Mira", "since": 5, "until": None}]
    assert score_possessions(expected, actual_far, tolerance=1)["recall"] == 0.0


def test_score_possessions_open_interval():
    expected = [{"object": "Brass Lantern", "holder": "Mira Solen", "since": 8, "until": None}]
    actual = [{"object": "Brass Lantern", "holder": "Mira Solen", "since": 8, "until": None}]
    assert score_possessions(expected, actual)["recall"] == 1.0


def test_score_knowledge_word_overlap():
    expected = [{"character": "Mira Solen", "fact": "the brass lantern opens the undervault", "since": 3}]
    actual = [
        {"character": "Mira Solen", "fact": "lantern is the key that opens the undervault", "since": 3},
    ]
    assert score_knowledge(expected, actual)["recall"] == 1.0
    assert score_knowledge(expected, [])["recall"] == 0.0


def test_recall_at_k():
    assert recall_at_k([1], [3, 1, 2], k=2) == 1.0
    assert recall_at_k([1], [3, 2, 1], k=2) == 0.0
    assert recall_at_k([1, 2], [1, 5, 6], k=3) == 0.5
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest evals/tests/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.loader'`.

- [ ] **Step 6: Implement `backend/evals/loader.py`**

```python
"""Load the golden fixture novel and its answer key."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

GOLDEN_DIR = Path(__file__).parent / "golden"
_CHAPTER_FILE = re.compile(r"^ch(\d+)\.txt$")


def load_chapters() -> list[tuple[int, str]]:
    """Return (chapter_number, text) for every golden chapter, ascending."""
    chapters: list[tuple[int, str]] = []
    for path in GOLDEN_DIR.iterdir():
        m = _CHAPTER_FILE.match(path.name)
        if m:
            chapters.append((int(m.group(1)), path.read_text(encoding="utf-8")))
    chapters.sort(key=lambda pair: pair[0])
    return chapters


def load_answer_key() -> dict[str, Any]:
    with (GOLDEN_DIR / "answer_key.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
```

- [ ] **Step 7: Implement `backend/evals/scoring.py`**

```python
"""Pure scoring functions for the eval harness. No DB, no LLM.

Name matching is deliberately forgiving (casefold + containment either way):
extraction may emit "Mira" where the key says "Mira Solen", and that is a
correct extraction, not a miss.
"""

from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _names_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def score_entities(expected: list[str], actual: list[str]) -> dict[str, Any]:
    exp = [_norm(e) for e in expected]
    act = [_norm(a) for a in actual]
    missing = [e for e in exp if not any(_names_match(e, a) for a in act)]
    extra = [a for a in act if not any(_names_match(e, a) for e in exp)]
    recall = (len(exp) - len(missing)) / len(exp) if exp else 1.0
    precision = (len(act) - len(extra)) / len(act) if act else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "missing": sorted(missing),
        "extra": sorted(extra),
    }


def _interval_matches(expected: dict, actual: dict, tolerance: int) -> bool:
    if not _names_match(expected["object"], actual["object"]):
        return False
    if not _names_match(expected["holder"], actual["holder"]):
        return False
    if abs(int(actual["since"]) - int(expected["since"])) > tolerance:
        return False
    exp_until, act_until = expected.get("until"), actual.get("until")
    if exp_until is None:
        return act_until is None or int(act_until) >= int(expected["since"])
    if act_until is None:
        return False
    return abs(int(act_until) - int(exp_until)) <= tolerance


def score_possessions(
    expected: list[dict], actual: list[dict], tolerance: int = 1
) -> dict[str, Any]:
    """Recall of expected possession intervals among the actual edges.

    An expected interval counts as found when some actual edge has the same
    object+holder (fuzzy names) and boundaries within `tolerance` chapters.
    """
    matched, missing = [], []
    for exp in expected:
        if any(_interval_matches(exp, act, tolerance) for act in actual):
            matched.append(exp)
        else:
            missing.append(exp)
    recall = len(matched) / len(expected) if expected else 1.0
    return {"recall": recall, "matched": matched, "missing": missing}


def _facts_overlap(expected_fact: str, actual_fact: str) -> bool:
    exp_words = set(_norm(expected_fact).split())
    act_words = set(_norm(actual_fact).split())
    if not exp_words or not act_words:
        return False
    return len(exp_words & act_words) >= max(2, len(exp_words) // 2)


def score_knowledge(expected: list[dict], actual: list[dict]) -> dict[str, Any]:
    """Recall of expected knowledge facts among actual knows-facts.

    A fact counts as found when some actual row has a matching character name
    and >= 50% word overlap with the expected fact description.
    """
    missing = []
    for exp in expected:
        found = any(
            _names_match(exp["character"], act.get("character", ""))
            and _facts_overlap(exp["fact"], act.get("fact", ""))
            for act in actual
        )
        if not found:
            missing.append(exp)
    recall = (len(expected) - len(missing)) / len(expected) if expected else 1.0
    return {"recall": recall, "missing": missing}


def recall_at_k(
    expected_chapters: list[int], result_chapters: list[int], k: int
) -> float:
    """Fraction of expected chapters that appear in the top-k results."""
    if not expected_chapters:
        return 1.0
    top = set(result_chapters[:k])
    hit = sum(1 for c in expected_chapters if c in top)
    return hit / len(expected_chapters)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest evals/tests/test_scoring.py -v`
Expected: 8 passed. Then the full suite: `cd backend && .venv/bin/python -m pytest` — all pass.

- [ ] **Step 9: Update docs**

`docs/reference.html`: in the backend package-layout section, add a one-row entry for `evals/` — "Offline eval harness: golden fixture novel + answer key, scoring library (full harness lands with retrieval/critic/extraction evals)". Keep it to one row; Task 6 writes the full description.

- [ ] **Step 10: Commit**

```bash
git add backend/evals backend/pyproject.toml docs/reference.html
git commit -m "feat: eval golden fixture novel, answer key, and scoring library"
```

---

### Task 4: Critic precision/recall eval

Labeled draft cases run through `ContinuityCritic` against a seeded novel: seeded violations must be flagged, the clean draft must not be. The critic's checks are deterministic SQL — no LLM — so the metrics are exact and asserted exactly.

**Files:**
- Create: `backend/evals/critic_eval.py`
- Create: `backend/evals/tests/conftest.py`
- Test: `backend/evals/tests/test_critic_eval.py`

**Interfaces:**
- Consumes: `reads.tests.seeding.seed_novel(db) -> dict` / `reads.tests.seeding.cleanup(db, novel_id)` (seeds Aria Vance/Borin Thale, Fogmere Docks/Sable Archive, Iron Compass with an active possession edge for Aria since ch1, one canon fact `eye_color='storm-grey'` on Aria (unlocked), and materialized state through ch3). `pipeline.critic.runner.ContinuityCritic(db).critique(draft: DraftChapter) -> CritiqueReport`. `pipeline.critic.types.DraftChapter`.
- Produces: `evals.critic_eval.build_cases(db, seeded: dict) -> list[dict]` — each `{name, draft, should_flag, expected_check}`; `evals.critic_eval.run_critic_eval(db, cases) -> dict` — keys `precision`, `recall`, `true_positives`, `false_positives`, `false_negatives`, `per_case`.

- [ ] **Step 1: Create `backend/evals/tests/conftest.py`**

```python
"""DB fixtures for eval tests, mirroring reads/tests/conftest.py."""

from __future__ import annotations

import pytest

from pipeline.db.client import DBClient


@pytest.fixture(scope="module")
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()
```

- [ ] **Step 2: Write the failing eval test**

Create `backend/evals/tests/test_critic_eval.py`:

```python
from __future__ import annotations

import pytest

from evals.critic_eval import build_cases, run_critic_eval
from pipeline.db.client import DBClient
from reads.tests import seeding


@pytest.fixture()
def seeded(db: DBClient):
    data = seeding.seed_novel(db)
    yield data
    seeding.cleanup(db, data["novel_id"])


def test_critic_flags_every_seeded_violation(db, seeded):
    cases = build_cases(db, seeded)
    metrics = run_critic_eval(db, cases)
    assert metrics["recall"] == 1.0, metrics["per_case"]


def test_critic_passes_clean_draft(db, seeded):
    cases = build_cases(db, seeded)
    metrics = run_critic_eval(db, cases)
    assert metrics["false_positives"] == 0, metrics["per_case"]
    assert metrics["precision"] == 1.0


def test_metrics_shape(db, seeded):
    metrics = run_critic_eval(db, build_cases(db, seeded))
    assert set(metrics) >= {
        "precision", "recall",
        "true_positives", "false_positives", "false_negatives",
        "per_case",
    }
    # 4 seeded violations + 1 clean case
    assert len(metrics["per_case"]) == 5
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest evals/tests/test_critic_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.critic_eval'`.

- [ ] **Step 4: Implement `backend/evals/critic_eval.py`**

```python
"""Critic precision/recall eval.

Builds labeled DraftChapter cases against the seeded reads-test novel:
four seeded violations (each targeting one deterministic check) plus one
clean draft, then measures whether the critic flags exactly the violations.

A case counts as "flagged" when the report contains at least one finding
from the case's targeted check (any severity — the possession check
deliberately WARNs rather than FAILs on pickups).
"""

from __future__ import annotations

from typing import Any

from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import DraftChapter
from pipeline.db.client import DBClient


def build_cases(db: DBClient, seeded: dict[str, Any]) -> list[dict[str, Any]]:
    novel_id = seeded["novel_id"]

    # The seeded canon fact (Aria eye_color=storm-grey) is unlocked; lock it
    # so a contradiction is a FAIL-grade violation.
    db.execute(
        "UPDATE canon_facts SET locked = true WHERE id = %s",
        (seeded["canon_fact_id"],),
    )
    # Give Aria one known fact so the clean draft can assert prior knowledge.
    db.execute(
        """
        INSERT INTO knows_edges (character_id, fact_description, learned_chapter, source_type)
        VALUES (%s, %s, 1, 'observation')
        """,
        (seeded["char_a_id"], "the iron compass points to the sunken vault"),
    )

    two_places = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Aria haggled at Fogmere Docks at noon; at the same hour she read in the Sable Archive.",
        location_claims=[
            {"character_id": seeded["char_a_id"], "location_id": seeded["loc_a_id"],
             "quote": "Aria haggled at Fogmere Docks at noon"},
            {"character_id": seeded["char_a_id"], "location_id": seeded["loc_b_id"],
             "quote": "at the same hour she read in the Sable Archive"},
        ],
    )

    unknown_knowledge = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Borin recalled that the iron compass points to the sunken vault.",
        knowledge_claims=[
            {"character_id": seeded["char_b_id"],
             "fact_description": "the iron compass points to the sunken vault",
             "learned_this_chapter": False,
             "quote": "Borin recalled that the iron compass points to the sunken vault"},
        ],
    )

    wrong_possessor = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Borin turned the Iron Compass over in his hands, as he had for years.",
        possession_claims=[
            {"character_id": seeded["char_b_id"], "object_id": seeded["obj_id"],
             "quote": "Borin turned the Iron Compass over in his hands"},
        ],
    )

    canon_contradiction = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Aria's amber eyes caught the lamplight.",
        mentions=[
            {"entity_id": seeded["char_a_eid"], "predicate": "eye_color",
             "claimed_value": "amber", "quote": "Aria's amber eyes caught the lamplight"},
        ],
    )

    clean = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Aria stood alone in the Sable Archive, compass in hand, sure of its secret.",
        location_claims=[
            {"character_id": seeded["char_a_id"], "location_id": seeded["loc_b_id"],
             "quote": "Aria stood alone in the Sable Archive"},
        ],
        possession_claims=[
            {"character_id": seeded["char_a_id"], "object_id": seeded["obj_id"],
             "quote": "compass in hand"},
        ],
        knowledge_claims=[
            {"character_id": seeded["char_a_id"],
             "fact_description": "the iron compass points to the sunken vault",
             "learned_this_chapter": False,
             "quote": "sure of its secret"},
        ],
    )

    return [
        {"name": "two_places_at_once", "draft": two_places,
         "should_flag": True, "expected_check": "location_possession"},
        {"name": "unknown_knowledge", "draft": unknown_knowledge,
         "should_flag": True, "expected_check": "knowledge_state"},
        {"name": "wrong_possessor", "draft": wrong_possessor,
         "should_flag": True, "expected_check": "location_possession"},
        {"name": "canon_contradiction", "draft": canon_contradiction,
         "should_flag": True, "expected_check": "entity_mention"},
        {"name": "clean_draft", "draft": clean,
         "should_flag": False, "expected_check": None},
    ]


def run_critic_eval(db: DBClient, cases: list[dict[str, Any]]) -> dict[str, Any]:
    critic = ContinuityCritic(db)
    tp = fp = fn = 0
    per_case: list[dict[str, Any]] = []
    for case in cases:
        report = critic.critique(case["draft"])
        if case["should_flag"]:
            flagged = any(f.check == case["expected_check"] for f in report.findings)
        else:
            flagged = bool(report.findings)
        if case["should_flag"] and flagged:
            tp += 1
        elif case["should_flag"] and not flagged:
            fn += 1
        elif not case["should_flag"] and flagged:
            fp += 1
        per_case.append({
            "name": case["name"],
            "should_flag": case["should_flag"],
            "flagged": flagged,
            "findings": [(f.check, f.severity.value, f.message) for f in report.findings],
        })
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "per_case": per_case,
    }
```

- [ ] **Step 5: Run the eval tests**

Run: `cd backend && .venv/bin/python -m pytest evals/tests/test_critic_eval.py -v -s`
Expected: 3 passed. If a violation case is not flagged, the assertion message prints `per_case` with the actual findings — debug from there (likely a wrong id key or the seeded edge shape changed; check `reads/tests/seeding.py`), don't loosen the assertion.

- [ ] **Step 6: Full suite + docs + commit**

Run: `cd backend && .venv/bin/python -m pytest` — all pass. No docs change needed beyond Task 3's row (harness not yet complete).

```bash
git add backend/evals
git commit -m "feat: critic precision/recall eval over seeded violations"
```

---

### Task 5: Retrieval recall@k eval + env-gated extraction-fidelity eval

Ingest the golden novel through the real `analyze_chapter` spine with the mock LLM (deterministic, offline: chunking is real, embeddings are hash-based, BM25 is real Postgres FTS), then measure retrieval recall@k over the golden queries. Extraction fidelity against the answer key is only meaningful with a real LLM, so those thresholds are env-gated behind `RUN_LLM_EVALS=1`; the mock-mode test proves the harness plumbing end-to-end.

**Files:**
- Create: `backend/evals/harness.py`
- Modify: `backend/evals/tests/conftest.py` (add `golden_novel` fixture)
- Test: `backend/evals/tests/test_retrieval_eval.py`
- Test: `backend/evals/tests/test_extraction_fidelity.py`

**Interfaces:**
- Consumes: `evals.loader.load_chapters/load_answer_key`, `evals.scoring.*` (Task 3), `pipeline.pipeline.analyze_chapter(*, novel_id, chapter_number, raw_text, chapter_title, use_mock_llm, chunk_size, chunk_overlap, db=..., replace=False, source="human")`, `reads.search.search(db, novel_id, query_text, up_to_chapter, k=8)` (returns `{"results": [{kind, chapter_number, score, snippet}]}`).
- Produces: `evals.harness.ingest_fixture(db, *, use_mock_llm: bool = True) -> str` (novel_id); `evals.harness.read_projections(db, novel_id: str) -> dict` with keys `entities` (`{characters, locations, objects, factions}` name lists), `possessions` (`[{object, holder, since, until}]`), `knowledge` (`[{character, fact, since}]`); `evals.harness.run_retrieval_eval(db, novel_id, k: int = 8) -> dict` with keys `mean_recall_at_k`, `k`, `per_query`.

- [ ] **Step 1: Implement `backend/evals/harness.py`**

```python
"""DB-facing half of the eval harness: ingest the golden fixture through the
real write spine and read back the projections the answer key grades.

Mock-LLM ingestion is deterministic and offline (hash embeddings, real
chunking/BM25) — it exercises the plumbing. Fidelity *thresholds* only mean
anything on a real-LLM run (see test_extraction_fidelity.py's env gate).
"""

from __future__ import annotations

from typing import Any

from evals.loader import load_answer_key, load_chapters
from evals.scoring import recall_at_k
from pipeline.config import settings
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService
from pipeline.pipeline import analyze_chapter
from pipeline.retrieval.hybrid import HybridRetriever
from reads.search import search


def ingest_fixture(db: DBClient, *, use_mock_llm: bool = True) -> str:
    """Create the golden novel and run every chapter through analyze_chapter."""
    key = load_answer_key()
    # commit=True is load-bearing: DBClient.fetchval defaults to ROLLBACK,
    # which would silently discard the INSERT.
    novel_id = str(db.fetchval(
        "INSERT INTO novels (title) VALUES (%s) RETURNING id",
        (key["novel"]["title"],),
        commit=True,
    ))
    for number, text in load_chapters():
        analyze_chapter(
            novel_id=novel_id,
            chapter_number=number,
            raw_text=text,
            chapter_title=None,
            use_mock_llm=use_mock_llm,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            db=db,
            replace=False,
            source="human",
        )
    return novel_id


def read_projections(db: DBClient, novel_id: str) -> dict[str, Any]:
    """Read back the materialized projections the answer key grades."""
    def names(table: str) -> list[str]:
        return [
            r[0] for r in db.fetchall(
                f"SELECT name FROM {table} WHERE novel_id = %s ORDER BY name",
                (novel_id,),
            )
        ]

    possessions = [
        {"object": r["object"], "holder": r["holder"],
         "since": r["since_chapter"], "until": r["until_chapter"]}
        for r in db.fetchall(
            """
            SELECT o.name AS object, c.name AS holder,
                   p.since_chapter, p.until_chapter
              FROM possesses_edges p
              JOIN characters c ON c.id = p.character_id
              JOIN objects o ON o.id = p.object_id
             WHERE c.novel_id = %s
             ORDER BY p.since_chapter, o.name
            """,
            (novel_id,),
            dict_rows=True,
        )
    ]
    knowledge = [
        {"character": r["character"], "fact": r["fact_description"],
         "since": r["learned_chapter"]}
        for r in db.fetchall(
            """
            SELECT c.name AS character, k.fact_description, k.learned_chapter
              FROM knows_edges k
              JOIN characters c ON c.id = k.character_id
             WHERE c.novel_id = %s AND k.superseded_by_id IS NULL
             ORDER BY k.learned_chapter
            """,
            (novel_id,),
            dict_rows=True,
        )
    ]
    return {
        "entities": {
            "characters": names("characters"),
            "locations": names("locations"),
            "objects": names("objects"),
            "factions": names("factions"),
        },
        "possessions": possessions,
        "knowledge": knowledge,
    }


def run_retrieval_eval(db: DBClient, novel_id: str, k: int = 8) -> dict[str, Any]:
    """recall@k over the golden queries, via the production reads.search path.

    The retriever is injected with mock (hash) embeddings so the eval is
    offline and deterministic regardless of the environment's USE_MOCK_LLM:
    the fixture was ingested with hash embeddings, so real query embeddings
    would mismatch anyway. This grades the BM25/keyword half plus the full
    RRF/MMR plumbing.
    """
    key = load_answer_key()
    retriever = HybridRetriever(db, EmbeddingService(use_mock=True))
    per_query: list[dict[str, Any]] = []
    for q in key["queries"]:
        got = search(db, novel_id, q["text"], up_to_chapter=None, k=k, retriever=retriever)
        result_chapters = [
            r["chapter_number"] for r in got["results"]
            if r["chapter_number"] is not None
        ]
        score = recall_at_k(q["expect_chapters"], result_chapters, k)
        per_query.append({
            "query": q["text"],
            "expected": q["expect_chapters"],
            "got": result_chapters[:k],
            "recall_at_k": score,
        })
    mean = sum(p["recall_at_k"] for p in per_query) / len(per_query)
    return {"mean_recall_at_k": mean, "k": k, "per_query": per_query}
```

- [ ] **Step 2: Add the `golden_novel` fixture to `backend/evals/tests/conftest.py`**

Append:

```python
@pytest.fixture(scope="module")
def golden_novel(db):
    """Golden fixture novel ingested once per test module (mock LLM)."""
    from evals.harness import ingest_fixture

    novel_id = ingest_fixture(db, use_mock_llm=True)
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
```

(Everything cascades from `novels.id` — same cleanup as `reads/tests/seeding.py`.)

- [ ] **Step 3: Write the failing retrieval test**

Create `backend/evals/tests/test_retrieval_eval.py`:

```python
from __future__ import annotations

from evals.harness import run_retrieval_eval


def test_retrieval_recall_at_k(db, golden_novel):
    report = run_retrieval_eval(db, golden_novel, k=8)
    print(f"\nretrieval mean recall@{report['k']}: {report['mean_recall_at_k']:.2f}")
    for row in report["per_query"]:
        print(f"  {row['recall_at_k']:.1f}  {row['query']!r} -> {row['got']}")
    # Mock-mode floor: dense vectors are hash noise, so this measures the
    # BM25/keyword half. The golden queries carry distinctive proper nouns;
    # 0.5 failing means retrieval (not the harness) regressed.
    assert report["mean_recall_at_k"] >= 0.5, report["per_query"]


def test_retrieval_report_shape(db, golden_novel):
    report = run_retrieval_eval(db, golden_novel, k=8)
    assert len(report["per_query"]) == 10
    assert 0.0 <= report["mean_recall_at_k"] <= 1.0
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest evals/tests/test_retrieval_eval.py -v -s`
Expected first run: FAIL with `ModuleNotFoundError` if Step 1 was skipped, otherwise this is the first real execution — it may legitimately fail the 0.5 floor only if BM25 misses; inspect the printed per-query table. (Ingesting 10 chapters in mock mode takes seconds.)

- [ ] **Step 5: Write the extraction-fidelity tests**

Create `backend/evals/tests/test_extraction_fidelity.py`:

```python
from __future__ import annotations

import os

import pytest

from evals.harness import ingest_fixture, read_projections
from evals.loader import load_answer_key
from evals.scoring import score_entities, score_knowledge, score_possessions


def _fidelity_report(db, novel_id) -> dict:
    key = load_answer_key()
    got = read_projections(db, novel_id)
    report = {
        kind: score_entities(key["entities"][kind], got["entities"][kind])
        for kind in ("characters", "locations", "objects", "factions")
    }
    report["possessions"] = score_possessions(key["possessions"], got["possessions"])
    report["knowledge"] = score_knowledge(key["knowledge"], got["knowledge"])
    return report


def test_fidelity_harness_runs_in_mock_mode(db, golden_novel):
    """Plumbing test: the full ingest -> projections -> scoring path works.

    Mock extraction is not expected to match the answer key, so no
    thresholds here — only that every metric computes and is well-formed.
    """
    report = _fidelity_report(db, golden_novel)
    for kind in ("characters", "locations", "objects", "factions"):
        assert 0.0 <= report[kind]["recall"] <= 1.0
    assert 0.0 <= report["possessions"]["recall"] <= 1.0
    assert 0.0 <= report["knowledge"]["recall"] <= 1.0


@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="real-LLM extraction fidelity: set RUN_LLM_EVALS=1 (spends API credits)",
)
def test_extraction_fidelity_real_llm(db):
    novel_id = ingest_fixture(db, use_mock_llm=False)
    try:
        report = _fidelity_report(db, novel_id)
        print("\nextraction fidelity (real LLM):")
        for name, metrics in report.items():
            print(f"  {name}: {metrics}")
        assert report["characters"]["recall"] >= 0.75, report["characters"]
        assert report["locations"]["recall"] >= 0.75, report["locations"]
        assert report["objects"]["recall"] >= 1.0, report["objects"]
        assert report["possessions"]["recall"] >= 0.6, report["possessions"]
        assert report["knowledge"]["recall"] >= 0.5, report["knowledge"]
    finally:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
```

- [ ] **Step 6: Run the offline eval suite**

Run: `cd backend && .venv/bin/python -m pytest evals/ -v -s`
Expected: all pass, 1 skipped (`test_extraction_fidelity_real_llm`). The retrieval and fidelity metric tables print to the terminal — that satisfies the spec's "harness reports recall@k and critic precision/recall".

- [ ] **Step 7: Full suite**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all pass, 1 skipped.

- [ ] **Step 8: Update docs + commit**

`docs/reference.html`: expand the Task 3 `evals/` row into the final short description: golden fixture (10 chapters + `answer_key.yaml`), `pytest evals/` offline (mock LLM + Postgres), `RUN_LLM_EVALS=1` gates the real-LLM fidelity run. Add `RUN_LLM_EVALS` to the env-var table.

```bash
git add backend/evals docs/reference.html
git commit -m "feat: retrieval recall@k and env-gated extraction-fidelity evals"
```

---

### Task 6: Regenerate the docs site for the final architecture

The three HTML pages were kept incrementally true through Plans 1–2; this task does the final coherent pass now that the system is finished. **Read each file fully before editing** — these are hand-maintained, styled HTML pages (dark theme, shared header/nav); match their existing markup patterns and CSS classes exactly, and do not touch the shared styling.

**Files:**
- Modify: `docs/architecture.html`
- Modify: `docs/reference.html`
- Modify: `docs/state-of-the-system.html`

**Interfaces:**
- Consumes: the final code state after Tasks 1–5 (verify claims against code with grep before writing them — never document from memory of the spec).
- Produces: nothing code-facing. Task 7's audit greps assume these pages no longer mention deleted components.

- [ ] **Step 1: `docs/architecture.html` — final two-spine narrative**

Required content state (edit sections in place, preserving page structure):
1. The system overview presents exactly two spines over one Postgres database: the **write spine** (`analyze_chapter`: INGEST → EXTRACT (13 passes incl. typed `state_deltas`) → PERSIST (one transaction) → MATERIALIZE (`StateReplay` folds deltas; sole writer of `character_states` and edge tables) → CRITIQUE (5 checks persisted per chapter, non-blocking)) and the **read layer** (`backend/reads/`, every public fn cutoff-aware via `up_to_chapter`, SQL only there; API translates `cap`, MCP translates `writing_chapter - 1` and masks future-anchored fields).
2. A short **Eval harness** section: golden fixture novel, extraction fidelity (env-gated real-LLM), retrieval recall@k, critic precision/recall — with the `pytest evals/` invocation.
3. Zero mentions of: generation loop, planner, drafter, `generate-chapter`, revision loop. The style fingerprint is described only as ingest-time chapter metadata (`pipeline/style.py`).
4. The three-tier data model story stays (raw_text ground truth → immutable extraction incl. `state_deltas` → rebuildable projections).

Verification: `grep -icE "planner|drafter|generation loop|generate-chapter" docs/architecture.html` → `0`.

- [ ] **Step 2: `docs/reference.html` — exact tables**

Required content state:
1. CLI table lists exactly the surviving `novel-pipeline` subcommands (`init-db`, `create-novel`, `list-novels`, `process-chapter` — verify against `build_parser()` in `backend/pipeline/pipeline.py`).
2. Env-var table matches `backend/pipeline/config.py` field-for-field (no `DRAFT_*`/`GENERATION_*`; includes `RUN_LLM_EVALS` from Task 5).
3. Package-layout section reflects the final tree: `pipeline/` (incl. `style.py`, no `generation/`/`planner/`), `reads/`, `api/`, `mcp_server/`, `evals/`.
4. API table has no `/chapters/generate`; MCP tool table matches `backend/mcp_server/server.py`.
5. Schema section matches `backend/pipeline/db/schema.sql` (spot-check `state_deltas`, `critique_reports`, `critique_findings`, `schema_version` are documented; no `temporal_constraints`).

Verification: `grep -icE "generate-chapter|DRAFT_MODEL|GENERATION_MAX|chapters/generate" docs/reference.html` → `0`.

- [ ] **Step 3: `docs/state-of-the-system.html` — close out the redesign**

Required content state: the page's status/roadmap sections say the analyzer redesign (spec 2026-07-11) is **complete** as of 2026-07-13 — write spine (Plan 1), read layer + search + critique surfacing (Plan 2), generation teardown + eval harness + docs (Plan 3) — and the "known gaps / next steps" list contains only genuinely open items (carry forward any still-true entries; delete superseded roadmap items about generation or the old query modules). Update the last-updated date to today's date.

- [ ] **Step 4: Render-check and commit**

Open each page once to confirm it still renders (e.g. `open docs/architecture.html`, or at minimum `python3 -c "import html.parser"`-level eyeballing of the diff for unclosed tags). Run the three verification greps from Steps 1–2.

```bash
git add docs/*.html
git commit -m "docs: regenerate docs site for the final two-spine architecture + eval harness"
```

---

### Task 7: README rewrite, stale top-level file cleanup, success-criteria audit

**Files:**
- Modify: `README.md` (full rewrite)
- Delete: `architecture.md`, `architecture-recommendations.html`
- Move: `continuity-arch-deep-research.md` → `docs/research/continuity-arch-deep-research.md`
- **Do not touch `BLOG_OUTLINE.md`** (user scratch, untracked — global constraint).

**Interfaces:**
- Consumes: final repo state from Tasks 1–6.
- Produces: the shippable repo. Nothing depends on this task.

- [ ] **Step 1: Rewrite `README.md`**

Replace the whole file with (verify every command works before committing — run each one, or for destructive ones check `--help` exists):

```markdown
# Continuum

Continuity tracking and agent memory for novels. Chapters go in; a
queryable, spoiler-safe knowledge base comes out: characters, locations,
objects, factions, relationships, plot threads, commitments, who-knows-what,
and per-chapter continuity critiques — browsable in a React wiki and
exposed to writing agents over MCP.

Continuum **analyzes** prose; it does not write it. The intended writer is
an agent (e.g. Claude via the MCP server) that queries Continuum for
ground truth while drafting and saves finished chapters back.

## Architecture (two spines, one database)

- **Write spine** — `analyze_chapter` (`backend/pipeline/pipeline.py`):
  INGEST → EXTRACT (13 LLM passes, including typed `state_deltas`) →
  PERSIST (one transaction, immutable extraction tier) → MATERIALIZE
  (`StateReplay` folds deltas into projections; sole writer of
  `character_states` and the bitemporal edge tables) → CRITIQUE
  (5 deterministic continuity checks, persisted per chapter).
- **Read layer** — `backend/reads/`: every public function takes
  `up_to_chapter` (None = whole novel) so both the wiki and MCP serve
  spoiler-safe, point-in-time views. API routes and MCP tools contain no
  SQL — enforced by contract tests.

Chapter `raw_text` is ground truth: projections can always be deleted and
rebuilt, and novels can be re-processed after schema changes.

See `docs/architecture.html` for the full design and
`docs/reference.html` for schema/API/CLI/MCP reference.

## Setup

Backend (Python 3.11+, Postgres with pgvector):

```bash
cd backend
uv sync
createdb continuum
psql -d continuum -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
psql -d continuum -c 'CREATE EXTENSION IF NOT EXISTS vector;'
cp .env.example .env   # set DATABASE_URL, DEFAULT_MODEL, EMBEDDING_MODEL
uv run novel-pipeline init-db
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

API server: `cd backend && uv run novel-webapp`.
MCP server (for writing agents): `cd backend && uv run novel-mcp`.

## Usage

```bash
# create a novel and process a chapter
uv run novel-pipeline create-novel --title "My Novel"
uv run novel-pipeline process-chapter --novel-id <id> --number 1 --file ch1.txt
```

Or paste chapter text into the wiki's Process page. Writing agents use the
MCP tools (`save_chapter`, `check_continuity`, `search_story`, and the
cutoff-aware read tools that take `writing_chapter`).

## Tests and evals

```bash
cd backend && .venv/bin/python -m pytest        # full suite (needs Postgres)
cd backend && .venv/bin/python -m pytest evals/ # eval harness (offline)
RUN_LLM_EVALS=1 .venv/bin/python -m pytest evals/  # + real-LLM extraction fidelity
cd frontend && npm run build                    # frontend gate
```

The eval harness (`backend/evals/`) grades the system against a
hand-written golden novel: extraction fidelity, retrieval recall@k, and
critic precision/recall.
```

Adjust the setup section to match reality before committing: check whether `backend/.env.example` exists (if not, list the required env vars from `backend/pipeline/config.py` inline instead), and confirm the `createdb` database name matches what `.env`/`config.py` defaults expect.

- [ ] **Step 2: Stale file cleanup**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git rm architecture.md architecture-recommendations.html
mkdir -p docs/research
git mv continuity-arch-deep-research.md docs/research/continuity-arch-deep-research.md
```

Then `grep -rn "architecture.md\|architecture-recommendations\|continuity-arch-deep-research" README.md docs/*.html backend frontend/src --include="*.py" --include="*.ts" --include="*.tsx" 2>/dev/null` — fix any dangling references (point research references at `docs/research/`).

- [ ] **Step 3: Success-criteria audit (from the spec)**

Run each and record the result in the commit message body:

1. One write path: `grep -rniE "INSERT INTO (characters|events|relationships|knows_edges|possesses_edges|located_in_edges|character_states|plot_threads|commitments)" backend/api backend/mcp_server backend/reads --include="*.py"` → only `backend/api/admin.py` (explicit admin mutations) may match.
2. One read path: `cd backend && .venv/bin/python -m pytest reads/tests/test_contract.py -v` → passes (no SQL in surfaces).
3. Single writer of `character_states`: `grep -rn "INSERT INTO character_states\|UPDATE character_states" backend --include="*.py" | grep -v tests | grep -v state/` → nothing outside `pipeline/state/`.
4. No generation remnants: `grep -rn "pipeline.generation\|pipeline.planner\|generate-chapter\|generate_chapter\|submit_generation_job\|GenerateRequest" backend frontend/src docs/*.html README.md --include="*.py" --include="*.ts" --include="*.tsx" 2>/dev/null` → nothing (plan/spec files under `docs/superpowers/` are exempt — they're historical records).
5. Eval harness reports metrics: `cd backend && .venv/bin/python -m pytest evals/ -s` → passes and prints recall@k + precision/recall tables.

If any audit fails, fix the leak in this task (it's a one-line stray, not a redesign) and re-run.

- [ ] **Step 4: Final gates + commit**

```bash
cd backend && .venv/bin/python -m pytest
cd ../frontend && npm run build
cd .. && git add README.md docs/ && git status   # confirm BLOG_OUTLINE.md untouched/untracked
git commit -m "docs: rewrite README for the analyzer architecture; remove stale top-level docs"
```

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** step 8 (delete generation/planner + stale files) → Tasks 1, 2, 7; step 9 (eval harness: golden set, fidelity, recall@k, critic P/R) → Tasks 3, 4, 5; step 10 (docs site + README) → Tasks 6, 7; success criteria → Task 7 Step 3. `BLOG_OUTLINE.md` deletion is deliberately overridden (user scratch — global constraint).
- **Placeholder scan:** all code steps carry full code; docs tasks specify exact required content states + verification greps (the HTML pages are 1000–1900 lines and hand-styled — inlining full regenerated HTML would be less reliable than precise content requirements against files the implementer must read first).
- **Type consistency:** `compute_style_fingerprint` name/signature consistent across Tasks 2/6; `ingest_fixture`/`read_projections`/`run_retrieval_eval` signatures consistent between Task 5 interface block and code; scoring function names consistent between Tasks 3 and 5; seeded-dict keys (`char_a_id`, `char_a_eid`, `char_b_id`, `loc_a_id`, `loc_b_id`, `obj_id`, `canon_fact_id`) verified against `reads/tests/seeding.py`.
