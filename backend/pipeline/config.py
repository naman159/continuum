from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
# Per-branch overrides: if scripts/branch_db.sh wrote a .env.branch (gitignored),
# load it with override=true so this branch's DATABASE_URL takes precedence
# without ever touching the user's .env file. branch_db.sh writes the file next
# to backend/ — anchor there, not to the CWD, or the override silently vanishes
# when the process starts from any other directory (and writes hit the main DB).
load_dotenv(Path(__file__).resolve().parents[1] / ".env.branch", override=True)


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
    # These defaults are the same values .env.example ships. They used to be a
    # second, OpenAI-flavoured set, which meant the README documented one model
    # and every actual run used another -- and EMBEDDING_DIMENSIONS disagreeing
    # is not cosmetic, it decides the width of every vector column.
    default_model: str = field(
        default_factory=lambda: os.getenv("DEFAULT_MODEL", "gemini/gemini-3.1-flash-lite")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "gemini/gemini-embedding-2")
    )
    embedding_dimensions: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
    )
    # Connection-pool ceiling. Every API route handler is a plain `def`, so
    # Starlette runs them on its 40-slot threadpool; a pool smaller than that
    # makes concurrent requests queue and then fail with PoolTimeout after
    # psycopg's 30s default, surfacing as an unhandled 500.
    db_max_connections: int = field(
        default_factory=lambda: int(os.getenv("DB_MAX_CONNECTIONS", "20"))
    )
    # Whether ingestion critiques the chapter at all. The critique is a
    # separate judgement, not part of getting a chapter into the memory layer
    # — turn it off for ingest-only deployments and run
    # `python -m pipeline.critic.cli` later if you ever want the reports.
    # It does NOT relax a blocking caller: `on_continuity_fail="block"`
    # refuses the write rather than treating a missing verdict as a pass.
    critic_enabled: bool = field(default_factory=lambda: _bool_env("CRITIC_ENABLED", True))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))
    chunk_size: int = field(default_factory=lambda: int(os.getenv("CHUNK_SIZE", "2000")))
    chunk_overlap: int = field(default_factory=lambda: int(os.getenv("CHUNK_OVERLAP", "200")))
    use_mock_llm: bool = field(default_factory=lambda: _bool_env("USE_MOCK_LLM", False))
    # Max roster entries serialized into a single canonicalization LLM call.
    # Bounds prompt cost as the novel grows; <= 0 disables the cap.
    canonicalizer_max_roster: int = field(
        default_factory=lambda: int(os.getenv("CANONICALIZER_MAX_ROSTER", "80"))
    )
    # Caps for the entity roster injected into extraction prompts. Entities
    # mentioned in the chapter text are always preferred; the remainder is
    # back-filled by recency. <= 0 disables the cap.
    context_max_characters: int = field(
        default_factory=lambda: int(os.getenv("CONTEXT_MAX_CHARACTERS", "40"))
    )
    context_max_locations: int = field(
        default_factory=lambda: int(os.getenv("CONTEXT_MAX_LOCATIONS", "30"))
    )


settings = Settings()


LLM_CONFIG = {
    "model": settings.default_model,
    "temperature": settings.llm_temperature,
    "response_format": {"type": "json_object"},
}
