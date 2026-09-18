from __future__ import annotations

import pytest

from evals.critic_eval import build_cases, run_critic_eval
from pipeline.db.client import DBClient
from testing import seeding


@pytest.fixture()
def seeded(db: DBClient):
    data = seeding.seed_novel(db)
    yield data
    seeding.cleanup(db, data["novel_id"])


def test_critic_flags_every_seeded_violation(db, seeded):
    cases = build_cases(db, seeded)
    metrics = run_critic_eval(db, cases)
    assert metrics["recall"] == 1.0, metrics["per_case"]


def test_critic_passes_clean_draft(db, seeded):
    cases = build_cases(db, seeded)
    metrics = run_critic_eval(db, cases)
    assert metrics["false_positives"] == 0, metrics["per_case"]
    assert metrics["precision"] == 1.0


def test_metrics_shape(db, seeded):
    metrics = run_critic_eval(db, build_cases(db, seeded))
    assert set(metrics) >= {
        "precision", "recall",
        "true_positives", "false_positives", "false_negatives",
        "per_case",
    }
    # 3 seeded violations + 1 clean case
    assert len(metrics["per_case"]) == 4
