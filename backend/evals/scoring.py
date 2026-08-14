"""Pure scoring functions for the eval harness. No DB, no LLM.

Name matching is deliberately forgiving (casefold + containment either way):
extraction may emit "Mira" where the key says "Mira Solen", and that is a
correct extraction, not a miss.
"""

from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _names_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def score_entities(expected: list[str], actual: list[str]) -> dict[str, Any]:
    exp = [_norm(e) for e in expected]
    act = [_norm(a) for a in actual]
    missing = [e for e in exp if not any(_names_match(e, a) for a in act)]
    extra = [a for a in act if not any(_names_match(e, a) for e in exp)]
    recall = (len(exp) - len(missing)) / len(exp) if exp else 1.0
    precision = (len(act) - len(extra)) / len(act) if act else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "missing": sorted(missing),
        "extra": sorted(extra),
    }


def _interval_matches(expected: dict, actual: dict, tolerance: int) -> bool:
    if not _names_match(expected["object"], actual["object"]):
        return False
    if not _names_match(expected["holder"], actual["holder"]):
        return False
    if abs(int(actual["since"]) - int(expected["since"])) > tolerance:
        return False
    exp_until, act_until = expected.get("until"), actual.get("until")
    if exp_until is None:
        return act_until is None or int(act_until) >= int(expected["since"])
    if act_until is None:
        return False
    return abs(int(act_until) - int(exp_until)) <= tolerance


def score_possessions(
    expected: list[dict], actual: list[dict], tolerance: int = 1
) -> dict[str, Any]:
    """Recall of expected possession intervals among the actual edges.

    An expected interval counts as found when some actual edge has the same
    object+holder (fuzzy names) and boundaries within `tolerance` chapters.
    """
    matched, missing = [], []
    for exp in expected:
        if any(_interval_matches(exp, act, tolerance) for act in actual):
            matched.append(exp)
        else:
            missing.append(exp)
    recall = len(matched) / len(expected) if expected else 1.0
    return {"recall": recall, "matched": matched, "missing": missing}


def _facts_overlap(expected_fact: str, actual_fact: str) -> bool:
    exp_words = set(_norm(expected_fact).split())
    act_words = set(_norm(actual_fact).split())
    if not exp_words or not act_words:
        return False
    return len(exp_words & act_words) >= max(2, len(exp_words) // 2)


def score_knowledge(expected: list[dict], actual: list[dict]) -> dict[str, Any]:
    """Recall of expected knowledge facts among actual knows-facts.

    A fact counts as found when some actual row has a matching character name
    and shares at least half the expected fact's words, with a floor of 2
    shared words so a single common word never matches. Expected facts must
    therefore be at least ~4 words long to be matchable at 50% overlap; the
    golden answer key's facts all are.
    """
    missing = []
    for exp in expected:
        found = any(
            _names_match(exp["character"], act.get("character", ""))
            and _facts_overlap(exp["fact"], act.get("fact", ""))
            for act in actual
        )
        if not found:
            missing.append(exp)
    recall = (len(expected) - len(missing)) / len(expected) if expected else 1.0
    return {"recall": recall, "missing": missing}


def recall_at_k(
    expected_chapters: list[int], result_chapters: list[int], k: int
) -> float:
    """Fraction of expected chapters that appear in the top-k results."""
    if not expected_chapters:
        return 1.0
    top = set(result_chapters[:k])
    hit = sum(1 for c in expected_chapters if c in top)
    return hit / len(expected_chapters)


def pairwise_resolution(
    truth: dict[str, str], predicted: dict[str, str]
) -> dict[str, Any]:
    """Pairwise precision/recall for entity resolution.

    Both maps are surface-form -> cluster id: `truth` from the answer key,
    `predicted` from whatever entity the pipeline filed that surface under.
    Every unordered pair of surfaces is one judgement — same cluster or not —
    which is the standard way to score clustering without needing the two id
    spaces to correspond.

    Only surfaces present in *both* maps are graded. A surface the extractor
    never emitted is an extraction miss, not a resolution error, and folding
    the two together would make this metric move for reasons that have nothing
    to do with resolution; `coverage` reports it separately.

    The two error lists are the point of this function. `false_merges` are
    distinct entities welded together, which corrupt every edge attached to
    either and are expensive to unpick. `missed_merges` leave duplicate rows,
    which are visible and cheap to repair. Precision and recall respectively
    track them, and they should not be traded off one-for-one.
    """
    gradeable = sorted(set(truth) & set(predicted))
    tp = 0
    false_merges: list[tuple[str, str]] = []
    missed_merges: list[tuple[str, str]] = []
    for i, a in enumerate(gradeable):
        for b in gradeable[i + 1:]:
            same_truth = truth[a] == truth[b]
            same_pred = predicted[a] == predicted[b]
            if same_truth and same_pred:
                tp += 1
            elif same_pred:
                false_merges.append((a, b))
            elif same_truth:
                missed_merges.append((a, b))
    precision = tp / (tp + len(false_merges)) if tp or false_merges else 1.0
    recall = tp / (tp + len(missed_merges)) if tp or missed_merges else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positive_pairs": tp,
        "false_merges": false_merges,
        "missed_merges": missed_merges,
        "coverage": len(gradeable) / len(truth) if truth else 1.0,
        "not_extracted": sorted(set(truth) - set(predicted)),
    }
