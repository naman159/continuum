# Release check — 2026-09-16

The four outstanding commits on `unify-critique-and-fix-intervals` include the
schema/retrieval branch. The other feature branches were already ancestors of
`main`; there were no open pull requests, stashes, or pre-existing working-tree
changes.

## Fixes from the review

- A passing resubmission now clears its parked draft in the same transaction
  as the replacement chapter. Extraction, persistence, and commit-path failures
  leave the original available for review.
- A failed embedding provider leaves keyword search working. Failed MMR
  embedding reads fall back to the fused ranking, and diversification excludes
  vectors from other models. A total search outage is an error, not an empty
  result; the UI displays that error.
- An expired processing job shows recovery actions and preserves the draft.
- Schema comparison ignores equivalent constraint/index names while still
  detecting a missing NOT NULL constraint.
- Manuscripts can quote tokenizer control-token spellings as ordinary text.
- Critic integration fixtures explicitly set their required configuration,
  so they work under CI's `USE_MOCK_LLM=true`.
- Affected frontend and backend dependencies were updated; CI now installs
  the Python lockfile with `uv sync --frozen --dev`.
- Graph routes load on demand. The initial JavaScript bundle fell from
  1,097 kB to 337 kB (99 kB gzip). The graph library remains a larger optional
  chunk and still produces Vite's size advisory when building.
- Setup instructions now cover locked installs, supported Node versions,
  a no-key demo, and the current pre-ingest critique sequence.

## Verification

- Full backend suite: **484 passed, 3 skipped** on an isolated PostgreSQL 17
  database with pgvector. Local `.env` files were disabled for this run;
  existing novel databases were not used. The skipped tests require paid live
  LLM/embedding calls. Starlette emits one test-client deprecation warning.
- Frontend: `npm run lint`, `npm run typecheck`, and `npm run build` pass.
- Headless Chrome against the built app: create a novel, process a chapter,
  browse 16 content routes, render graphs, search, apply a chapter cutoff,
  and recover from an unknown route. No browser exceptions or failed requests
  occurred in this normal workflow.
- A separate populated fixture verifies graph rendering, character details,
  and spoiler filtering. Injected search and job-status failures verify error
  messages, recovery actions, and preservation of unsaved chapter text.
- `npm audit`: **0 known vulnerabilities** after updates.
- `pip-audit`: **0 known vulnerabilities** after updates.
- Gitleaks 8.30.1 scanned all 326 pre-merge commits with redacted output:
  **no leaks found**. Only `.env.example` is tracked; local credentials are ignored.
- Local links in the HTML documentation resolve, and `git diff --check` passes.

## Scope and limits

These checks validate application behavior and deterministic integration
contracts. They do not establish real-model extraction quality: the three
provider-backed evaluations were not run. Run `RUN_LLM_EVALS=1 uv run pytest
evals/` against an isolated database with configured provider credentials to
measure those paths; this spends API credits.

Publishing this source is separate from hosting the application. Continuum
assumes local, trusted access and has no authentication. A public server needs
an access-control layer. The existing limitations in
[State of the System](state-of-the-system.html#roadmap), including cross-type
entity deduplication and in-memory processing jobs, remain documented.
