# Architecture conformance — 2026-09-19

**Verdict: the component structure is coherent; several data-consistency promises
are broader than the implementation.** Continuum is a modular application with
one PostgreSQL database, a shared chapter coordinator, shared presentation reads,
and selected state projections. It is not a fully versioned, fully replayable
event-sourced system. A rewrite or service split would not address the gaps below.

This is a source and stored-data audit of commit `da975e2`. The two read-layer
contract tests passed. The data comparison used read-only queries against the
isolated successful real-Gemini audit novels, not the user's original library.
No new model call was needed for this comparison. Runtime behavior was not changed
as part of this conformance review; the findings below remain open.

## What matches

| Responsibility | Implementation | Assessment |
| --- | --- | --- |
| Coordinate chapter processing | CLI, `api/jobs.py`, and MCP `save_chapter` call `pipeline/pipeline.py::analyze_chapter`. Review acceptance also delegates there. | One shared ingestion path. |
| Separate judgement from enforcement | `critic/service.py` returns a verdict; `analyze_chapter` applies advisory or blocking policy. | Appropriate for human and agent callers. Advisory callers can explicitly skip critique. |
| Share presentation queries | HTTP routes and MCP readers delegate to `reads/`. Structural tests prohibit SQL in those transport files. | The boundary is implemented. Registry/review helpers intentionally have no chapter cutoff. |
| Derive selected state | `StateReplay` folds `state_deltas`; `StateMaterializer` rebuilds `character_states`, `located_in_edges`, and `possesses_edges`. | These three projections have one normal producer. Entity repair and deletion also maintain their references. |
| Keep repairs separate from ingest | Novel/canon administration and entity merging use dedicated mutation helpers. | Legitimate exceptions to a chapter coordinator, not duplicate ingestion pipelines. |
| Recover after projection/report failures | Materialization and critique recording run after commit and expose failure status. | A deliberate partial-completion contract; not one transaction across the entire workflow. |

## Actual chapter flow

1. Reject an existing chapter unless replacement is requested.
2. Critique when enabled; blocking callers always require it. Stop or park a
   blocked draft before extraction.
3. Read context, chunk, extract, deduplicate, and canonicalize names. **Some alias
   updates are committed here, before the chapter transaction.**
4. In one session, replace/insert the chapter and persist its assertions, deltas,
   summaries, embeddings, and optional review bookkeeping. Some enrichment rows
   use savepoints and may be skipped after a logged failure.
5. Commit, then rebuild selected state projections and record the earlier critique.

Raw chapter insertion occurs after extraction, inside persistence. Embedding
network calls still occur inside that database transaction.

## Divergences and priorities

### 1. Knowledge has two independent sources — highest priority

The `state_deltas` pass supplies knowledge entries for `character_states.knowledge`.
The separate `knowledge_state_deltas` pass supplies `learnings`, written directly
to `knows_edges`. The critic and knowledge page use the latter; character snapshots
use the former. Rebuilding the materialized state does not reconcile them.

Evidence from the successful real-Gemini chapters:

| Character | Latest snapshot knowledge entries | Knowledge edges |
| --- | ---: | ---: |
| Kitty Bennet | 0 | 1 |
| Mary Bennet | 0 | 1 |
| Jake | 9 | 23 |

Counts alone are not semantic accuracy scores: independently extracted facts can
be paraphrased or split differently. The zero-versus-one cases nevertheless show
that the two views can disagree about whether any knowledge was recorded.

**Recommended correction:** one enriched knowledge assertion source should feed
both views and the critic. Preserve source/certainty/sharing metadata while
consolidating; do not simply delete one representation and its existing data.

Source: `extraction/extractor.py::_compose_from_pass_payload`,
`extraction/persist_extras.py::persist_knows_edges`, `state/replay.py::replay`.

### 2. The chapter transaction does not cover alias updates — highest priority

`EntityCanonicalizer._append_alias` writes through `DBClient` before
`analyze_chapter` enters `client.session()`. A subsequent persistence failure can
therefore leave aliases from a chapter that was never saved. The comment calls
these updates harmless, but aliases affect later identity resolution; that is a
real boundary exception, not a guarantee of harmlessness.

**Recommended correction:** compute canonicalization decisions before persistence,
then apply accepted alias changes inside the chapter session. This also keeps the
model call outside the transaction. Add a failure regression that checks aliases
as well as chapter rows.

### 3. Temporal coverage and replayability are partial

Chapter-anchored state, events, retrieval, and explicit relationship endings have
cutoff handling. Entity metadata, unlocked canon-fact updates, and thread metadata
are mutable global records. Replacing a chapter does not reconstruct all of them,
and does not re-extract subsequent chapters. Context loading can also read those
global records during reprocessing.

**Consequence:** shared query code prevents HTTP/MCP implementations from drifting;
it does not make every returned field historically correct. Replaying stored
deltas rebuilds three projections. Re-extracting raw text is a separate,
probabilistic operation, not deterministic restoration of all prior facts.

### 4. Sequential processing is a usage constraint, not an enforced invariant

`api/jobs.py` permits two concurrent jobs. The per-novel advisory lock exists only
inside materialization, after critique and extraction have already read context.
It prevents overlapping rebuilds; it does not serialize the complete chapter
workflow. The database client also does not request repeatable-read isolation,
so sharing one transaction/cursor alone is not a transaction-wide snapshot claim.

**Recommended correction:** enforce admission/order per novel across all chapter
entry points. Durable jobs can wait until a hosted-service use case requires them.

### 5. Transaction atomicity does not imply complete enrichment

Scenes, knowledge edges, and commitments use savepoints and catch some row-level
failures. The outer transaction can commit successfully with fewer rows than were
extracted. These warnings are logged; they are not aggregated into the pipeline's
public result. Materialization and critique-recording failures do have result flags.

**Recommended correction:** return structured skipped/failed enrichment counts and
show partial status, or make required enrichment failures abort the chapter. Choose
that policy explicitly per category. Move embedding calls out of the transaction
when reducing latency and lock duration becomes a priority.

## Recommendation

Keep the existing module boundaries. Correct knowledge ownership and alias
transactionality first, then enforce per-novel sequencing and make enrichment
degradation visible. Versioned metadata is a larger, separate history feature.
Publishing an explicitly experimental repository remains reasonable; presenting
all state as consistent, fully historical, or fully atomic would overstate it.
