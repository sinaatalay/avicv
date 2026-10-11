from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuration from environment variables and `backend/.env`."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    openrouter_api_key: SecretStr | None = None
    typesafe_api_key: SecretStr | None = None

    # jobs.db (written by `jobs-ingest`) and app.db (résumés and matches) live here
    data_dir: Path = BACKEND_DIR / "data"

    # Résumé parsing: OpenRouter model and its reasoning effort
    parse_model: str = "google/gemini-3.8-flash"
    parse_reasoning: Literal["none", "minimal", "low", "medium", "high"] = "low"

    # Matching: TypeSafe model, how many shortlisted jobs to score, and how many requests in flight
    jev_model: str = "jev-1.13.0"
    match_limit: int = 1000
    match_concurrency: int = 32

    # `jobs-ingest` skips a run when the last successful one is more recent than this
    jobs_refresh_hours: float = 6

    @property
    def jobs_db(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
