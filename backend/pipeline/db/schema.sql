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
    entity_type TEXT NOT NULL CHECK (entity_type IN ('character', 'location', 'faction', 'object')),
    name TEXT NOT NULL,
    UNIQUE(novel_id, entity_type, name)
);

CREATE INDEX IF NOT EXISTS idx_entities_novel ON entities(novel_id, entity_type);

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
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

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

CREATE INDEX IF NOT EXISTS idx_chapters_embedding
ON chapters USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_events_embedding
ON events USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
