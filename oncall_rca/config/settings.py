"""Application settings loaded from environment variables and .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings


class LogApiSettings(BaseSettings):
    """Log API connection settings."""

    model_config = {"env_prefix": "LOG_API_"}

    base_url: str = "https://bqapi.cleartripcorp.me/bqAPI"
    token: str = ""
    user: str = ""


class GmailSettings(BaseSettings):
    """Gmail polling settings."""

    model_config = {"env_prefix": "GMAIL_"}

    label: str = ""
    credentials_path: Path = Path("credentials.json")
    poll_interval_seconds: int = 60


class RepoSettings(BaseSettings):
    """Repository clone settings."""

    model_config = {"env_prefix": "REPO_"}

    clone_base: Path = Path("~/.oncall_rca/repos").expanduser()
    supply_core_new_branch: str = ""
    air_sms_branch: str = ""
    air_sms_new_branch: str = ""


class ModelSettings(BaseSettings):
    """LLM model settings."""

    model_config = {"env_prefix": "GEMINI_"}

    model: str = "gemini-2.0-flash"
    api_key: str = ""


class BudgetSettings(BaseSettings):
    """Execution budget limits."""

    investigator_max_iterations: int = 10
    tokens_per_stage_limit: int = 50_000
    wall_clock_per_run_seconds: int = 300


class OutputSettings(BaseSettings):
    """Output path settings."""

    model_config = {"env_prefix": "OUTPUT_"}

    rca_dir: Path = Path("rca_reports")
    cache_dir: Path = Path("cache")


class Settings(BaseSettings):
    """Root settings aggregating all subsections.

    Loads from .env file if present, then from environment variables.
    """

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    log_api: Annotated[LogApiSettings, Field(default_factory=LogApiSettings)]
    gmail: Annotated[GmailSettings, Field(default_factory=GmailSettings)]
    repos: Annotated[RepoSettings, Field(default_factory=RepoSettings)]
    model: Annotated[ModelSettings, Field(default_factory=ModelSettings)]
    budgets: Annotated[BudgetSettings, Field(default_factory=BudgetSettings)]
    output: Annotated[OutputSettings, Field(default_factory=OutputSettings)]


def load_settings() -> Settings:
    """Load settings from .env and environment."""
    return Settings()
