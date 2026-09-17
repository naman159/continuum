"""Shared LLM plumbing used across extraction and the continuity critic's
claim extraction: the litellm loader and the lenient JSON parser for model output."""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Respect HTTP Retry-After and Gemini's JSON RetryInfo, capped at 60s."""
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", {}).get("retry-after")
    delay = None
    if header:
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    if delay is None:
        payload = safe_json_loads(str(exc))
        error = payload.get("error", {})
        details = error.get("details", []) if isinstance(error, dict) else []
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith("RetryInfo"):
                continue
            try:
                delay = float(str(detail["retryDelay"]).removesuffix("s"))
            except (KeyError, ValueError):
                pass
    if delay is not None and math.isfinite(delay):
        return min(60.0, max(0.0, delay) + 1.0)
    return 60.0 if getattr(exc, "status_code", None) == 429 else float(2 ** attempt)


def call_with_retry(function, **kwargs):
    """Bound transient provider failures; never turn them into empty output.

    The SDK's immediate/default retries do not honor Gemini's RetryInfo body.
    Own the retry budget here so extraction, claims, identity resolution, and
    embeddings all wait for the same provider reset instead of racing it.
    """
    kwargs = {"timeout": 60, **kwargs, "num_retries": 0, "max_retries": 0}
    for attempt in range(4):
        try:
            return function(**kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if attempt == 3 or status not in {408, 429, 500, 502, 503, 504}:
                raise
            delay = _retry_delay(exc, attempt)
            logger.warning(
                "provider %s returned HTTP %s; retrying in %.1fs (%s/3)",
                kwargs.get("model", "unknown"), status, delay, attempt + 1,
            )
            time.sleep(delay)


def load_completion():
    """Return a retrying LiteLLM completion callable, or None when LiteLLM is
    unavailable (callers treat None as mock mode or raise, per their
    fail-loud policy)."""
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return partial(call_with_retry, completion)


def safe_json_loads(raw: str) -> dict[str, Any]:
    """``json.loads`` with a fallback to the outermost ``{...}`` slice
    (models sometimes wrap JSON in prose or code fences). Returns ``{}``
    when no dict can be parsed."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


__all__ = ["call_with_retry", "load_completion", "safe_json_loads"]
