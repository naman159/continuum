"""Minimal valid provider responses for pass orchestration tests."""
from pipeline.extraction.prompts import PASS_SCHEMAS


def empty_pass(pass_name: str) -> dict:
    return {key: [] if isinstance(value, list) else 'Test summary.'
            for key, value in PASS_SCHEMAS[pass_name].items()}
