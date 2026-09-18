"""Critic precision/recall eval.

Builds labeled DraftChapter cases against the seeded reads-test novel:
three seeded violations (each targeting one deterministic check) plus one
clean draft, then measures whether the critic flags exactly the violations.

A case counts as "flagged" when the report contains at least one finding
from the case's targeted check (any severity — the possession check
deliberately WARNs rather than FAILs on pickups).
"""

from __future__ import annotations

from typing import Any

from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import DraftChapter
from pipeline.db.client import DBClient


def build_cases(db: DBClient, seeded: dict[str, Any]) -> list[dict[str, Any]]:
    novel_id = seeded["novel_id"]

    # The seeded canon fact (Aria eye_color=storm-grey) is unlocked; lock it
    # so a contradiction is a FAIL-grade violation.
    db.execute(
        "UPDATE canon_facts SET locked = true WHERE id = %s",
        (seeded["canon_fact_id"],),
    )
    # Give Aria one known fact so the clean draft can assert prior knowledge.
    db.execute(
        """
        INSERT INTO knows_edges (character_id, fact_description, learned_chapter, source_type)
        VALUES (%s, %s, 1, 'observation')
        """,
        (seeded["char_a_id"], "the iron compass points to the sunken vault"),
    )

    unknown_knowledge = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Borin recalled that the iron compass points to the sunken vault.",
        knowledge_claims=[
            {"character_id": seeded["char_b_id"],
             "fact_description": "the iron compass points to the sunken vault",
             "learned_this_chapter": False,
             "quote": "Borin recalled that the iron compass points to the sunken vault"},
        ],
    )

    wrong_possessor = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Borin turned the Iron Compass over in his hands, as he had for years.",
        possession_claims=[
            {"character_id": seeded["char_b_id"], "object_id": seeded["obj_id"],
             "quote": "Borin turned the Iron Compass over in his hands"},
        ],
    )

    canon_contradiction = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Aria's amber eyes caught the lamplight.",
        mentions=[
            {"entity_id": seeded["char_a_eid"], "predicate": "eye_color",
             "claimed_value": "amber", "quote": "Aria's amber eyes caught the lamplight"},
        ],
    )

    clean = DraftChapter(
        novel_id=novel_id,
        chapter_number=4,
        text="Aria stood alone in the Sable Archive, compass in hand, sure of its secret.",
        possession_claims=[
            {"character_id": seeded["char_a_id"], "object_id": seeded["obj_id"],
             "quote": "compass in hand"},
        ],
        knowledge_claims=[
            {"character_id": seeded["char_a_id"],
             "fact_description": "the iron compass points to the sunken vault",
             "learned_this_chapter": False,
             "quote": "sure of its secret"},
        ],
    )

    return [
        {"name": "unknown_knowledge", "draft": unknown_knowledge,
         "should_flag": True, "expected_check": "knowledge_state"},
        {"name": "wrong_possessor", "draft": wrong_possessor,
         "should_flag": True, "expected_check": "possession"},
        {"name": "canon_contradiction", "draft": canon_contradiction,
         "should_flag": True, "expected_check": "entity_mention"},
        {"name": "clean_draft", "draft": clean,
         "should_flag": False, "expected_check": None},
    ]


def run_critic_eval(db: DBClient, cases: list[dict[str, Any]]) -> dict[str, Any]:
    critic = ContinuityCritic(db)
    tp = fp = fn = 0
    per_case: list[dict[str, Any]] = []
    for case in cases:
        report = critic.critique(case["draft"])
        if case["should_flag"]:
            flagged = any(f.check == case["expected_check"] for f in report.findings)
        else:
            flagged = bool(report.findings)
        if case["should_flag"] and flagged:
            tp += 1
        elif case["should_flag"] and not flagged:
            fn += 1
        elif not case["should_flag"] and flagged:
            fp += 1
        per_case.append({
            "name": case["name"],
            "should_flag": case["should_flag"],
            "flagged": flagged,
            "findings": [(f.check, f.severity.value, f.message) for f in report.findings],
        })
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "per_case": per_case,
    }
