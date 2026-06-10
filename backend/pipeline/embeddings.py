from __future__ import annotations

import hashlib
import struct
from typing import Sequence

from pipeline.config import settings
from pipeline.db.client import DBClient


def _load_embedding():
    try:
        from litellm import embedding
    except Exception:  # pragma: no cover
        return None
    return embedding


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


class EmbeddingService:
    def __init__(self, *, use_mock: bool | None = None) -> None:
        embedding = _load_embedding()
        if use_mock is None:
            self.use_mock = embedding is None or settings.use_mock_llm
        else:
            self.use_mock = use_mock

    def embed_text(self, text: str) -> list[float]:
        embedding = _load_embedding()
        text = text or ""
        if self.use_mock:
            return self._hash_embedding(text)

        if embedding is None:
            return self._hash_embedding(text)

        try:
            response = embedding(
                model=settings.embedding_model,
                input=[text],
                dimensions=settings.embedding_dimensions,
            )
            vector = response["data"][0]["embedding"]
            return [float(v) for v in vector]
        except Exception:  # pragma: no cover
            return self._hash_embedding(text)

    @staticmethod
    def _hash_embedding(text: str, dimensions: int | None = None) -> list[float]:
        dims = dimensions or settings.embedding_dimensions
        output: list[float] = []
        seed = text.encode("utf-8", errors="ignore") or b"seed"

        counter = 0
        while len(output) < dims:
            digest = hashlib.sha256(seed + counter.to_bytes(4, byteorder="little")).digest()
            for idx in range(0, len(digest), 4):
                if len(output) >= dims:
                    break
                chunk = digest[idx : idx + 4]
                value = struct.unpack("<I", chunk)[0]
                # Map to [-1, 1].
                normalized = (value / 4294967295.0) * 2.0 - 1.0
                output.append(float(normalized))
            counter += 1
        return output


def embed_chapter_and_events(
    db: DBClient,
    *,
    chapter_id: str,
    chapter_summary: str,
    event_rows: list[dict[str, str]],
    service: EmbeddingService | None = None,
    embed_chapter: bool = True,
) -> None:
    service = service or EmbeddingService()

    # embed_chapter=False when persist_multi_summaries will immediately
    # overwrite chapters.embedding with the medium summary's embedding.
    if embed_chapter:
        chapter_embedding = service.embed_text(chapter_summary)
        db.execute(
            """
            UPDATE chapters
            SET embedding = %s::vector
            WHERE id = %s
            """,
            (vector_literal(chapter_embedding), chapter_id),
        )

    for row in event_rows:
        event_id = row["id"]
        description = row["description"]
        event_embedding = service.embed_text(description)
        db.execute(
            """
            UPDATE events
            SET embedding = %s::vector
            WHERE id = %s
            """,
            (vector_literal(event_embedding), event_id),
        )
