# Agent Write Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `chapters` row mean "this passed continuity" — an agent-authored draft that FAILs the critic is refused before extraction and parked for human adjudication in the wiki.

**Architecture:** A gate in `pipeline/pipeline.py::analyze_chapter`, keyed on the existing `source == "agent"` discriminator, runs the cheap draft-claims critic path *before* the 13-pass EXTRACT. A FAIL, a critic exception, or a disabled critic refuses the write; a FAIL or exception also parks the draft in a new `draft_submissions` table. A human accepts (optionally with edits) or rejects from a Review queue in the React wiki; accepting re-runs `analyze_chapter` with an internal bypass and writes the findings through to `continuity_flags` so a blessed contradiction stays visible.

**Tech Stack:** Python 3.11 (`backend/.venv`), FastAPI, psycopg3 + Postgres (pgvector), pytest, React + TypeScript (Vite, react-router-dom), hand-maintained HTML docs in `docs/`.

**Spec:** `docs/superpowers/specs/2026-08-24-agent-write-gate-design.md`

## Global Constraints

- Work on branch `agent-write-gate` off `main`.
- Backend tests: **always** `cd backend && .venv/bin/python -m pytest`. The repo-root `.venv` collects DB-integration tests against an uninitialized DB — never use it. Tests need the dev Postgres from `backend/.env`.
- Frontend gate: `cd frontend && npm run build` after any frontend change.
- CLAUDE.md mandate: "Ensure the docs website is updated after every change." Tasks 1–7 make no docs claims; Task 8 is the full docs pass. Do not write final docs prose twice.
- **Never delete, edit, or commit `BLOG_OUTLINE.md`, `PALANTIR_ESSAY.md`, or `RESUME_TALKING_POINTS.md`.** They are the user's untracked scratch files.
- Never push to origin; commits stay local.
- Severity is an enum: `Severity.FAIL` / `Severity.WARN` / `Severity.INFO` (`pipeline/critic/types.py:8`). `CritiqueReport.passed` is `len(self.fails) == 0` — WARNs never block.
- **Name trap:** there are two different `critique_chapter` functions. `pipeline/critic/service.py::critique_chapter(db, novel_id=…, chapter_number=…, chapter_id=…, raw_text=…, extracted=…)` is the post-commit one used at `pipeline.py:456`. `pipeline/critic/runner.py::critique_chapter(draft, db)` takes a `DraftChapter`. This plan uses **neither** directly — the gate calls `ContinuityCritic(db).critique(draft)`, matching `mcp_server/queries.py:50`.
- Schema changes go in `pipeline/db/schema.sql` as idempotent `IF NOT EXISTS` statements. There is no migration tool: `init_db` (`pipeline.py:88`) re-runs the whole file, so existing databases are upgraded by re-running `uv run novel-pipeline init-db`.
- **`DBClient` commit semantics — read before writing any SQL.** `execute()` and `execute_many()` commit. `fetchone()`, `fetchall()`, and `fetchval()` default to `commit=False`, and that path calls `conn.rollback()` (`pipeline/db/client.py:39`) — so an `INSERT ... RETURNING id` issued through `fetchval` **silently discards the row and still returns the id**. For a write that returns a value, either pass `commit=True` explicitly or use `with db.transaction() as cur:`, which commits on success. Use `transaction()` whenever two statements must land together.

---

### Task 1: `draft_submissions` schema + read layer

Creates the parking table and the two queue reads. Nothing writes to it yet.

**Files:**
- Modify: `backend/pipeline/db/schema.sql` (append before the `schema_version` block at line 421)
- Modify: `backend/pipeline/pipeline.py:38` (`SCHEMA_VERSION = 1` → `2`)
- Create: `backend/reads/drafts.py`
- Modify: `backend/reads/tests/test_contract.py` (add `CUTOFF_EXEMPT` entries)
- Create: `backend/reads/tests/test_drafts.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: table `draft_submissions`; `reads.drafts.list_submissions(db, novel_id, status="pending") -> list[dict]`, `reads.drafts.get_submission(db, submission_id) -> dict | None`, and `reads.drafts.count_pending(db, novel_id) -> int`. The first two return dicts with keys `id, novel_id, chapter_number, title, raw_text, status, findings, submitted_at, resolved_at, resolution_note`. Tasks 2, 5, and 6 depend on these.

- [ ] **Step 1: Write the failing tests**

Create `backend/reads/tests/test_drafts.py`:

```python
"""Queue reads over draft_submissions."""

from __future__ import annotations

import json

from pipeline.db.client import DBClient
from reads import drafts as drafts_reads


def _park(db: DBClient, novel_id: str, number: int, status: str = "pending") -> str:
    return str(
        db.fetchval(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (novel_id, number, f"Ch {number}", "draft text", status,
             json.dumps({"fails": [], "warns": []})),
            commit=True,
        )
    )


def test_list_submissions_returns_only_pending_by_default(db, seed_novel):
    seeded = seed_novel(db)
    novel_id = seeded["novel_id"]
    pending_id = _park(db, novel_id, 90)
    _park(db, novel_id, 91, status="rejected")

    rows = drafts_reads.list_submissions(db, novel_id)

    assert [r["id"] for r in rows] == [pending_id]
    assert rows[0]["chapter_number"] == 90
    assert rows[0]["findings"] == {"fails": [], "warns": []}


def test_list_submissions_can_filter_to_all(db, seed_novel):
    seeded = seed_novel(db)
    novel_id = seeded["novel_id"]
    _park(db, novel_id, 90)
    _park(db, novel_id, 91, status="rejected")

    rows = drafts_reads.list_submissions(db, novel_id, status="all")

    assert {r["chapter_number"] for r in rows} == {90, 91}


def test_get_submission_returns_row_and_none_for_missing(db, seed_novel):
    seeded = seed_novel(db)
    submission_id = _park(db, seeded["novel_id"], 90)

    row = drafts_reads.get_submission(db, submission_id)

    assert row is not None
    assert row["raw_text"] == "draft text"
    assert drafts_reads.get_submission(
        db, "00000000-0000-0000-0000-000000000000"
    ) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest reads/tests/test_drafts.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'reads.drafts'`.

- [ ] **Step 3: Add the schema**

In `backend/pipeline/db/schema.sql`, insert immediately **before** the `-- ---- Schema version (single row, stamped by init-db) ----` comment at line 420:

```sql
-- ---- Agent draft submissions parked for human review ----
CREATE TABLE IF NOT EXISTS draft_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    title TEXT,
    raw_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','accepted','rejected')),
    findings JSONB NOT NULL,
    submitted_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_draft_submissions_pending
    ON draft_submissions(novel_id, chapter_number) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_draft_submissions_novel
    ON draft_submissions(novel_id, submitted_at DESC);
```

- [ ] **Step 4: Bump the schema version**

In `backend/pipeline/pipeline.py:38`, change `SCHEMA_VERSION = 1` to `SCHEMA_VERSION = 2`.

- [ ] **Step 5: Apply the schema to the dev database**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pipeline.pipeline init-db
```

Expected: no error. Verify the table landed:

```bash
psql -d novel_wiki -c '\d draft_submissions'
```

- [ ] **Step 6: Write `reads/drafts.py`**

Create `backend/reads/drafts.py`:

```python
"""reads.drafts: the agent-draft review queue.

These are queue reads, not story reads: a parked draft is not canon and has
no point-in-time semantics, so unlike the rest of `reads/` these functions
take no `up_to_chapter` (see CUTOFF_EXEMPT in reads/tests/test_contract.py).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

_COLUMNS = """
    id, novel_id, chapter_number, title, raw_text, status, findings,
    submitted_at, resolved_at, resolution_note
"""


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    out["id"] = str(out["id"])
    out["novel_id"] = str(out["novel_id"])
    return out


def list_submissions(
    db: Any, novel_id: UUID | str, status: str = "pending"
) -> list[dict[str, Any]]:
    """Submissions for a novel, newest first. status='all' returns every row."""
    if status == "all":
        rows = db.fetchall(
            f"SELECT {_COLUMNS} FROM draft_submissions WHERE novel_id = %s"
            " ORDER BY submitted_at DESC",
            (str(novel_id),),
            dict_rows=True,
        )
    else:
        rows = db.fetchall(
            f"SELECT {_COLUMNS} FROM draft_submissions"
            " WHERE novel_id = %s AND status = %s ORDER BY submitted_at DESC",
            (str(novel_id), status),
            dict_rows=True,
        )
    return [_row(r) for r in rows]


def get_submission(db: Any, submission_id: UUID | str) -> dict[str, Any] | None:
    row = db.fetchone(
        f"SELECT {_COLUMNS} FROM draft_submissions WHERE id = %s",
        (str(submission_id),),
        dict_rows=True,
    )
    return _row(row) if row else None


def count_pending(db: Any, novel_id: UUID | str) -> int:
    """Badge count for the wiki nav."""
    return int(
        db.fetchval(
            "SELECT COUNT(*) FROM draft_submissions"
            " WHERE novel_id = %s AND status = 'pending'",
            (str(novel_id),),
        )
        or 0
    )
```

- [ ] **Step 7: Exempt the queue reads from the cutoff contract**

`reads/tests/test_contract.py` asserts every public `reads` function takes `up_to_chapter`. Add three entries to the `CUTOFF_EXEMPT` set (around line 22), with the others:

```python
    # Review-queue reads: a parked draft is not canon and has no
    # point-in-time semantics, so there is nothing to cut off.
    ("reads.drafts", "list_submissions"),
    ("reads.drafts", "get_submission"),
    ("reads.drafts", "count_pending"),
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest reads/tests/test_drafts.py reads/tests/test_contract.py -v
```

Expected: PASS (all four tests).

- [ ] **Step 9: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/pipeline/db/schema.sql backend/pipeline/pipeline.py \
        backend/reads/drafts.py backend/reads/tests/test_drafts.py \
        backend/reads/tests/test_contract.py
git commit -m "feat: add draft_submissions table and review-queue reads"
```

---

### Task 2: `pipeline/gate.py` — the gate, in isolation

The gate function: run draft claims + critic on unsaved text, park on anything but a clean pass, never raise. No pipeline wiring yet.

**Files:**
- Create: `backend/pipeline/gate.py`
- Create: `backend/pipeline/tests/test_gate.py`

**Interfaces:**
- Consumes: `draft_submissions` (Task 1). `pipeline.critic.adapter.extract_draft_claims(text, use_mock=…)`, `pipeline.critic.adapter.build_draft_chapter(db, novel_id=…, chapter_number=…, text=…, raw_claims=…, planned_thread_ids=…, planned_commitment_ids=…)`, `pipeline.critic.runner.ContinuityCritic(db).critique(draft) -> CritiqueReport`.
- Produces: `pipeline.gate.GateVerdict` (frozen dataclass: `passed: bool`, `submission_id: str | None = None`, `fails: list[dict] | None = None`, `warns: list[dict] | None = None`, `reason: str | None = None`) and `pipeline.gate.gate_agent_draft(db, *, novel_id, chapter_number, title, raw_text, use_mock_llm) -> GateVerdict`. `reason` is one of `"fail"`, `"critic_error"`, `"critic_disabled"`, or `None` when passed. `fails`/`warns` are `Optional` on the dataclass, so consumers must use `verdict.fails or []` — Task 3 does. Tasks 3 and 4 depend on these names.

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/tests/test_gate.py`:

```python
"""The agent write gate: verdicts and fail-closed behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pipeline import gate as gate_mod
from pipeline.critic.types import CritiqueReport, Finding, Severity
from reads import drafts as drafts_reads


def _report(*findings: Finding) -> CritiqueReport:
    return CritiqueReport(novel_id="n", chapter_number=1, findings=list(findings))


def _fail() -> Finding:
    return Finding(
        check="knowledge_state",
        severity=Severity.FAIL,
        message="Elara knows something she shouldn't",
        quote="She already knew.",
    )


def _warn() -> Finding:
    return Finding(check="thread_coverage", severity=Severity.WARN, message="cold thread")


@pytest.fixture
def stub_critic(monkeypatch):
    """Replace claim extraction and the critic with controllable stubs."""

    def _install(report=None, raises: Exception | None = None):
        monkeypatch.setattr(
            gate_mod, "extract_draft_claims", lambda text, use_mock=None: {"mentions": []}
        )
        monkeypatch.setattr(
            gate_mod, "build_draft_chapter", lambda db, **kw: object()
        )

        class _Critic:
            def __init__(self, db):
                pass

            def critique(self, draft):
                if raises is not None:
                    raise raises
                return report

        monkeypatch.setattr(gate_mod, "ContinuityCritic", _Critic)

    return _install


def test_clean_report_passes_and_parks_nothing(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report())

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title="Ch 90",
        raw_text="text", use_mock_llm=True,
    )

    assert verdict.passed is True
    assert verdict.reason is None
    assert verdict.submission_id is None
    assert drafts_reads.list_submissions(db, novel_id) == []


def test_warns_alone_do_not_block(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report(_warn()))

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="text", use_mock_llm=True,
    )

    assert verdict.passed is True
    assert len(verdict.warns) == 1


def test_fail_parks_the_draft_with_findings(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report(_fail(), _warn()))

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title="Ch 90",
        raw_text="the draft", use_mock_llm=True,
    )

    assert verdict.passed is False
    assert verdict.reason == "fail"
    assert verdict.submission_id is not None

    parked = drafts_reads.get_submission(db, verdict.submission_id)
    assert parked["status"] == "pending"
    assert parked["raw_text"] == "the draft"
    assert parked["chapter_number"] == 90
    assert parked["findings"]["fails"][0]["quote"] == "She already knew."
    assert len(parked["findings"]["warns"]) == 1


def test_critic_exception_parks_rather_than_passing(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(raises=RuntimeError("draft_claims: extraction failed"))

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="the draft", use_mock_llm=True,
    )

    assert verdict.passed is False
    assert verdict.reason == "critic_error"
    parked = drafts_reads.get_submission(db, verdict.submission_id)
    assert parked["status"] == "pending"
    assert "extraction failed" in parked["findings"]["error"]


def test_disabled_critic_refuses_without_parking(db, seed_novel, stub_critic, monkeypatch):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report())
    # `Settings` is a frozen dataclass (pipeline/config.py:25), so the
    # attribute cannot be set in place — setattr raises FrozenInstanceError.
    # Rebind gate.py's module-level `settings` to a copy with the flag off.
    monkeypatch.setattr(
        gate_mod, "settings", replace(gate_mod.settings, critic_enabled=False)
    )

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="text", use_mock_llm=True,
    )

    assert verdict.passed is False
    assert verdict.reason == "critic_disabled"
    assert verdict.submission_id is None
    assert drafts_reads.list_submissions(db, novel_id) == []


def test_resubmission_supersedes_the_pending_row(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report(_fail()))

    first = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="v1", use_mock_llm=True,
    )
    second = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="v2", use_mock_llm=True,
    )

    superseded = drafts_reads.get_submission(db, first.submission_id)
    assert superseded["status"] == "rejected"
    assert superseded["resolution_note"] == "superseded by resubmission"

    pending = drafts_reads.list_submissions(db, novel_id)
    assert [r["id"] for r in pending] == [second.submission_id]
```

Add the shared fixtures. Create `backend/pipeline/tests/conftest.py`:

```python
"""Postgres fixtures for pipeline tests (mirrors reads/tests/conftest.py)."""

from __future__ import annotations

from typing import Any

import pytest

from pipeline.db.client import DBClient
from reads.tests import seeding


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seed_novel(db: DBClient):
    seeded_novel_ids: list[str] = []

    def _seed_novel(db_client: DBClient) -> dict[str, Any]:
        seeded = seeding.seed_novel(db_client)
        seeded_novel_ids.append(seeded["novel_id"])
        return seeded

    yield _seed_novel

    for novel_id in seeded_novel_ids:
        seeding.cleanup(db, novel_id)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest pipeline/tests/test_gate.py -v
```

Expected: FAIL — `ImportError: cannot import name 'gate' from 'pipeline'`.

- [ ] **Step 3: Write `pipeline/gate.py`**

Create `backend/pipeline/gate.py`:

```python
"""The agent write gate.

Agent-authored drafts are critiqued *before* extraction: a FAIL, a critic
exception, or a disabled critic refuses the write. Refusal is a return value,
never an exception — `gate_agent_draft` is the only thing standing between an
agent and canon, so a raise inside it must not be able to look like an error
the caller decides to ignore.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pipeline.config import settings
from pipeline.critic.adapter import build_draft_chapter, extract_draft_claims
from pipeline.critic.runner import ContinuityCritic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    submission_id: str | None = None
    fails: list[dict[str, Any]] | None = None
    warns: list[dict[str, Any]] | None = None
    reason: str | None = None  # 'fail' | 'critic_error' | 'critic_disabled'


def _finding_dict(finding: Any) -> dict[str, Any]:
    return {
        "check": finding.check,
        "severity": str(finding.severity.value),
        "message": finding.message,
        "quote": finding.quote,
        "suggested_fix": finding.suggested_fix,
        "context": finding.context,
    }


def _park(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    title: str | None,
    raw_text: str,
    findings: dict[str, Any],
) -> str:
    """Supersede any pending row for this chapter, then park a new one.

    Both statements run in ONE transaction: superseding the old row without
    parking the new one would drop the author's draft on the floor.

    Note `DBClient.fetchval` defaults to commit=False and ROLLS BACK, so an
    `INSERT ... RETURNING` through it silently discards the row. `transaction()`
    commits on success, which is why the insert goes through its cursor.

    Not wrapped in try/except: refusing an agent write without recording the
    draft would lose the author's work, so a failed park is a hard error.
    """
    with db.transaction() as cur:
        cur.execute(
            """
            UPDATE draft_submissions
               SET status = 'rejected',
                   resolved_at = now(),
                   resolution_note = 'superseded by resubmission'
             WHERE novel_id = %s AND chapter_number = %s AND status = 'pending'
            """,
            (str(novel_id), chapter_number),
        )
        cur.execute(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, %s, %s, 'pending', %s::jsonb)
            RETURNING id
            """,
            (str(novel_id), chapter_number, title, raw_text, json.dumps(findings)),
        )
        return str(cur.fetchone()[0])


def gate_agent_draft(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    title: str | None,
    raw_text: str,
    use_mock_llm: bool | None,
) -> GateVerdict:
    """Critique an unsaved agent draft. Park it unless it passes cleanly."""
    if not settings.critic_enabled:
        # A disabled critic is an outage, not a free pass. Nothing is parked:
        # the agent still holds the text and can resubmit once it is back.
        logger.error(
            "refusing agent write for novel %s ch %s: critic is disabled",
            novel_id, chapter_number,
        )
        return GateVerdict(
            passed=False, fails=[], warns=[], reason="critic_disabled"
        )

    try:
        raw_claims = extract_draft_claims(raw_text, use_mock=use_mock_llm)
        draft = build_draft_chapter(
            db,
            novel_id=str(novel_id),
            chapter_number=chapter_number,
            text=raw_text,
            raw_claims=raw_claims,
            planned_thread_ids=[],
            planned_commitment_ids=[],
        )
        report = ContinuityCritic(db).critique(draft)
        # Converting the report stays INSIDE the try: a critic that returns a
        # malformed CritiqueReport is a critic error, and letting that raise
        # here would drop the draft with no record at all — a worse outcome
        # than the one sanctioned exception (a failed park).
        fails = [_finding_dict(f) for f in report.fails]
        warns = [_finding_dict(f) for f in report.warns]
        passed = report.passed
    except Exception as exc:
        logger.exception(
            "critic errored on agent draft for novel %s ch %s; parking",
            novel_id, chapter_number,
        )
        submission_id = _park(
            db,
            novel_id=novel_id,
            chapter_number=chapter_number,
            title=title,
            raw_text=raw_text,
            findings={"fails": [], "warns": [], "error": f"{type(exc).__name__}: {exc}"},
        )
        return GateVerdict(
            passed=False, submission_id=submission_id, fails=[], warns=[],
            reason="critic_error",
        )

    # _park stays OUTSIDE the try: a failed park must still raise.
    if passed:
        return GateVerdict(passed=True, fails=fails, warns=warns)

    submission_id = _park(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        title=title,
        raw_text=raw_text,
        findings={"fails": fails, "warns": warns},
    )
    return GateVerdict(
        passed=False, submission_id=submission_id, fails=fails, warns=warns,
        reason="fail",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest pipeline/tests/test_gate.py -v
```

Expected: PASS (six tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/pipeline/gate.py backend/pipeline/tests/test_gate.py \
        backend/pipeline/tests/conftest.py
git commit -m "feat: add the agent draft gate with fail-closed verdicts"
```

---

### Task 3: Wire the gate into `analyze_chapter`

This is the task that makes the bound real. After it, an agent-source FAIL cannot produce a `chapters` row.

**Files:**
- Modify: `backend/pipeline/pipeline.py` (import at line ~11; gate block after the duplicate check at lines 278–287; `_gate_bypass` parameter at line ~273)
- Create: `backend/pipeline/tests/test_gate_integration.py`

**Interfaces:**
- Consumes: `pipeline.gate.gate_agent_draft`, `pipeline.gate.GateVerdict` (Task 2).
- Produces: `analyze_chapter` gains keyword-only `_gate_bypass: bool = False`. When the gate refuses, `analyze_chapter` returns `{"ingested": False, "status": "pending_review", "submission_id": str | None, "reason": str, "fails": list, "warns": list}` instead of its normal result dict. The normal (ingested) result dict is unchanged and does **not** grow an `ingested` key. Tasks 4 and 5 depend on this.

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/tests/test_gate_integration.py`:

```python
"""The gate as enforced by analyze_chapter — i.e. the bypass attempts."""

from __future__ import annotations

import pytest

from pipeline import pipeline as pipeline_mod
from pipeline.gate import GateVerdict


@pytest.fixture
def refusing_gate(monkeypatch):
    """Force the gate to refuse, and record whether extraction ever ran."""
    calls = {"extract": 0}

    def _fake_gate(db, **kw):
        return GateVerdict(
            passed=False, submission_id="11111111-1111-1111-1111-111111111111",
            fails=[{"check": "knowledge_state", "message": "nope"}], warns=[],
            reason="fail",
        )

    monkeypatch.setattr(pipeline_mod, "gate_agent_draft", _fake_gate)

    # Extraction runs as ChapterExtractor(use_mock=…).extract_chapter(…)
    # (pipeline.py:22 import, :307 call) — there is no module-level
    # extract_chapter to patch. Subclass so the human-source and bypass tests
    # still get real extraction behavior.
    original_cls = pipeline_mod.ChapterExtractor

    class _CountingExtractor(original_cls):  # type: ignore[misc,valid-type]
        def extract_chapter(self, *a, **kw):
            calls["extract"] += 1
            return super().extract_chapter(*a, **kw)

    monkeypatch.setattr(pipeline_mod, "ChapterExtractor", _CountingExtractor)
    return calls


@pytest.fixture
def passing_gate(monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "gate_agent_draft",
        lambda db, **kw: GateVerdict(passed=True, fails=[], warns=[]),
    )


def _chapter_count(db, novel_id: str, number: int) -> int:
    return int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, number),
        )
    )


def test_agent_fail_writes_no_chapter_row(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent",
    )

    assert result["ingested"] is False
    assert result["status"] == "pending_review"
    assert result["reason"] == "fail"
    assert _chapter_count(db, novel_id, 90) == 0


def test_agent_fail_never_runs_extraction(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent",
    )

    assert refusing_gate["extract"] == 0


def test_run_critic_false_does_not_bypass_the_gate(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent", run_critic=False,
    )

    assert result["ingested"] is False
    assert _chapter_count(db, novel_id, 90) == 0


def test_replace_true_does_not_bypass_the_gate(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent", replace=True,
    )

    assert result["ingested"] is False
    assert _chapter_count(db, novel_id, 90) == 0


def test_human_source_is_not_gated(db, seed_novel, refusing_gate):
    """A refusing gate must not affect human writes — it should never be called."""
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="Elara walked into the hall.",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="human",
    )

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1


def test_gate_bypass_ingests_a_would_fail_draft(db, seed_novel, refusing_gate):
    """The human-override path used by accept_submission."""
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="Elara walked into the hall.",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent", _gate_bypass=True,
    )

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1


def test_agent_pass_ingests_normally(db, seed_novel, passing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="Elara walked into the hall.",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent",
    )

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1
```

- [ ] **Step 2: Confirm the extraction patch target**

The `refusing_gate` fixture subclasses `pipeline_mod.ChapterExtractor`. Confirm that is still how `analyze_chapter` reaches extraction:

```bash
cd backend
grep -n "ChapterExtractor" pipeline/pipeline.py
```

Expected: an import at the top and an instantiation inside `analyze_chapter` (`extractor = ChapterExtractor(use_mock=use_mock_llm)` followed by `extractor.extract_chapter(...)`). If extraction has moved behind a different symbol, update the fixture to wrap whatever `analyze_chapter` actually calls.

Then verify the counter is actually wired — a patch target that never fires makes `test_agent_fail_never_runs_extraction` pass vacuously:

```bash
.venv/bin/python -m pytest pipeline/tests/test_gate_integration.py::test_human_source_is_not_gated -v
```

That test takes the human path, which DOES extract, so it only passes if the subclass is really in the call path. Do not proceed while it fails.

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest pipeline/tests/test_gate_integration.py -v
```

Expected: FAIL — `AttributeError: module 'pipeline.pipeline' has no attribute 'gate_agent_draft'`.

- [ ] **Step 4: Import the gate in `pipeline.py`**

Add to the imports near `pipeline/pipeline.py:11` (beside `from pipeline.critic.service import critique_chapter`):

```python
from pipeline.gate import gate_agent_draft
```

- [ ] **Step 5: Add the `_gate_bypass` parameter**

In the `analyze_chapter` signature (`pipeline/pipeline.py:260-274`), add a final keyword-only parameter after `run_critic`:

```python
    run_critic: bool | None = None,
    _gate_bypass: bool = False,
) -> dict[str, Any]:
```

Leading underscore and no exposure through MCP, the CLI, or any HTTP request body: only `accept_submission` (Task 5) sets it.

- [ ] **Step 6: Insert the gate block**

In `analyze_chapter`, immediately after the duplicate-chapter check (the `if existing is not None and not replace: raise ValueError(...)` block ending at line 287) and **before** the `custom_entity_types` load at line 289:

```python
        # ---- phase 0: GATE (agent writes only) ----
        # A chapter row means it passed continuity. Runs before EXTRACT so a
        # refused draft costs one claim-extraction call instead of 13 passes,
        # and so contradictory material never reaches the extraction tier.
        # Independent of `replace`: re-processing is exactly when a
        # contradiction is most likely.
        if source == "agent" and not _gate_bypass:
            verdict = gate_agent_draft(
                client,
                novel_id=novel_id,
                chapter_number=chapter_number,
                title=chapter_title,
                raw_text=raw_text,
                use_mock_llm=use_mock_llm,
            )
            if not verdict.passed:
                return {
                    "ingested": False,
                    "status": "pending_review",
                    "submission_id": verdict.submission_id,
                    "reason": verdict.reason,
                    "fails": verdict.fails or [],
                    "warns": verdict.warns or [],
                }
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest pipeline/tests/ -v
```

Expected: PASS (all gate and gate-integration tests).

- [ ] **Step 8: Run the full backend suite for regressions**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest
```

Expected: PASS, except possibly MCP tests asserting `ingested: True` — those are fixed in Task 4. Note any failures and confirm they are only in `mcp_server/tests/`.

- [ ] **Step 9: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/pipeline/pipeline.py backend/pipeline/tests/test_gate_integration.py
git commit -m "feat: enforce the continuity gate on agent writes in analyze_chapter"
```

---

### Task 4: MCP contract — `save_chapter` returns a structured refusal

`save_chapter` currently hardcodes `"ingested": True`. It must pass the refusal through instead.

**Files:**
- Modify: `backend/mcp_server/queries.py:61-94` (`save_chapter`)
- Modify: `backend/mcp_server/server.py:198-211` (both docstrings)
- Modify: `backend/mcp_server/tests/test_queries.py` (existing `ingested: True` assertions)

**Interfaces:**
- Consumes: the refusal dict from `analyze_chapter` (Task 3).
- Produces: `mcp_server.queries.save_chapter` returns either `{"ingested": True, "chapter_id", "materialized", "critique"}` (unchanged) or the refusal `{"ingested": False, "status": "pending_review", "submission_id", "reason", "fails", "warns"}`.

- [ ] **Step 1: Write the failing test**

Add to `backend/mcp_server/tests/test_queries.py`:

```python
def test_save_chapter_passes_refusal_through(monkeypatch):
    """A gate refusal reaches the agent as ingested:False, not a raised error."""
    from mcp_server import queries as queries_mod

    refusal = {
        "ingested": False,
        "status": "pending_review",
        "submission_id": "11111111-1111-1111-1111-111111111111",
        "reason": "fail",
        "fails": [{"check": "knowledge_state", "message": "nope"}],
        "warns": [],
    }
    monkeypatch.setattr(queries_mod, "analyze_chapter", lambda **kw: refusal)

    result = queries_mod.save_chapter("n", 90, "draft text")

    assert result["ingested"] is False
    assert result["status"] == "pending_review"
    assert result["submission_id"] == "11111111-1111-1111-1111-111111111111"
    assert result["reason"] == "fail"
    assert result["fails"][0]["check"] == "knowledge_state"
    assert "chapter_id" not in result


def test_save_chapter_still_reports_success_on_ingest(monkeypatch):
    from mcp_server import queries as queries_mod

    monkeypatch.setattr(
        queries_mod, "analyze_chapter",
        lambda **kw: {"chapter_id": "abc", "materialized": True, "critique": {"passed": True}},
    )

    result = queries_mod.save_chapter("n", 90, "draft text")

    assert result["ingested"] is True
    assert result["chapter_id"] == "abc"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest mcp_server/tests/test_queries.py -k save_chapter -v
```

Expected: `test_save_chapter_passes_refusal_through` FAILs — the current code returns `ingested: True` and a `chapter_id` of `"None"`.

- [ ] **Step 3: Pass the refusal through**

Replace the return block of `save_chapter` in `backend/mcp_server/queries.py` (lines 87–94):

```python
    # A gate refusal is a normal outcome, not an error: pass it through
    # verbatim so the agent gets the findings and can revise. The presence of
    # an "ingested" key means analyze_chapter refused before writing.
    if "ingested" in outcome:
        return outcome
    # materialized/critique are the caller's only signal that the best-effort
    # MATERIALIZE/CRITIQUE phases failed (see analyze_chapter's result dict).
    return {
        "ingested": True,
        "chapter_id": str(outcome.get("chapter_id")),
        "materialized": bool(outcome.get("materialized")),
        "critique": outcome.get("critique"),
    }
```

- [ ] **Step 4: Update the MCP tool docstrings**

In `backend/mcp_server/server.py`, replace the `check_continuity` docstring (lines 199–201):

```python
    """Run the continuity critic on a draft WITHOUT saving it. Returns
    passed/fails/warns with quotes and suggested fixes. Use this while
    revising: save_chapter runs the same checks and will refuse a draft that
    fails them."""
```

And the `save_chapter` docstring (lines 209–210):

```python
    """Ingest a finished draft into the novel as a generated chapter.

    Drafts are gated: the continuity critic runs BEFORE extraction, and a
    draft with any FAIL finding is refused and parked for human review.
    A refusal returns {"ingested": false, "status": "pending_review",
    "submission_id", "reason", "fails", "warns"} — revise against `fails` and
    resubmit; a resubmission supersedes the parked draft. Warnings do not
    block. Refuses to overwrite an existing chapter."""
```

- [ ] **Step 5: Fix any existing assertions on the old contract**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
grep -rn "ingested" mcp_server/tests/ api/tests/
```

Update every assertion that assumes `ingested` is always `True` so it reflects the new contract. Do not delete tests — adjust their expectations.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest mcp_server/ -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/mcp_server/queries.py backend/mcp_server/server.py \
        backend/mcp_server/tests/test_queries.py
git commit -m "feat: return gate refusals through the save_chapter MCP contract"
```

---

### Task 5: Resolution writes — accept and reject

The human side, as functions. No HTTP yet.

**Files:**
- Create: `backend/pipeline/drafts.py`
- Create: `backend/pipeline/tests/test_drafts_resolution.py`

**Interfaces:**
- Consumes: `reads.drafts.get_submission` (Task 1), `analyze_chapter(..., _gate_bypass=True)` (Task 3).
- Produces: `pipeline.drafts.accept_submission(db, submission_id, *, note=None, edited_text=None) -> dict` returning `{"accepted": True, "chapter_id": str, "flags_written": int}`; `pipeline.drafts.reject_submission(db, submission_id, *, note=None) -> dict` returning `{"rejected": True, "submission_id": str}`. Both raise `ValueError` for a missing or already-resolved submission. Task 6 depends on these.

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/tests/test_drafts_resolution.py`:

```python
"""Accepting and rejecting parked drafts."""

from __future__ import annotations

import json

import pytest

from pipeline import drafts as drafts_mod
from reads import drafts as drafts_reads


def _park(db, novel_id: str, number: int = 90, text: str = "Elara walked in.") -> str:
    findings = {
        "fails": [{"check": "knowledge_state", "severity": "FAIL",
                   "message": "Elara knows something she shouldn't",
                   "quote": "She already knew.", "suggested_fix": None, "context": {}}],
        "warns": [],
    }
    return str(
        db.fetchval(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, 'Ch 90', %s, 'pending', %s::jsonb)
            RETURNING id
            """,
            (novel_id, number, text, json.dumps(findings)),
            commit=True,
        )
    )


def test_accept_ingests_the_draft_and_marks_it_accepted(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.accept_submission(db, submission_id, note="deliberate retcon")

    assert result["accepted"] is True
    assert result["chapter_id"]

    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "accepted"
    assert row["resolution_note"] == "deliberate retcon"
    assert row["resolved_at"] is not None

    assert int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = 90",
            (novel_id,),
        )
    ) == 1


def test_accept_writes_findings_through_to_continuity_flags(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.accept_submission(db, submission_id, note=None)

    assert result["flags_written"] == 1
    flags = db.fetchall(
        "SELECT description, flag_type FROM continuity_flags WHERE chapter_id = %s",
        (result["chapter_id"],),
        dict_rows=True,
    )
    assert len(flags) == 1
    assert flags[0]["flag_type"] == "accepted_override:knowledge_state"
    assert "Elara knows something she shouldn't" in flags[0]["description"]


def test_accept_with_edits_ingests_the_edited_text(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id, text="original text")

    result = drafts_mod.accept_submission(
        db, submission_id, note="fixed the timeline", edited_text="Kael walked in."
    )

    stored = db.fetchval(
        "SELECT raw_text FROM chapters WHERE id = %s", (result["chapter_id"],)
    )
    assert stored == "Kael walked in."
    row = drafts_reads.get_submission(db, submission_id)
    assert row["raw_text"] == "Kael walked in."
    assert "edited" in row["resolution_note"]


def test_reject_marks_rejected_and_writes_no_chapter(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.reject_submission(db, submission_id, note="off voice")

    assert result["rejected"] is True
    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "rejected"
    assert row["resolution_note"] == "off voice"
    assert int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = 90",
            (novel_id,),
        )
    ) == 0


def test_resolving_a_missing_or_resolved_submission_raises(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    drafts_mod.reject_submission(db, submission_id, note="no")

    with pytest.raises(ValueError, match="already resolved"):
        drafts_mod.reject_submission(db, submission_id, note="again")
    with pytest.raises(ValueError, match="not found"):
        drafts_mod.accept_submission(
            db, "00000000-0000-0000-0000-000000000000", note=None
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest pipeline/tests/test_drafts_resolution.py -v
```

Expected: FAIL — `ImportError: cannot import name 'drafts' from 'pipeline'`.

- [ ] **Step 3: Write `pipeline/drafts.py`**

Create `backend/pipeline/drafts.py`:

```python
"""Human adjudication of parked agent drafts.

`accept_submission` is the only caller allowed to set `_gate_bypass` on
analyze_chapter. Accepting does not erase the findings that blocked the draft:
they are written through to `continuity_flags` so a human-blessed
contradiction stays visible in the wiki instead of being silently absolved.
"""

from __future__ import annotations

from typing import Any

from pipeline.config import settings
from pipeline.pipeline import analyze_chapter
from reads.drafts import get_submission


def _load_pending(db: Any, submission_id: str) -> dict[str, Any]:
    row = get_submission(db, submission_id)
    if row is None:
        raise ValueError(f"draft submission {submission_id} not found")
    if row["status"] != "pending":
        raise ValueError(
            f"draft submission {submission_id} is already resolved "
            f"(status={row['status']})"
        )
    return row


def _write_findings_through(db: Any, chapter_id: str, findings: dict[str, Any]) -> int:
    """Record the blocking findings against the accepted chapter."""
    rows = [
        (
            chapter_id,
            (f"[accepted despite continuity FAIL] {f.get('message', '')}"
             + (f" — quote: {f['quote']}" if f.get("quote") else "")),
            f"accepted_override:{f.get('check', 'unknown')}",
        )
        for f in findings.get("fails", [])
    ]
    if not rows:
        return 0
    db.execute_many(
        "INSERT INTO continuity_flags (chapter_id, description, flag_type)"
        " VALUES (%s, %s, %s)",
        rows,
    )
    return len(rows)


def accept_submission(
    db: Any,
    submission_id: str,
    *,
    note: str | None = None,
    edited_text: str | None = None,
) -> dict[str, Any]:
    """Ingest a parked draft as canon, bypassing the gate (human override)."""
    row = _load_pending(db, submission_id)
    text = edited_text if edited_text is not None else row["raw_text"]
    resolution_note = note
    if edited_text is not None:
        resolution_note = f"{note or 'accepted'} (edited before ingest)"

    outcome = analyze_chapter(
        novel_id=row["novel_id"],
        chapter_number=row["chapter_number"],
        raw_text=text,
        chapter_title=row["title"],
        use_mock_llm=None,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        db=db,
        replace=False,
        source="agent",
        _gate_bypass=True,
    )
    chapter_id = str(outcome["chapter_id"])
    flags_written = _write_findings_through(db, chapter_id, row["findings"] or {})

    db.execute(
        """
        UPDATE draft_submissions
           SET status = 'accepted', resolved_at = now(),
               resolution_note = %s, raw_text = %s
         WHERE id = %s
        """,
        (resolution_note, text, submission_id),
    )
    return {
        "accepted": True,
        "chapter_id": chapter_id,
        "flags_written": flags_written,
    }


def reject_submission(
    db: Any, submission_id: str, *, note: str | None = None
) -> dict[str, Any]:
    """Mark a parked draft rejected. The agent must resubmit a revision."""
    _load_pending(db, submission_id)
    db.execute(
        """
        UPDATE draft_submissions
           SET status = 'rejected', resolved_at = now(), resolution_note = %s
         WHERE id = %s
        """,
        (note, submission_id),
    )
    return {"rejected": True, "submission_id": str(submission_id)}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest pipeline/tests/test_drafts_resolution.py -v
```

Expected: PASS (five tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/pipeline/drafts.py backend/pipeline/tests/test_drafts_resolution.py
git commit -m "feat: accept/reject parked drafts with findings written through"
```

---

### Task 6: API routes for the review queue

**Files:**
- Create: `backend/api/routes/drafts.py`
- Modify: `backend/api/schemas.py` (append the new models)
- Modify: `backend/api/app.py` (register the router, near line 52)
- Create: `backend/api/tests/test_drafts.py`

**Interfaces:**
- Consumes: `reads.drafts.list_submissions/get_submission/count_pending` (Task 1), `pipeline.drafts.accept_submission/reject_submission` (Task 5).
- Produces: `GET /api/novels/{novel_id}/drafts?status=pending`, `GET /api/novels/{novel_id}/drafts/pending-count`, `GET /api/drafts/{submission_id}`, `POST /api/drafts/{submission_id}/accept`, `POST /api/drafts/{submission_id}/reject`. Task 7 consumes these.

- [ ] **Step 1: Write the failing tests**

Create `backend/api/tests/test_drafts.py`:

```python
"""HTTP surface for the draft review queue."""

from __future__ import annotations

import json


def _park(db, novel_id: str, number: int = 90) -> str:
    findings = {"fails": [{"check": "knowledge_state", "severity": "FAIL",
                           "message": "nope", "quote": "q",
                           "suggested_fix": None, "context": {}}], "warns": []}
    return str(
        db.fetchval(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, 'Ch 90', 'Elara walked in.', 'pending', %s::jsonb)
            RETURNING id
            """,
            (novel_id, number, json.dumps(findings)),
            commit=True,
        )
    )


def test_list_drafts_returns_pending(client, db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    res = client.get(f"/api/novels/{novel_id}/drafts")

    assert res.status_code == 200
    body = res.json()
    assert [r["id"] for r in body] == [submission_id]
    assert body[0]["fail_count"] == 1


def test_pending_count_endpoint(client, db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    _park(db, novel_id)

    res = client.get(f"/api/novels/{novel_id}/drafts/pending-count")

    assert res.status_code == 200
    assert res.json()["pending"] == 1


def test_get_draft_returns_text_and_findings(client, db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    res = client.get(f"/api/drafts/{submission_id}")

    assert res.status_code == 200
    body = res.json()
    assert body["raw_text"] == "Elara walked in."
    assert body["findings"]["fails"][0]["check"] == "knowledge_state"


def test_get_missing_draft_404s(client):
    res = client.get("/api/drafts/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_reject_endpoint_resolves_the_draft(client, db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    res = client.post(f"/api/drafts/{submission_id}/reject", json={"note": "off voice"})

    assert res.status_code == 200
    assert res.json()["rejected"] is True
    assert client.get(f"/api/novels/{novel_id}/drafts").json() == []


def test_rejecting_twice_returns_409(client, db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    client.post(f"/api/drafts/{submission_id}/reject", json={"note": "no"})

    res = client.post(f"/api/drafts/{submission_id}/reject", json={"note": "again"})

    assert res.status_code == 409


def test_accept_endpoint_ingests_the_draft(client, db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    res = client.post(
        f"/api/drafts/{submission_id}/accept", json={"note": "deliberate retcon"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] is True
    assert body["flags_written"] == 1
```

- [ ] **Step 2: Check the API test fixtures**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
sed -n '1,60p' api/tests/conftest.py
```

Confirm the `client`, `db`, and `seed_novel` fixture names match what the tests above use. If the existing conftest names them differently (e.g. `api_client`), rename the usages in `test_drafts.py` to match rather than adding duplicate fixtures.

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest api/tests/test_drafts.py -v
```

Expected: FAIL — 404s on every route.

- [ ] **Step 4: Add the schemas**

Append to `backend/api/schemas.py`:

```python
class DraftSummary(BaseModel):
    id: str
    novel_id: str
    chapter_number: int
    title: str | None = None
    status: str
    fail_count: int
    warn_count: int
    submitted_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class DraftDetail(DraftSummary):
    raw_text: str
    findings: dict[str, Any]


class PendingCount(BaseModel):
    pending: int


class ResolveRequest(BaseModel):
    note: str | None = None


class AcceptRequest(ResolveRequest):
    edited_text: str | None = None
```

If `datetime` or `Any` are not already imported in that file, add `from datetime import datetime` and `from typing import Any` at the top.

- [ ] **Step 5: Write the routes**

Create `backend/api/routes/drafts.py`:

```python
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.schemas import (
    AcceptRequest,
    DraftDetail,
    DraftSummary,
    PendingCount,
    ResolveRequest,
)
from pipeline import drafts as drafts_writes
from reads import drafts as drafts_reads
from reads.db import get_db

router = APIRouter(tags=["drafts"])


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    findings = row.get("findings") or {}
    return {
        **row,
        "fail_count": len(findings.get("fails", [])),
        "warn_count": len(findings.get("warns", [])),
    }


@router.get("/api/novels/{novel_id}/drafts", response_model=list[DraftSummary])
def list_drafts(
    novel_id: UUID,
    status: Literal["pending", "accepted", "rejected", "all"] = Query(default="pending"),
) -> list[DraftSummary]:
    return [
        DraftSummary(**_summary(r))
        for r in drafts_reads.list_submissions(get_db(), novel_id, status)
    ]


@router.get("/api/novels/{novel_id}/drafts/pending-count", response_model=PendingCount)
def pending_count(novel_id: UUID) -> PendingCount:
    return PendingCount(pending=drafts_reads.count_pending(get_db(), novel_id))


@router.get("/api/drafts/{submission_id}", response_model=DraftDetail)
def get_draft(submission_id: UUID) -> DraftDetail:
    row = drafts_reads.get_submission(get_db(), submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="draft submission not found")
    return DraftDetail(**_summary(row))


@router.post("/api/drafts/{submission_id}/accept")
def accept_draft(submission_id: UUID, body: AcceptRequest) -> dict[str, Any]:
    try:
        return drafts_writes.accept_submission(
            get_db(), str(submission_id), note=body.note, edited_text=body.edited_text
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        ) from exc


@router.post("/api/drafts/{submission_id}/reject")
def reject_draft(submission_id: UUID, body: ResolveRequest) -> dict[str, Any]:
    try:
        return drafts_writes.reject_submission(
            get_db(), str(submission_id), note=body.note
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        ) from exc
```

Note the routes carry full paths and the router has no `prefix`, because `/api/novels/{novel_id}/drafts` and `/api/drafts/{id}` do not share one.

- [ ] **Step 6: Register the router**

In `backend/api/app.py`, add the import beside the other route imports and register it after line 52:

```python
app.include_router(drafts.router)
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
.venv/bin/python -m pytest api/tests/test_drafts.py reads/tests/test_contract.py -v
```

Expected: PASS. The contract test confirms the new route file contains no SQL.

- [ ] **Step 8: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/api/routes/drafts.py backend/api/schemas.py backend/api/app.py \
        backend/api/tests/test_drafts.py
git commit -m "feat: add review-queue API endpoints for parked drafts"
```

---

### Task 7: Frontend Review queue

**Files:**
- Modify: `frontend/src/api.ts` (types + `api.*` methods)
- Create: `frontend/src/routes/Review.tsx`
- Modify: `frontend/src/App.tsx` (import + route, near line 61)
- Modify: `frontend/src/components/Sidebar.tsx` (icon at ~line 42, link at ~line 97)

**Interfaces:**
- Consumes: the five endpoints from Task 6.
- Produces: route `/novels/:novelId/review`; `api.drafts`, `api.draft`, `api.pendingDrafts`, `api.acceptDraft`, `api.rejectDraft`.

- [ ] **Step 1: Add the API client methods**

In `frontend/src/api.ts`, add the types beside the other exported interfaces:

```ts
export interface DraftFinding {
  check: string;
  severity: string;
  message: string;
  quote: string | null;
  suggested_fix: string | null;
  context: Record<string, unknown>;
}

export interface DraftSummary {
  id: string;
  novel_id: string;
  chapter_number: number;
  title: string | null;
  status: string;
  fail_count: number;
  warn_count: number;
  submitted_at: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
}

export interface DraftDetail extends DraftSummary {
  raw_text: string;
  findings: { fails?: DraftFinding[]; warns?: DraftFinding[]; error?: string };
}
```

And inside the exported `api` object:

```ts
  drafts: (novelId: string, status = "pending") =>
    fetchJson<DraftSummary[]>(`/api/novels/${novelId}/drafts?status=${status}`),
  pendingDrafts: (novelId: string) =>
    fetchJson<{ pending: number }>(`/api/novels/${novelId}/drafts/pending-count`),
  draft: (submissionId: string) =>
    fetchJson<DraftDetail>(`/api/drafts/${submissionId}`),
  acceptDraft: (submissionId: string, note: string, editedText?: string) =>
    postJson<{ accepted: boolean; chapter_id: string; flags_written: number }>(
      `/api/drafts/${submissionId}/accept`,
      { note, edited_text: editedText ?? null }
    ),
  rejectDraft: (submissionId: string, note: string) =>
    postJson<{ rejected: boolean }>(`/api/drafts/${submissionId}/reject`, { note }),
```

- [ ] **Step 2: Build the Review route**

Create `frontend/src/routes/Review.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api, type DraftDetail, type DraftSummary } from "../api";

export default function Review() {
  const { novelId = "" } = useParams();
  const [rows, setRows] = useState<DraftSummary[]>([]);
  const [selected, setSelected] = useState<DraftDetail | null>(null);
  const [note, setNote] = useState("");
  const [edited, setEdited] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.drafts(novelId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [novelId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(id: string) {
    setError(null);
    setNote("");
    setEdited(null);
    setSelected(await api.draft(id));
  }

  async function resolve(action: "accept" | "reject") {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "accept") {
        await api.acceptDraft(selected.id, note, edited ?? undefined);
      } else {
        await api.rejectDraft(selected.id, note);
      }
      setSelected(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h1>Review queue</h1>
      <p className="muted">
        Drafts submitted by a writing agent that failed continuity. Accepting
        one records its findings against the chapter as an override.
      </p>

      {error && <div className="error">{error}</div>}

      {rows.length === 0 && <p className="muted">No drafts awaiting review.</p>}

      <ul className="draft-list">
        {rows.map((r) => (
          <li key={r.id}>
            <button type="button" onClick={() => void open(r.id)}>
              Chapter {r.chapter_number}
              {r.title ? ` — ${r.title}` : ""}
              <span className="badge badge-fail">{r.fail_count} fail</span>
              <span className="badge">{r.warn_count} warn</span>
            </button>
          </li>
        ))}
      </ul>

      {selected && (
        <section className="draft-detail">
          <h2>
            Chapter {selected.chapter_number}
            {selected.title ? ` — ${selected.title}` : ""}
          </h2>

          <h3>Findings</h3>
          {selected.findings.error && (
            <p className="error">Critic error: {selected.findings.error}</p>
          )}
          <ul>
            {(selected.findings.fails ?? []).map((f, i) => (
              <li key={`f${i}`}>
                <strong>{f.check}</strong>: {f.message}
                {f.quote && <blockquote>{f.quote}</blockquote>}
              </li>
            ))}
            {(selected.findings.warns ?? []).map((f, i) => (
              <li key={`w${i}`} className="muted">
                <strong>{f.check}</strong> (warn): {f.message}
              </li>
            ))}
          </ul>

          <h3>Draft</h3>
          <textarea
            value={edited ?? selected.raw_text}
            onChange={(e) => setEdited(e.target.value)}
            rows={20}
          />

          <label htmlFor="review-note">Resolution note</label>
          <input
            id="review-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why are you accepting or rejecting this?"
          />

          <div className="actions">
            <button type="button" disabled={busy} onClick={() => void resolve("accept")}>
              {edited === null ? "Accept" : "Accept with edits"}
            </button>
            <button type="button" disabled={busy} onClick={() => void resolve("reject")}>
              Reject
            </button>
            <button type="button" disabled={busy} onClick={() => setSelected(null)}>
              Cancel
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Register the route**

In `frontend/src/App.tsx`, add the import beside the others and the route after the Process line (~line 61):

```tsx
<Route path="/novels/:novelId/review" element={<Layout><Review /></Layout>} />
```

- [ ] **Step 4: Add the sidebar link with a pending badge**

In `frontend/src/components/Sidebar.tsx`, add an icon entry beside `"Process Chapter"` (~line 42) — reuse an existing imported icon rather than adding a dependency:

```tsx
  "Review Queue": <Zap />,
```

and add the link to the same group as `"Process Chapter"` (~line 97):

```tsx
    links: [
      ["Process Chapter", `/novels/${novelId}/process`],
      ["Review Queue", `/novels/${novelId}/review`],
    ],
```

If the sidebar has an existing badge mechanism, wire `api.pendingDrafts(novelId)` into it. If it does not, skip the badge — do not restructure the sidebar to add one. The count is visible on the Review page itself.

- [ ] **Step 5: Verify the build passes**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/frontend
npm run build
```

Expected: build succeeds with no TypeScript errors. Fix any type errors before continuing (common cause: the `api` object's methods must be added inside the exported object literal, not after it).

- [ ] **Step 6: Check it renders**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend && .venv/bin/python -m uvicorn api.app:app --port 8000 &
cd /Users/naman/Desktop/gitprojs/continuum/frontend && npm run dev
```

Visit `/novels/<a novel id>/review`. With an empty queue it should render "No drafts awaiting review" rather than erroring. Stop both servers when done.

- [ ] **Step 7: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add frontend/src/api.ts frontend/src/routes/Review.tsx \
        frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: add the draft review queue to the wiki"
```

---

### Task 8: Docs

Per CLAUDE.md, the docs site must match the system. This is the single docs pass for the whole feature.

**Files:**
- Modify: `docs/architecture.html`
- Modify: `docs/reference.html`
- Modify: `docs/state-of-the-system.html`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: no code.

- [ ] **Step 1: Re-read what actually shipped**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git log --oneline main..HEAD
git diff main..HEAD --stat
```

Write the docs from the diff, not from this plan — if implementation diverged, the docs follow the code.

- [ ] **Step 2: Update `docs/architecture.html`**

Find the pipeline-phases section (search for `MATERIALIZE` or `CRITIQUE`) and add GATE as the phase that precedes EXTRACT for agent writes. Cover:

- The write spine now branches on `chapters.source`: human writes run INGEST → EXTRACT → PERSIST → MATERIALIZE → CRITIQUE unchanged; agent writes run GATE first.
- The gate runs the cheap draft-claims critic path *before* EXTRACT, so a refused draft never reaches the extraction tier and costs one LLM call rather than 13.
- A chapter row now means it passed continuity. Refused drafts live in `draft_submissions`, which is deliberately not part of canon.
- Fail-closed: a critic exception parks the draft; `critic_enabled=false` refuses agent writes outright.
- The human is the only override, via the wiki Review queue, and accepting writes the findings through to `continuity_flags`.

Add `gate.py` and `drafts.py` to the directory map if one is present in that page.

- [ ] **Step 3: Update `docs/reference.html`**

- Add `draft_submissions` to the schema section, with the same column list as Task 1, next to the other tables.
- Note the `SCHEMA_VERSION` bump to 2 and that existing databases upgrade by re-running `novel-pipeline init-db`.
- Add the five endpoints from Task 6 to the API table.
- Update the `save_chapter` MCP tool row with the new return contract (`ingested: false`, `status`, `submission_id`, `reason`, `fails`, `warns`) and update the `check_continuity` row to say it is a revision aid, not the enforcement point.

- [ ] **Step 4: Update `docs/state-of-the-system.html`**

- Move "no hard bounds on agent writes / the critic is advisory" from open to closed, describing what now enforces it.
- Add the human review queue to the wired list.
- Leave the other open items (cross-type duplicate prevention, per-chapter cost accounting, embedding-dimension migration, in-memory job state) alone.
- Add as newly open, if you agree it is: `knowledge_state_check` still matches facts by word overlap, so the gate's precision is bounded by that (its own docstring flags embedding similarity as the fix).

- [ ] **Step 5: Update `README.md`**

In the "Architecture (two spines, one database)" section, note the gate on the write spine. In the "Writing agents (MCP)" section, state that `save_chapter` refuses drafts failing continuity and routes them to human review.

- [ ] **Step 6: Verify the docs pages still open**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
open docs/architecture.html docs/reference.html docs/state-of-the-system.html
```

Confirm no broken markup and that new sections render in place.

- [ ] **Step 7: Run the full suite one last time**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend && .venv/bin/python -m pytest
cd /Users/naman/Desktop/gitprojs/continuum/frontend && npm run build
```

Expected: both green.

- [ ] **Step 8: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add docs/architecture.html docs/reference.html docs/state-of-the-system.html README.md
git commit -m "docs: document the agent write gate and review queue"
```

---

## Notes for the executor

- **The load-bearing tests are the bypass attempts** (Task 3, Steps 1–2). If `test_agent_fail_never_runs_extraction` passes for the wrong reason — a mistyped monkeypatch target — the whole feature is decorative. Step 2 of that task exists to prevent exactly that; do not skip it.
- **Do not expose `_gate_bypass`.** It is not a request field, not a CLI flag, not an MCP parameter. Only `pipeline/drafts.py::accept_submission` sets it. If a task seems to need it elsewhere, stop and ask.
- **`use_mock_llm=True` in tests** keeps `extract_draft_claims` returning empty claims (`adapter.py:52`), which is why the gate tests stub the critic rather than relying on real LLM behavior.
