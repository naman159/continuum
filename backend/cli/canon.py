"""CLI: `novel-wiki-canon` — list canon facts (immutable + lockable)."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from api import queries


def main() -> None:
    parser = argparse.ArgumentParser(description="List canon facts for a novel")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--locked-only", action="store_true",
                        help="Show only locked facts (hard FAIL on contradiction)")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    rows = queries.list_canon_facts(UUID(args.novel_id), args.locked_only)

    if args.format == "json":
        print(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        print("# Canon Facts\n\n_No facts recorded._")
        return

    print("# Canon Facts\n")
    by_subject: dict[str, list[dict]] = {}
    for f in rows:
        by_subject.setdefault(f.get("subject_name") or "(unsubjected)", []).append(f)

    for subject, facts in by_subject.items():
        print(f"\n## {subject}\n")
        for f in facts:
            lock = " 🔒" if f["locked"] else ""
            src = f" — set ch {f['source_chapter']}" if f.get("source_chapter") else ""
            print(f"- `{f['predicate']}`: **{f['value']}**{lock}{src}")


if __name__ == "__main__":
    main()
