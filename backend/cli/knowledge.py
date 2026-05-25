"""CLI: `novel-wiki-knowledge` — who-knows-what + location/possession history."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from api import queries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect theory-of-mind and bitemporal state edges"
    )
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--up-to-chapter", type=int, default=None)
    parser.add_argument(
        "--view",
        choices=["knows", "locations", "possessions", "all"],
        default="all",
    )
    parser.add_argument("--character-id", default=None,
                        help="(knows view) restrict to one character")
    parser.add_argument("--only-active", action="store_true",
                        help="(locations/possessions) only currently-open edges")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    novel_id = UUID(args.novel_id)
    cap = args.up_to_chapter
    payload: dict[str, list] = {}

    if args.view in ("knows", "all"):
        char_id = UUID(args.character_id) if args.character_id else None
        payload["knows"] = queries.list_knows_edges(novel_id, cap, char_id)
    if args.view in ("locations", "all"):
        payload["locations_history"] = queries.list_location_edges(
            novel_id, cap, args.only_active
        )
    if args.view in ("possessions", "all"):
        payload["possessions"] = queries.list_possession_edges(
            novel_id, cap, args.only_active
        )

    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
        return

    if "knows" in payload:
        print("# Who knows what\n")
        by_char: dict[str, list] = {}
        for k in payload["knows"]:
            by_char.setdefault(k["character_name"], []).append(k)
        for name, edges in by_char.items():
            print(f"\n## {name}\n")
            for k in edges:
                shared = (
                    f" (shared with: {', '.join(k['shared_with_names'])})"
                    if k["shared_with_names"]
                    else ""
                )
                src = f" via {k['source_type']}" if k["source_type"] else ""
                print(f"- ch {k['learned_chapter']}{src}: {k['fact_description']}{shared}")

    if "locations_history" in payload:
        print("\n# Location history\n")
        for e in payload["locations_history"]:
            until = e["until_chapter"] if e["until_chapter"] is not None else "open"
            print(f"- {e['entity_name']} at {e['location_name']} "
                  f"(ch {e['since_chapter']} → {until})")

    if "possessions" in payload:
        print("\n# Possessions\n")
        for p in payload["possessions"]:
            until = p["until_chapter"] if p["until_chapter"] is not None else "held"
            print(f"- {p['character_name']} possesses {p['object_name']} "
                  f"(ch {p['since_chapter']} → {until})")


if __name__ == "__main__":
    main()
