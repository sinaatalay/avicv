from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuration from environment variables and `backend/.env`."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    openrouter_api_key: SecretStr | None = None
    openrouter_provider: str = Field(
        default="google-vertex",
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9]+(?:[-/][a-z0-9]+)*$",
    )
    typesafe_api_key: SecretStr | None = None
    app_origin: str = "http://localhost:5173"
    uploads_per_day: int = Field(default=10, ge=1)
    tailors_per_day: int = Field(default=50, ge=1)
    chats_per_day: int = Field(default=50, ge=1)
    retries_per_day: int = Field(default=10, ge=1)
    global_actions_per_day: int = Field(default=1000, ge=1)
    data_dir: Path = BACKEND_DIR / "data"
    parse_model: str = "google/gemini-3.8-flash"
    parse_reasoning: Literal["none", "minimal", "low", "medium", "high"] = "low"
    design_model: str = "google/gemini-3.8-flash"
    tailor_model: str = "google/gemini-3.8-flash"
    design_max_iterations: int = Field(default=3, ge=1, le=5)
    design_timeout_seconds: float = Field(default=300, ge=30, le=900)
    render_timeout_seconds: float = Field(default=60, ge=1, le=300)
    artifact_workers_enabled: bool = True
    artifact_queue_max_pending: int = Field(default=100, ge=1, le=10000)
    artifact_lease_seconds: float = Field(default=90, ge=3, le=600)
    artifact_timeout_seconds: float = Field(default=900, ge=1, le=3600)
    artifact_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    jev_model: str = "jev-1.13.0"
    match_limit: int = Field(default=1000, ge=1, le=10000)
    match_concurrency: int = Field(default=32, ge=1, le=128)
    jobs_refresh_hours: float = Field(default=6, gt=0, le=168)

    # A SQLite lease permits only one active résumé across API worker processes.
    queue_max_pending: int = Field(default=100, ge=1, le=10000)
    queue_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    queue_lease_seconds: float = Field(default=60, ge=3, le=600)
    catalog_retry_seconds: float = Field(default=30, ge=1, le=3600)
    processing_timeout_seconds: float = Field(default=1800, ge=1, le=14400)

    @field_validator("app_origin")
    @classmethod
    def validate_app_origin(cls, value: str) -> str:
        """One public origin is shared by cookies, CSRF, MCP and file links."""
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError(
                "APP_ORIGIN must be an http(s) origin, e.g. https://avicv.com"
            )
        # Accessing port also rejects malformed or out-of-range ports.
        _ = parsed.port
        return value

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        """Relative DATA_DIR always means relative to backend/, regardless of launch cwd."""
        return (BACKEND_DIR / value.expanduser()).resolve()

    @property
    def jobs_db(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
