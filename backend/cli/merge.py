"""CLI: `novel-wiki-merge-entity` — merge a duplicate entity into its canonical twin."""

from __future__ import annotations

import argparse
import json

from pipeline.db.client import DBClient
from pipeline.db.entity_merge import EntityMergeError, merge_entities


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge entity SOURCE into TARGET")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--source", required=True, help="entities.id to be absorbed (disappears)")
    parser.add_argument("--target", required=True, help="entities.id that survives")
    args = parser.parse_args()

    with DBClient() as db:
        try:
            result = merge_entities(
                db,
                novel_id=args.novel_id,
                source_entity_id=args.source,
                target_entity_id=args.target,
            )
        except EntityMergeError as exc:
            raise SystemExit(f"merge refused: {exc}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
