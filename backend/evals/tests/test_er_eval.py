from __future__ import annotations

import os

import pytest

from evals.er_eval import count_duplicate_pairs, run_resolution_eval
from evals.harness import ingest_fixture
from evals.scoring import pairwise_resolution


def _print_resolution(report: dict) -> None:
    print(
        f"\nER pairwise: p={report['precision']:.2f} r={report['recall']:.2f} "
        f"f1={report['f1']:.2f} coverage={report['coverage']:.2f} "
        f"type_acc={report['type_accuracy']:.2f}"
    )
    for a, b in report["false_merges"]:
        print(f"  FALSE MERGE  {a!r} + {b!r}")
    for a, b in report["missed_merges"]:
        print(f"  missed merge {a!r} + {b!r}")
    for row in report["mistyped"]:
        print(f"  mistyped     {row['surface']!r} {row['expected']} -> {row['got']}")
    for surface in report["not_extracted"]:
        print(f"  not extracted {surface!r}")


def _print_duplicates(report: dict) -> None:
    print(
        f"\nduplicates: {report['entity_count']} entities, "
        f"{report['same_type_count']} same-type, "
        f"{report['cross_type_count']} cross-type"
    )
    for pair in report["cross_type_pairs"] + report["same_type_pairs"]:
        print(f"  {pair['score']:.2f} {pair['kind']:5} "
              f"{pair['a']['type']}:{pair['a']['name']!r} <-> "
              f"{pair['b']['type']}:{pair['b']['name']!r}")


def test_pairwise_resolution_perfect():
    truth = {"mira": "Mira Solen", "harbor-girl": "Mira Solen", "odo": "Odo Bram"}
    got = pairwise_resolution(truth, {"mira": "e1", "harbor-girl": "e1", "odo": "e2"})
    assert got["precision"] == 1.0 and got["recall"] == 1.0
    assert got["coverage"] == 1.0


def test_pairwise_resolution_separates_the_two_error_kinds():
    truth = {"mira": "Mira Solen", "harbor-girl": "Mira Solen", "kessa": "Kessa Dray"}
    # "kessa" welded onto Mira (false merge) and "harbor-girl" split off (miss).
    got = pairwise_resolution(truth, {"mira": "e1", "harbor-girl": "e2", "kessa": "e1"})
    assert got["false_merges"] == [("kessa", "mira")]
    assert got["missed_merges"] == [("harbor-girl", "mira")]
    assert got["precision"] == 0.0 and got["recall"] == 0.0


def test_pairwise_resolution_excludes_unextracted_surfaces():
    truth = {"mira": "Mira Solen", "harbor-girl": "Mira Solen"}
    got = pairwise_resolution(truth, {"mira": "e1"})
    # One surface never extracted: that is an extraction miss, so precision and
    # recall stay clean and coverage carries the signal instead.
    assert got["precision"] == 1.0 and got["recall"] == 1.0
    assert got["coverage"] == 0.5
    assert got["not_extracted"] == ["harbor-girl"]


def test_er_harness_runs_in_mock_mode(db, golden_novel):
    """Plumbing test: ingest -> read entities -> pairwise scoring all works.

    Mock extraction files nearly everything as a character, so type accuracy
    and coverage are meaningless here — thresholds live in the real-LLM test
    below. What still holds in mock mode is that a false merge must not appear,
    since that would mean the *scoring* is wrong rather than the extraction.
    """
    report = run_resolution_eval(db, golden_novel)
    _print_resolution(report)
    assert 0.0 <= report["f1"] <= 1.0
    assert 0.0 <= report["type_accuracy"] <= 1.0
    assert not report["false_merges"], report["false_merges"]

    dupes = count_duplicate_pairs(db, golden_novel)
    _print_duplicates(dupes)
    assert dupes["entity_count"] > 0
    assert dupes["same_type_count"] + dupes["cross_type_count"] == len(
        dupes["same_type_pairs"]
    ) + len(dupes["cross_type_pairs"])


@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="real-LLM entity resolution: set RUN_LLM_EVALS=1 (spends API credits)",
)
def test_entity_resolution_real_llm(db):
    novel_id = ingest_fixture(db, use_mock_llm=False)
    try:
        report = run_resolution_eval(db, novel_id)
        _print_resolution(report)
        dupes = count_duplicate_pairs(db, novel_id)
        _print_duplicates(dupes)

        # Precision before recall: a false merge welds two entities together
        # and drags every edge on both along with it, while a missed merge
        # leaves a visible duplicate row that the merge endpoint can repair.
        assert not report["false_merges"], report["false_merges"]
        assert report["recall"] >= 0.6, report["missed_merges"]
        assert report["type_accuracy"] >= 0.8, report["mistyped"]
        # Cross-type duplicates are unreachable by the canonicalizer (per-type
        # rosters) and only repairable by hand afterwards, so any at all on a
        # 10-chapter fixture is a regression worth failing on.
        assert dupes["cross_type_count"] == 0, dupes["cross_type_pairs"]
    finally:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
