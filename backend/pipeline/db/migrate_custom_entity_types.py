from __future__ import annotations

from pipeline.db.client import DBClient


def migrate() -> None:
    with DBClient() as db:
        with db.cursor(commit=True) as cur:
            # Drop the hard-coded CHECK on entity_type (if it exists).
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'entities_entity_type_check'
                          AND conrelid = 'entities'::regclass
                    ) THEN
                        ALTER TABLE entities DROP CONSTRAINT entities_entity_type_check;
                    END IF;
                END $$;
            """)
            # Create novel_entity_types table.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS novel_entity_types (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT,
                    UNIQUE(novel_id, name)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_novel_entity_types_novel
                ON novel_entity_types(novel_id);
            """)
    print("Migration complete: custom entity types schema applied.")


if __name__ == "__main__":
    migrate()
