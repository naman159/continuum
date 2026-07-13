from __future__ import annotations

import os

import pytest

from evals.harness import ingest_fixture, read_projections
from evals.loader import load_answer_key
from evals.scoring import score_entities, score_knowledge, score_possessions


def _fidelity_report(db, novel_id) -> dict:
    key = load_answer_key()
    got = read_projections(db, novel_id)
    report = {
        kind: score_entities(key["entities"][kind], got["entities"][kind])
        for kind in ("characters", "locations", "objects", "factions")
    }
    report["possessions"] = score_possessions(key["possessions"], got["possessions"])
    report["knowledge"] = score_knowledge(key["knowledge"], got["knowledge"])
    return report


def test_fidelity_harness_runs_in_mock_mode(db, golden_novel):
    """Plumbing test: the full ingest -> projections -> scoring path works.

    Mock extraction is not expected to match the answer key, so no
    thresholds here — only that every metric computes and is well-formed.
    """
    report = _fidelity_report(db, golden_novel)
    for kind in ("characters", "locations", "objects", "factions"):
        assert 0.0 <= report[kind]["recall"] <= 1.0
    assert 0.0 <= report["possessions"]["recall"] <= 1.0
    assert 0.0 <= report["knowledge"]["recall"] <= 1.0


@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="real-LLM extraction fidelity: set RUN_LLM_EVALS=1 (spends API credits)",
)
def test_extraction_fidelity_real_llm(db):
    novel_id = ingest_fixture(db, use_mock_llm=False)
    try:
        report = _fidelity_report(db, novel_id)
        print("\nextraction fidelity (real LLM):")
        for name, metrics in report.items():
            print(f"  {name}: {metrics}")
        assert report["characters"]["recall"] >= 0.75, report["characters"]
        assert report["locations"]["recall"] >= 0.75, report["locations"]
        assert report["objects"]["recall"] >= 1.0, report["objects"]
        assert report["possessions"]["recall"] >= 0.6, report["possessions"]
        assert report["knowledge"]["recall"] >= 0.5, report["knowledge"]
    finally:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
