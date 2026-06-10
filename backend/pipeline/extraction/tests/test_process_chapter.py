from __future__ import annotations

import inspect

from pipeline import pipeline as pipeline_mod


def test_process_chapter_accepts_db_replace_and_source():
    sig = inspect.signature(pipeline_mod.process_chapter)
    for param in ("db", "replace", "source", "generation_meta"):
        assert param in sig.parameters, f"process_chapter missing {param!r} param"


def test_persist_extraction_inserts_relationships_with_chapter_id():
    src = inspect.getsource(pipeline_mod._persist_extraction)
    assert "chapter_id" in src.split("INSERT INTO relationships")[1].split(")")[0], (
        "relationships INSERT must include chapter_id"
    )
