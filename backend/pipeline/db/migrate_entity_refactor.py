"""
One-shot migration: populate entities table, backfill entity_id FKs on type tables,
migrate relationships to use entities.id FKs, migrate character_states.relationships
JSONB to shared_dynamics rows.

Run: uv run python -m pipeline.db.migrate_entity_refactor
"""

from __future__ import annotations

from pipeline.db.client import DBClient

ENTITY_TYPES = [
    ("character", "characters"),
    ("location", "locations"),
    ("faction", "factions"),
    ("object", "objects"),
]


def migrate(db: DBClient) -> None:
    print("Step 1: Populate entities table from type tables...")
    for entity_type, table in ENTITY_TYPES:
        db.execute(
            f"""
            INSERT INTO entities (novel_id, entity_type, name)
            SELECT novel_id, %s, name FROM {table}
            ON CONFLICT (novel_id, entity_type, name) DO NOTHING
            """,
            (entity_type,),
        )
        print(f"  Inserted {entity_type} entities.")

    print("Step 2: Backfill entity_id on type tables...")
    for entity_type, table in ENTITY_TYPES:
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS entity_id UUID REFERENCES entities(id)",
            commit=True,
        )
        db.execute(
            f"""
            UPDATE {table} t
            SET entity_id = e.id
            FROM entities e
            WHERE e.novel_id = t.novel_id
              AND e.entity_type = %s
              AND e.name = t.name
              AND t.entity_id IS NULL
            """,
            (entity_type,),
        )
        print(f"  Backfilled entity_id on {table}.")

    print("Step 3: Migrate relationships to use entities.id FKs...")
    # Add temp columns for new FKs
    db.execute(
        "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS new_entity_a_id UUID",
        commit=True,
    )
    db.execute(
        "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS new_entity_b_id UUID",
        commit=True,
    )
    db.execute(
        "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS from_chapter INTEGER",
        commit=True,
    )

    # Populate new_entity_a_id / new_entity_b_id using type-specific tables
    for entity_type, table in ENTITY_TYPES:
        db.execute(
            f"""
            UPDATE relationships r
            SET new_entity_a_id = t.entity_id
            FROM {table} t
            WHERE r.entity_a_type = %s
              AND r.entity_a_id = t.id
              AND r.new_entity_a_id IS NULL
            """,
            (entity_type,),
        )
        db.execute(
            f"""
            UPDATE relationships r
            SET new_entity_b_id = t.entity_id
            FROM {table} t
            WHERE r.entity_b_type = %s
              AND r.entity_b_id = t.id
              AND r.new_entity_b_id IS NULL
            """,
            (entity_type,),
        )

    # Populate from_chapter from chapter_id
    db.execute("""
        UPDATE relationships r
        SET from_chapter = ch.number
        FROM chapters ch
        WHERE ch.id = r.chapter_id
          AND r.from_chapter IS NULL
        """)

    # Drop old columns, rename new ones
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_a_id")
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_b_id")
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_a_type")
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_b_type")
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS status")
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS chapter_id")
    db.execute("ALTER TABLE relationships RENAME COLUMN new_entity_a_id TO entity_a_id")
    db.execute("ALTER TABLE relationships RENAME COLUMN new_entity_b_id TO entity_b_id")
    print("  relationships migrated.")

    print("Step 4: Migrate character_states.relationships JSONB to shared_dynamics...")
    rows = db.fetchall(
        """
        SELECT cs.id, cs.character_id, cs.chapter_id, cs.relationships
        FROM character_states cs
        WHERE cs.relationships IS NOT NULL AND cs.relationships != '{}'::jsonb
        """,
        dict_rows=True,
    )
    inserted = 0
    for row in rows:
        char_entity = db.fetchone(
            "SELECT entity_id FROM characters WHERE id = %s",
            (str(row["character_id"]),),
        )
        if not char_entity or not char_entity[0]:
            continue
        a_entity_id = str(char_entity[0])

        rels: dict = (
            row["relationships"] if isinstance(row["relationships"], dict) else {}
        )
        for target_name, description in rels.items():
            target = db.fetchone(
                """
                SELECT c.entity_id FROM characters c
                JOIN chapters ch ON ch.id = %s
                WHERE c.novel_id = ch.novel_id
                  AND (lower(c.name) = lower(%s)
                       OR EXISTS (SELECT 1 FROM unnest(c.aliases) alias WHERE lower(alias) = lower(%s)))
                LIMIT 1
                """,
                (str(row["chapter_id"]), str(target_name), str(target_name)),
            )
            if not target or not target[0]:
                continue
            b_entity_id = str(target[0])
            db.execute(
                """
                INSERT INTO shared_dynamics (entity_a_id, entity_b_id, chapter_id, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (a_entity_id, b_entity_id, str(row["chapter_id"]), str(description)),
            )
            inserted += 1
    print(f"  Inserted {inserted} shared_dynamics rows from character_states.")

    print("Step 5: Drop character_states.relationships column...")
    db.execute("ALTER TABLE character_states DROP COLUMN IF EXISTS relationships")
    print("  Done.")


def main() -> None:
    print("Starting entity refactor migration...")
    with DBClient() as db:
        migrate(db)
    print("Migration complete.")


if __name__ == "__main__":
    main()
