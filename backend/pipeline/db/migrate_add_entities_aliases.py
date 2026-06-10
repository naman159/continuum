from __future__ import annotations

from pipeline.db.client import DBClient


def run() -> None:
    with DBClient() as db:
        db.execute("ALTER TABLE entities ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}'")
    print("Migration complete: aliases column added to entities (custom entity type dedup).")


if __name__ == "__main__":
    run()
