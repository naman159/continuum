"""Shared fixture for the draft-resolution tests.

Imported (not redefined) by both ``pipeline/tests/test_drafts_resolution.py``
and ``api/tests/test_drafts.py``; pytest collects fixtures bound into a test
module's namespace, so a plain import is enough to activate it.

It is deliberately not in the root ``conftest.py``: as an autouse fixture there
it would wrap ``analyze_chapter`` for every suite, hiding the real call path
from tests that mean to exercise it.
"""

from __future__ import annotations

import pytest

from pipeline import drafts as drafts_mod


@pytest.fixture(autouse=True)
def _force_mock_llm(monkeypatch):
    """accept_submission hardcodes use_mock_llm=None (correct for production,
    where a real extraction should run). In tests that would make live LLM
    calls, so wrap analyze_chapter to force mock mode — everything else in
    the path stays real.
    """
    real = drafts_mod.analyze_chapter

    def _forced(**kwargs):
        kwargs["use_mock_llm"] = True
        return real(**kwargs)

    monkeypatch.setattr(drafts_mod, "analyze_chapter", _forced)
