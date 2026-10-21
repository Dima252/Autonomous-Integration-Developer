from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a .env file.

    The backend usually runs from `backend/`, so both the repo-root `.env` and a
    local one are read; later files win.
    """

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://aidev:aidev@localhost:5432/aidev"
    redis_url: str = "redis://localhost:6379/0"
    # Downloaded specs and docs (gitignored). Re-creatable from the pinned sources.
    data_dir: Path = REPO_ROOT / "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
