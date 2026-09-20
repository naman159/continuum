from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from pipeline.db.client import DBClient
from pipeline.state.materializer import StateMaterializer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize derived state projections from the event log. "
            "WARNING: --through-chapter below the novel's latest chapter truncates "
            "state_deltas replay there — every chapter after it loses its "
            "materialized character_states/located_in/possesses projection until a "
            "full re-run (a subsequent call with no --through-chapter, or one at/above "
            "the novel's latest chapter) restores them."
        ),
    )
    parser.add_argument("--novel-id", required=True, help="Novel UUID.")
    parser.add_argument(
        "--through-chapter",
        type=int,
        default=None,
        help="Replay events up to and including this chapter number. "
        "Defaults to the highest chapter number for the novel. "
        "WARNING: a value below the novel's latest chapter truncates later "
        "chapters' projections until a full re-run.",
    )
    args = parser.parse_args(argv)

    with DBClient() as db:
        result = StateMaterializer(db).materialize(args.novel_id, args.through_chapter)
        through_chapter = result.through_chapter

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

        print(json.dumps(asdict(result)))
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
