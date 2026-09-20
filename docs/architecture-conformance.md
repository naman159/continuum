# Architecture conformance — fixes, 2026-09-19

**The five identified consistency gaps now have explicit implementations.** The
module boundaries remain: one chapter coordinator, one shared read layer, one
PostgreSQL database, and derived state projections. No service split was needed.

## Corrections

| Finding | Implemented contract |
| --- | --- |
| Independent knowledge extraction sources | `knows_edges` owns knowledge. The character snapshot, knowledge page, and critic consume it. The state-delta pass now extracts only location, possession, and status. Schema version 4 migrates legacy knowledge deltas into enriched assertions without dropping unique recorded facts, then rebuilds projections. |
| Alias writes before persistence | Canonicalization plans alias changes in memory. `persist_aliases` applies them in the chapter transaction. A later failure rolls back both aliases and chapter rows. |
| Mutable global metadata and incomplete retcons | `metadata_versions` stores changed entity, typed-entity, canon, and plot-thread records per chapter. Shared reads and critic name resolution use those versions. Replacement restores the prior metadata, deletes the affected suffix, and re-extracts every retained manuscript chapter in order within one transaction. Failure restores the original novel. |
| Concurrent/stale processing context | A PostgreSQL session advisory lock admits one writer per novel across processes and entry points. Duplicate/out-of-order work fails before model calls. Chapters append from 1 in sequence. Manual canon edits, entity repairs, deletion, and standalone materialization use the same lock. Query sessions and rebuilds use repeatable-read isolation. A known stale projection is rebuilt before the next append reads context. |
| Hidden partial enrichment | Database/embedding errors in scenes, knowledge, and commitments propagate and abort persistence. Unresolved references, unmatched payoffs, and optional identity-resolution outages produce structured enrichment counts/warnings. Process, Review, and MCP expose the warnings. |

## Transaction boundaries

For an append, admission covers critique, extraction, persistence, materialization,
and critique recording. Model extraction and canonicalization happen before the
persistence transaction; embeddings remain inside it. Projection or report failure
after append commit is explicitly reported and has a recovery path.

A replacement holds the same admission lock and a transaction across the entire
suffix, including model calls. Each rebuilt chapter supplies context for the next.
Projection/report write errors abort that replacement rather than leave a partially
rebuilt novel. Other database clients continue seeing the original chapters until
commit. This is deliberately more expensive than appending one chapter; the UI
explains that replacing an earlier chapter incurs additional model calls.

Explicit entity merges are retroactive identity repairs: stored references and
metadata versions move to the surviving identity, preventing a rewind from
resurrecting the deleted duplicate. Manual canon edits apply at the current
chapter horizon, preserving earlier recorded versions.

## Upgrade boundary

Run `uv run novel-pipeline init-db` with processing stopped. Existing databases
have only their current mutable metadata, so the migration records that known
baseline at the latest chapter. It does **not** invent past descriptions or aliases.
Historical metadata queries before that baseline return an explicit error; so do
replacements whose preceding context predates it. Re-importing the saved manuscript
into a new novel builds complete history from chapter 1. Stored chapter text is
preserved by the schema upgrade.

## Verification

Database regressions cover knowledge equality across views, alias rollback,
metadata cutoffs, suffix re-extraction, whole-suffix rollback, concurrent admission,
ordering, and visible enrichment skips. The full existing suite remains part of
CI. Provider checks use actual Pride and Prejudice and Primal Hunter chapters with
Gemini extraction, critique, embeddings, and retrieval; synthetic paid evaluations
remain disabled. Final results are recorded in [public readiness](public-readiness.md).

## Remaining scope limits

Extraction and identity resolution are probabilistic. Critic matching is heuristic;
it does not establish detection recall or perfect continuity. Re-extraction can
change inferred facts. Long replacements keep a database transaction open during
model calls. The app remains a local, single-server application with in-memory jobs,
not a durable hosted job service. Metadata history is chapter-based, not a wall-clock
audit trail of every edit. Direct SQL mutations bypass the supported write contracts.
