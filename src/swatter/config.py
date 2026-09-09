"""All configuration comes from environment variables (or a .env file). See .env.example."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Slack
    slack_bot_token: str
    slack_app_token: str

    # GitHub: either a PAT or the three GitHub App fields
    github_token: str | None = None
    github_app_id: int | None = None
    github_app_private_key_path: Path | None = None
    github_app_installation_id: int | None = None
    github_default_repo: str | None = None

    # LLM: any OpenAI-compatible endpoint
    llm_base_url: str = "https://api.anthropic.com/v1/"
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"
    # JSON object of extra HTTP headers, for provider quirks such as Anthropic's
    # anthropic-workspace-id on organisation-level keys or OpenRouter's HTTP-Referer.
    llm_extra_headers: dict[str, str] = {}

    # Embeddings
    embedding_provider: Literal["fastembed", "endpoint"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_extra_headers: dict[str, str] = {}

    # Swatter behaviour
    swatter_db_path: Path = Path("data/swatter.db")
    swatter_poll_interval_seconds: int = 120
    swatter_closed_lookback_days: int = 30
    swatter_clarification_timeout_minutes: int = 30
    swatter_max_clarification_questions: int = 3
    swatter_max_candidates: int = 3
    swatter_assets_branch: str = "swatter-assets"
    swatter_log_level: str = "INFO"

    @property
    def uses_github_app(self) -> bool:
        return self.github_token is None

    @model_validator(mode="after")
    def _check_github_auth(self) -> Settings:
        app_fields = (
            self.github_app_id,
            self.github_app_private_key_path,
            self.github_app_installation_id,
        )
        if self.github_token:
            return self
        if all(f is not None for f in app_fields):
            return self
        raise ValueError(
            "Set GITHUB_TOKEN, or all of GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH "
            "and GITHUB_APP_INSTALLATION_ID."
        )

    @model_validator(mode="after")
    def _check_embedding_endpoint(self) -> Settings:
        if self.embedding_provider == "endpoint" and not self.embedding_base_url:
            raise ValueError("EMBEDDING_PROVIDER=endpoint requires EMBEDDING_BASE_URL.")
        return self


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
