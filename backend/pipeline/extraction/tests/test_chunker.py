from __future__ import annotations

import pytest

from pipeline.extraction import chunker
from pipeline.extraction.chunker import sliding_window_chunks

PROSE = 'She lit the lantern.\n\nOutside, the rain had stopped.\n\n"Come in," he said.'


def test_single_chunk_round_trips_losslessly():
    # The whitespace fallback this replaced re-joined on single spaces, so every
    # paragraph break vanished before the extractor read the chunk.
    assert sliding_window_chunks(PROSE, chunk_size=10_000) == [PROSE]


def test_chunks_are_windowed_with_overlap():
    text = " ".join(str(n) for n in range(100))
    chunks = sliding_window_chunks(text, chunk_size=10, overlap=3)
    assert len(chunks) > 1
    # Consecutive windows share `overlap` tokens, so the tail of one chunk is
    # the head of the next.
    tail = chunker.tokenize(chunks[0])[-3:]
    assert chunker.tokenize(chunks[1])[:3] == tail


def test_empty_text_yields_no_chunks():
    assert sliding_window_chunks("") == []


@pytest.mark.parametrize(
    "chunk_size,overlap",
    [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 200)],
)
def test_invalid_window_parameters_are_rejected(chunk_size, overlap):
    with pytest.raises(ValueError):
        sliding_window_chunks("some text", chunk_size=chunk_size, overlap=overlap)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-4o-mini", "o200k_base"),
        ("gpt-4-turbo", "cl100k_base"),
        # Anything tiktoken can't map -- every Gemini and Claude model -- gets
        # the estimator rather than a hardcoded mismatched vocab.
        ("gemini/gemini-3.1-flash-lite", "o200k_base"),
        ("claude-opus-5", "o200k_base"),
    ],
)
def test_encoding_is_derived_from_the_configured_model(model, expected):
    assert chunker._encoding(model).name == expected


def test_unloadable_encoding_raises_instead_of_degrading(monkeypatch):
    # The predecessor silently split on whitespace here, changing both chunk
    # size and paragraph structure with nothing to detect it afterward.
    def boom(name):
        raise RuntimeError("no vocab and no network")

    monkeypatch.setattr(chunker.tiktoken, "get_encoding", boom)
    chunker._encoding.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            sliding_window_chunks(PROSE, model="gpt-4-turbo")
    finally:
        chunker._encoding.cache_clear()
