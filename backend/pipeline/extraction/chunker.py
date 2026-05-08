from __future__ import annotations

from typing import Iterable

try:
    import tiktoken
except Exception:  # pragma: no cover
    tiktoken = None


def _encoding(model_name: str = "cl100k_base"):
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(model_name)
    except Exception:
        return None


def tokenize(text: str) -> list[str] | list[int]:
    enc = _encoding()
    if enc is not None:
        return enc.encode(text)
    return text.split()


def detokenize(tokens: Iterable[str] | Iterable[int]) -> str:
    token_list = list(tokens)
    if not token_list:
        return ""
    enc = _encoding()
    if enc is not None and isinstance(token_list[0], int):
        return enc.decode(token_list)
    return " ".join(str(token) for token in token_list)


def sliding_window_chunks(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    tokens = tokenize(text)
    if not tokens:
        return []

    chunks: list[str] = []
    start = 0
    stride = chunk_size - overlap

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunks.append(detokenize(tokens[start:end]))
        if end >= len(tokens):
            break
        start += stride

    return chunks
