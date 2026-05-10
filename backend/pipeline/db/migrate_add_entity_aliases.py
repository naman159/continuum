from __future__ import annotations

from pipeline.db.client import DBClient


def run() -> None:
    with DBClient() as db:
        db.execute("ALTER TABLE locations ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}'")
        db.execute("ALTER TABLE factions ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}'")
        db.execute("ALTER TABLE objects ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}'")
    print("Migration complete: aliases columns added to locations, factions, objects.")


if __name__ == "__main__":
    run()
