"""CLI: `novel-wiki-commitments` — list CFPG foreshadow/payoff commitments."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from api import queries


def main() -> None:
    parser = argparse.ArgumentParser(description="List foreshadow/payoff commitments")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--up-to-chapter", type=int, default=None)
    parser.add_argument(
        "--status",
        choices=["all", "pending", "satisfied", "broken", "abandoned"],
        default="all",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    rows = queries.list_commitments(UUID(args.novel_id), args.up_to_chapter, args.status)

    if args.format == "json":
        print(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        print("# Commitments\n\n_None recorded._")
        return

    print("# Commitments\n")
    by_status: dict[str, list[dict]] = {}
    for c in rows:
        by_status.setdefault(c["status"], []).append(c)

    for status in ("pending", "satisfied", "broken", "abandoned"):
        if status not in by_status:
            continue
        print(f"\n## {status.title()}\n")
        for c in by_status[status]:
            age = f" — {c['age_chapters']} chapters old" if c.get("age_chapters") else ""
            print(f"- **ch {c['foreshadow_chapter']}**{age}: {c['foreshadow_text']}")
            if c.get("payoff_text"):
                print(f"  - Paid off ch {c['payoff_chapter']}: {c['payoff_text']}")
            if c.get("related_entity_names"):
                print(f"  - Related: {', '.join(c['related_entity_names'])}")


if __name__ == "__main__":
    main()
