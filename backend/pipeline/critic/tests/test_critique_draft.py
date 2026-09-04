"""`critique_draft`: one verdict, and what counts as a verdict at all.

The distinction these pin down is `ok` vs `unavailable`/`error`. A caller that
blocks on FAIL must not read "the critic never ran" as "the draft is clean",
so an outage has to be a distinguishable status rather than a passing report.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import pipeline.critic.service as service
from pipeline.critic.types import CritiqueReport, Finding, Severity


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
            service, "extract_draft_claims", lambda text, use_mock=None: {"mentions": []}
        )
        monkeypatch.setattr(service, "build_draft_chapter", lambda db, **kw: object())

        class _Critic:
            def __init__(self, db):
                pass

            def critique(self, draft):
                if raises is not None:
                    raise raises
                return report

        monkeypatch.setattr(service, "ContinuityCritic", _Critic)

    return _install


def _critique(**kw):
    return service.critique_draft(
        None, novel_id="n", chapter_number=1, text="the draft",
        use_mock_llm=False, **kw,
    )


def test_clean_report_passes(stub_critic):
    stub_critic(report=_report())
    critique = _critique()

    assert critique.status == "ok"
    assert critique.passed is True
    assert critique.fails == [] and critique.warns == []


def test_warns_alone_do_not_fail(stub_critic):
    stub_critic(report=_report(_warn()))
    critique = _critique()

    assert critique.passed is True
    assert len(critique.warns) == 1


def test_fail_reports_the_findings(stub_critic):
    stub_critic(report=_report(_fail(), _warn()))
    critique = _critique()

    assert critique.status == "ok"
    assert critique.passed is False
    assert critique.fails[0]["quote"] == "She already knew."
    assert critique.fails[0]["severity"] == "FAIL"
    assert len(critique.warns) == 1


def test_critic_exception_is_an_error_not_a_pass(stub_critic):
    stub_critic(raises=RuntimeError("draft_claims: extraction failed"))
    critique = _critique()

    assert critique.status == "error"
    assert critique.passed is False
    assert "extraction failed" in critique.error


def test_malformed_report_is_an_error_not_a_pass(stub_critic):
    """A report that can't be turned into findings is a critic error — and,
    like every other outcome, must never raise out of critique_draft."""

    class _BadFinding:
        """Looks like a Finding but `severity` is a plain str with no
        `.value`, so `_finding_dict` blows up converting it."""

        check = "knowledge_state"
        severity = "FAIL"
        message = "malformed"
        quote = None
        suggested_fix = None
        context: dict = {}

    stub_critic(report=CritiqueReport(novel_id="n", chapter_number=1, findings=[_BadFinding()]))
    critique = _critique()

    assert critique.status == "error"
    assert "AttributeError" in critique.error


def test_disabled_critic_is_unavailable_not_a_pass(stub_critic, monkeypatch):
    stub_critic(report=_report())
    # `Settings` is a frozen dataclass (pipeline/config.py), so the attribute
    # cannot be set in place. Rebind the module-level `settings` instead.
    monkeypatch.setattr(
        service, "settings", replace(service.settings, critic_enabled=False)
    )

    critique = _critique()

    assert critique.status == "unavailable"
    assert critique.passed is False
    assert critique.report is None


def test_mock_mode_is_unavailable_not_a_vacuous_pass(stub_critic):
    """extract_draft_claims returns empty claims without a real LLM, and an
    empty draft passes every check. Reporting that as a pass would make
    USE_MOCK_LLM=true a silent bypass for blocking callers."""
    stub_critic(report=_report())

    critique = service.critique_draft(
        None, novel_id="n", chapter_number=1, text="the draft", use_mock_llm=True
    )

    assert critique.status == "unavailable"
    assert critique.passed is False


def test_summary_has_one_shape_for_every_outcome(stub_critic):
    stub_critic(report=_report(_fail()))
    ok = _critique().summary()
    assert ok["status"] == "ok" and ok["passed"] is False and ok["fails"] == 1

    stub_critic(raises=RuntimeError("boom"))
    bad = _critique().summary()
    assert bad["status"] == "error" and bad["passed"] is None and "boom" in bad["error"]
