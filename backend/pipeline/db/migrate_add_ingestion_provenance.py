from __future__ import annotations

from pipeline.db.client import DBClient


def run() -> None:
    with DBClient() as db:
        db.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'human'")
        db.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS generation_meta JSONB")
        db.execute(
            "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS chapter_id "
            "UUID REFERENCES chapters(id) ON DELETE CASCADE"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_relationships_chapter ON relationships(chapter_id)"
        )
    print("Migration complete: chapters.source/generation_meta + relationships.chapter_id added.")


if __name__ == "__main__":
    run()
