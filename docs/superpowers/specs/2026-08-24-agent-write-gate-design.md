# Agent Write Gate — Design

## Problem

Continuum has no hard bounds on writing agents. Every continuity control in the
system is advisory:

1. **The pre-save check is a docstring.** `mcp_server/server.py:198` exposes
   `check_continuity`, whose enforcement mechanism is the sentence "Always run
   this before save_chapter." Nothing prevents an agent from calling
   `save_chapter` directly and never running the check.

2. **The critic runs after the commit.** In `pipeline/pipeline.py:435`,
   CRITIQUE is phase 5 — it fires after the extraction transaction has already
   committed, and the comment above it states that a failure there "must not
   roll back the saved chapter." By the time the critic has a verdict, the
   contradictory chapter is canon and its `state_deltas` are folded into the
   projections.

3. **A FAIL verdict changes nothing.** `mcp_server/queries.py:89` returns
   `{"ingested": True, ...}` with the critique attached as data — the same
   result whether the critique passed, failed, or threw. The handler at
   `pipeline/pipeline.py:466` swallows critic exceptions, and
   `settings.critic_enabled` can disable the critic entirely.

The only existing hard bound is `replace=False`, which prevents silent
overwrites of an existing chapter.

The expensive machinery is already built and correct: five deterministic
checks over real bitemporal state, and a `check_continuity` path that returns
`{passed, fails, warns}` for an unsaved draft. What is missing is that nothing
is wired to the verdict.

## Principle

**A chapter row in `chapters` means it passed.** Enforcement lives at the write
door, not the caller's door, and a FAIL routes to a human rather than to the
agent that produced it.

The agent cannot override. The human is the only release valve, and an
override is recorded rather than absorbed.

## Decisions

| Decision | Choice |
|---|---|
| FAIL policy | Park the draft for human review; agent is refused |
| Bound scope | Commit chokepoint only — reads and `check_continuity` stay advisory |
| Gate location | `analyze_chapter`, keyed on `source == "agent"` |
| Gate ordering | Before EXTRACT, using the cheap draft-claims path |
| Parked draft storage | New `draft_submissions` table, not a status column on `chapters` |
| WARN severity | Advisory, non-blocking |

## Architecture

### Gate location: `analyze_chapter`, keyed on `source`

The gate goes in `pipeline/pipeline.py::analyze_chapter`, **not** in
`mcp_server/queries.py::save_chapter`.

Gating `save_chapter` would gate the caller's door. The HTTP `/api/process`
route and the `novel-pipeline process-chapter` CLI both reach `analyze_chapter`
directly, so an agent reaching Continuum by any other route would be
unbounded. `chapters.source` already exists (`schema.sql:232`, `NOT NULL
DEFAULT 'human'`) and `analyze_chapter` already takes `source: str = "human"`
(`pipeline.py:272`), so the discriminator needs no schema change:

- `source == "agent"` → gate enforced, no exceptions
- `source == "human"` (CLI, wiki paste, re-processing) → behavior unchanged

One predicate, at the only place chapters become canon.

The gate is independent of `replace`. `save_chapter` passes `replace=False`
today, but an agent write with `replace=True` is gated identically — re-processing
an existing chapter is exactly when a contradiction is most likely, so the
duplicate check being satisfied must not imply the continuity check is.

### Ordering: gate before extraction

Current agent-write order is INGEST → EXTRACT (13 LLM passes) → PERSIST →
MATERIALIZE → CRITIQUE. The new order for `source="agent"`:

```
INGEST (hold raw_text, do not write)
  → DRAFT-CLAIMS      extract_draft_claims() — the cheap path check_continuity uses
  → CRITIQUE          ContinuityCritic over build_draft_chapter()
  → FAIL?             park submission, return refusal, stop — EXTRACT never runs
  → PASS?             EXTRACT → PERSIST → MATERIALIZE → post-critique (unchanged)
```

Two consequences. A rejected draft costs one claim-extraction call instead of
13 extraction passes. More importantly, contradictory material never reaches
the extraction tier, so there is no rollback or cleanup path that can be got
wrong.

Both critic entry points already exist — `build_draft_chapter` + `raw_claims`
for unsaved drafts (`mcp_server/queries.py:40-50`), and the `extracted` dict
for committed chapters (`pipeline.py:456`). This reuses the first.

The duplicate-chapter check at `pipeline.py:279` stays where it is, ahead of
everything, so a duplicate submission is still refused before any LLM cost.

### Parked drafts: `draft_submissions`

```sql
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
```

`findings` stores the critic report as of submission — the fails and warns with
their quotes and evidence, so the reviewer sees what the critic saw without
re-running it.

A status column on `chapters` was rejected. It would require every function in
`reads/` to filter on it, forever, and the read-layer contract tests cannot
catch a forgotten `WHERE`. A separate table keeps canon clean by construction.

Resubmission of a chapter number with a `pending` submission supersedes it: the
older row moves to `rejected` with a `resolution_note` of
`"superseded by resubmission"`. This prevents an agent retry loop from filling
the queue with near-identical drafts.

## Fail-closed rules

These are the bypass paths, and they matter more than the happy path.

**A critic exception parks the draft.** `pipeline.py:466` currently swallows
critic exceptions and proceeds to return success. For `source="agent"` this
inverts: an exception in DRAFT-CLAIMS or CRITIQUE is a rejection, and the draft
is parked with `findings` recording the error. Without this, crashing the
critic is a trivial escape. Human-source writes keep today's best-effort
behavior.

**`critic_enabled = false` does not bypass the gate.** The setting is currently
a global off-switch consulted at `pipeline.py:453`. For `source="agent"` it is
ignored, and an agent write is *refused outright* while the critic is
disabled — returning a refusal that names the disabled critic as the reason. A
silent unbounded mode is worse than a loud outage. `run_critic=False` passed
explicitly is likewise ignored for agent writes.

## Components

### `pipeline/gate.py` — new module

```python
@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    submission_id: str | None      # set when a draft was parked
    fails: list[dict]
    warns: list[dict]
    reason: str | None             # 'fail' | 'critic_error' | 'critic_disabled'

def gate_agent_draft(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    title: str | None,
    raw_text: str,
    use_mock_llm: bool | None,
) -> GateVerdict:
    """Run draft claims + critic on an unsaved agent draft.

    On anything other than a clean pass, park a draft_submissions row and
    return passed=False. Never raises: a critic error becomes a verdict.
    """
```

Keeping the gate in its own module rather than inline in `analyze_chapter`
keeps it independently testable and keeps `pipeline.py` from growing another
responsibility.

### `pipeline/pipeline.py` — integration

In `analyze_chapter`, after the duplicate check at line 279 and before the
`custom_entity_types` load:

```python
if source == "agent":
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
            "fails": verdict.fails,
            "warns": verdict.warns,
        }
```

### `reads/drafts.py` — new read module

Follows the existing read-layer contract (no SQL in routes):

```python
def list_submissions(db, novel_id, status: str = "pending") -> list[dict]: ...
def get_submission(db, submission_id) -> dict | None: ...
```

### `pipeline/drafts.py` — resolution writes

```python
def accept_submission(db, submission_id, *, note: str | None,
                      edited_text: str | None = None) -> dict:
    """Ingest a parked draft as canon, bypassing the gate (human override).

    Runs analyze_chapter with source='agent', gate bypassed. The submission's
    findings are written through to continuity_flags on the resulting chapter
    so a human-blessed contradiction stays visible. Marks status='accepted'.
    """

def reject_submission(db, submission_id, *, note: str | None) -> dict:
    """Mark status='rejected'. The agent must resubmit a revised draft."""
```

Accept-with-edits passes `edited_text`, which replaces `raw_text` before
ingestion and is recorded in `resolution_note`.

The gate bypass is an explicit internal parameter on `analyze_chapter`
(`_gate_bypass: bool = False`) that is never exposed through MCP, the CLI, or
any HTTP request body. Only `accept_submission` sets it.

### MCP contract change

`save_chapter`'s return type changes from always-`ingested: True` to:

```json
{"ingested": false, "status": "pending_review", "submission_id": "…",
 "reason": "fail", "fails": [...], "warns": [...]}
```

The tool docstring changes from advising `check_continuity` to stating that
drafts failing continuity are refused and routed to human review. `save_chapter`
remains the only write tool; no `list_pending_drafts` tool is added, because
the agent's role ends at submission and the reviewer is a human in the wiki.

### API routes — `api/routes/drafts.py`

- `GET  /api/novels/{novel_id}/drafts?status=pending` — list
- `GET  /api/drafts/{submission_id}` — draft text + findings
- `POST /api/drafts/{submission_id}/accept` — body `{note, edited_text?}`
- `POST /api/drafts/{submission_id}/reject` — body `{note}`

### Frontend — Review queue

A Review page listing pending submissions per novel, and a detail view showing
the draft text beside its findings (each finding with its quote and evidence),
with Accept / Accept with edits / Reject actions. A pending-count badge on the
novel nav so a parked draft is not missed.

## Data flow

```
writing agent --save_chapter--> analyze_chapter(source='agent')
                                        |
                                  duplicate check
                                        |
                                   gate_agent_draft
                                    /          \
                              PASS              FAIL / error / critic-disabled
                               |                        |
                    EXTRACT → PERSIST            draft_submissions
                    → MATERIALIZE                 (status='pending')
                    → CRITIQUE                           |
                               |                   wiki Review queue
                          chapters row                   |
                                              human: accept / edit / reject
                                                         |
                                              accept → analyze_chapter
                                                       (gate bypassed,
                                                        findings → continuity_flags)
```

## Error handling

| Condition | Behavior |
|---|---|
| Critic returns FAIL | Park, `reason='fail'`, refuse |
| Draft-claims or critic raises | Park, `reason='critic_error'`, refuse, error in `findings` |
| `critic_enabled=false`, agent write | Refuse, `reason='critic_disabled'`, no submission parked |
| Chapter number already in `chapters` | Existing `ValueError` at `pipeline.py:283`, unchanged |
| Pending submission for same chapter | Older row → `rejected`, note `"superseded by resubmission"` |
| Parking write itself fails | Raise — refusing without recording is not acceptable |
| Human write (`source='human'`) | Entirely unaffected |

## Testing

The load-bearing tests are the bypass attempts, not the happy path:

1. Agent-source write with a FAIL verdict produces **no** `chapters` row, and a
   `draft_submissions` row with `status='pending'`.
2. Agent-source write with a FAIL runs **no** extraction — assert the extraction
   entry point is never called.
3. Critic raising during an agent write parks the draft rather than ingesting
   (`reason='critic_error'`).
4. `critic_enabled=false` refuses an agent write rather than letting it through.
5. `run_critic=False` does not bypass the gate for an agent write.
6. Human-source writes with a would-FAIL draft still ingest — the gate is
   source-scoped.
7. `accept_submission` ingests the parked text and writes findings through to
   `continuity_flags`.
8. Resubmission supersedes a pending row rather than creating a second.
9. Read-layer contract test: `api/routes/drafts.py` contains no SQL.

Existing MCP tests asserting `ingested: True` need updating for the new
contract.

## Docs

Per `CLAUDE.md`, all three pages are updated as part of the work:

- `docs/architecture.html` — the gate as a pipeline phase; the two-tier
  (agent-refused / human-adjudicated) write model
- `docs/reference.html` — `draft_submissions` schema, the four new endpoints,
  the changed `save_chapter` return contract
- `docs/state-of-the-system.html` — move "no hard bounds on agent writes" from
  open to closed; note the human review queue as newly wired

## Out of scope

- Mandatory pre-draft brief and in-flight checkpoints (considered, deliberately
  deferred — the commit gate is the only chokepoint an agent cannot route
  around, and it is worth confirming it holds before adding protocol).
- Embedding-backed fact matching in `knowledge_state_check.py` (its own
  docstring flags word-overlap as a weakness). Independent improvement to check
  *quality*; this spec changes only what happens to a verdict.
- Auth on the review endpoints. Continuum is currently single-user and unauthed
  throughout; adding auth here alone would be misleading.
