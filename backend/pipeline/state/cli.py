from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from pipeline.db.client import DBClient
from pipeline.state.materializer import StateMaterializer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize derived state projections from the event log.",
    )
    parser.add_argument("--novel-id", required=True, help="Novel UUID.")
    parser.add_argument(
        "--through-chapter",
        type=int,
        default=None,
        help="Replay events up to and including this chapter number. "
        "Defaults to the highest chapter number for the novel.",
    )
    args = parser.parse_args(argv)

    with DBClient() as db:
        through_chapter = args.through_chapter
        if through_chapter is None:
            row = db.fetchone(
                "SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s",
                (args.novel_id,),
            )
            through_chapter = int(row[0]) if row and row[0] is not None else 0

        if through_chapter <= 0:
            print(
                json.dumps(
                    {
                        "novel_id": args.novel_id,
                        "through_chapter": through_chapter,
                        "error": "no chapters found for novel",
                    }
                )
            )
            return 1

        result = StateMaterializer(db).materialize(args.novel_id, through_chapter)
        print(json.dumps(asdict(result)))
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
