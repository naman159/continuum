"""CLI: `novel-wiki-scenes` — browse extracted scene segmentation."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from api import queries


def main() -> None:
    parser = argparse.ArgumentParser(description="List scene segmentation for a novel")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--up-to-chapter", type=int, default=None,
                        help="Spoiler cap: include scenes from chapters <= this number")
    parser.add_argument("--chapter", type=int, default=None,
                        help="Show scenes for one specific chapter only")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    rows = queries.list_scenes(UUID(args.novel_id), args.up_to_chapter, args.chapter)

    if args.format == "json":
        print(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        print("# Scenes\n\n_No scenes recorded._")
        return

    print("# Scenes\n")
    last_chapter: int | None = None
    for s in rows:
        if s["chapter_number"] != last_chapter:
            print(f"\n## Chapter {s['chapter_number']}\n")
            last_chapter = s["chapter_number"]
        pov = s.get("pov_character_name") or "(no POV)"
        loc = s.get("location_name") or "(no location)"
        time_anchor = s.get("time_anchor") or ""
        print(f"### Scene {s['scene_index']} — POV: {pov} · {loc}"
              + (f" · {time_anchor}" if time_anchor else ""))
        if s.get("summary"):
            print(f"\n{s['summary']}\n")
        if s.get("present_character_names"):
            print(f"_Present: {', '.join(s['present_character_names'])}_\n")


if __name__ == "__main__":
    main()
