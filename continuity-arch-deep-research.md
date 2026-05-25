# Continuity Architecture for Agentic Long-Novel Writing Systems: SOTA Survey and an Opinionated Design

## TL;DR
- **Continuity is fundamentally a state-management and retrieval problem, not a prompting problem.** The best-performing academic systems (DOC, DOME, SCORE, StoryWriter, CFPG) and the most polished industry tools (NovelAI's lorebook, Sudowrite's Story Bible, novelcrafter's Codex with Progressions) all converge on the same answer: an externalized, structured representation of story state — characters, locations, objects, factions, events, plot threads, knowledge edges, foreshadowing triples — that is updated chapter-by-chapter and selectively injected into generation prompts via targeted retrieval. Your existing Postgres + pgvector pipeline is already ~80% of the right architecture; what's missing is a temporal/bitemporal event log, an explicit knowledge graph layer (especially "who knows what" edges), and codified plot-thread / foreshadowing predicates.
- **The agentic layer should be a small, opinionated pipeline of specialized agents — Planner, Retriever, Drafter, Continuity Critic, Style Critic, State Updater — orchestrated by a deterministic state machine, not a free-form agent swarm.** This is the consensus pattern across Re3/DOC, StoryWriter, Agents' Room, Novel-OS, and Sudowrite's pipeline. Free-form agent swarms drift; staged pipelines with hard quality gates between phases do not.
- **The single highest-leverage change you can make beyond what you have today is to replace pure embedding retrieval with a hybrid retrieval stack** (BM25 + dense + graph-traversal + cross-encoder rerank) gated by a *scene plan* that names which entities, threads, and facts must appear. Embedding-only retrieval reliably misses the literal tokens (proper nouns, item IDs, rule names) on which continuity errors most depend, and reliably surfaces thematically-similar-but-canonically-wrong passages.

## Key Findings

1. **Hierarchical, outline-driven generation is the dominant SOTA paradigm.** Every serious long-form system from Yao et al.'s 2018 plan-and-write through Re3 (2022), DOC (2023), CONCOCT (2023), DOME (2025), and StoryWriter (2025) decomposes generation into plan → expand → draft → critique → revise. DOC achieved a 22.5% absolute gain in plot coherence over Re3 on stories averaging 3,500+ words (per the DOC GitHub README, yangkevin2/doc-story-generation: "we provide instructions for automatically generating longer stories (avg 3500+ words in our paper experiments)"), by deepening the outline and adding a soft controller. DOME (NAACL 2025) made the outline *dynamic* — re-planned during writing — and added a temporal-knowledge-graph memory module to reduce contradictions.

2. **State-of-the-art consistency mechanisms are converging on temporal knowledge graphs + structured state tracking.** DOME's "Temporal Conflict Analyzer," STORYTELLER's Narrative Entity Knowledge Graph (NEKG) with SVO-triple plot nodes, SCORE's symbolic state machine for objects (active/lost/destroyed), and Zep/Graphiti's bitemporal validity intervals all do the same thing: maintain a structured representation of who/what/where/when with explicit validity, and check generation against it. Pure vector retrieval is consistently shown to be insufficient for entity- and timeline-level consistency.

3. **The hardest two failure modes for current LLMs are factual/detail consistency and timeline/plot logic.** The ConStory-Bench evaluation (arXiv 2603.05890, 2026) found these are the dominant error categories across all major models, including GPT-5-class reasoning models. Knowledge-state errors ("who knows what") are even harder: on the KNP/IKD benchmarks (arXiv 2601.12410, "Are LLMs Smarter Than Chimpanzees?"), the best LLM (Claude-4.5-Opus) reaches only 69% on KNP — 23 percentage points below the human baseline of 92% — while GPT-5.4 reaches 61% and GPT-4o performs at random chance. **Implication: theory-of-mind / knowledge-state tracking has to be encoded externally; you cannot rely on the LLM to do it from raw chapter text.**

4. **Industry tools have settled on a "Story Bible + injection-by-keyword" pattern that you should beat, not copy.** NovelAI's lorebook activates entries by keyword presence in recent context; Sudowrite's Story Bible flows Braindump → Synopsis → Characters → Worldbuilding → Outline → Scenes and reads up to 20,000 words of preceding linked-chapter text directly; novelcrafter's Codex uses tagging + Progressions for time-varying state and lets users attach entries to specific scenes. All three are essentially manual or shallow-automatic retrieval — they place static blocks of text into the prompt. The frontier for an agentic system is to make the retrieval **scene-plan-aware, state-aware, and graph-aware** rather than keyword-aware.

5. **Foreshadowing-and-payoff is the single most under-served continuity dimension, and is solvable with codified triples.** The CFPG framework (arXiv 2601.07033, 2026) represents narrative commitments as (F, T, P) triples — Foreshadow, Trigger condition, Payoff — maintained in a "foreshadow pool" that is eligibility-checked at every generation step. This is directly implementable in Postgres and is currently absent from every consumer product surveyed.

6. **Voice/style consistency is best handled by sample-conditioning + a dedicated style critic, not by structured data.** Sudowrite's "Match My Style" (paste ~2,000 words), novelcrafter's per-prompt sampling parameters, and academic style-transfer work like ZeroStylus all converge on: extract a compact style descriptor + retain a small rotating buffer of representative passages, and run a dedicated post-draft style-critique pass.

## Details

### Part 1 — State of the Art Survey

#### 1.1 Academic foundations of long-form story generation

**Plan-and-write and hierarchical generation.** The lineage starts with Fan et al. (2018) hierarchical neural story generation (premise → story) and Yao et al. (2018) plan-and-write (storyline → story). These established the core insight that an LLM drafting at the sentence level cannot maintain plot-level coherence; it needs a separate planning representation.

**Re3 (Yang et al., EMNLP 2022, arXiv 2210.06774).** Recursive Reprompting and Revision. Four stages: Plan (premise → setting + characters + outline), Draft (recursively reprompted continuations injecting plan + story state), Rewrite (rerank alternative continuations), Edit (small local fixes for factual consistency). The Re3 abstract reports a **14% absolute increase in coherent overarching plot and 20% absolute increase in premise relevance on stories of over two thousand words** vs. rolling-window baselines.

**DOC (Yang et al., ACL 2023, arXiv 2212.10077).** Detailed Outline Control. Two innovations: a *detailed outliner* that recursively expands a brief outline into a hierarchically structured one via breadth-first expansion with filtering/reranking, and a *detailed controller* (an OPT-350m FUDGE-style controller, contrastive-trained) that biases generation toward outline-leaf faithfulness during drafting. DOC also explicitly injects "character development over time" and "future context" (next outline nodes) into the drafter prompt. Beat Re3 by 22.5% on plot coherence, 28.2% on outline relevance, 20.7% on interestingness in pairwise human eval. The DOC pattern — *fine-grained outline tree + per-passage controller* — is the de-facto baseline for modern systems.

**CONCOCT (Wang et al., EMNLP Findings 2023, arXiv 2311.04459).** Pacing control. Trains a concreteness evaluator (which of two events is more low-level-detailed) and uses it to drive a "vaguest-first" expansion in the outline tree, plus concreteness-based filtering of new outline items. Improves pacing consistency in ~57% of pairwise comparisons across multiple outline lengths.

**DOME (Wang et al., NAACL 2025, arXiv 2412.13575).** Dynamic Hierarchical Outlining with Memory-Enhancement. Key advance: the outline is *not* fixed up-front; a Dynamic Hierarchical Outline (DHO) module re-plans during writing to adapt to drift, and a Memory-Enhancement Module (MEM) backed by a **temporal knowledge graph** stores generated story content and supplies context for both outline planning and draft writing. A Temporal Conflict Analyzer scores contextual consistency.

**STORYTELLER (Li et al., ACL Findings 2025).** Plot-planning via dynamic plot nodes encoded as SVO (subject-verb-object) triples, plus a STORYLINE and a Narrative Entity Knowledge Graph (NEKG) that interact bidirectionally with the generation process. Explicit non-redundancy and continuity enforcement during plot planning.

**StoryWriter (Lin et al., CIKM 2025 / arXiv 2506.16445).** Modular open-source multi-agent framework. Three agents: (1) Outline Agent generates event-based outlines with rich event plots, characters, and event-event relationships; (2) Planning Agent details events and plans which events go in which chapter using Non-Linear-Narration; (3) Writing Agent dynamically compresses story history based on the current event before generating. This is the cleanest published example of the agent decomposition you want.

**Agents' Room (Huot et al., ICLR 2025 — DeepMind).** Decomposes narrative generation into subtasks owned by specialized agents inspired by narrative theory; introduces the "Tell Me A Story" dataset and an LLM-judged evaluation framework for long narratives. Expert evaluators preferred Agents' Room output to single-LLM baselines.

**SCORE (Yi et al., arXiv 2503.23512, 2025).** Three components: (1) Dynamic State Tracking using symbolic logic — each key item *i* has a state Sᵢ(t) ∈ {active, lost, destroyed}, with absorbing-state Markov semantics so a "destroyed" or "lost" item flagged as "active" later is treated as a continuity error (and the prior state is preserved); (2) Context-Aware Summarization producing per-episode structured summaries containing A_c(t) (actions of character c at time t) and I_i(t) (interactions with key item i at time t), stored as structured JSON; (3) Hybrid Retrieval combining FAISS-backed OpenAI embeddings, TF-IDF, and a sentiment-aware re-ranking kernel K(e_c, e_p) = exp([S(e_c, e_p) − γ·|σ(e_c) − σ(e_p)|] / τ). The paper's table-level results (e.g., +7.7 points coherence on GPT-4o, +98 points on item-status tracking on GPT-4, with ablations showing Dynamic State Tracking as the dominant module) are well-substantiated; the abstract's headline numbers ("23.6% higher coherence on NCI-2.0," "89.7% emotional consistency on EASM") are not backed by experimental tables in the paper itself — these metric names never appear in the body and were dropped in later revisions, so treat them with skepticism.

**CFPG — Codified Foreshadowing-Payoff Generation (Yun et al., arXiv 2601.07033, 2026).** Reframes narrative continuity as a set of executable causal predicates. Maintains a "foreshadow pool" C_t where each element is a structured (F, T, P) triple — Foreshadow, Trigger, Payoff. At each generation step, an eligibility selection module deterministically selects S_t ⊆ C_t based on codified trigger constraints; the generated text updates both the narrative prefix and the foreshadow pool via a codified state transition that resolves satisfied commitments and introduces new ones. This is the cleanest formalism in the literature for the "Chekhov's gun must fire" problem and maps directly to a Postgres table.

**ConStory-Bench (arXiv 2603.05890, 2026).** Benchmark for consistency bugs in long-form story generation. Defines a "Consistency Error Density" (CED) and a "Grounded Resolution Rate" (GRR). On their evaluation, GPT-5-Reasoning achieves the lowest CED (0.113); Gemini-2.5-Pro (0.305) and Claude-Sonnet-4.5 (0.520, GRR 4.54) follow. Critically: *"error analysis reveals Factual & Detail Consistency and Timeline & Plot Logic as dominant failure modes, indicating entity tracking and temporal reasoning remain primary challenges."* This is the single most useful empirical finding for prioritizing your effort: **build for entity tracking and temporal reasoning first**, then everything else.

**Entity tracking and knowledge state benchmarks.** Kim & Schuster (ACL 2023, "Entity Tracking in Language Models," arXiv 2305.02363) showed that pure-text-pretrained LLMs struggle to maintain entity state; only code-pretrained models (GPT-3.5+) do it reliably. The KNP/IKD benchmarks (arXiv 2601.12410) confirm and extend this for character knowledge: even Claude-4.5-Opus, the best LLM, reaches only 69% on KNP (vs. 92% human baseline), GPT-5.4 reaches 61%, and GPT-4o performs at random chance. Papalampidi et al. (arXiv 2202.01709, 2022) propose augmenting LLMs with a *dynamic entity memory* trained end-to-end. **Implication: you cannot trust the LLM to track character knowledge from raw chapter text; you must externalize it.**

**Narrative planning (arXiv 2506.10161, 2025).** Frames story generation as a planning problem in an ASP-style transition system. Finding: LLMs frequently hallucinate motivations that were never established, and prior commonsense can *hurt* by misleading them away from the actual narrative state. Argues for explicit symbolic plan validation against character intent and conflict.

**Knowledge-graph storytelling (Pan et al., 2025, arXiv 2505.24803; Li et al., 2025, arXiv 2508.03137).** Both demonstrate measurable narrative-quality and user-control gains from grounding LLM storytelling in editable knowledge graphs that explicitly represent entities and relationships. Li et al.'s "main character's current goal as anchor node" pattern is specifically designed to prevent theme drift.

#### 1.2 Agent memory architectures (transferable to novel writing)

**MemGPT / Letta.** Hierarchical memory inspired by OS virtual memory: a small "main context" (RAM) and a large "external memory" (disk), with the LLM itself invoking memory-management functions to page information in/out. The agent decides what to load into context.

**Zep + Graphiti (arXiv 2501.13956).** Production-grade temporally-aware knowledge graph for agent memory. Bi-temporal model: every edge carries explicit validity intervals (t_valid, t_invalid), and conflicting facts are *invalidated rather than deleted*, preserving historical state. Per the Zep paper: "In the DMR benchmark, which the MemGPT team established as their primary evaluation metric, Zep demonstrates superior performance (94.8% vs 93.4%)" (gpt-4-turbo; Zep reaches 98.2% with gpt-4o-mini). Graphiti is open-source (Neo4j-backed). This is the single closest agent-memory primitive to what a novel-continuity system needs.

**Mem0, LangMem, H-MEM, E-mem.** Variants on hierarchical memory with semantic/episodic/procedural distinctions; H-MEM organizes by semantic abstraction level; E-mem uses heterogeneous master-assistant agents with each assistant holding a raw segment.

**CoALA (Cognitive Architectures for Language Agents, Sumers et al.).** Theoretical framework distinguishing working / episodic / semantic / procedural memory with a central LLM executive — useful as a mental model when designing your stores.

#### 1.3 Retrieval-augmented generation primitives

**GraphRAG (Microsoft Research, Edge et al., arXiv 2404.16130).** Two-stage indexing: LLM-extract entities and relationships into a knowledge graph, then run Leiden community detection and generate hierarchical *community summaries*. Supports local queries (precise factual lookup) and global queries (corpus-wide thematic). Reported win rates of 72–83% on comprehensiveness over naïve RAG on the Podcast dataset and 72–80% on the News dataset (Microsoft Research blog summarizes this as a "~70–80% win rate" on comprehensiveness and diversity). The hierarchical-community-summary pattern is directly applicable to novel arcs (Act → Subplot → Chapter cluster → Chapter → Scene).

**Hybrid retrieval (BM25 + dense + RRF + cross-encoder rerank).** Industry consensus pattern. BM25 catches literal tokens (character names, place names, item IDs, rule names) that dense embeddings smear; dense catches paraphrase and semantic similarity; Reciprocal Rank Fusion (k≈60) combines them robustly without score-normalization headaches; a cross-encoder reranker (BGE-reranker-v2, Cohere rerank-3) does final precision filtering. For novels, this matters acutely: "the dagger" in chapter 3 and "Aelric's dagger" in chapter 17 must collide.

#### 1.4 Industry / practical systems

**NovelAI** uses three context layers: **Memory** (always-on, top-of-context, holds invariants and ATTG metadata — Author/Title/Tags/Genre), **Author's Note** (closer to story tail, steers next-passage tone/beat), and **Lorebook** (entries triggered by keyword presence in the last ~2,500 characters of story, with configurable Insertion Order, Search Range, Token Budget, Reserved Tokens, and Cascading Activation). Lorebook entries support String Comparison advanced conditions (AND/OR groups, contains/starts-with/equals). This is keyword-activated RAG, not embedding RAG — fast and predictable but limited.

**Sudowrite Story Bible** is a deterministic pipeline: Braindump → Genre → Style → Synopsis (influenced by Braindump+Genre) → Characters → Worldbuilding (both influenced by Synopsis) → Outline (influenced by Genre+Synopsis+Characters+Worldbuilding) → Scenes → Draft. **Chapter Continuity** links chapter documents in sequence so the Write feature reads up to 20,000 words of preceding text across up to 25 linked documents directly (no summarization). Series Folders share Characters/Worldbuilding across books; the Series Timeline is auto-assembled from per-book outlines. Match My Style accepts a 2,000-word style sample.

**novelcrafter Codex** is a tagged, custom-schema wiki of characters/locations/objects/lore with **Progressions** that record how each entry changes at different points in the timeline (the single most important feature for time-varying state — equivalent to Graphiti's validity intervals). Codex entries can be attached to specific scenes to force them into prompt context regardless of keyword activation. Entries can have hidden sub-details ("never show the AI"). Practical guidance from the docs: keep entries short, move infrequently-needed facts to notes, watch for over-referencing.

**Novarrium** automates fact extraction after each chapter into a Story Bible and applies "Logic-Locking" (its term for runtime contradiction prevention during generation), plus OCEAN-personality-modeled character behavior. The exact mechanism is proprietary, but the *direction* — automatic post-chapter extraction + runtime enforcement — matches the SOTA academic direction.

**Open-source agentic novel writers worth studying:**
- **NousResearch/autonovel** uses a layered Markdown architecture: voice.md (style guardrails), world.md, characters.md, outline.md, canon.md (hard-facts DB), plus state.json. Has a dual-persona automated review loop (literary critic + fiction professor) and 6 automated revision cycles + 6 Opus review rounds.
- **Novel-OS (mrigankad/Novel-OS)** crystallizes the multi-agent pattern with 5 named agents — **The Architect** (story planner producing outline.json), **The Scribe** (prose drafter), **The Editor** (5 modes: line, developmental, pacing, dialogue, tension), **The Continuity Guardian** (4-category validator: Character, Timeline, World, Plot continuity, returning PASS/WARNING/FAIL with suggested fixes), **The Style Curator** (voice drift detection). All read/write a central `story_state.json` with a verbatim schema: `characters` keyed by ID with `full_name`, `role` (e.g. "protagonist"), `arc_stage`, `arc_progress` (0–100), `current_location`, `emotional_state`, `relationships`; `plot_threads` with `name`, `status` (e.g. "active"), `priority`; `timeline` array; `story_bible` with `themes`, `setting`, `world_rules`; `style_profile` with `tone`, `prose_style`, `point_of_view`; `chapters` keyed by chapter number with `status` and `word_count`. Workflow is a 6-gate loop: PLAN → DRAFT → EDIT → VALIDATE → STYLE → STATE update, and "A chapter cannot advance until: ✅ The Editor approves prose quality; ✅ The Continuity Guardian approves consistency; ✅ The Style Curator approves voice." (Caveat: the repo is small and personal — 1 star, 7 commits at time of review — but the schema and gating pattern are well-designed and worth adopting.)
- **LibriScribe (guerra2fernando)** — Concept → Outliner → Character → Worldbuilding → ChapterWriter → Editor → ContentReviewer (plot holes) → StyleEditor → Formatter agent chain.
- **Novel Engine (john-paul-ruf)** — 7-agent local-first Electron app: Spark (pitch), Verity (drafter, only one that writes prose, captures a Voice Profile), Ghostlight (cold first read), Forge (revision planning), plus copy-edit and build agents. Series support with a shared Story Bible auto-injected into all creative agents' context.
- **GOAT-Storytelling-Agent** — multi-stage init_book_spec → enhance_book_spec → create_plot_chapters → split_chapters_into_scenes → write_a_scene loop.

---

### Part 2 — Recommended Information Architecture (Primary Focus)

Your existing pipeline already extracts characters, locations, factions, objects, events, plot threads, relationships, continuity flags, and per-chapter snapshots into Postgres + pgvector. The recommended architecture **keeps the relational core, layers on a graph layer and a bitemporal event log, and adds three specific tables that almost no consumer product has** (knowledge-state edges, foreshadowing triples, and explicit canon-fact assertions).

#### 2.1 The four-layer information model

Treat the knowledge base as four logical layers, all in Postgres, with pgvector embeddings attached where useful:

1. **Canon layer (immutable facts).** Things that are true in the story-world independent of time: physical laws / magic system rules, geography, faction founding dates, character birth dates, hard worldbuilding axioms. Stored as `canon_fact(id, kind, subject_entity_id, predicate, value, source_chapter, confidence, locked_bool)`. Lock high-stakes facts (eye color, names, magic costs) so they cannot be silently mutated.
2. **State layer (time-varying snapshots).** Per-chapter snapshot of each entity's mutable state. You already have per-chapter snapshots; structure them as `entity_state(entity_id, chapter_id, location_id, emotional_state, physical_status, possessions[], current_goal, last_action, …)`. This is the "current state" projection that the Drafter consults.
3. **Event log (deltas / append-only history).** Every chapter-extraction emits events: `event(id, chapter_id, scene_id, story_time, kind, subject, object, location, predicate, payload_jsonb, embedding)`. State snapshots are derived projections of the event log up to chapter N. This is event sourcing for narrative — and gives you free time-travel ("what did Aelric know as of chapter 12?").
4. **Graph layer (relationships and edges).** A property graph over entities. Edges: `relationship(a, b, kind, sentiment, since_chapter, until_chapter, evidence_chapter_ids[])`, `knows(character_id, fact_id, since_chapter, source, certainty)` (the critical "who knows what" table), `located_in(entity_id, location_id, since_chapter, until_chapter)`, `possesses(character_id, object_id, since_chapter, until_chapter)`. Implement either with Postgres recursive CTEs on edge tables (simpler, sufficient for novels with <10⁴ entities) or with an external Neo4j/Memgraph if you want Cypher and don't mind the operational cost. **Bitemporal validity intervals are non-negotiable here** — adopt Graphiti's invalidate-don't-delete pattern: when a fact is contradicted, set `until_chapter` and append the new fact, never overwrite.

#### 2.2 What to extract per chapter — the extraction schema

Augment your current extraction with these high-value categories that academic SOTA shows matter most. After each chapter, your extractor LLM should produce a typed JSON object validated against a Pydantic / JSON-Schema spec:

- **Entities (new and referenced):** characters, locations, factions, objects, concepts, vehicles. Each gets a canonical_id (resolve aliases via embedding + name string match + LLM disambiguation pass).
- **Entity state deltas:** for each referenced entity, what changed this chapter — location, emotional_state, physical_status (injured, dead, transformed), possessions added/removed, knowledge gained, relationships changed, goals changed.
- **Events:** SVO-style structured triples (subject, verb/action, object, location, time-anchor) — adopting STORYTELLER's plot-node representation. Each event gets `causes[]` and `caused_by[]` edges pointing at other event_ids when the chapter implies causation. Each event gets an `embedding` for retrieval.
- **Knowledge-state changes:** explicit "who learned what when" — `(character_id, fact_id_or_description, learned_in_chapter, source: dialogue|observation|inference, certainty)`. Without this you cannot enforce theory-of-mind; the KNP/IKD benchmark results show even the best LLM (Claude-4.5-Opus) at only 69% on this from raw text — too unreliable to leave implicit.
- **Plot thread mentions:** every chapter, tag which plot_threads were advanced/stalled/resolved/introduced. `thread_event(thread_id, chapter_id, status: introduced|advanced|paused|resolved|abandoned, summary, weight)`.
- **Foreshadowing-payoff triples (CFPG-style):** every chapter, extract any **Foreshadow** introduced (planted hint), any **Trigger condition** that determines when it must pay off, and any **Payoff** that resolves a previously-pending foreshadow. Table: `commitment(id, foreshadow_text, foreshadow_chapter, trigger_predicate, payoff_text, payoff_chapter, status: pending|satisfied|broken, weight)`. The pool of pending commitments is the canonical answer to "what Chekhov's guns are still on the wall?"
- **Continuity flags:** what you already do — anything the extractor noticed that *seemed* inconsistent with prior canon. Each flag carries the conflicting fact_ids for the critic to adjudicate.
- **Style descriptors:** per-chapter measured style fingerprint — avg sentence length, lexical diversity (MTLD or similar), POV, tense, register, dialogue ratio, sentence-rhythm signature. Plus a few representative passages (200-word samples) embedded for style-conditioning retrieval.
- **Per-chapter summary at three granularities:** one-sentence (50 chars), one-paragraph (~150 words), full-chapter (~500 words). Used for hierarchical context compression.
- **Scene-level segmentation:** within each chapter, segment into scenes with POV, location, time, present characters, and a scene-summary. Embed both at the chapter and scene granularity.

#### 2.3 Snapshots vs deltas — both, derived

The right answer is **event log as ground truth, snapshots as derived projections**. After each chapter:
1. The extractor emits events (deltas).
2. A `materialize_state(chapter_id)` worker replays events from chapter 1 through chapter_id to produce/update an `entity_state(entity_id, chapter_id)` row per relevant entity.
3. The graph layer's validity intervals are similarly derived/updated.

This gives you (a) time-travel queries for free ("character X's possessions as of chapter 14"), (b) bitemporal auditability ("at the time we wrote chapter 20, what did we believe was true?"), and (c) the ability to retroactively edit chapter 3 and regenerate downstream state cleanly.

#### 2.4 Knowledge graph vs relational vs vector — pick all three, by role

- **Relational (Postgres tables)** is your source of truth for canon, state, events, and commitments. Strong typing, transactions, easy migrations.
- **Graph (edges as relational tables, or Neo4j / Memgraph if you prefer)** is what you query when the question is multi-hop ("which characters know that Aelric is the heir, and how did they learn it?", "which factions have unresolved grievances with the protagonist's family?"). For novel scale, edge tables + recursive CTEs are sufficient; reach for Neo4j only if traversal queries become a bottleneck.
- **Vector (pgvector with HNSW)** is for fuzzy semantic retrieval: "passages where the protagonist felt grief," "scenes thematically similar to the upcoming chapter." Embed at multiple granularities — entity descriptions, event payloads, scene summaries, full-paragraph passages — and tag with metadata so you can filter pre-vector-search.

The mistake to avoid: using vectors as your *primary* store for facts. Vectors are for "find me passages like this," not "what color are her eyes." Eye color belongs in a typed column with a lock bit.

#### 2.5 Timeline / chronology representation

Use a dual representation:
- **Story-time** (in-world time): event has `story_time_iso` if your world has a calendar, else a `story_time_ordinal` (a monotonic integer of in-world ordering). Allows non-linear narration — flashbacks have an earlier story_time but a later narrative_order.
- **Narrative-order** (publication order): `chapter_id, scene_id, paragraph_offset`.

Maintain `temporal_constraint(event_a, event_b, relation: before|after|simultaneous|caused-by, certainty)` edges so a temporal-consistency check can run SAT / constraint-propagation over the event set. This is how DOME's Temporal Conflict Analyzer works and is the canonical way to catch "character X was in Paris on Tuesday but also in Rome on Tuesday" errors.

#### 2.6 Foreshadowing and payoff — adopt CFPG's (F, T, P) triples

Implement the CFPG framework directly:
- `commitment(id, foreshadow_text, foreshadow_chapter, trigger_predicate_json, payoff_chapter, payoff_text, status, weight)`.
- The Planner agent reads the pool of `status='pending'` commitments before planning each chapter and decides which to advance/satisfy.
- The Continuity Critic checks for "broken" commitments — story state has met the trigger predicate but no payoff has fired — and surfaces them as warnings.
- The Drafter receives a list of "active commitments that may be advanced this scene" as part of its context.

#### 2.7 Plot-thread tracking

A plot thread is a meta-entity tying together a sequence of events with a common arc. Schema: `plot_thread(id, name, kind: A_plot|B_plot|subplot|character_arc, status, priority, owner_character_id, theme, opened_chapter, closed_chapter, summary, embedding)`. Every event can be associated with one or more threads via `event_thread(event_id, thread_id, role: introduces|advances|complicates|reverses|resolves)`. The Planner's per-chapter plan must explicitly state which threads it intends to advance and how — making thread coverage a first-class checkable property.

#### 2.8 Character knowledge graph — the under-served win

Maintain `knows(character_id, fact_id, learned_chapter, source_event_id, certainty, shared_with_character_ids[])`. Resolved by the extractor every chapter ("in this scene, Aelric learns that Mira is his sister"). The Continuity Critic queries this whenever a character says or does something that implies knowledge of a fact: was that fact in the `knows` set for that character as of the current chapter? This single mechanism eliminates the largest class of theory-of-mind continuity errors that LLMs make.

#### 2.9 Embedding strategy and chunking

- **Embed at four granularities**: entity-card (full canonical description of an entity, refreshed on state change), event (the SVO payload + a one-line LLM-generated description), scene-summary, and passage (~200-token windows of raw prose).
- **Store rich metadata** alongside embeddings: entity_ids referenced, chapter_id, scene_id, story_time, thread_ids, sentiment. pgvector with metadata filters (`WHERE chapter_id < $cur AND $entity_id = ANY(entity_ids)`) using HNSW gives you fast filtered ANN.
- **Use multiple embedding models if helpful** — a small/fast model (e.g., bge-small or text-embedding-3-small) for high-volume passage retrieval, and optionally a larger model for entity-card and scene-summary embeddings where precision matters.

---

### Part 3 — Recommended Agentic System Design (Secondary Focus)

#### 3.1 Top-level architecture: deterministic orchestrator + specialized agents

Reject free-form multi-agent debate; adopt a **deterministic phase-gated pipeline** modeled on Novel-OS / StoryWriter / Re3 / DOC. Your orchestrator is a state machine; agents are pure-ish functions producing structured artifacts. Each phase has a hard quality gate.

Per-chapter loop:

```
1. PLAN     → Planner agent (Macro + Micro)
2. RETRIEVE → Retriever agent
3. DRAFT    → Drafter agent (one or more attempts)
4. CRITIQUE → Continuity Critic + Style Critic (parallel)
5. REVISE   → Drafter (targeted patches, not full rewrites)
6. UPDATE   → Extractor + State Updater
7. COMMIT   → write to Postgres, advance chapter pointer
```

Wrap this with a higher-level loop:

```
A. PROJECT INIT  → Premise → Worldbuilder → Architect (3-level hierarchical outline) → Style Profile
B. ARC PLAN      → Per-arc plan (every 3–8 chapters) re-checking pacing (CONCOCT-style) and thread coverage
C. CHAPTER LOOP  → as above
D. ARC REVIEW    → After each arc: re-validate commitments, re-summarize, optionally re-outline downstream
E. FINAL PASS    → Whole-book consistency sweep + line-edit pass
```

This is essentially DOC + DOME + StoryWriter merged with Novel-OS's gating discipline.

#### 3.2 The agent roster (minimal viable set — 7 agents)

1. **Architect (Planner-Macro).** Builds and maintains the hierarchical outline tree (Premise → Acts → Arcs/Subplots → Chapter intents → Scene intents). Adopts DOC's breadth-first expansion with filtering. Uses CONCOCT-style concreteness control to keep pacing uniform. Re-plans down-tree (DOME-style) when significant drift is detected by the critic.
2. **Planner-Micro (Scene Planner).** For each chapter, produces a **scene plan**: list of scenes, each with POV, location, time, present characters, scene goal, plot threads to advance, commitments (foreshadows) to plant or pay off, key facts that must be respected, target word count, target emotional beat, target prose style notes. This artifact is the contract the Drafter must satisfy and the Critic will verify against. **This scene plan is the single most important data structure in your runtime.**
3. **Retriever.** Given a scene plan, returns a structured context bundle. Hybrid retrieval pipeline (see 3.4 below). Output is *typed*: a list of canon facts, entity cards, recent state snapshots, recent passages, related events, active commitments, and style exemplars — not a raw text blob. The Drafter prompt template assembles this into sections.
4. **Drafter.** The only agent that produces prose. Takes scene plan + retrieved context. Generates one scene at a time. Should be your strongest model (Claude Opus / GPT-5-class for literary work). Use temperature 0.7–0.9 for prose, lower for dialogue continuity-heavy scenes.
5. **Continuity Critic.** A separate model invocation per scene that checks the draft against the knowledge base. Specifically validates: (a) every named entity matches canon (eye color, role, etc.); (b) every action is consistent with the actor's known location and possessions; (c) every utterance implying knowledge maps to a `knows` edge for that character; (d) timeline is consistent (no impossible travel); (e) commitments — was the foreshadow planted/paid off as planned, and were any prior commitments accidentally satisfied/broken?; (f) plot-thread advancement matches the plan. Output is structured findings with severity (FAIL/WARN/INFO), specific quotes, and proposed fixes.
6. **Style Critic.** Checks prose against the style profile and recent chapter style fingerprint. Flags drift (sentence-length distribution, vocab register, POV breaks, tense slips, dialogue-tag patterns). Output is structured edits.
7. **Extractor / State Updater.** After approval, parses the final chapter prose and emits the event log, entity-state deltas, knowledge-edge updates, commitment updates, and thread updates into Postgres. This is the agent you already built — keep it; it's the foundation.

A few notes:
- **Worldbuilder** can be a one-shot agent run during PROJECT INIT, then a tool the Architect calls when new world detail is needed mid-book.
- The **Critic should be a different model than the Drafter** when budget allows — heterogeneous critique catches errors a single model is blind to.
- Avoid "agent debate" loops; cap revision rounds at 2–3 and escalate to human-in-the-loop on persistent failure. Free-running revision often degrades quality (the StoryWriter and DOC papers both note this).

#### 3.3 Context-window management — three-tier compression

For each Drafter call, assemble context from three pools with explicit budgets:

- **Tier 1 (~30% of context budget): structured canon and active state.** Compact JSON/markdown: scene plan, current location card, present-character cards (state snapshots), active commitments, active plot-thread summaries, hard canon facts marked relevant. This tier is **never summarized** — it's the ground truth.
- **Tier 2 (~30%): hierarchical narrative summary.** The book-so-far at decreasing resolution as you go back in narrative time: previous chapter full summary, previous 3 chapters as one-paragraph each, previous arc as one-paragraph, earlier arcs as one-sentence. Follows the GraphRAG hierarchical-community-summary pattern at the narrative scale.
- **Tier 3 (~30%): retrieved relevant passages and exemplars.** Top-K (K≈5–10) raw passages from prior chapters surfaced by hybrid retrieval against the scene plan, plus 1–2 style exemplar passages. **Always include raw prose, not just summaries** — Sudowrite's Chapter Continuity reads up to 20,000 words of preceding raw prose for a reason; LLMs imitate style and tone from prose, not from summaries.

The remaining ~10% is system prompt, formatting instructions, and slack. Token-budget each tier explicitly and have a `compress_to_budget(tier, target_tokens)` function that progressively drops the least-relevant items.

#### 3.4 The retrieval pipeline

The Retriever agent's job is to turn a scene plan into a typed context bundle. Pipeline:

1. **Plan-driven exact lookup.** From the scene plan's named entities (e.g., "Aelric, Mira, Fogwood Keep"), pull entity cards and current state directly from Postgres. No vector search needed — these are foreign-key lookups.
2. **Graph expansion (1–2 hops).** From those entities, traverse the graph to pull immediate relationships, possessions, factions, ongoing conflicts. Use a depth/relevance budget so it doesn't explode.
3. **Commitment lookup.** Fetch all `pending` commitments whose trigger predicate might be satisfied in the scene's planned context. The Planner-Micro should also explicitly list commitments to advance.
4. **BM25 search.** Run BM25 over chapter passages, scene summaries, and event payloads using the scene plan's keywords + entity names + thread names. Catches literal token matches (proper nouns, specific items) that embeddings smear.
5. **Dense vector search.** pgvector HNSW over passages/scenes/events with metadata pre-filters (chapter_id < current; entity_ids intersect plan; thread_ids intersect plan). Query embedding is the scene plan text + the scene goal sentence.
6. **Fuse with Reciprocal Rank Fusion** (k≈60).
7. **Cross-encoder rerank** the fused top ~50 down to top ~10 (BGE-reranker-v2 or Cohere rerank-3 work well).
8. **Diversify with MMR** (λ≈0.7) to avoid five near-duplicate passages.
9. **Assemble bundle** with explicit provenance — every retrieved item tagged with chapter_id, scene_id, and a snippet — so the Drafter prompt can cite and the Critic can audit.

#### 3.5 Continuity verification — how to actually check

The Continuity Critic should not be a "read the chapter and tell me if anything is off" prompt. That's what current LLMs are bad at. Instead, run **a battery of typed checks**, each backed by a structured query:

- **Entity-mention check.** For each named-entity mention in the draft, fetch the entity card and ask the LLM (in a focused sub-prompt) whether the mention contradicts the card. Locked facts are hard checks.
- **Location/possession consistency.** Run a query: as of this chapter, is character X plausibly at location Y, possessing item Z? If not, flag.
- **Knowledge-state check.** Extract every utterance/inference in the draft that implies a character knows fact F. Check the `knows` table. If a character speaks knowledge they shouldn't have, flag with the specific quote.
- **Temporal-consistency check.** Run constraint propagation over events emitted by the extractor on the draft, combined with prior events. Flag impossibilities.
- **Commitment check.** Did we plant the foreshadows we planned to? Did we accidentally satisfy/break any unplanned commitments?
- **Thread coverage.** Did each thread the plan said we'd advance actually advance? (Use the extractor's `thread_event` output as evidence.)

Each check returns structured findings; the orchestrator decides FAIL (re-draft required), WARN (note for next chapter), or PASS. This is essentially Novel-OS's Continuity Guardian formalized, with the SCORE symbolic-state mechanism, DOME's Temporal Conflict Analyzer, and CFPG's commitment-pool semantics combined.

#### 3.6 Style continuity — narrower than people think

Three mechanisms in priority order:
1. **Style profile + exemplar passages in every Drafter prompt.** The profile is a structured object (POV, tense, register, sentence-rhythm targets, dialogue conventions) plus 2–3 representative passages embedded and retrieved when stylistically similar context is needed.
2. **Style Critic post-pass** computes the style fingerprint of the draft and compares to the rolling-window average. Flags drift; suggests targeted edits.
3. **Sample-conditioning at fine-tune time** if you go that far — a small LoRA on your target voice produces more stable style than prompting alone, but is overkill until you have ~50k+ words of confirmed in-voice prose.

#### 3.7 Concrete build order

Given what you already have, a pragmatic 6-phase rollout:

1. **Phase 1 — Schema upgrade (1–2 weeks).** Extend your existing schema with: event log table; bitemporal validity intervals on relationships and located_in / possesses edges; `knows` edges; `commitment` table; locked-canon flag; multi-granularity summaries; scene-level segmentation. Backfill from existing chapter extractions by re-running the extractor on prior chapters with the expanded schema.
2. **Phase 2 — Hybrid retriever (1–2 weeks).** Implement the BM25 + pgvector + RRF + cross-encoder rerank pipeline. Use this anywhere you previously did pure embedding search. Measure recall@10 on a held-out set of "given scene plan, retrieve the canonically-correct supporting passages" — a small handcrafted eval set of 20–50 scenes is enough to compare.
3. **Phase 3 — Scene Planner + structured context bundles (2–3 weeks).** Build the Planner-Micro agent that emits typed scene plans. Build the prompt-assembly module that turns retriever output + scene plan into a Drafter prompt. Re-route your existing drafting through this.
4. **Phase 4 — Continuity Critic with typed checks (2–3 weeks).** Build the six structured checks. Start with entity-mention + location/possession + knowledge-state — these are highest-ROI. Then add temporal and commitment checks. Wire as a quality gate before chapter commit.
5. **Phase 5 — Foreshadowing/commitment loop (1–2 weeks).** Add CFPG-style triple extraction to the post-chapter extractor; thread the commitment pool through the Planner and Drafter; add the commitment-pool check to the Critic.
6. **Phase 6 — Style Critic and style fingerprinting (1 week).** Often the smallest module. Layer in last.

After this, the next investments are: a graph-traversal retrieval mode for multi-hop questions; arc-level review automation; and (if budget allows) heterogeneous-model critique.

#### 3.8 What to leave out (for now)

- **Don't build a free-form multi-agent debate system.** They are slower, more expensive, and rarely beat phase-gated pipelines on long-form narrative tasks.
- **Don't fine-tune your own writer model until you've maxed out the architecture.** A good architecture around GPT-5 / Claude Opus beats a fine-tuned 70B in coherence; the gap is much wider than people expect.
- **Don't try to embed entire chapters as a single vector.** It's worse than embedding scenes or passages on every retrieval metric.
- **Don't use the LLM as your knowledge-state inference engine.** Track it explicitly; the LLM is for prose.

## Recommendations

**Immediate (next 2 weeks):**
- Add the event log, `knows` edges, `commitment` table, and bitemporal validity intervals to your existing schema. These are the four highest-leverage data-structure additions.
- Replace any pure-embedding retrieval with hybrid BM25 + dense + RRF + cross-encoder rerank. Use a tiny hand-built eval (~30 scenes with known canonical citations) to verify recall@10 improves.
- Lock high-stakes canon facts (eye color, names, magic-system rules) with a `locked` flag and have the Critic treat any contradiction as FAIL.

**Short-term (1–2 months):**
- Build a typed Scene Planner agent and route all drafting through scene plans, not free-form chapter prompts.
- Implement the six typed Continuity Critic checks; treat the Critic as a hard gate before chapter commit.
- Add CFPG-style commitment triples to the extractor and thread them through the loop.

**Medium-term (3–6 months):**
- Adopt the three-tier context-budget pattern (structured canon / hierarchical summary / retrieved prose) explicitly.
- Add a Style Critic with prose-fingerprint comparison.
- Add arc-level (every 3–8 chapters) re-planning and re-pacing checks (CONCOCT-style concreteness control on outline expansion).

**Benchmarks / thresholds that should change the plan:**
- If continuity errors per 10k words remain >2 after Phases 1–4, prioritize the temporal-consistency constraint solver and consider heterogeneous-model critique before moving to Phase 5.
- If retrieval recall@10 stays below ~0.85 on your eval set after Phase 2, consider adding a knowledge-graph traversal step *before* hybrid retrieval, not after.
- If style drift detected by the Style Critic exceeds ~15% of chapters, invest in sample-conditioning / LoRA before adding more agents.
- If the LLM frequently overrides the scene plan, your scene plans are too vague — push more concreteness into them (CONCOCT's lesson) before adding more agents or more retrieval.

**Operational disciplines:**
- Treat chapter generation as a database transaction: PLAN → DRAFT → CRITIQUE → REVISE → EXTRACT → COMMIT. No partial state escapes to the next chapter.
- Version every artifact (outline tree, scene plans, drafts, state snapshots) with chapter and revision identifiers so you can roll back cleanly.
- Always-on observability: log per-chapter token usage by tier, retrieval hit-rates, Critic finding counts by category, commitment-pool size and age. These tell you where the system is degrading.

## Caveats

- **The SCORE paper's headline metrics ("23.6% higher coherence on NCI-2.0," "89.7% emotional consistency on EASM," "41.8% fewer hallucinations") are abstract-only claims not substantiated by the experimental tables in the paper itself**; the metric names "NCI-2.0" and "EASM" never appear in the paper's body and were dropped in later revisions. The defensible claims from SCORE are the per-model gains in Tables 2–3 (e.g., GPT-4o gains ~7.7 points of coherence with SCORE, and the Dynamic State Tracking module is the dominant contributor per ablation). I cite the *mechanism* (symbolic state + structured summaries + sentiment-aware hybrid retrieval) as worth adopting, not the abstract's percentages.
- **Novel-OS** is a small personal repository (1 star, 7 commits at time of review). I cite its schema and gating pattern because they are well-designed and illustrate the pattern clearly, not because it is a battle-tested production system. Sudowrite, novelcrafter, NovelAI, and Zep/Graphiti are the more mature reference points.
- **Industry tools' continuity claims should be read as marketing.** Sudowrite blog claims and Novarrium's "Logic-Locking" branding are vendor self-reports; the academic benchmarks (ConStory-Bench, KNP/IKD) are the more reliable signal on the state of the art.
- **Long-context models do not eliminate the need for this architecture.** Even GPT-5-Reasoning, currently the lowest-CED model on ConStory-Bench (0.113), still makes consistency errors — and putting a 200k-token chapter history into a single prompt is expensive, lossy ("lost in the middle"), and uncheckable. Structured state + targeted retrieval is cheaper, more deterministic, and more debuggable.
- **Theory-of-mind / "who knows what" remains an open research problem.** Even the best LLM (Claude-4.5-Opus) reaches only 69% on KNP-style benchmarks (vs. 92% human baseline). Externalizing knowledge edges (the `knows` table) and having the Critic check them is the most reliable known mitigation, but it depends on the extractor correctly identifying knowledge-acquisition events — which is itself imperfect. Expect some manual cleanup, especially in early chapters.
- **Generation cost grows roughly linearly with agent count.** A 7-agent pipeline per chapter with strong models can easily cost on the order of $5–$20/chapter at current API prices for literary-quality output. Budget for this; consider routing low-stakes agents (extractor, style critic) to cheaper models via your LiteLLM abstraction.