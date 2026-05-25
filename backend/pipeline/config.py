from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()
# Per-branch overrides: if scripts/branch_db.sh wrote a .env.branch (gitignored),
# load it with override=true so this branch's DATABASE_URL takes precedence
# without ever touching the user's .env file.
load_dotenv(".env.branch", override=True)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "postgresql://localhost/novel_wiki")
    )
    default_model: str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL", "gpt-4o-mini"))
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    embedding_dimensions: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
    )
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))
    chunk_size: int = field(default_factory=lambda: int(os.getenv("CHUNK_SIZE", "2000")))
    chunk_overlap: int = field(default_factory=lambda: int(os.getenv("CHUNK_OVERLAP", "200")))
    use_mock_llm: bool = field(default_factory=lambda: _bool_env("USE_MOCK_LLM", False))


settings = Settings()


LLM_CONFIG = {
    "model": settings.default_model,
    "temperature": settings.llm_temperature,
    "response_format": {"type": "json_object"},
}
