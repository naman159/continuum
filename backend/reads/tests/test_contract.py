"""The read-layer contract, enforced structurally.

1. No SQL in surfaces: api/routes/* and mcp_server/server.py contain no SQL.
2. Every public reads function (except documented exceptions) accepts
   up_to_chapter.
"""

from __future__ import annotations

import inspect
import pkgutil
import re
from importlib import import_module
from pathlib import Path

import reads

SQL_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\bFROM\b|\bINSERT INTO\b", re.I | re.S)

# Functions that legitimately take no cutoff (registry/config reads).
CUTOFF_EXEMPT = {
    ("reads.novels", "list_novels"),
    ("reads.novels", "get_novel"),
    ("reads.world", "list_entity_types"),
    ("reads.continuity", "get_chapter_critique"),  # keyed by explicit chapter
    # Pure label-classification helpers: no `db`/`novel_id` param at all, no
    # SQL, no point-in-time state to cut off. They're exported (not
    # underscore-prefixed) because reads.world/graphs/characters share them
    # to compute a relationship's `symmetric` flag from data those modules
    # already fetched under their own cutoff.
    ("reads.relationship_types", "is_symmetric"),
    ("reads.relationship_types", "resolve_symmetric"),
    # Review-queue reads: a parked draft is not canon and has no
    # point-in-time semantics, so there is nothing to cut off.
    ("reads.drafts", "list_submissions"),
    ("reads.drafts", "get_submission"),
    ("reads.drafts", "count_pending"),
}


def test_no_sql_in_surfaces():
    backend = Path(__file__).resolve().parents[2]
    surface_files = list((backend / "api" / "routes").glob("*.py"))
    surface_files.append(backend / "mcp_server" / "server.py")
    offenders = [
        str(f) for f in surface_files if SQL_PATTERN.search(f.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"SQL found in surface files: {offenders}"


def test_reads_functions_take_up_to_chapter():
    missing = []
    for mod_info in pkgutil.iter_modules(reads.__path__):
        if mod_info.name in {"db", "common", "tests"}:
            continue
        module = import_module(f"reads.{mod_info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            if (module.__name__, name) in CUTOFF_EXEMPT:
                continue
            if "up_to_chapter" not in inspect.signature(fn).parameters:
                missing.append(f"{module.__name__}.{name}")
    assert missing == [], f"reads functions missing up_to_chapter: {missing}"
