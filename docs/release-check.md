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
- A follow-up with actual novel chapters exposed Gemini quota errors being
  swallowed during extraction. Chat and embedding requests now retry transient
  errors up to three times, respecting the provider's retry delay. Exhausted
  extraction requests and empty/invalid JSON stop processing before chapter
  persistence instead of continuing with incomplete results.

## Verification

- Full backend suite after the live-provider fix: **497 passed, 3 skipped** on an isolated PostgreSQL 17
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

## Real Gemini follow-up — 2026-09-16–17

The first two saved chapters of **Pride and Prejudice** and **Primal Hunter**
were read from the existing local library using a read-only database connection,
then processed in a separate disposable database. These were actual saved novel
chapters, totaling **37,153 characters**, not generated test prose. The original
library was not modified, and no manuscript text or credentials were added to Git.

The configured models were `gemini/gemini-3.1-flash-lite` for extraction and
critique and `gemini/gemini-embedding-2` for embeddings, with mock mode disabled.
The initial run failed after hitting the account's request-rate quota. After
the retry and fail-fast fixes above, all four chapters passed:

| Novel | Chapter | Chunks | Events | State deltas | Processing time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pride and Prejudice | 1 | 1 | 2 | 2 | 83.1 s |
| Pride and Prejudice | 2 | 1 | 2 | 4 | 32.4 s |
| Primal Hunter | 1 | 1 | 5 | 4 | 109.1 s |
| Primal Hunter | 2 | 3 | 11 | 18 | 195.1 s |

- All extraction passes completed; the saved source text matched the input,
  chapter embeddings carried the real model provenance, and state materialization
  succeeded. All four continuity critiques completed with zero findings.
- Three actual HTTP 429 responses recovered after provider-directed waits of
  51, 58, and 44 seconds. These waits are included in the processing times.
- Hybrid search returned results for both novels. Separate dense-only searches
  ranked the expected chapters first: Bingley's rental of Netherfield in chapter
  1 and Jake's Archer class selection in chapter 2.
- Chapter-1 cutoffs excluded future chapters from hybrid and dense search and
  future characters from character lists. The Bennets' marriage remained present
  in the relationship graph at both chapter cutoffs.
- Headless Chrome displayed the live results across 16 content routes per novel,
  rendered populated graphs, opened character details, applied chapter cutoffs,
  and performed live hybrid searches. All 38 browser checks passed with no
  browser exceptions or failed requests.

## Scope and limits

These checks validate application behavior, deterministic integration contracts,
and live Gemini processing and retrieval on four real chapters. The extracted
events were spot-checked against the chapter content. This small sample does not
establish extraction completeness, broad factual accuracy, or continuity-detection
recall; zero critique findings is not proof that no continuity issues exist.
The three separate provider-backed evaluations using synthetic golden fixtures
remain skipped, as requested for the live follow-up. Model output can vary between
runs, and account quota limits affect processing time.

Publishing this source is separate from hosting the application. Continuum
assumes local, trusted access and has no authentication. A public server needs
an access-control layer. The existing limitations in
[State of the System](state-of-the-system.html#roadmap), including cross-type
entity deduplication and in-memory processing jobs, remain documented.
