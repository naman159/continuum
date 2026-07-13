"""DB fixtures for eval tests, mirroring reads/tests/conftest.py."""

from __future__ import annotations

import pytest

from pipeline.db.client import DBClient


@pytest.fixture(scope="module")
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()
