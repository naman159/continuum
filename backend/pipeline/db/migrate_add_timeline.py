from __future__ import annotations

from pipeline.db.client import DBClient


def migrate(db: DBClient) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS timeline (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            story_date TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            involved_characters UUID[] DEFAULT '{}',
            involved_locations UUID[] DEFAULT '{}',
            involved_objects UUID[] DEFAULT '{}',
            involved_factions UUID[] DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_timeline_novel ON timeline(novel_id, sort_order)
        """
    )


if __name__ == "__main__":
    with DBClient() as db:
        migrate(db)
        print("Migration complete: added timeline table")
