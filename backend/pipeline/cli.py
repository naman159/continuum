"""Command-line interface for the pipeline.

Kept out of ``pipeline.py`` so the write spine stays importable as a library
without dragging in argparse, stdin handling, or process exit semantics.
Every subcommand here is a thin shell over a function in ``pipeline.pipeline``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.config import settings
from pipeline.pipeline import (
    analyze_chapter,
    create_novel,
    init_db,
    list_novels,
)


def _read_chapter_text(file_path: str | None) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")

    if not sys.stdin.isatty():
        return sys.stdin.read()

    print("Paste chapter text. Submit EOF (Ctrl-D) when done:")
    lines: list[str] = []
    try:
        while True:
            lines.append(input())
    except EOFError:
        pass
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Novel knowledge pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db", help="Initialize database schema")
    init_db_parser.add_argument("--schema", default=None, help="Path to schema.sql (defaults to bundled)")

    create_novel_parser = subparsers.add_parser("create-novel", help="Create a novel record")
    create_novel_parser.add_argument("--title", required=True)
    create_novel_parser.add_argument("--author")
    create_novel_parser.add_argument("--language", default="en")

    subparsers.add_parser("list-novels", help="List novels")

    process_parser = subparsers.add_parser("process-chapter", help="Process one chapter")
    process_parser.add_argument("--novel-id", required=True)
    process_parser.add_argument("--number", required=True, type=int)
    process_parser.add_argument("--title")
    process_parser.add_argument("--file", help="Path to chapter text file")
    process_parser.add_argument("--mock-llm", action="store_true", help="Use deterministic mock extraction")
    process_parser.add_argument("--chunk-size", type=int, default=settings.chunk_size)
    process_parser.add_argument("--chunk-overlap", type=int, default=settings.chunk_overlap)
    process_parser.add_argument(
        "--replace", action="store_true",
        help="Delete this chapter's previously extracted data and re-process it",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        init_db(args.schema)
        print(json.dumps({"status": "ok", "schema": args.schema}, indent=2))
        return

    if args.command == "create-novel":
        novel_id = create_novel(args.title, args.author, args.language)
        print(json.dumps({"novel_id": novel_id, "title": args.title}, indent=2))
        return

    if args.command == "list-novels":
        print(json.dumps(list_novels(), indent=2, default=str))
        return

    if args.command == "process-chapter":
        chapter_text = _read_chapter_text(args.file)
        if not chapter_text.strip():
            raise SystemExit("Chapter text is empty.")

        result = analyze_chapter(
            novel_id=args.novel_id,
            chapter_number=args.number,
            raw_text=chapter_text,
            chapter_title=args.title,
            use_mock_llm=True if args.mock_llm else None,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            replace=args.replace,
        )
        print(json.dumps(result, indent=2))
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
