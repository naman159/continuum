from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

from pipeline.config import settings
from pipeline.retrieval.types import RetrievalQuery, RetrievalResult


logger = logging.getLogger(__name__)


from pipeline.llm import load_completion as _load_completion


_RERANK_SYSTEM = (
    "You are a relevance scoring assistant for a novel-knowledge retrieval system. "
    "Given a query and a list of passages, score each passage from 0 to 10 by how "
    "relevant it is to answering the query. Respond ONLY with valid JSON of the "
    "form {\"scores\": [<int>, <int>, ...]} with exactly one score per passage, "
    "in the same order as the input passages."
)


class LLMReranker:
    """Cross-encoder-style reranker that scores query/passage pairs via litellm.

    Batches passages into a single LLM call (default ~10 per batch). Falls back
    silently to identity ordering when litellm is unavailable or the call fails.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        batch_size: int = 10,
        max_snippet_chars: int = 600,
        temperature: float = 0.0,
    ) -> None:
        self.model = model or settings.default_model
        self.batch_size = max(1, batch_size)
        self.max_snippet_chars = max_snippet_chars
        self.temperature = temperature

    def rerank(
        self,
        query: RetrievalQuery,
        candidates: list[RetrievalResult],
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        if not candidates:
            return []

        completion = _load_completion()
        if completion is None or settings.use_mock_llm:
            logger.warning("LLMReranker: litellm unavailable or mock mode; returning candidates unchanged")
            return candidates[:top_k]

        scored: list[RetrievalResult] = []
        try:
            for start in range(0, len(candidates), self.batch_size):
                batch = candidates[start : start + self.batch_size]
                batch_scores = self._score_batch(completion, query.text, batch)
                for cand, raw_score in zip(batch, batch_scores):
                    norm = max(0.0, min(1.0, float(raw_score) / 10.0))
                    meta = dict(cand.metadata)
                    meta["rerank_score"] = norm
                    meta["rerank_raw"] = float(raw_score)
                    scored.append(
                        replace(cand, score=norm, metadata=meta)
                    )
        except Exception as exc:  # pragma: no cover - depends on external API
            logger.warning("LLMReranker call failed (%s); falling back to original order", exc)
            return candidates[:top_k]

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    def _score_batch(
        self, completion: Any, query_text: str, batch: list[RetrievalResult]
    ) -> list[float]:
        passages = []
        for idx, c in enumerate(batch, start=1):
            snippet = (c.snippet or "")[: self.max_snippet_chars]
            passages.append(f"[{idx}] ({c.kind}) {snippet}")
        user = (
            f"QUERY:\n{query_text}\n\n"
            f"PASSAGES (n={len(batch)}):\n" + "\n".join(passages) + "\n\n"
            "Return JSON only."
        )
        response = completion(
            model=self.model,
            messages=[
                {"role": "system", "content": _RERANK_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )
        content = response["choices"][0]["message"]["content"]
        scores = _parse_scores(content, expected=len(batch))
        return scores


def _parse_scores(content: str, expected: int) -> list[float]:
    """Parse model output as JSON {"scores": [...]} with robust fallback."""
    try:
        data = json.loads(content)
    except Exception:
        # Try to extract a JSON object substring.
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise ValueError(f"Could not parse rerank output: {content[:200]}")
        data = json.loads(match.group(0))

    scores_obj = data.get("scores") if isinstance(data, dict) else None
    if not isinstance(scores_obj, list):
        raise ValueError(f"Rerank output missing 'scores' list: {content[:200]}")

    if len(scores_obj) < expected:
        scores_obj = list(scores_obj) + [0.0] * (expected - len(scores_obj))
    elif len(scores_obj) > expected:
        scores_obj = scores_obj[:expected]

    out: list[float] = []
    for s in scores_obj:
        try:
            out.append(float(s))
        except (TypeError, ValueError):
            out.append(0.0)
    return out
