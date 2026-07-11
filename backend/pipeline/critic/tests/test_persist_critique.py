"""persist_critique writes one report per chapter, replacing prior runs."""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic.persist import persist_critique
from pipeline.critic.types import CritiqueReport, Finding, Severity
from pipeline.db.client import DBClient


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def chapter(db: DBClient):
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    chapter_id = db.fetchval(
        "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s, 1, 'x') RETURNING id",
        (novel_id,), commit=True,
    )
    yield {"novel_id": novel_id, "chapter_id": str(chapter_id)}
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_persists_and_replaces(db: DBClient, chapter):
    report = CritiqueReport(novel_id=chapter["novel_id"], chapter_number=1)
    report.findings.append(Finding(
        check="knowledge_state", severity=Severity.FAIL,
        message="Aelric acts on unknown fact", quote="he knew",
        context={"fact": "the harbor is watched"},
    ))
    persist_critique(db, chapter_id=chapter["chapter_id"], report=report)

    row = db.fetchone(
        "SELECT passed FROM critique_reports WHERE chapter_id = %s", (chapter["chapter_id"],)
    )
    assert row is not None and row[0] is False
    findings = db.fetchall(
        """
        SELECT f.check_name, f.severity, f.message, f.quote
          FROM critique_findings f
          JOIN critique_reports r ON r.id = f.report_id
         WHERE r.chapter_id = %s
        """,
        (chapter["chapter_id"],), dict_rows=True,
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "fail"

    # Re-run with a clean report: old findings replaced, passed flips.
    persist_critique(
        db, chapter_id=chapter["chapter_id"],
        report=CritiqueReport(novel_id=chapter["novel_id"], chapter_number=1),
    )
    assert db.fetchval(
        "SELECT passed FROM critique_reports WHERE chapter_id = %s", (chapter["chapter_id"],)
    ) is True
    assert db.fetchval(
        """
        SELECT count(*) FROM critique_findings f
          JOIN critique_reports r ON r.id = f.report_id
         WHERE r.chapter_id = %s
        """,
        (chapter["chapter_id"],),
    ) == 0
