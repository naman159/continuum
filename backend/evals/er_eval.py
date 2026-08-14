"""Entity-resolution eval: does the pipeline put the right surface forms under
the right entity, and how many duplicate entities does it leave behind?

Two independent measurements, because they answer different questions:

`run_resolution_eval` grades the golden novel against hand-labelled alias sets
(answer_key.yaml `resolution:`). It needs ground truth, so it only runs on the
fixture.

`count_duplicate_pairs` needs no ground truth at all — it finds entities that
*look* like duplicates of each other using the canonicalizer's own similarity
function and threshold, so it can be pointed at any ingested novel. It splits
the count by same-type versus cross-type, which matters more than the total:
the canonicalizer loads its roster per entity type
(`EntityCanonicalizer._load_roster`), so a cross-type duplicate is outside what
the pipeline can fix on its own. It is repairable by hand — `entity_merge`
treats a cross-type merge as a reclassification, and `/entities/duplicates`
surfaces the candidates — but nothing detects it at ingest.
"""

from __future__ import annotations

from typing import Any

from evals.loader import load_answer_key
from evals.scoring import pairwise_resolution
from pipeline.db.duplicates import find_duplicate_candidates, load_entity_surfaces
from pipeline.extraction.canonicalizer import normalize_name


def run_resolution_eval(db: Any, novel_id: str) -> dict[str, Any]:
    """Pairwise precision/recall of surface->entity assignment, plus the rate
    at which entities land under the expected entity_type."""
    key = load_answer_key()
    truth: dict[str, str] = {}
    expected_type: dict[str, str] = {}
    for entry in key["resolution"]:
        for surface in entry["surfaces"]:
            truth[normalize_name(surface)] = entry["canonical"]
            expected_type[normalize_name(surface)] = entry["type"]

    entities = load_entity_surfaces(db, novel_id)
    predicted: dict[str, str] = {}
    predicted_type: dict[str, str] = {}
    for ent in entities:
        for surface in ent["surfaces"]:
            # First writer wins: a surface claimed by two entities is itself a
            # duplicate, and count_duplicate_pairs is what reports that.
            predicted.setdefault(surface, ent["id"])
            predicted_type.setdefault(surface, ent["type"])

    report = pairwise_resolution(truth, predicted)
    graded = sorted(set(truth) & set(predicted))
    mistyped = [
        {"surface": s, "expected": expected_type[s], "got": predicted_type[s]}
        for s in graded
        if predicted_type[s] != expected_type[s]
    ]
    report["type_accuracy"] = (len(graded) - len(mistyped)) / len(graded) if graded else 1.0
    report["mistyped"] = mistyped
    return report


def count_duplicate_pairs(db: Any, novel_id: str) -> dict[str, Any]:
    """Duplicate-looking entity pairs — see `pipeline.db.duplicates`.

    Re-exported here so the eval and the `/entities/duplicates` route grade the
    same thing. If these two ever diverge, the eval stops measuring what the
    product actually surfaces.
    """
    return find_duplicate_candidates(db, novel_id)
