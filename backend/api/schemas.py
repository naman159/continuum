from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NovelSummary(BaseModel):
    id: UUID
    title: str
    author: str | None = None
    language: str | None = None
    created_at: datetime
    max_chapter: int
