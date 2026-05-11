from __future__ import annotations

from pipeline.db.client import DBClient


def run() -> None:
    with DBClient() as db:
        db.execute(
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS involved_factions UUID[] DEFAULT '{}'"
        )
    print("Migration complete: involved_factions column added to events.")


if __name__ == "__main__":
    run()
