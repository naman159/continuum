CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS novels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    author TEXT,
    language TEXT DEFAULT 'en',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chapters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT,
    raw_text TEXT NOT NULL,
    summary TEXT,
    embedding VECTOR(__EMBEDDING_DIM__),
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(novel_id, number)
);

CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases TEXT[] DEFAULT '{}',
    UNIQUE(novel_id, entity_type, name)
);

ALTER TABLE entities ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}';

-- entity_type used to be CHECK-constrained to the four built-in types. Custom
-- entity types (novel_entity_types, below) made that wrong, and the CREATE
-- TABLE above dropped it -- but CREATE TABLE IF NOT EXISTS is a no-op on a
-- database that already has the table, so every DB created before the custom
-- types landed still carries the old constraint and rejects custom entities at
-- INSERT. Drop it here, where existing databases actually get brought forward.
ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_entity_type_check;

CREATE INDEX IF NOT EXISTS idx_entities_novel ON entities(novel_id, entity_type);

CREATE TABLE IF NOT EXISTS novel_entity_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_novel_entity_types_novel ON novel_entity_types(novel_id);

CREATE TABLE IF NOT EXISTS characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    aliases TEXT[] DEFAULT '{}',
    first_appearance_chapter INTEGER,
    description TEXT,
    embedding VECTOR(__EMBEDDING_DIM__),
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_characters_novel_name ON characters(novel_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_characters_entity_id ON characters(entity_id);

CREATE TABLE IF NOT EXISTS locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    aliases TEXT[] DEFAULT '{}',
    parent_location_id UUID REFERENCES locations(id),
    first_appearance_chapter INTEGER,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_locations_novel_name ON locations(novel_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_locations_entity_id ON locations(entity_id);

CREATE TABLE IF NOT EXISTS factions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    aliases TEXT[] DEFAULT '{}',
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_factions_novel_name ON factions(novel_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_factions_entity_id ON factions(entity_id);

CREATE TABLE IF NOT EXISTS objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    aliases TEXT[] DEFAULT '{}',
    significance TEXT,
    first_appearance_chapter INTEGER,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_objects_novel_name ON objects(novel_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_objects_entity_id ON objects(entity_id);

CREATE TABLE IF NOT EXISTS character_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    location_id UUID REFERENCES locations(id),
    emotional_state TEXT,
    goals TEXT,
    knowledge TEXT[] DEFAULT '{}',
    physical_state TEXT,
    appearance TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Retrofit: added to the CREATE TABLE body after the first databases existed,
-- where CREATE TABLE IF NOT EXISTS could not add it. StateMaterializer INSERTs
-- this column by name, so a database missing it fails every state write.
ALTER TABLE character_states ADD COLUMN IF NOT EXISTS appearance TEXT;

CREATE INDEX IF NOT EXISTS idx_character_states_character_chapter
ON character_states(character_id, chapter_id);

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    event_type TEXT,
    impact_level TEXT,
    involved_characters UUID[] DEFAULT '{}',
    involved_locations UUID[] DEFAULT '{}',
    involved_objects UUID[] DEFAULT '{}',
    involved_factions UUID[] DEFAULT '{}',
    embedding VECTOR(__EMBEDDING_DIM__),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_chapter ON events(chapter_id);

CREATE TABLE IF NOT EXISTS relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel_type TEXT,
    from_chapter INTEGER,
    to_chapter INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    CHECK (entity_a_id <> entity_b_id)
);

CREATE INDEX IF NOT EXISTS idx_relationships_a ON relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_relationships_b ON relationships(entity_b_id);

CREATE TABLE IF NOT EXISTS shared_dynamics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    CHECK (entity_a_id <> entity_b_id),
    UNIQUE(entity_a_id, entity_b_id, chapter_id)
);

CREATE INDEX IF NOT EXISTS idx_shared_dynamics_entities ON shared_dynamics(entity_a_id, entity_b_id);
CREATE INDEX IF NOT EXISTS idx_shared_dynamics_chapter ON shared_dynamics(chapter_id);

CREATE TABLE IF NOT EXISTS plot_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'open',
    opened_chapter INTEGER,
    closed_chapter INTEGER,
    thread_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(novel_id, title)
);

CREATE INDEX IF NOT EXISTS idx_plot_threads_novel_status ON plot_threads(novel_id, status);

CREATE TABLE IF NOT EXISTS thread_events (
    thread_id UUID NOT NULL REFERENCES plot_threads(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    impact TEXT,
    PRIMARY KEY (thread_id, event_id)
);

CREATE TABLE IF NOT EXISTS continuity_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    flag_type TEXT,
    resolved BOOLEAN DEFAULT false,
    resolved_chapter_id UUID REFERENCES chapters(id),
    created_at TIMESTAMPTZ DEFAULT now()
);


-- =====================================================================
-- SOTA upgrade: bitemporal edges, knowledge graph, commitments, scenes
-- Added on branch closing-the-gap-with-sota. See architecture-recommendations.html.
-- All statements are idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- =====================================================================

-- ---- Multi-granularity summaries on chapters ----
-- `summary` (existing) stays as the medium-granularity (~150 word) summary.
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS summary_short TEXT;
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS summary_long TEXT;
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS style_fingerprint JSONB;

-- ---- Relationships: invalidate-don't-delete + evidence ----
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS superseded_by_id UUID REFERENCES relationships(id);
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS evidence_event_ids UUID[] DEFAULT '{}';
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS sentiment FLOAT;
-- NULL = extractor didn't judge it; the API falls back to a static label heuristic
-- (reads/relationship_types.py). TRUE/FALSE = the extractor read the chapter text and
-- judged whether the relationship is genuinely mutual or reflects one side's view
-- (e.g. A considers B a friend, but B doesn't feel the same).
-- "symmetric" is a fully reserved PostgreSQL keyword (it comes from BETWEEN
-- SYMMETRIC), so it needs quoting anywhere it appears as a bare identifier --
-- here in the DDL and in INSERT/UPDATE column lists alike. It parses unquoted
-- only after a dot (SELECT r.symmetric) or as an AS label.
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS "symmetric" BOOLEAN;

-- ---- Ingestion provenance + replayability ----
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'human';
-- relationships become traceable to the chapter that asserted them; rows with a
-- non-NULL chapter_id cascade-delete when that chapter is replaced (rows created
-- before this column existed have NULL and are not covered).
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS chapter_id UUID REFERENCES chapters(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_relationships_chapter ON relationships(chapter_id);

-- ---- Scene-level segmentation ----
CREATE TABLE IF NOT EXISTS scenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    scene_index INTEGER NOT NULL,
    pov_character_id UUID REFERENCES characters(id),
    location_id UUID REFERENCES locations(id),
    time_anchor TEXT,
    story_time_ordinal INTEGER,
    present_characters UUID[] DEFAULT '{}',
    summary TEXT,
    embedding VECTOR(__EMBEDDING_DIM__),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(chapter_id, scene_index)
);
CREATE INDEX IF NOT EXISTS idx_scenes_chapter ON scenes(chapter_id);

-- ---- Canon facts (immutable, lockable) ----
CREATE TABLE IF NOT EXISTS canon_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    subject_entity_id UUID REFERENCES entities(id),
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    source_chapter INTEGER,
    confidence FLOAT DEFAULT 1.0,
    locked BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(novel_id, subject_entity_id, predicate)
);
CREATE INDEX IF NOT EXISTS idx_canon_facts_subject ON canon_facts(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_canon_facts_novel_locked ON canon_facts(novel_id, locked);

-- ---- Knowledge graph: who-knows-what (theory of mind) ----
CREATE TABLE IF NOT EXISTS knows_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    fact_description TEXT NOT NULL,
    learned_chapter INTEGER NOT NULL,
    source_event_id UUID REFERENCES events(id),
    source_type TEXT CHECK (source_type IN (
        'dialogue','observation','inference','witnessed','told','assumed'
    )),
    certainty FLOAT DEFAULT 1.0,
    shared_with UUID[] DEFAULT '{}',
    superseded_by_id UUID REFERENCES knows_edges(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_knows_character ON knows_edges(character_id, learned_chapter);
CREATE INDEX IF NOT EXISTS idx_knows_active ON knows_edges(character_id) WHERE superseded_by_id IS NULL;

-- ---- Bitemporal possession edges ----
CREATE TABLE IF NOT EXISTS possesses_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    object_id UUID NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    since_chapter INTEGER NOT NULL,
    until_chapter INTEGER,
    evidence_event_id UUID REFERENCES events(id),
    certainty FLOAT DEFAULT 1.0,
    superseded_by_id UUID REFERENCES possesses_edges(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_possesses_character ON possesses_edges(character_id);
CREATE INDEX IF NOT EXISTS idx_possesses_object ON possesses_edges(object_id);
CREATE INDEX IF NOT EXISTS idx_possesses_active
    ON possesses_edges(character_id, since_chapter)
    WHERE until_chapter IS NULL;

-- ---- Bitemporal location edges (entity_id can be character/object/etc.) ----
CREATE TABLE IF NOT EXISTS located_in_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    location_id UUID NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    since_chapter INTEGER NOT NULL,
    until_chapter INTEGER,
    evidence_event_id UUID REFERENCES events(id),
    certainty FLOAT DEFAULT 1.0,
    superseded_by_id UUID REFERENCES located_in_edges(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_located_in_entity ON located_in_edges(entity_id);
CREATE INDEX IF NOT EXISTS idx_located_in_location ON located_in_edges(location_id);
CREATE INDEX IF NOT EXISTS idx_located_in_active
    ON located_in_edges(entity_id, since_chapter)
    WHERE until_chapter IS NULL;

-- ---- CFPG foreshadow / payoff commitments ----
CREATE TABLE IF NOT EXISTS commitments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    foreshadow_text TEXT NOT NULL,
    foreshadow_chapter INTEGER NOT NULL,
    foreshadow_event_id UUID REFERENCES events(id),
    trigger_predicate JSONB,
    payoff_text TEXT,
    payoff_chapter INTEGER,
    payoff_event_id UUID REFERENCES events(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','satisfied','broken','abandoned')),
    weight FLOAT DEFAULT 1.0,
    related_entity_ids UUID[] DEFAULT '{}',
    embedding VECTOR(__EMBEDDING_DIM__),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_commitments_novel_status ON commitments(novel_id, status);
CREATE INDEX IF NOT EXISTS idx_commitments_pending
    ON commitments(novel_id, foreshadow_chapter)
    WHERE status = 'pending';

-- ---- Materialized state runs (audit of derived projections) ----
CREATE TABLE IF NOT EXISTS materialized_state_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    through_chapter INTEGER NOT NULL,
    materialized_at TIMESTAMPTZ DEFAULT now(),
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_materialized_state_novel
    ON materialized_state_runs(novel_id, through_chapter DESC);

-- =====================================================================
-- Write-spine consolidation (see docs/superpowers/specs/
-- 2026-07-11-continuum-analyzer-architecture-design.md).
-- Idempotent DROPs converge databases created before the consolidation.
-- =====================================================================
DROP TABLE IF EXISTS temporal_constraints;
ALTER TABLE events DROP COLUMN IF EXISTS subject_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS verb;
ALTER TABLE events DROP COLUMN IF EXISTS object_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS story_time_ordinal;
ALTER TABLE events DROP COLUMN IF EXISTS narrative_order;
ALTER TABLE events DROP COLUMN IF EXISTS scene_id;
ALTER TABLE knows_edges DROP COLUMN IF EXISTS fact_id;
ALTER TABLE chapters DROP COLUMN IF EXISTS generation_meta;

-- ---- Typed state deltas: the extraction-time event log for state ----
-- Tier-2 rows: expensive LLM output, immutable, cascade-deleted with their
-- chapter. The materializer folds them into character_states and the
-- bitemporal edges; nothing else interprets prose for state.
CREATE TABLE IF NOT EXISTS state_deltas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    event_id UUID REFERENCES events(id) ON DELETE SET NULL,
    -- narrative order within the chapter; created_at can't order rows written
    -- in one transaction (now() is transaction-stable) and UUIDs are random.
    ordinal INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL CHECK (kind IN ('possession','location','knowledge','status')),
    subject_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    object_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    location_id UUID REFERENCES locations(id) ON DELETE CASCADE,
    change TEXT CHECK (change IN ('gain','loss','move','learn','update')),
    attribute TEXT,
    detail TEXT,
    certainty FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_state_deltas_chapter ON state_deltas(chapter_id);
CREATE INDEX IF NOT EXISTS idx_state_deltas_subject ON state_deltas(subject_id);

-- ---- Persisted continuity critique, one report per chapter ----
CREATE TABLE IF NOT EXISTS critique_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL UNIQUE REFERENCES chapters(id) ON DELETE CASCADE,
    passed BOOLEAN NOT NULL,
    ran_at TIMESTAMPTZ DEFAULT now(),
    stats JSONB
);
CREATE TABLE IF NOT EXISTS critique_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES critique_reports(id) ON DELETE CASCADE,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('fail','warn','info')),
    message TEXT NOT NULL,
    quote TEXT,
    evidence JSONB
);
CREATE INDEX IF NOT EXISTS idx_critique_findings_report ON critique_findings(report_id);

-- ---- Agent draft submissions parked for human review ----
CREATE TABLE IF NOT EXISTS draft_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    title TEXT,
    raw_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','accepted','rejected')),
    findings JSONB NOT NULL,
    submitted_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_draft_submissions_pending
    ON draft_submissions(novel_id, chapter_number) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_draft_submissions_novel
    ON draft_submissions(novel_id, submitted_at DESC);

-- ---- Schema version (single row, stamped by init-db) ----
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT now()
);

-- ---- Full-text search (BM25 component of hybrid retrieval) ----
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS search_tsv tsvector;
ALTER TABLE scenes   ADD COLUMN IF NOT EXISTS search_tsv tsvector;
ALTER TABLE events   ADD COLUMN IF NOT EXISTS search_tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_chapters_tsv ON chapters USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_scenes_tsv   ON scenes   USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_events_tsv   ON events   USING GIN (search_tsv);

-- ---- Embedding provenance ----
-- Which model produced each stored vector. Without this, a hash vector (from
-- a USE_MOCK_LLM ingest) is indistinguishable from a real embedding forever,
-- and a store that mixes the two silently returns near-random dense results.
-- Mixing is the normal case, not an exotic one: ingesting with mock and later
-- switching to a real model is the default development path.
-- NULL means "written before this column existed" — provenance unknown.
ALTER TABLE chapters    ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE events      ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE scenes      ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS embedding_model TEXT;

-- ---- events.involved_* array containment ----
-- These UUID[] columns are the read layer's primary access path: every
-- character/location/object/faction detail page runs `%s = ANY(e.involved_*)`,
-- and reads/graphs.py self-joins events against characters on one of them.
-- Without GIN, each of those is a sequential scan over events, whose heap
-- tuples carry a VECTOR and a tsvector apiece.
CREATE INDEX IF NOT EXISTS idx_events_involved_characters
    ON events USING GIN (involved_characters);
CREATE INDEX IF NOT EXISTS idx_events_involved_locations
    ON events USING GIN (involved_locations);
CREATE INDEX IF NOT EXISTS idx_events_involved_objects
    ON events USING GIN (involved_objects);
CREATE INDEX IF NOT EXISTS idx_events_involved_factions
    ON events USING GIN (involved_factions);

-- ---- FK columns driving the delete cascade ----
-- Deleting a chapter cascades to its events; Postgres then enforces every
-- referencing column with a per-deleted-row lookup. Unindexed, that is a
-- sequential scan per event per table, which is what makes --replace on a
-- mature novel take minutes while holding write locks.
CREATE INDEX IF NOT EXISTS idx_continuity_flags_chapter
    ON continuity_flags(chapter_id);
CREATE INDEX IF NOT EXISTS idx_continuity_flags_resolved_chapter
    ON continuity_flags(resolved_chapter_id);
CREATE INDEX IF NOT EXISTS idx_thread_events_event ON thread_events(event_id);
CREATE INDEX IF NOT EXISTS idx_character_states_chapter ON character_states(chapter_id);
CREATE INDEX IF NOT EXISTS idx_character_states_location ON character_states(location_id);
CREATE INDEX IF NOT EXISTS idx_scenes_pov_character ON scenes(pov_character_id);
CREATE INDEX IF NOT EXISTS idx_scenes_location ON scenes(location_id);
CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_location_id);
CREATE INDEX IF NOT EXISTS idx_shared_dynamics_entity_b ON shared_dynamics(entity_b_id);
CREATE INDEX IF NOT EXISTS idx_state_deltas_event ON state_deltas(event_id);
CREATE INDEX IF NOT EXISTS idx_state_deltas_object ON state_deltas(object_id);
CREATE INDEX IF NOT EXISTS idx_state_deltas_location ON state_deltas(location_id);
CREATE INDEX IF NOT EXISTS idx_commitments_foreshadow_event
    ON commitments(foreshadow_event_id);
CREATE INDEX IF NOT EXISTS idx_commitments_payoff_event ON commitments(payoff_event_id);
CREATE INDEX IF NOT EXISTS idx_knows_source_event ON knows_edges(source_event_id);
CREATE INDEX IF NOT EXISTS idx_possesses_evidence_event
    ON possesses_edges(evidence_event_id);
CREATE INDEX IF NOT EXISTS idx_located_in_evidence_event
    ON located_in_edges(evidence_event_id);

CREATE OR REPLACE FUNCTION chapters_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.search_tsv :=
    setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(NEW.summary_short, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(NEW.summary_long, '')), 'C') ||
    setweight(to_tsvector('english', coalesce(NEW.raw_text, '')), 'D');
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS chapters_tsv_trigger ON chapters;
CREATE TRIGGER chapters_tsv_trigger BEFORE INSERT OR UPDATE ON chapters
FOR EACH ROW EXECUTE FUNCTION chapters_tsv_update();

CREATE OR REPLACE FUNCTION scenes_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.search_tsv := setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'A');
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS scenes_tsv_trigger ON scenes;
CREATE TRIGGER scenes_tsv_trigger BEFORE INSERT OR UPDATE ON scenes
FOR EACH ROW EXECUTE FUNCTION scenes_tsv_update();

CREATE OR REPLACE FUNCTION events_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.search_tsv := setweight(to_tsvector('english', coalesce(NEW.description, '')), 'A');
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS events_tsv_trigger ON events;
CREATE TRIGGER events_tsv_trigger BEFORE INSERT OR UPDATE ON events
FOR EACH ROW EXECUTE FUNCTION events_tsv_update();

-- ---- Switch IVFFlat -> HNSW (better recall at novel scale) ----
DROP INDEX IF EXISTS idx_chapters_embedding;
DROP INDEX IF EXISTS idx_events_embedding;
CREATE INDEX IF NOT EXISTS idx_chapters_embedding_hnsw
    ON chapters USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_events_embedding_hnsw
    ON events USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_scenes_embedding_hnsw
    ON scenes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_commitments_embedding_hnsw
    ON commitments USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_characters_embedding_hnsw
    ON characters USING hnsw (embedding vector_cosine_ops);
