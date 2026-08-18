"""Run the continuity critic over already-ingested chapters.

Mirrors ``pipeline.state.cli``. The critic is decoupled from ingestion, so
this is how you critique a chapter that was ingested with ``CRITIC_ENABLED=false``,
re-run one whose critique failed transiently, or re-judge a back catalogue
after changing a check.

Re-running replaces the chapter's previous report (``persist_critique`` deletes
and rewrites), so it is safe to run repeatedly.

    python -m pipeline.critic.cli --novel-id <uuid>               # every chapter
    python -m pipeline.critic.cli --novel-id <uuid> --chapter 12  # just chapter 12
"""

from __future__ import annotations

import argparse
import json
import sys

from pipeline.critic.service import critique_chapter
from pipeline.db.client import DBClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the continuity critic over ingested chapters and persist reports.",
    )
    parser.add_argument("--novel-id", required=True, help="Novel UUID.")
    parser.add_argument(
        "--chapter", type=int, default=None,
        help="Chapter number to critique. Defaults to every chapter of the novel.",
    )
    parser.add_argument(
        "--mock-llm", action="store_true",
        help="Force mock mode (no LLM calls). Claims fall back to reused "
             "extraction output, so knowledge and possession checks stay quiet.",
    )
    args = parser.parse_args(argv)

    with DBClient() as db:
        if args.chapter is not None:
            numbers = [args.chapter]
        else:
            numbers = [
                r[0] for r in db.fetchall(
                    "SELECT number FROM chapters WHERE novel_id = %s ORDER BY number",
                    (args.novel_id,),
                )
            ]

        if not numbers:
            print(json.dumps({
                "novel_id": args.novel_id,
                "error": "no chapters found for novel",
            }))
            return 1

        results: list[dict] = []
        failed = 0
        for number in numbers:
            try:
                summary = critique_chapter(
                    db,
                    novel_id=args.novel_id,
                    chapter_number=number,
                    use_mock_llm=True if args.mock_llm else None,
                )
                results.append({"chapter": number, "critique": summary})
            except Exception as exc:
                # Keep going: one bad chapter should not abandon the rest of a
                # back-catalogue run. The non-zero exit code reports it.
                failed += 1
                results.append({"chapter": number, "error": str(exc)})

        print(json.dumps({
            "novel_id": args.novel_id,
            "chapters": len(numbers),
            "failed": failed,
            "results": results,
        }, indent=2))
        return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
