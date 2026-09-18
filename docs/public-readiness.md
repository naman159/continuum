# Public-readiness audit — updated 2026-09-18

**Recommendation: publish as an experimental, local-first project.** The core
architecture is coherent and the tested workflows work. The remaining limitations
below matter to how the project is described; this is not a claim of production
service readiness or perfect continuity detection.

This audit followed the [real-Gemini release check](release-check.md). It covered
runtime code, entry points, database boundaries, historical reads, frontend
imports and dependencies, installation, tests, CI, and the current documentation.
The repository's visibility was not changed.

## Follow-up: remove unused paths (2026-09-18)

The critic now runs four checks with production inputs: canon assertions,
knowledge, possessions, and possible commitment payoffs. Removed the unused
planned-thread check, planned-commitment branch, planner fields, and the location
claims whose adapter made the two-locations failure unreachable. Removed the
corresponding fixture-only evaluation and tests. Plot-thread extraction and
browsing remain supported; a chapter planner is not part of this analyzer.

Explicit relationship endings now append a chapter-anchored assertion and
supersede the earlier one. All five read paths evaluate supersession at the
requested cutoff. Deleting the ending chapter restores the previous assertion;
reopening a relationship creates a new interval. Entity merging preserves
relationship direction and assertion history, deduplicating only equivalent rows.
The relationship tests now use real PostgreSQL instead of recording SQL strings
in a fake database. Chunk merging also preserves endings and directional pairs
before they reach persistence.

The updated backend suite passes **506 tests**, with three paid fixture
evaluations skipped. A fresh Gemini request on the saved Primal Hunter chapter 2
returned all four required claim lists; the real critic produced one possession
warning and no failures. All 38 browser checks passed against the isolated
real-novel library, with no browser exceptions or failed requests. Python lint
and documentation link checks passed. This is integration evidence, not a recall score.

Existing installations should run `uv run novel-pipeline init-db` from `backend/`
after updating. Schema version 3 changes the relationship supersession foreign
key to `ON DELETE SET NULL`, preserving the predecessor on chapter replacement.
Existing data is retained. Old stored findings keep their original check labels;
new possession findings use `possession`.

## Correctness fixes

| Finding | Change and evidence |
| --- | --- |
| A reviewed draft could become a saved chapter even if recording its acceptance failed or another reviewer rejected it. | Chapter persistence, override findings, and the conditional review-status update now share one transaction. Database tests cover failed status writes, failed findings writes, retries, and competing decisions. |
| An earlier location disappeared after a later move because historical reads filtered out every superseded edge. | Supersession is evaluated at the requested cutoff. Tests exercise the materializer's actual output across a move, including hidden future end dates. |
| Possessions remained active in the chapter where they were lost. | Current possession reads and graph edges now treat the loss chapter as the exclusive end. |
| Historical responses exposed future thread closures and commitment payoffs in secondary fields. | The shared read layer masks those fields for both HTTP and MCP consumers. Regression checks verify the complete returned row, not only the displayed status. |
| Direct object/location links exposed identities before their first appearance; backdated relationships could appear before their asserting chapter. | Detail reads apply appearance cutoffs; relationship reads require both effective start and source chapter to be visible. |
| Reversing a directional relationship was mistaken for a duplicate. | Reversed pairs are deduplicated only when both assertions are explicitly mutual. Database tests preserve both directions otherwise. |
| A queued state rebuild could use a chapter horizon read before waiting for its lock. | The default horizon is read after acquiring the per-novel materialization lock. A two-connection regression commits a chapter while the rebuild waits. |
| Parseable but malformed critic JSON could become an empty, passing critique. | Every required claim list must be present and contain objects. One fresh Gemini call on the saved Primal Hunter chapter 2 passed this validation. |
| The UI reported success without showing failed state rebuilding or failed critique-report persistence. | The result records report persistence, and the Process page shows recovery messages. Browser fault injection verifies both messages without ingesting another chapter. |
| A manual canon fact could reference an entity from another novel. | The insert now requires the subject to belong to the selected novel; the API returns 404 otherwise. |

## Code removed or simplified

- Removed the unused prose-style calculator, its per-chapter write, and its tests.
  No UI, MCP tool, or retrieval path consumed the stored fingerprint. The database
  column remains for compatibility, preserving existing data.
- Removed resolver branches that inspected in-memory test-double attributes.
  Tests now exercise the same database interface as production.
- Removed the unused `CharacterCanonicalizer` compatibility alias and JSON
  renderer. Made internally used TypeScript types/helpers private to their modules.
- Moved extraction persistence into `pipeline/extraction/persist.py`, alongside
  the other persistence helpers. The main coordinator dropped from 888 to roughly
  570 lines and now delegates those writes.
- Removed the separate draft-acceptance transaction and its manual-recovery path.
- Added a small Python lint gate for undefined names and unused imports, updated
  deprecated CI actions, and kept CI's GitHub permissions read-only.

Knip reports no unused frontend files, dependencies, exports, or exported types.
Python dead-code candidates were checked manually: framework routes, MCP tools,
Pydantic fields, fixtures, and supported entry points must remain. There was no
large abandoned runtime subsystem to delete. The historical blog/research pages
are substantial, but they are documentation rather than runtime dependencies.

## Verification

- **504 backend tests passed; 3 paid synthetic-fixture evaluations skipped.**
  The tests used an isolated PostgreSQL database. One Starlette test-client
  deprecation warning remains.
- Python lint, frontend lint, TypeScript checking, and production build passed.
- A clean source copy, excluding ignored credentials, installed Python dependencies
  with `uv sync --frozen --dev` and frontend dependencies with `npm ci` successfully.
  Both CLI entry points started; schema initialization succeeded against a new,
  empty database; the clean frontend built successfully.
- The freshly installed MCP server initialized over its actual stdio transport,
  listed all 13 tools, and read the isolated real-novel library.
- The updated UI passed all 38 checks on the previously generated real-Gemini
  results: 16 content routes per novel, populated graphs, character details,
  chapter cutoffs, and live hybrid search. No browser exceptions or failed requests.
- The preceding live run processed four real novel chapters through extraction,
  critique, embeddings, and materialization. This audit reused that saved output
  and made a fresh real-Gemini claims request; it did not run the paid synthetic
  golden-novel evaluations.
- Runtime dependency versions remain those audited in the release check. Ruff
  was added only to the development environment. The optional graph bundle still
  produces a size advisory; the initial bundle is approximately 338 kB / 99 kB gzip.
- Refreshed npm and Python audits found **zero known vulnerabilities** (85 Python
  packages checked, none skipped). Gitleaks scanned **328 commits** and the pending
  patch with redacted output and found no secrets.
- The security scanner's medium-severity findings were SQL-construction warnings.
  Review traced the interpolated identifiers and fragments to fixed internal maps
  or constants; user values are passed as query parameters. No exploitable SQL
  injection was identified in those paths. This is a code audit, not a penetration test.

## Remaining limits and next priorities

1. **Historical data is not fully versioned.** Entity aliases and descriptions,
   canon facts, and plot-thread metadata are global records. Faction/custom-entity
   appearance can only be inferred from references. Chapter-anchored events,
   state, and retrieval are capped, but this is not a guarantee that every metadata
   field is free of later information. Full versioned provenance is the next
   substantive architecture improvement.
2. **Replacing a chapter is not a complete retcon.** Derived chapter rows and state
   projections are rebuilt, but entity creation/aliases, canon-fact updates, and
   thread metadata can survive from the old interpretation. Replacing earlier
   chapters does not re-extract later ones. For a major rewrite, process the
   revised manuscript sequentially into a fresh novel.
3. **Relationship vocabulary remains free-form.** Explicit endings are preserved,
   but different labels can describe one relationship. The system does not infer
   that “father” and “parent_of” are equivalent, or that a new relationship label
   implicitly ends a different one. Those changes require explicit evidence.
4. **The critic is a heuristic assistant.** Knowledge and commitment matching use
   word overlap. Simultaneous-location checking and planner validation are outside
   its supported scope. Unknown/unresolved entities drop out of some checks.
   Four successful chapters
   and zero findings do not establish detection recall or extraction completeness.
5. **Operate locally and process chapters sequentially per novel.** There is no
   authentication. Jobs are in memory and need one server process; restarts lose
   their status. Chapter processing makes many sequential model calls and holds a
   persistence transaction during embedding calls. Durable jobs, admission limits,
   batched embeddings, and stronger serialization are future service work.
6. **Entity resolution remains probabilistic.** Cross-type duplicates can be
   repaired but are not prevented at ingest. Model choices and API quotas affect
   accuracy, latency, and cost; per-chapter cost reporting is not implemented.

These are scope and design limitations to explain in a portfolio discussion.
A broad rewrite, a second database, a microservice split, or deletion of the
working retrieval/materializer layers would add risk without solving them.
