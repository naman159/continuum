"""The agent write gate: verdicts and fail-closed behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pipeline import gate as gate_mod
from pipeline.critic.types import CritiqueReport, Finding, Severity
from reads import drafts as drafts_reads


def _report(*findings: Finding) -> CritiqueReport:
    return CritiqueReport(novel_id="n", chapter_number=1, findings=list(findings))


def _fail() -> Finding:
    return Finding(
        check="knowledge_state",
        severity=Severity.FAIL,
        message="Elara knows something she shouldn't",
        quote="She already knew.",
    )


def _warn() -> Finding:
    return Finding(check="thread_coverage", severity=Severity.WARN, message="cold thread")


@pytest.fixture
def stub_critic(monkeypatch):
    """Replace claim extraction and the critic with controllable stubs."""

    def _install(report=None, raises: Exception | None = None):
        monkeypatch.setattr(
            gate_mod, "extract_draft_claims", lambda text, use_mock=None: {"mentions": []}
        )
        monkeypatch.setattr(
            gate_mod, "build_draft_chapter", lambda db, **kw: object()
        )

        class _Critic:
            def __init__(self, db):
                pass

            def critique(self, draft):
                if raises is not None:
                    raise raises
                return report

        monkeypatch.setattr(gate_mod, "ContinuityCritic", _Critic)

    return _install


def test_clean_report_passes_and_parks_nothing(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report())

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title="Ch 90",
        raw_text="text", use_mock_llm=True,
    )

    assert verdict.passed is True
    assert verdict.reason is None
    assert verdict.submission_id is None
    assert drafts_reads.list_submissions(db, novel_id) == []


def test_warns_alone_do_not_block(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report(_warn()))

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="text", use_mock_llm=True,
    )

    assert verdict.passed is True
    assert len(verdict.warns) == 1


def test_fail_parks_the_draft_with_findings(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report(_fail(), _warn()))

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title="Ch 90",
        raw_text="the draft", use_mock_llm=True,
    )

    assert verdict.passed is False
    assert verdict.reason == "fail"
    assert verdict.submission_id is not None

    parked = drafts_reads.get_submission(db, verdict.submission_id)
    assert parked["status"] == "pending"
    assert parked["raw_text"] == "the draft"
    assert parked["chapter_number"] == 90
    assert parked["findings"]["fails"][0]["quote"] == "She already knew."
    assert len(parked["findings"]["warns"]) == 1


def test_critic_exception_parks_rather_than_passing(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(raises=RuntimeError("draft_claims: extraction failed"))

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="the draft", use_mock_llm=True,
    )

    assert verdict.passed is False
    assert verdict.reason == "critic_error"
    parked = drafts_reads.get_submission(db, verdict.submission_id)
    assert parked["status"] == "pending"
    assert "extraction failed" in parked["findings"]["error"]


def test_disabled_critic_refuses_without_parking(db, seed_novel, stub_critic, monkeypatch):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report())
    # `Settings` is a frozen dataclass (pipeline/config.py:25), so the
    # attribute cannot be set in place — setattr raises FrozenInstanceError.
    # Rebind gate.py's module-level `settings` to a copy with the flag off.
    monkeypatch.setattr(
        gate_mod, "settings", replace(gate_mod.settings, critic_enabled=False)
    )

    verdict = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="text", use_mock_llm=True,
    )

    assert verdict.passed is False
    assert verdict.reason == "critic_disabled"
    assert verdict.submission_id is None
    assert drafts_reads.list_submissions(db, novel_id) == []


def test_resubmission_supersedes_the_pending_row(db, seed_novel, stub_critic):
    novel_id = seed_novel(db)["novel_id"]
    stub_critic(report=_report(_fail()))

    first = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="v1", use_mock_llm=True,
    )
    second = gate_mod.gate_agent_draft(
        db, novel_id=novel_id, chapter_number=90, title=None,
        raw_text="v2", use_mock_llm=True,
    )

    superseded = drafts_reads.get_submission(db, first.submission_id)
    assert superseded["status"] == "rejected"
    assert superseded["resolution_note"] == "superseded by resubmission"

    pending = drafts_reads.list_submissions(db, novel_id)
    assert [r["id"] for r in pending] == [second.submission_id]
