# Continuum: decisions, questions, and defensible reasons

Prepared October 18, 2026 and updated after implementing review findings R1–R8 and the three Naman review items, from the current code and repository docs. Read with the [review findings](interview-review.md); a rationale does not erase a defect.

This is a major-decision inventory, not a claim to recover every historical thought or every implementation detail. **D** means rationale is documented in code/comments/project docs. **I** means the rationale is inferred from implementation and should be confirmed by you before saying “I chose this because…”. **O** marks an open decision requiring a policy or evidence. No personal motivation, performance gain, or experiment is invented here.

## A concise project explanation

“Continuum turns novel chapters into a relational story memory and a continuity-review tool. Models extract candidate entities, facts, events, and changes. A shared pipeline resolves identities and persists them; deterministic replay derives character state and temporal edges. A React wiki and MCP tools use the same chapter-aware query layer. The difficult part is preserving identity and historical meaning as chapters and beliefs change. It is a local prototype, and I separate deterministic software tests from real-model quality evaluation.”

Be ready to explain your own contribution and the role of coding assistants in your actual workflow. This review cannot establish authorship or which experiments you personally ran.

## Product and system boundaries

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D01 / D | Why structured story memory instead of pasting the whole book? | Chapters can exceed useful prompt budgets, while structured state supports targeted queries and explicit cutoffs. Extraction loses nuance; keep source prose and retrieve passages as well. No measured superiority over long-context baselines is established here. | README; `docs/blog/01-the-problem.md` |
| D02 / D | Why combine a wiki and an agent tool server? | Humans inspect/correct memory; agents consult it during drafting. Two interfaces need a shared semantic contract to avoid contradictory answers. | `backend/reads/`; `backend/mcp_server/server.py` |
| D03 / D | Why local trusted use? | The project omits authentication and production controls; localhost exposure fits the stated scope. This restricts collaboration and makes public deployment a separate engineering task. | README; `compose.yaml` |
| D04 / D | Why keep writing outside Continuum? | Its job is memory and critique; authors and writing clients remain responsible for prose. Better downstream writing is not measured. | README |
| D05 / D | Why genre presets/custom entity types? | Extraction categories vary by setting; registration lets one schema support skills, technologies, deities, etc. It does not supply new genre-specific continuity rules. | `pipeline/extraction/presets.py`; prompts; README |
| D06 / I | Why Python/FastAPI and React/TypeScript? | Python integrates model and PostgreSQL tooling; typed UI code and async query caching support the wiki. This is a stack fit explanation, not evidence that alternatives were benchmarked. | `backend/pyproject.toml`; `frontend/package.json` |
| D07 / D | Why one write pipeline across CLI/API/MCP? | Admission, replacement, critique policy, and persistence rules belong in one implementation. Shared code couples these surfaces but prevents bypass by interface drift. | `pipeline/pipeline.py`; CLI/API/MCP callers |

## Storage, identity, and data contracts

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D08 / D | Why PostgreSQL with pgvector? | One store supplies transactions, relational joins, FTS, and vectors. This reduces operational coordination across SQL/search/vector services; it does not prove one store will meet arbitrary scale. | schema; `docs/blog/02-the-mvp.md` |
| D09 / I | Why no separate graph database? | Entity IDs and relational edge tables cover current traversals. A graph database adds synchronization and operations; consider it only after traversal/query evidence warrants it. | `reads/graphs.py`; edge tables |
| D10 / D | Why universal entities plus typed tables? | Relationships need one identity namespace while character/location/object records need specialized fields. Two ID spaces require deliberate mapping and can cause subtle join errors. | `pipeline/entity_tables.py`; schema; `docs/blog/06-modelling-a-world.md` |
| D11 / I | Why UUIDs? | IDs can be created without a centralized application counter and referenced consistently across interfaces. They increase index width; no distribution requirement or benchmark should be implied. | schema defaults and API schemas |
| D12 / D | Why names and aliases instead of names as keys? | One referent has many surface forms; names can collide or change. IDs preserve relationships, but aliases must not turn generic descriptions into permanent identity claims. | resolver; canonicalizer |
| D13 / D | Why deterministic lookup plus contextual model resolution? | Exact/alias matching is cheap and inspectable; prose descriptions need context. False merges corrupt attached facts, so ambiguity should often remain unresolved. Model decisions remain fallible. | `pipeline/extraction/resolver.py`; canonicalizer; blog 04 |
| D14 / D | Why separate within-chapter dedup from existing-roster canonicalization? | One removes duplicate surfaces in current output; the other attaches them to saved identities. Both stages remain fallible. Intra-dedup now prefers an unambiguous established identity, otherwise a validated model-nominated input name; ambiguous groups abstain. | `IntraExtractionDeduplicator`; `EntityCanonicalizer` |
| D15 / D | Why reference-only resolution for state/knowledge links? | A claim about an unknown referent should not create phantom people or objects. Dropped claims reduce coverage and need visible diagnostics. | `persist_deltas.py`; `persist_extras.py` |
| D16 / D | Why allow manual entity merge? | Automated resolution is imperfect; a human needs repair tooling. Merge must repoint references and repair history so later rewind does not resurrect duplicates. It is a retroactive correction, not ordinary story evolution. | entity merge module; `db/story_records.py::repair_identity_history` |
| D17 / O | Why resolve mostly within types? | Per-type matching reduces the candidate space but misses cross-type duplicates. Decide when a wrong type should be corrected instead of creating a second entity. | resolver; known gaps; review |
| D18 / D | Why distinguish symmetric and directed relationships? | “Sibling of” and “owes money to” have different direction semantics; preserving direction prevents incorrect deduplication and display. | relationship persistence and reads |
| D19 / D | Why multiple edge kinds instead of one untyped graph? | Location, possession, relationship, event participation, and dynamics have different time/evidence semantics. Generic display graphs are projections of those records. | schema; `reads/graphs.py` |
| D20 / I | Why relational constraints if models are probabilistic? | Models propose records; the database can still enforce references, uniqueness, and legal shapes. Constraints prevent structural corruption, not factual falsehood. Cross-novel business invariants also require application checks. | schema; persistence modules |

## Extraction and context selection

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D21 / D | Why 13 extraction passes? | Focused prompts separate tasks and output shapes. They increase calls, latency, repeated context cost, and cross-pass disagreement. Pass count is an implementation choice, not evidence of optimal accuracy. | `extraction/prompts.py::PASS_ORDER`; extractor |
| D22 / D | Why JSON mode plus strict validation? | JSON gives a transport shape; Pydantic contracts validate required/nested fields, enums, types, and ranges before normalization. Invalid extraction output retries only that pass; missing fields cannot silently become empty findings (R3). | `extraction/validation.py`; extractor |
| D23 / D | Why overlapping token windows? | Bound input size and reduce facts split across boundaries. Overlap duplicates evidence and can disturb ordering without source spans. | `extraction/chunker.py`; merge logic |
| D24 / D | Why tiktoken for Gemini? | A stable approximate token budget is useful even without the provider's tokenizer. Fallback `o200k_base` is an estimator, not Gemini's exact count or a full prompt-budget guarantee. | `extraction/chunker.py` |
| D25 / D | Why cap selected entity context? | Mentioned identities first and recency backfill bound roster cost. The caps can omit relevant characters, and other context collections still need budgeting. | `context_select.py`; config; `load_story_context` |
| D26 / D | Why persist deltas instead of inferring movement/ownership with verb regexes? | Typed extracted changes express subject, object, direction, and order explicitly. Correct replay still depends on extraction quality and resolved references. | `persist_deltas.py`; `state/replay.py`; blog 05 |
| D27 / D | Why preserve delta order? | Gain→loss and loss→gain differ. Blind deduplication can erase legitimate repeated actions; overlap needs evidence identity rather than dedup by entity alone. | `extractor.py::merge_extractions`; ordinal column |
| D28 / D | Why assemble summaries from every chunk? | Ordered concatenation preserves early/middle/late chunk coverage without another model call, replacing the defective last-chunk heuristic (R5). Summary length grows with chunk count; it cannot recover model omissions. | extractor merge; summary persistence |
| D29 / D | Why bounded provider retries? | Transient rate limits/outages should not silently become empty output. Four transport attempts, retry hints capped at 60 seconds, and per-pass invalid-output retries bound work. Repeated attempts still multiply latency/cost. | `pipeline/llm.py`; extractor |
| D30 / D | Why low temperature? | Intended to reduce extraction variation; it does not make provider output deterministic. Pin model/prompt versions and evaluate repeated runs. | config; blog 03 |
| D31 / D | Why explicit deterministic mock mode? | Develop and regress software without provider cost. SDK failure now errors unless mock was explicitly selected (R6). Hash vectors and heuristic extraction measure plumbing, not semantics. | embeddings; extractor; canonicalizer; critic adapter |

## Temporal state, transactions, and recovery

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D32 / D | Why chapter cutoffs? | An agent drafting chapter N should consult only through N−1; historical wiki views need the same boundary. Filtering chapters alone is insufficient when mutable metadata contains future values. | `reads/common.py`; history; MCP tools |
| D33 / D | Why version full changed rows as JSONB? | Reconstruct mutable story-domain record values at a cutoff without hand-maintaining a separate version schema per table. Version 6 uses explicit story-record names with a preserving in-place upgrade. Changed-row storage limits some duplication; full payloads repeat data and must evolve with schema. | `db/story_records.py`; `story_record_versions` |
| D34 / D | Why history baselines for old databases? | Earlier metadata cannot be truthfully reconstructed from today's records. Refusing pre-baseline queries is safer than inventing historical values. Re-import is needed for full history. | `restore_story_records`; `story_records_at`; README |
| D35 / D | Why assertions plus materialized snapshots/edges? | Deterministic replay gives inspectable, rebuildable read models. Snapshots simplify combined character history; edge tables support focused queries. A status-only prototype saved measured row bytes but had slower history reads in two synthetic workloads; the fields are retained. Those warm local timings do not establish scale necessity. | state replay/materializer; `naman-review.md` |
| D36 / D | What does knowledge supersession mean? | A belief is active from acquisition until its replacement is learned; earlier cutoffs retain it. Replay expires assertion IDs at that chapter, including same-chapter chains (R1/R2). Retroactive corrections and undated forgetting remain distinct future policies. | state replay; knowledge reads/check; rewind |
| D37 / D | Why not carry emotion to the next snapshot? | Tests explicitly treat emotional state as transient while location/goals/condition carry forward. Explain the product meaning; notes need an equally explicit policy. | `state/tests/test_materializer.py` |
| D38 / D | Why process chapters consecutively? | Extraction consumes established prior context. Gaps would make derived state and identity decisions less coherent. This limits out-of-order imports. | `analyze_chapter` admission |
| D39 / D | Why reprocess the suffix after an earlier edit? | Later facts, names, and state may depend on the edited text; updating only one chapter leaves downstream assumptions stale. It costs fresh calls and is not bitwise reproducible. | replacement branch in pipeline |
| D40 / D | Why one enclosing transaction for replacement? | Readers retain the previous committed novel until the entire suffix succeeds; failure restores it. The transaction can span slow network calls and is operationally expensive. | `DBSession`; replacement pipeline |
| D41 / D | Why a PostgreSQL per-novel advisory lock? | API, CLI, MCP, and other processes share the database; a Python lock would cover only one process. Nonblocking admission avoids parallel stale-context extraction. Every mutation must cooperate with the convention. | `DBClient.novel_lock`; admin writes |
| D42 / D | Why a dedicated autocommit lock connection? | Hold admission without a transaction open throughout ordinary extraction, or occupying a query-pool slot. It still consumes a real database connection. | `db/client.py` |
| D43 / D | Why repeatable-read sessions? | Related statements inside a transaction see a stable snapshot, and rollback can cover the write unit. Separate pooled reads outside that session do not inherit that guarantee. | `DBClient.transaction/session` |
| D44 / D | Why append source data before materializing? | A derived projection failure should not discard successfully saved extraction; rebuilding is retryable. Readers can temporarily see stale state. Flags and horizon checks mitigate, not eliminate, this boundary. | `_process_chapter`; materializer |
| D45 / D | Why rebuild projections in one transaction? | Snapshots, edges, and checkpoint must agree on one source view. Semantic idempotence does not guarantee stable generated row IDs across rebuilds. | materializer; replay |
| D46 / O | Why full replay rather than incremental state? | Full replay is simple to reason about and repairs stale derived rows. Incremental checkpoints require invalidation/replacement logic. Measure cumulative cost before adding that complexity. | materializer; review scale questions |
| D47 / D | Why schema SQL plus drift checks? | `CREATE TABLE IF NOT EXISTS` alone cannot upgrade existing constraints. Explicit alterations and schema comparison expose divergence. This is not a general reversible migration framework. | `init_db`; `db/schema_drift.py` |

## Retrieval and critique

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D48 / D | Why lexical plus vector search? | Names/exact terms and paraphrases need different signals. PostgreSQL FTS uses `ts_rank_cd`, not BM25; dense uses cosine distance. Relative value must be measured with ablations. | `retrieval/bm25.py`; dense |
| D49 / D | Why relax lexical terms to OR? | Natural-language questions often include words absent from the relevant passage. OR increases candidate recall at possible precision cost; negation retains special handling. | `_TSQUERY_CTE` |
| D50 / D | Why reciprocal rank fusion? | Lexical and cosine scores are not directly comparable. RRF combines ranks without calibrating their raw scales. Equal channel/kind weighting and k=60 remain heuristics. | fusion; hybrid |
| D51 / D | Why MMR after fusion? | Diversify redundant candidates while retaining fused relevance. Incoming scores are normalized; item vectors supply only the redundancy penalty. Lambda=0.7 and candidate limits need evaluation. | `retrieval/mmr.py` |
| D52 / D | Why record embedding model provenance? | Hash vectors or different model spaces must not mix in dense ranking. Model name and dimensions help, but future model revisions/task modes may need a richer embedding fingerprint. | embeddings; dense |
| D53 / D | Why HNSW indexes? | Approximate nearest-neighbor lookup can avoid full scans on suitable queries. Filtering and historical reconstruction may change the plan/recall; no blanket speed claim is established. | schema; dense; facts |
| D54 / D | Why search scenes/events and structured facts? | Narrative summaries and explicit knowledge/canon/commitments answer different queries. More candidate lists can change ranking balance and cost; historical records must be reconstructed before ranking. | `retrieval/facts.py`; hybrid |
| D55 / D | Why keyword fallback if embeddings fail? | Local search can still work during provider outages. Degraded ranking should be observable; a successful empty stage does not prove all kinds were searched successfully. | hybrid stage handling |
| D56 / D | Why critique before extraction? | Avoid comparing a chapter to facts it just wrote; blocked drafts also avoid expensive ingestion. Claims still come from an imperfect model. | `critic/service.py`; pipeline phase 0 |
| D57 / D | Why typed critic checks rather than a single opinion prompt? | Explicit checks expose the evidence, rule, and severity behind findings. They cover only modeled constraints; the current runner has four checks, not all conceivable continuity errors. | `critic/runner.py`; checks |
| D58 / D | Why block agents but warn human ingestion? | Agent drafts need a reviewable verdict; failures/errors and unresolved references are parked. The human importer can save findings for review. Strict shapes, verbatim evidence, and coverage close R4, but do not prove model completeness. | critic validation/service; pipeline; MCP save |
| D59 / D | Why park failed drafts? | Preserve the author's text for human adjudication rather than lose it on refusal. Superseding old pending submissions and accepting them must be transactionally consistent. | submissions; drafts |
| D60 / D | Why possession/payoff warnings rather than automatic rejection? | A new pickup can occur within the draft, and lexical payoff overlap does not prove an intended payoff. Human review is appropriate for uncertain evidence. | possession/commitment checks |
| D61 / O | Why lexical knowledge/canon comparison? | Cheap and explainable, but paraphrase, negation, and belief changes break simple overlap/equality. Consider better matching only with labeled false-positive/negative evidence. | knowledge/entity checks |

## Interfaces, evaluation, and operations

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D62 / D | Why shared reads for REST and MCP? | One cutoff/identity contract reduces duplicated SQL and inconsistent answers. Adapters still need tests for parameter translation and error behavior. | `backend/reads/`; contract tests |
| D63 / D | Why stdio MCP? | Local writing clients can invoke tools using the existing backend configuration without another network service. It does not supply a remote multi-user authorization system. | MCP setup; README |
| D64 / D | Why bounded background jobs with polling? | Processing outlasts interactive requests. Eight active jobs per process and one active job per novel bound the local backlog; 500,000-character input limit bounds retained text. Full queue returns 503, duplicate novel 409. Status remains in-memory and single-process (R8). | jobs; Process route/API schema |
| D65 / I | Why React Query and route-specific chunks? | Cached server state and distinct graph loading support navigation. Cache keys must include novel/cutoff; build artifacts alone do not establish UX performance. | frontend routes; package manifest |
| D66 / D | Why real PostgreSQL tests? | Transactions, constraints, pgvector, and PostgreSQL SQL semantics cannot be validated faithfully with SQLite or mocks alone. Test DB isolation adds setup cost. | CI; backend test fixtures |
| D67 / D | Why gate real-provider evaluations? | Routine checks should be offline, reproducible, and inexpensive. Passing mocks tests plumbing; separate real runs are necessary for extraction/retrieval quality. | eval tests; harness |
| D68 / D | Why separate coverage, resolution, and retrieval metrics? | Missing an identity differs from merging identities or retrieving the wrong passage. Entity coverage now uses one-to-one, word-boundary matching with ambiguous predictions uncredited and optional curated aliases (R7). The noisy offline retrieval label still needs care. | `evals/scoring.py`; ER/harness |
| D69 / O | Why a ten-chapter golden fixture? | Small fixtures make failures inspectable and cheap. One synthetic story cannot establish broad generalization; add held-out genres and adversarial names/knowledge changes. | golden fixtures; blog 10 |
| D70 / O | Why these thresholds and search parameters? | Thresholds can guard regressions, but observed baseline performance is not automatically acceptable quality. Calibrate against a justified task requirement, baselines, and repeated measurements. | eval tests; blog 10; hybrid constants |
| D71 / D | Why Docker Compose? | Package UI/API/pgvector into a reproducible local environment, retain data in a volume, and limit exposed ports. Backups, key handling, upgrades, and volume deletion remain operational responsibilities. | Compose; README |
| D72 / O | Why not immediately add a distributed queue or incremental replay? | The local backlog is now bounded (R8); durable shared jobs and incremental replay add state/invalidation complexity. Choose them from measured bottlenecks and deployment needs. One API process is still the supported job-tracking scope. | jobs; materializer; review |

## Naman review follow-up

| ID / basis | Decision and likely question | Reason and tradeoff | Source |
|---|---|---|---|
| D73 / D | Why ask the model for a preferred name instead of sorting by length? | Proper names require context; a longer description is not necessarily a better name. The nominated name must be a real input member unless a single saved identity supplies its canonical name. Conflicting known identities and ambiguous overlap abstain. This remains a model judgment, distinct from permanent alias admission. | canonicalizer; prompts; `naman-review.md` |
| D74 / D | Why rename metadata history and how are saved versions preserved? | These are story-domain row versions, so `story_record_versions`, `story_record_history`, and `story_records_at` communicate the stored meaning. Version 6 renames tables in place, updates helpers, preserves baselines/tombstones, and refuses split histories rather than guessing. | schema migration; `db/story_records.py`; migration regression |
| D75 / D | Why retain duplicated snapshot fields after comparing alternatives? | A status-only prototype reduced measured row bytes roughly 87–89%, but added per-history-row temporal queries and had slower warm reads in both small synthetic fixtures. No rebuild improvement was demonstrated. Retain simpler reads now; revisit if measured storage/replay costs dominate. | [experiment and limits](snapshot-design.md); [raw data](snapshot-comparison.json) |

## Questions you should answer with evidence, not a prepared excuse

1. **What did you personally build, debug, and verify?** Identify concrete changes and tests you can explain line by line, including how you used AI assistance. The two follow-ups contain 53 additional offline regression cases compared with the initial 555-test review.
2. **What invariant matters most?** A cutoff response must not reveal later story information; identity links must resolve to the right entity; a failed replacement must preserve the original novel. Give a counterexample and test for each.
3. **Can you trace one fact end to end?** Prose → chunk/pass → candidate → resolution → persisted row → version/delta → projection → cutoff query → API/MCP output. State where evidence/run provenance is currently missing.
4. **What happens on failure at each phase?** Extraction failure: no ordinary append write yet. Embedding/persistence failure: transaction rollback. Append projection failure: chapter remains, state may be stale. Replacement failure: enclosing suffix transaction rolls back. Critic unavailable: agent write refused. Malformed claims or unresolved references: agent draft parked for human review. Lost HTTP response after commit: reconcile before retrying.
5. **How do you know search is better than a simple baseline?** You do not have that evidence from this review. Prepare lexical-only, dense-only, fusion, and MMR ablations on identical labeled queries, plus cost/latency and early/middle/late chapter coverage.
6. **What changes at 100 novels or 1,000 chapters?** Measure total DB connections including lock connections and per-process pools; concurrent retrieval fan-out; queued text size; replay work; history-query plans; model tokens/cost. Do not invent throughput numbers.
7. **How would you handle a public deployment?** Establish user/novel authorization, durable work, request limits, observability, backups/recovery, and provider-data policy. Tie each to a concrete failure in the existing local architecture.
8. **Can a model hallucination become canon?** Yes. Structural constraints and replay do not prove factual truth. Explain locked facts, human review, dropped-reference coverage, and the limits of a passing critic.
9. **What does “idempotent” mean here?** Rebuilding the same assertions should produce equivalent state, although generated projection IDs may differ. Repeating extraction or retrying an entire request is a different operation.
10. **What would you remove?** Candidates include unnecessary duplicate projections or costly passes, but only after measuring read needs and fidelity. Do not claim extra complexity is justified because it is already implemented.
11. **What is the hardest unresolved issue?** The eight reviewed defects are fixed. Remaining priorities include real-model claim coverage, overlap/source-span identity, multi-query snapshot consistency, and scale. Explain which evidence is still missing and how the next experiment would resolve it.
12. **Which result would change your mind?** An ablation showing merged extraction passes preserve fidelity at much lower cost; query plans showing history retrieval dominates latency; human labels showing lexical knowledge matching is unusable; scale data showing full replay is cheap enough for the intended workload.

## Evidence to gather before quoting numbers

- A pinned commit, configuration, dependency lock, prompt/model versions, and fixture revision for every reported experiment.
- Real-provider extraction coverage and false merges, critic precision/recall including unresolved claims, and retrieval relevance with uncertainty across repeated runs.
- Chapter processing p50/p95 time, request count/tokens/dollars, transaction duration, replay duration, search latency, and database size at declared workload sizes.
- At least one held-out story and one adversarial fixture with shared names, negated beliefs, supersession, repeated actions in overlap, and a retcon.
- A short demo showing both success and failure: historical lookup, blocked draft/review, atomic failed replacement, and recovery from stale projections.

Use measured results you actually ran. “I identified the limit and designed a falsifiable next test” is stronger than presenting an unmeasured benefit as a fact.

## Fix follow-up: the choices you should now be able to defend

- **R1/R2:** supersession is effective at the replacement chapter. Acquisition and expiration are separate replay events; a suffix rewind restores the prior belief transactionally.
- **R3:** validate strict pass contracts before normalization, with bounded retries. Nullable fields are explicit; a valid empty list differs from a missing key.
- **R4:** require claim shapes and verbatim evidence, expose coverage, and route unresolved references to human review. New characters in an agent draft may need approval; zero extracted claims still cannot prove model completeness.
- **R5:** concatenate chunk summaries in order without another model call. Whole-chapter coverage wins over fixed summary length; long embedding inputs remain a budgeting concern.
- **R6:** synthetic output requires explicit mock selection; dependency failure is an error.
- **R7:** score entity coverage with one-to-one assignments and reject ambiguous shortcuts; allow curated aliases deliberately.
- **R8:** bound the local queue and text size now; keep restart loss and multiple-worker polling out of the supported job-tracking scope until durable infrastructure exists.

Validation after this follow-up: 608 backend cases passed, 3 real-provider evaluations skipped; frontend lint, type checking, and production build passed. These results describe software behavior on the fixed working tree, not model-quality results.
