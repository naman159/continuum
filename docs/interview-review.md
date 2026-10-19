# Continuum: interview scrutiny review

Reviewed October 18, 2026 against commit `626b82a` **plus the existing uncommitted working tree**, including structured-fact retrieval changes. This is a code and offline-behavior review, not a certification of model quality or production readiness. The follow-up implements the eight fixes below and adds focused regressions. Existing working-tree edits were preserved. Original line numbers/probe observations refer to the pre-fix tree; current behavior is described under each resolution.

Companion: [decisions and interview questions](interview-decisions.md). Browser version: [interview review](interview-review.html).

## Assessment

Continuum is defensible as a local data application that combines probabilistic extraction with relational integrity, temporal reads, and deterministic state replay. Its strongest engineering story is managing the consequences of imperfect extraction: resolving identities, preserving chapter history, isolating writes, exposing reviewable findings, and serving the same read contracts to humans and agents.

Do not present it as a production service, a fully bitemporal database, a proven improvement to novel quality, or a checker that guarantees continuity. The eight identified defects are fixed and covered by offline regressions. Model coverage, scale, provenance, and deployment limits still require the evidence listed below.

For a data application and tooling interview, prioritize data lineage, read consistency, idempotency, backfills, evaluation validity, operational failure, and the boundary between a model suggestion and an authoritative record. These are role-relevant preparation topics, not claims about this employer's interview rubric.

## Validation performed

| Check | Result |
|---|---|
| Backend Ruff | Passed |
| Full backend pytest suite | **608 passed, 3 skipped**, 13.18 seconds after fixes (initial review: 555 passed, 3 skipped) |
| Skipped evaluations | Real-provider evaluations gated by `RUN_LLM_EVALS`; no paid model calls were made |
| Frontend ESLint | Passed |
| TypeScript type checking | Passed |
| Frontend production build | Passed; graph dependency chunk approximately 750 kB before gzip, with a size warning |
| Provider-output regressions | Wrong schemas, truncated/refused claims, missing flags, fabricated quotes, missing SDKs, and explicit mock mode covered |
| Summary/scoring regressions | All-chunk summaries survive storage and embedding; one-to-one entity scoring rejects ambiguous/collapsed identities |
| PostgreSQL regressions | Historical cutoffs, supersession chains, repeated replay, prefix rebuild, suffix rewind and rollback, and historical critic behavior covered |
| Admission regressions | Atomic concurrent job limits, duplicate novel admission, failure cleanup, HTTP 503/409, and oversize text rejection covered |
| MCP admission regressions | Malformed/unresolved model claims are parked through the real save → pipeline → critic stack |

Database checks used a new disposable PostgreSQL 17 cluster with pgvector, on port 55439, with dotenv disabled and a separate `continuum_review` database. CI targets PostgreSQL 16; this run does not independently confirm that exact version. No manuscript database was used. One dependency warning concerned Starlette's deprecated httpx TestClient integration.

The reviewed scope includes ingestion, persistence, history, replay/materialization, identity resolution, retrieval, critic rules and adapters, evaluation scoring, API jobs/admin operations, MCP integration, frontend result handling, schema/indexes, Compose, CI, and documentation. The full suite exercised more routes than were manually inspected. This was not a browser usability audit, penetration test, load test, or live-provider quality evaluation.

## Findings and resolutions

P1 identifies a core correctness/admission defect; P2 identifies a narrower correctness, measurement, or reliability defect. All eight resolutions below are implemented and offline-verified. This does not certify statistical model quality.

### R1 — P1 — Fixed: preserve knowledge history through supersession

**Original failure:** learning the cedar password in chapter 1 and replacing it with willow in chapter 3 removed cedar from chapter-1 snapshots during full replay. Replay filtered assertions by their current `superseded_by_id IS NULL` status.

**Implemented:** `state/replay.py` now replays acquisitions and explicit expiration events. A superseded assertion expires at the replacement assertion's `learned_chapter`; it remains visible before that chapter. Active knowledge is tracked by assertion ID, so expiring one row does not accidentally remove an independent assertion with identical text. Acquisitions precede expiration at the same chapter, supporting same-chapter replacement chains.

**Chosen semantics:** supersession replaces an active belief from the replacement's chapter onward. It does not retroactively erase a belief, and it does not represent undated forgetting or a transaction-time audit correction. Historical acquisition facts remain stored.

**Related rewind repair:** chapter deletion clears incoming supersession pointers before deleting replacement assertions, in the coordinator's transaction. Otherwise the self-reference FK prevents suffix rewind. Rolling back restores the links and chapters.

**Evidence:** `pipeline/state/tests/test_knowledge_history.py` compares snapshots with shared knowledge reads at cutoffs 1, 2, and 3; repeats replay; rebuilds a prefix; checks same-chapter chains and duplicate text; verifies suffix deletion and transactional rollback.

### R2 — P2 — Fixed: critique knowledge at the checked chapter

**Original failure:** after the chapter-3 replacement, re-critiquing chapter 2 falsely failed a valid cedar-password claim because the rule selected only assertions still active today.

**Implemented:** `critic/checks/knowledge_state_check.py` selects knowledge learned before the checked chapter and excludes it only if its replacement was also learned before that chapter. The boundary matches shared cutoff reads: the checked draft enters with chapter N−1 state.

**Evidence:** the historical-knowledge regression checks the chapter-2 verdict before and after chapter-3 supersession; it remains unchanged. Missing learning flags no longer receive an in-chapter-learning exemption in the low-level rule, and the provider adapter requires a real boolean.

### R3 — P1 — Fixed: validate every extraction pass before normalization

**Original failure:** any nonempty JSON object, including `{"unexpected": true}`, counted as successful model output. Missing fields were normalized into empty findings.

**Implemented:** `extraction/validation.py` defines strict Pydantic contracts for all 13 passes, including nested records, required keys, enums, nullable values, numeric ranges, kind-dependent delta fields, and registered custom types. Unknown fields are rejected. Empty finding lists remain valid; required summaries and identity fields must be nonblank. The extractor validates before composition/normalization and retries only the unusable pass, at most three output attempts. Diagnostics report field paths/error types instead of raw input values.

**Tradeoff:** a model response that previously limped through normalization may now fail ingestion; that is intentional. Transport retries retain their separate bounded budget. Contracts follow the prompt schema and must be updated together when adding fields.

**Evidence:** `extraction/tests/test_output_contracts.py` checks all pass envelopes, nested malformed records, invalid enums/conditional fields, custom registration, bounded repair retries, and privacy of diagnostics. Its real-database scenario confirms an invalid required pass leaves the existing novel's chapters/entities unchanged.

### R4 — P1 — Fixed: incomplete critic output cannot masquerade as a clean verdict

**Original failure:** parseable truncated output and claim dictionaries missing all meaningful fields were accepted; unresolved references disappeared; an omitted learning boolean could bypass the knowledge rule.

**Implemented:** `critic/validation.py` validates every claim shape, requires the learning boolean, and verifies nonempty claim quotes are verbatim substrings of the draft. The adapter rejects truncated, filtered, and refused responses and correctly reads text-block responses. It reports unresolved references and extracted/resolved/unresolved/event counts.

**Admission policy:** unresolved entity references produce `status="unreviewable"`, a resolution warning, and a report that cannot pass. MCP writes park these drafts for human review; human ingestion can still save with explicit findings. Legitimately new characters therefore may require human approval for agent submissions. Malformed output is an error and is also parked by the existing blocking policy. Valid all-empty claim lists remain supported and expose zero coverage; a zero-claim result is not proof that the model found every checkable assertion.

**Visibility:** coverage is returned by `check_continuity`, included in critique summaries, and saved with parked findings. Unreviewable historical re-critiques do not overwrite an existing completed report.

**Evidence:** critic contract tests cover malformed items, omitted flags, fabricated evidence, truncation/refusal, text blocks, zero claims, coverage, and unreviewable reports. `mcp_server/tests/test_save_chapter_e2e.py` sends fake provider output through the real MCP → pipeline → critic stack and confirms malformed/unresolved claims produce no canon chapter and preserve the text in the review queue.

### R5 — P2 — Fixed: whole-chapter summaries retain every chunk

**Original failure:** multi-summary merging chose the last chunk's nonempty value for each granularity. The ordinary merge-level summary combined chunks, but persistence later replaced the stored `chapters.summary` with the selected medium summary and embedded it; the earlier review understated this stored-summary impact.

**Implemented:** all three summary granularities concatenate distinct nonempty chunk summaries in narrative order. The stored medium summary and chapter vector therefore use the assembled representation. No extra synthesis model call is added.

**Tradeoff:** summary length grows with the number of chunks; “short/medium/long” describes per-chunk granularity, not a fixed whole-chapter token cap. Concatenation cannot recover content the model omitted within a chunk. Very long embedding inputs still need a measured budgeting strategy.

**Evidence:** summary regressions preserve beginning/middle/end content and verify the actual stored `chapters.summary` and embedding input retain early and late chunks. No real-vector relevance improvement is claimed.

### R6 — P2 — Fixed: mock mode requires explicit selection

**Original failure:** an unavailable provider loader implicitly enabled mock extraction/hash embeddings even when mock configuration was false.

**Implemented:** extractor, both identity-resolution stages, embedder, and critic claim adapter select mock behavior only from configuration or an explicit argument. Missing SDK callables with real mode selected raise actionable errors. Explicit mock mode remains deterministic/offline, and mock vectors retain `mock:hash` provenance.

**Evidence:** loader-failure tests with mock disabled assert errors across these paths; explicit mock extraction/embedding remains usable without provider callables. No runtime fallback to synthetic data is added.

### R7 — P2 — Fixed: entity coverage uses one-to-one matching

**Original failure:** one predicted “Aria” satisfied both “Aria North” and “Aria South,” yielding perfect precision/recall.

**Implemented:** `evals/scoring.py::score_entities` prioritizes exact names and optional curated aliases, allows partial names only at word boundaries when unambiguous, and assigns each prediction to at most one expected identity. Ambiguous predictions are reported and receive no credit; unmatched duplicates reduce precision.

**Tradeoff:** ambiguous shorthand is conservatively uncredited unless disambiguated by a curated alias. Possession/knowledge scoring still has separate matching heuristics; this fix concerns entity coverage.

**Evidence:** tests cover shared first names, duplicate predictions, an unrelated substring (“Ann”/“Joanne”), curated disambiguation, and the existing unambiguous partial-name case.

### R8 — P2 — Fixed for local scope: bound processing admission

**Original failure:** two worker threads limited execution but accepted an unlimited pending text backlog.

**Implemented:** `api/jobs.py` reserves jobs atomically before tokenization or executor submission. At most **8 jobs are active per process**, including two workers and queued work; only one job per novel can be queued/running in that process. Process submissions accept at most **500,000 characters**. Queue saturation returns **503** with `Retry-After: 30`; a duplicate novel job returns **409**; oversized API text returns **422**. Executor rejection discards the reservation, and processing success/error releases active capacity by completing the job record.

**Evidence:** concurrent admission tests prove the capacity bound, and tests cover duplicate novel admission, success/error capacity release, executor rejection, API error contracts, and oversized input before submission.

**Remaining boundary:** jobs are still in memory and process-local. Restart loses job status; multiple API workers do not share reservations or polling records. The database novel lock still protects write integrity across processes. Use one API process for this local app; a shared service needs durable job state and distributed admission. These pre-existing deployment limitations are not claimed fixed by a bounded local queue.

## Applying the fixes to existing data

The code changes do not rewrite saved manuscripts or call paid providers automatically. Restart the API/MCP processes to load them.

For a novel with superseded knowledge, rebuild its derived projections from recorded assertions (no LLM calls), from `backend/`:

```bash
uv run python -m pipeline.state.cli --novel-id NOVEL_ID
```

Do not pass an earlier `--through-chapter` when repairing the full novel: that option intentionally truncates later projections. Re-run historical critiques if previous false findings matter; critiques require real model calls. Existing incomplete extraction or last-chunk summaries require chapter reprocessing to repair, which reprocesses the suffix and incurs normal provider costs. Existing fidelity numbers should be re-measured with the stricter scorer. No user database was modified during this work.

## Naman review items: also addressed

- **Canonical naming:** replaced the length heuristic with an established-identity-first, validated model-nomination policy. Conflicting established identities, generic overlap, and conflicting owners abstain. Structured-reference rewriting and all built-in entity types are tested. Contextual renaming still does not itself authorize persistent aliases.
- **History terminology:** schema version 6 migrates the saved domain-record history in place to `story_record_versions` / `story_record_history`, with `story_records_at` and renamed Python helpers in `pipeline.db.story_records`. The migration preserves baselines, full row payloads, and deletion tombstones; idempotent upgrade and historical lookup are tested. Run `novel-pipeline init-db` before updated processes start; no manuscript database was migrated during this work.
- **Snapshot duplication:** implemented a status-only benchmark prototype and checked every historical location/knowledge row in two synthetic cases. Row storage dropped roughly 87–89%; history reads were slower, and no rebuild advantage was demonstrated. Retain the current derived fields, with the experiment's scope and alternative query complexity documented. Failure injection now verifies all projection tables and checkpoint roll back together.

See [Naman review resolution](../naman-review.md), [snapshot comparison](snapshot-design.md), and [raw results](snapshot-comparison.json). The decision log now contains 75 entries.

## Existing issues and open investigations

These are not all newly discovered defects, and several are deliberate limitations.

| Area | Observation / question | Next evidence to collect |
|---|---|---|
| Canonical naming quality | The length rule is removed; validated model nominations and established-identity guards are covered by regressions. The model can still misidentify new referents. | Real-provider false-merge/name-choice rates across genres, including generic descriptions. |
| Cross-type identity | Per-type resolution can file one referent under multiple entity types. | Cross-type diagnostic on more genres; review type correction vs identity merge policy. |
| Overlapping chunks | State deltas concatenate in chunk order without source-span identity. Repeated overlap can produce repeated intermediate movement/possession history. | A scene that gains then loses an item inside an overlap; compare extracted spans, replay order, final state, and intervals. Not reproduced in this review. |
| Read consistency | Atomic replacement commits all writes together, but a response assembled by separate pooled queries can straddle that commit. Hybrid retrieval uses multiple concurrent queries. | Force a replacement commit between constituent reads and check for mixed old/new results. Consider one read snapshot or a novel revision token. |
| Projection freshness | Appends commit source data before projection rebuilding; failure leaves stale state until repair. Result flags expose this and next append checks the horizon. | Read behavior during failed rebuild; persistent freshness indicators and explicit repair UX. |
| Context budget | Character/location rosters are capped; open threads/custom entity collections are not uniformly bounded. Mentioned entities beyond the cap can still be dropped. | Token-size and latency distribution for long novels; recall when relevant identities fall outside selected context. |
| Snapshot semantics | Emotional state is deliberately not carried forward, unlike goals/location/condition; tests document this. Notes also do not carry in snapshot construction. | Decide and document the product meaning of transient emotions and notes. Do not call emotional reset an accidental bug. |
| Time model | Chapter ordering is primary; within-chapter intervals can overlap, while flashbacks and world chronology require more than chapter numbers. | Explain acquisition time vs story-event time vs correction time; add scene ordinals only if product needs them. |
| Search scale | Structured facts reconstruct history and compute text vectors in the query. This can prevent direct reuse of current-table indexes. | `EXPLAIN (ANALYZE, BUFFERS)` on realistic chapter/novel sizes and restrictive cutoffs; recall under HNSW filtering. |
| Materialization scale | Full-novel replay and delete/reinsert occur after each append. Total ingestion work can grow quadratically with chapter count if per-chapter facts remain comparable. | Measure replay rows, wall time, writes, and DB size for 10/100/1000 chapters before incremental checkpoints. |
| Long transactions | Embedding network calls run while a persistence transaction is open; suffix replacement also holds a transaction across model calls. | Transaction duration, connection use, retries, rollback cost, and dead tuple/vacuum impact. |
| Recovery/retry | Duplicate chapter admission is rejected; there is no end-to-end request idempotency token for a client retry after a lost response. | Simulate commit followed by network loss; define status/reconciliation behavior before automatic retry. |
| Rule quality | Knowledge overlap is lexical, canon equality is normalized strings, unresolved references can disappear, and possession/payoff findings are warnings. | Negations, synonyms, ambiguous aliases, in-chapter learning/transfer, and human-labeled precision/recall. |
| Lineage | Some evidence fields exist, but state-delta persistence does not preserve the extracted `quote` or attach an `event_id`. | Trace a displayed state value back to the exact source span, extraction run/model/prompt, and resolution decision. |
| Access control | No auth, ownership, quotas, or tenant isolation policy. Compose binds the UI to localhost. | Keep local scope explicit; shared deployment needs authentication and authorization on every novel/job/tool operation. |
| Metrics | No verified per-chapter token/dollar/latency accounting or broad held-out model-quality evidence in this run. | Repeated real-provider runs with model/prompt versions, cost and latency, baselines, and error examples. |
| Frontend performance | Build flags a large graph library chunk; it is already split separately. | Measure actual graph route load and interaction on representative networks; chunk size alone is not a user-latency benchmark. |

## Claims to use carefully

- **“Hybrid retrieval”** is accurate: lexical full-text retrieval plus cosine-vector retrieval, RRF, then optional MMR. **“BM25”** is not the implemented ranker: `ts_rank_cd` supplies PostgreSQL cover-density ranking.
- **“Deterministic replay”** applies to a fixed set of stored assertions and replay semantics. Re-extracting prose is nondeterministic. R1 now preserves supersession at the replacement chapter; this is belief-validity history, not a full immutable audit log.
- **“Atomic replacement”** is supported by an enclosing transaction. **“Every read response is snapshot-consistent”** is a separate claim that needs validation.
- **“Event-sourcing-inspired state”** is defensible. **“A complete immutable event-sourced system”** overstates it: metadata is mutable/versioned by chapter, merges repair earlier identities, and not every edit has independent immutable provenance.
- **“Chapter-aware history”** is accurate with documented migration baselines. **“Fully bitemporal”** requires independently queryable valid-time and transaction-time semantics, which this design does not generally provide.
- **“Agent writes require a critic verdict”** describes the policy. **“A passing verdict proves continuity”** overstates extraction coverage and heuristic checks; see R4.
- **“608 offline tests passed on the fixed working tree”** is established. **“The model is accurate”**, a production SLA, a user adoption claim, or a particular relevance score is not established by this run.
- The offline metric named `bm25_recall_at_k` still includes hash-vector noise and uses PostgreSQL FTS. Describe it as an **offline retrieval regression**, not a pure BM25 baseline or real hybrid quality measurement. Older blog counts, five-check descriptions, and six-list search descriptions predate the current implementation; use code and fresh run evidence.

## Recommended next steps

1. Review the new regressions and the companion decision log so you can explain the failure, invariant, chosen fix, and tradeoff.
2. Repair existing derived state with the replay command above if your novel contains superseded knowledge. Reprocessing manuscripts is a separate, paid action.
3. Run a small real-provider evaluation on a held-out manuscript with shared names, changed beliefs, long chapters, and contradictory facts. Include lexical-only, dense-only, fusion, and MMR comparisons; record cost/latency and error examples.
4. Investigate source-span identity for overlap, response snapshot consistency, and query/replay scale before expanding infrastructure.
5. Keep the single-process, trusted-local deployment boundary explicit until durable jobs, access control, and operational recovery are implemented.

The eight concrete defects are resolved in code and offline regressions. The open investigations above remain open; a schema-valid model response can still be incomplete or factually wrong.
