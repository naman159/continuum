"""Shared LLM plumbing used across extraction and the continuity critic's
claim extraction: the litellm loader and the lenient JSON parser for model output."""

from __future__ import annotations

import json
from typing import Any


def load_completion():
    """Return litellm's ``completion`` callable, or None when litellm is
    unavailable (callers treat None as mock mode or raise, per their
    fail-loud policy)."""
    try:
        from litellm import completion
    except Exception:  # pragma: no cover
        return None
    return completion


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


__all__ = ["load_completion", "safe_json_loads"]
