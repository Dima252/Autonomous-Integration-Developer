from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a .env file.

    The backend usually runs from `backend/`, so both the repo-root `.env` and a
    local one are read; later files win.
    """

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://aidev:aidev@localhost:5432/aidev"
    redis_url: str = "redis://localhost:6379/0"
    # Downloaded specs and docs (gitignored). Re-creatable from the pinned sources.
    data_dir: Path = REPO_ROOT / "data"
    # Curated content checked into the repo (engineering playbook, eval sets).
    corpus_dir: Path = REPO_ROOT / "corpus"

    # Retrieval (docs/architecture.md §3.2)
    voyage_api_key: SecretStr | None = None
    embedding_model: str = "voyage-4"
    rerank_model: str = "rerank-3"
    # Client-side pacing. The defaults are Voyage's limits for accounts without a
    # payment method; raise them once one is added (free tokens still apply).
    voyage_rpm: int = 3
    voyage_tpm: int = 10_000

    # LLMs (docs/architecture.md §7). "dev" runs Haiku for every role; "final" moves the
    # reasoning roles and the coder to bigger models for evals and the demo.
    anthropic_api_key: SecretStr | None = None
    llm_profile: Literal["dev", "final"] = "dev"

    # Hard monthly spend caps, checked before every paid call (aidev.llm.meter). Voyage
    # is capped in tokens because its free allowance is denominated in tokens.
    anthropic_monthly_usd_cap: float = 5.0
    voyage_monthly_token_cap: int = 30_000_000

    # Chat API
    chat_rate_limit_per_minute: int = 20  # per client IP
    max_message_chars: int = 4000
    # Browser origins allowed to call the API directly (the dev UI). In production the
    # UI and API share an origin behind the reverse proxy.
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
