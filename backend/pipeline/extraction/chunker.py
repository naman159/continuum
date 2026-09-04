from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import tiktoken
import tiktoken.model

from pipeline.config import settings

# Used when the configured model is not one tiktoken can map -- which is every
# Gemini and Claude model, including the default. Chunk sizing only needs a
# stable approximation of the text's length; it is not the serving model's own
# count and nothing here pretends otherwise.
_FALLBACK_ENCODING = "o200k_base"


@lru_cache(maxsize=None)
def _encoding(model: str):
    """The encoding the configured model uses, or the fallback estimator.

    Derived from DEFAULT_MODEL rather than hardcoded so the chunker and the
    model cannot drift into different vocabularies -- they already had once,
    chunking in cl100k_base while the configured model read o200k_base.

    Deliberately not wrapped in a try/except. This used to fall back to
    text.split() when tiktoken could not load, which was worse than failing:
    whitespace tokens are ~1.5x coarser than BPE, so a "2000 token" chunk
    carried half again as much text as asked for, and re-joining on single
    spaces flattened every paragraph break before the extractor saw it.
    """
    try:
        name = tiktoken.model.encoding_name_for_model(model)
    except KeyError:
        name = _FALLBACK_ENCODING
    return tiktoken.get_encoding(name)


def tokenize(text: str, model: str | None = None) -> list[int]:
    return _encoding(model or settings.default_model).encode(text)


def detokenize(tokens: Iterable[int], model: str | None = None) -> str:
    token_list = list(tokens)
    if not token_list:
        return ""
    return _encoding(model or settings.default_model).decode(token_list)


def sliding_window_chunks(
    text: str, chunk_size: int = 2000, overlap: int = 200, model: str | None = None
) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    tokens = tokenize(text, model=model)
    if not tokens:
        return []

    chunks: list[str] = []
    start = 0
    stride = chunk_size - overlap

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunks.append(detokenize(tokens[start:end], model=model))
        if end >= len(tokens):
            break
        start += stride

    return chunks
