# Character snapshot duplication: measured decision

October 18, 2026. Resolution of the third item in [Naman's review](../naman-review.md).

## Decision

Retain `character_states.location_id` and `character_states.knowledge` as derived read fields. They are not independent sources of truth. The alternative is viable and saves substantial row storage, but adds temporal joins and was slower for full character-history reads in both measured synthetic cases. The current local workload does not justify a migration solely to eliminate duplication.

This is a bounded design decision, not proof that duplication is necessary at every scale. Revisit if snapshot storage, write amplification, or replay memory becomes the measured bottleneck.

## Compared designs

**Current:** state deltas and learning assertions feed one replay; one transaction writes snapshots, location intervals, possession intervals, and the successful-run checkpoint. Character history reads the stored snapshot fields.

**Alternative prototype:** keep the same snapshot row boundaries and status fields, but omit persisted location/knowledge. Resolve location from temporal edges and knowledge from assertions at each row's chapter. Keep all ended edges; historical intervals are not removed. The prototype uses a regular scratch PostgreSQL table, comparable indexes and foreign keys, the existing replay, and the same edge writes. It does not implement a separate optimized status-only replay engine.

Both queries return one character's complete history, including the fields under investigation. They are not whole API request timings. The alternative needs a temporal location lateral join, same-chapter supersession exclusion, and a cutoff-aware knowledge aggregate for each history row. A status-only snapshot schema would also need to preserve knowledge-only and movement-only history rows if the current UI contract is retained.

## Results

PostgreSQL **17.9**, local disposable database. Synthetic data: one learning and a location change per character per chapter, with mid-story belief replacement and same-chapter double movement. Every historical location/knowledge row agreed between designs for all characters in both cases.

| Characters × chapters | Snapshot rows | Stored row bytes | Status-only row bytes | Stored history read median | Assembled history read median | Current rebuild median | Status-only rebuild median |
|---|---:|---:|---:|---:|---:|---:|---:|
| 8 × 12 | 96 | 76,800 | 8,445 | 0.097 ms | 0.575 ms | 23.244 ms | 26.107 ms |
| 24 × 48 | 1,152 | 763,752 | 101,373 | 0.726 ms | 1.883 ms | 433.567 ms | 567.270 ms |

Row bytes are sums of PostgreSQL `pg_column_size(row)` for the selected novel; they are not total heap/index/TOAST allocation or an uncompressed manuscript-size estimate. Read measurements use 15 warm samples; rebuild measurements use 3. The raw output includes sample p95 values, but three rebuild samples do not establish a stable tail-latency percentile.

The status-only projection used roughly 87–89% fewer measured row bytes. Both read medians were below 2 ms; the absolute differences are small on this local machine. The tested storage change did not demonstrate a rebuild-time benefit because it retained the same replay and edge work. Candidate order was not randomized; these single-session, single-run timings do not establish statistically significant general performance differences or concurrent production capacity.

[Raw measurements](snapshot-comparison.json). Reproduce from `backend/` against an initialized **disposable** database:

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m benchmarks.snapshot_projection \
  --database-url postgresql://127.0.0.1:55439/continuum_review \
  --output ../docs/snapshot-comparison.json
```

The benchmark creates unique synthetic novels and regular scratch tables and removes them transactionally. It does not call a model. Run it separately from schema-drift tests because scratch tables temporarily exist in the disposable database's public schema.

## Correctness and failure boundaries

- Supersession takes effect at the replacement chapter; earlier character snapshots agree with cutoff-aware assertion reads. Repeated replay, same-chapter replacement chains, duplicate assertion text, prefix rebuilds, and suffix rewind/rollback are regression-tested.
- Same-chapter movement can have multiple history intervals. The final active location excludes an edge whose successor has already begun at the cutoff. Ended edges remain queryable as history.
- A new regression injects failure after snapshot/location writes and verifies snapshots, location edges, possession edges, and the checkpoint all roll back to their exact prior rows together. Source assertions committed before a failed rebuild remain saved; successful repair then advances the projections.
- During that failure window, direct source-assertion reads can be newer than derived snapshot/edge reads. Ingestion result flags and checkpoint repair expose/manage this existing boundary. Keeping duplicated fields does not make this window disappear; removing them would also mix live knowledge with potentially stale derived location unless freshness is addressed explicitly.
- Replacements restore story-record versions and rebuild dependent chapters transactionally. Naming changes in schema version 6 do not change snapshot or interval semantics.

## What would change the decision

Measure larger casts/books, realistic read concurrency, actual relation/TOAST/index sizes, replay memory, and WAL volume. Compare an optimized alternative that avoids constructing unused knowledge arrays, rather than assuming a narrower persisted table makes replay cheaper. If storage dominates, prototype that engine and verify UI-history behavior and cutoff equivalence before migrating fields.
