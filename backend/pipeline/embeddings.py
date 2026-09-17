from __future__ import annotations

import hashlib
import struct
from typing import Sequence

from pipeline.config import settings
from pipeline.db.client import DBClient
from pipeline.llm import call_with_retry


class EmbeddingError(RuntimeError):
    """Raised when a real embedding cannot be produced.

    Never fall back to ``_hash_embedding`` outside mock mode: hash vectors
    carry no semantic signal (near-identical strings score ~0 cosine against
    each other, the same as unrelated ones), so a fallback silently and
    permanently poisons dense retrieval for whatever it was written to.
    """


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

    @property
    def model_name(self) -> str:
        """Provenance tag stored alongside every vector this service writes.

        Mock vectors get an explicit sentinel rather than the configured model
        name — they are hash output with no semantic content, and the whole
        point of recording provenance is being able to find them later.
        """
        return "mock:hash" if self.use_mock else settings.embedding_model

    def embed_text(self, text: str) -> list[float]:
        embedding = _load_embedding()
        text = text or ""
        if self.use_mock:
            return self._hash_embedding(text)

        # Falling back to a hash vector here would write semantically empty
        # data into the vector store, indistinguishable from a real embedding
        # afterwards and undetectable without a provenance column. Ingestion
        # must fail loudly instead.
        if embedding is None:
            raise EmbeddingError(
                "litellm is not importable but use_mock is False; refusing to "
                "write hash vectors. Install litellm or set USE_MOCK_LLM=true."
            )

        try:
            response = call_with_retry(
                embedding,
                model=settings.embedding_model,
                input=[text],
                dimensions=settings.embedding_dimensions,
            )
            vector = response["data"][0]["embedding"]
        except Exception as exc:
            raise EmbeddingError(
                f"embedding call failed for model {settings.embedding_model!r}: {exc}"
            ) from exc

        values = [float(v) for v in vector]
        if len(values) != settings.embedding_dimensions:
            raise EmbeddingError(
                f"embedding model {settings.embedding_model!r} returned "
                f"{len(values)} dimensions, but EMBEDDING_DIMENSIONS is "
                f"{settings.embedding_dimensions}. The database vector columns "
                "were created at the configured size, so this would fail at "
                "insert time or silently corrupt retrieval."
            )
        return values

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
            SET embedding = %s::vector, embedding_model = %s
            WHERE id = %s
            """,
            (vector_literal(chapter_embedding), service.model_name, chapter_id),
        )

    for row in event_rows:
        event_id = row["id"]
        description = row["description"]
        event_embedding = service.embed_text(description)
        db.execute(
            """
            UPDATE events
            SET embedding = %s::vector, embedding_model = %s
            WHERE id = %s
            """,
            (vector_literal(event_embedding), service.model_name, event_id),
        )
