"""The critique is optional, decoupled, and re-runnable.

CRITIC_ENABLED=false leaves ingestion otherwise untouched, so a chapter
ingested with the critique off is still fully queryable and can be judged later
via pipeline.critic.cli — which is what `critique_chapter` is for. What it must
NOT do is overwrite an existing report with an empty one when it cannot judge.
"""

from __future__ import annotations

import uuid

import pytest

import pipeline.critic.service as service
import pipeline.pipeline as pipeline_mod
from pipeline.critic.types import CritiqueReport, Finding, Severity


@pytest.fixture
def real_critique(monkeypatch):
    """Make critique_draft produce a real report without an LLM.

    Mock mode is deliberately an outage rather than a vacuous pass, so a test
    that wants a persisted report has to stub the claims layer instead of
    reaching for use_mock_llm=True.
    """

    def _install(*findings: Finding):
        monkeypatch.setattr(
            service, "extract_draft_claims", lambda text, use_mock=None: {"mentions": []}
        )
        monkeypatch.setattr(service, "build_draft_chapter", lambda db, **kw: object())

        class _Critic:
            def __init__(self, db):
                pass

            def critique(self, draft):
                return CritiqueReport(
                    novel_id="n", chapter_number=1, findings=list(findings)
                )

        monkeypatch.setattr(service, "ContinuityCritic", _Critic)

    return _install


@pytest.fixture
def novel(db):
    novel_id = str(db.fetchval(
        "INSERT INTO novels (title) VALUES (%s) RETURNING id",
        (f"critic-toggle-{uuid.uuid4()}",), commit=True,
    ))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def _reports(db, novel_id):
    return db.fetchval(
        """
        SELECT count(*) FROM critique_reports cr
        JOIN chapters ch ON ch.id = cr.chapter_id
        WHERE ch.novel_id = %s
        """,
        (novel_id,),
    )


def _ingest(novel_id, *, run_critic):
    return pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=1,
        raw_text="Aelric carried the silver dagger from Pellis Harbor to the Old Mill.",
        chapter_title="T", use_mock_llm=True, chunk_size=2000, chunk_overlap=200,
        run_critic=run_critic,
    )


def test_run_critic_false_skips_the_critique_but_still_ingests(db, novel):
    result = _ingest(novel, run_critic=False)

    assert result["critique"] is None
    assert _reports(db, novel) == 0
    # The memory layer itself is unaffected — this is the whole point of
    # decoupling: ingestion's job is the chapter, not the judgement.
    assert result["chapter_id"] is not None
    assert db.fetchval(
        "SELECT count(*) FROM chapters WHERE novel_id = %s", (novel,)
    ) == 1
    assert result["events"] >= 0


def test_a_skipped_chapter_can_be_critiqued_later(db, novel, real_critique):
    _ingest(novel, run_critic=False)
    assert _reports(db, novel) == 0
    real_critique()

    summary = service.critique_chapter(db, novel_id=novel, chapter_number=1)

    assert summary["status"] == "ok"
    assert _reports(db, novel) == 1


def test_re_critiquing_replaces_rather_than_duplicates(db, novel, real_critique):
    _ingest(novel, run_critic=False)
    real_critique()

    service.critique_chapter(db, novel_id=novel, chapter_number=1)
    service.critique_chapter(db, novel_id=novel, chapter_number=1)

    assert _reports(db, novel) == 1


def test_an_outage_does_not_wipe_an_existing_report(db, novel, real_critique):
    """A re-run that cannot judge must leave the previous verdict standing —
    replacing it with nothing would silently turn a FAILing chapter clean."""
    _ingest(novel, run_critic=False)
    real_critique(
        Finding(check="knowledge_state", severity=Severity.FAIL, message="nope")
    )
    service.critique_chapter(db, novel_id=novel, chapter_number=1)
    assert _reports(db, novel) == 1

    summary = service.critique_chapter(
        db, novel_id=novel, chapter_number=1, use_mock_llm=True
    )

    assert summary["status"] == "unavailable"
    assert _reports(db, novel) == 1
    assert db.fetchval(
        """
        SELECT cr.passed FROM critique_reports cr
        JOIN chapters ch ON ch.id = cr.chapter_id WHERE ch.novel_id = %s
        """,
        (novel,),
    ) is False


def test_critique_chapter_rejects_a_chapter_that_does_not_exist(db, novel):
    with pytest.raises(ValueError, match="does not exist"):
        service.critique_chapter(db, novel_id=novel, chapter_number=99)
