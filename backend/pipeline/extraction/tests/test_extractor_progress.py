from __future__ import annotations

from types import SimpleNamespace


class FakeProgress:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def on_pass_start(self, name: str) -> None:
        self.calls.append(("start", name))

    def on_pass_done(self, name: str) -> None:
        self.calls.append(("done", name))


def _make_fake_completion():
    def fake(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary":"Test summary."}'))])
    return fake


def test_extract_chunk_calls_progress_hooks_for_each_pass(monkeypatch):
    from pipeline.extraction import extractor as ext_module
    from pipeline.extraction.prompts import PASS_ORDER

    monkeypatch.setattr(ext_module, "_load_completion", lambda: _make_fake_completion())

    from pipeline.extraction.extractor import ChapterExtractor

    progress = FakeProgress()
    extractor = ChapterExtractor(use_mock=False)
    extractor.extract_chunk("some text", {}, progress=progress)

    starts = [name for action, name in progress.calls if action == "start"]
    dones = [name for action, name in progress.calls if action == "done"]
    assert starts == list(PASS_ORDER)
    assert dones == list(PASS_ORDER)


def test_extract_chunk_skips_progress_in_mock_mode():
    from pipeline.extraction.extractor import ChapterExtractor

    progress = FakeProgress()
    extractor = ChapterExtractor(use_mock=True)
    extractor.extract_chunk("some text", {}, progress=progress)

    assert progress.calls == []


def test_extract_chapter_accumulates_progress_across_chunks(monkeypatch):
    from pipeline.extraction import extractor as ext_module
    from pipeline.extraction.prompts import PASS_ORDER

    monkeypatch.setattr(ext_module, "_load_completion", lambda: _make_fake_completion())

    from pipeline.extraction.extractor import ChapterExtractor

    progress = FakeProgress()
    extractor = ChapterExtractor(use_mock=False)
    extractor.extract_chapter(["chunk1", "chunk2"], {}, progress=progress)

    dones = [name for action, name in progress.calls if action == "done"]
    assert len(dones) == len(PASS_ORDER) * 2
