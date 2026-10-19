# Naman review notes — addressed

Updated October 18, 2026. All three items below have a code change or an explicit measured design resolution. Existing manuscripts were not reprocessed or migrated automatically during this work.

## 1. Canonical-name selection — implemented

The longest-string rule is removed from `IntraExtractionDeduplicator`.

- A model-proposed equivalence group now nominates `canonical_name` and explains its choice. The prompt prefers an explicit proper name over a temporary description, regardless of length; a descriptive name is a fallback when no proper name exists. This remains a contextual model judgment, not a claim that capitalization or string length identifies proper names reliably.
- An unambiguous established identity takes priority. Canonical names and aliases from the saved prefix roster resolve to identity IDs; the established name may be used even when only its saved alias occurs in the extraction. The roster is shared/cached with cross-chapter canonicalization.
- Groups that bridge different established identities, overlap through generic descriptions, or contain object names shared by distinct known owners are skipped with warnings. A missing/invented preferred name without an established target also skips rewriting.
- Group members must be real extraction inputs. An output target must be a valid nominated group member or the canonical name of the single matched established identity.
- Rewriting affects supported structured references, not the manuscript text. It does not itself persist reusable aliases. Cross-chapter alias admission retains its separate evidence policy.

Regression coverage includes long descriptive phrases vs short names for characters, places, objects, and factions; established-name/alias preference; consistent state/scene/knowledge/canon rewriting; distinct known people sharing a description; overlapping groups; conflicting object owners; and invalid nominations. Existing intra-dedup tests now explicitly nominate their intended canonical forms.

Model-proposed identity groups can still be wrong, particularly for new entities with no established identity to protect. These safeguards reduce specific failure modes; no new real-model false-merge rate is claimed.

## 2. Domain-record history naming — implemented with a preserving upgrade

Schema version **6** uses:

| Former identifier | Current identifier |
|---|---|
| `metadata_versions` | `story_record_versions` |
| `metadata_history` | `story_record_history` |
| `metadata_at` | `story_records_at` |
| `metadata_table` | `story_record_table` |
| `capture_metadata` / `restore_metadata` | `capture_story_records` / `restore_story_records` |
| `pipeline.db.history` | `pipeline.db.story_records` |

The tables store chapter versions of story-domain records, rather than schema metadata. Each payload is a full row from a source table, not a field delta or an entire assembled character profile. Only changed/new/deleted records receive a version; repeated changes in one chapter update that version. A null payload is a deletion tombstone. Reads choose the latest version at or before the cutoff.

The upgrade renames existing tables in place, preserving payloads, chapter numbers, row IDs, baselines, constraints, and tombstones. It replaces the SQL lookup function and updates Python consumers. Schema qualification ensures schema-drift probes cannot rename public tables accidentally. If both old and new histories exist, the upgrade refuses to guess which is authoritative.

Before starting updated application processes, finish processing and run from `backend/`:

```bash
uv run novel-pipeline init-db
```

Docker startup applies the upgrade while the API is stopped. The upgrade was verified in the disposable review database, not run against manuscript databases. Repeated upgrades, preserved history and deletion behavior, and the schema shape are regression-tested. The old internal Python module/SQL function names are retired; external scripts using those internals need updating.

## 3. Snapshot location/knowledge duplication — investigated; retain current fields

The alternative was implemented as a benchmark prototype: persist status-only snapshots and assemble location/knowledge at read time from temporal edges/assertions. No product columns were removed.

For synthetic 8-character/12-chapter and 24-character/48-chapter novels, every historical location/knowledge row agreed across designs, including belief supersession and same-chapter movement. Status-only rows used roughly 87–89% fewer measured row bytes. Full-history reads were slower in both cases (0.575 vs 0.097 ms; 1.883 vs 0.726 ms median), and retaining the same replay/edge work showed no rebuild-time advantage in this run.

**Decision:** keep the current derived snapshot fields for simpler character-history reads, and retain all historical edges. The storage savings are real in this fixture, but they do not alone justify a schema migration. Both absolute read costs are small; the experiment is synthetic, warm, single-session, and not a scale/concurrency result. A future status-only replay engine could have different rebuild costs.

Snapshots and location/possession edges remain projections of one replay, written atomically with their checkpoint. Source learning assertions remain authoritative. A failed rebuild can leave projections stale relative to committed assertions; an added failure-injection regression verifies the projection tables/checkpoint roll back together and can then be repaired. No schema simplification can substitute for a clear freshness contract.

See the [full comparison and decision](docs/snapshot-design.md), [raw measurements](docs/snapshot-comparison.json), and [reproducible benchmark](backend/benchmarks/snapshot_projection.py).

## Interview explanation

“Continuum separates extracted assertions from rebuildable read models. I removed the assumption that the longest reference is the best canonical name: established identities now take priority, model nominations are validated, and ambiguous merges abstain. I renamed the chapter-version tables to story-record history with an in-place migration that preserves saved versions. I also tested removing location and knowledge from snapshots. It saved row storage, but required more complex temporal reads and was slower for character history in my small synthetic experiments, so I retained the derived fields. That is a measured local tradeoff, not a claim about production scale.”
