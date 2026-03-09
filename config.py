"""Central configuration and logging setup for the automation pipeline."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().with_name(".env"))

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Initialize process-wide logging in a consistent format."""
    logging.basicConfig(level=level, format=LOG_FORMAT)


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    gemini_api_key: str
    email_host: str
    email_port: int
    email_user: str
    email_password: str
    owner_email: str
    linkedin_access_token: str
    linkedin_author_urn: str
    gemini_model: str
    scheduler_time: str
    posting_time: str


def _read_env(name: str, required: bool = True, default: str = "") -> str:
    """Read an environment variable with optional required validation."""
    value = os.getenv(name, default).strip()
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache validated runtime settings."""
    return Settings(
        gemini_api_key=_read_env("GEMINI_API_KEY"),
        email_host=_read_env("EMAIL_HOST"),
        email_port=int(_read_env("EMAIL_PORT")),
        email_user=_read_env("EMAIL_USER"),
        email_password=_read_env("EMAIL_PASSWORD"),
        owner_email=_read_env("OWNER_EMAIL"),
        linkedin_access_token=_read_env("LINKEDIN_ACCESS_TOKEN"),
        linkedin_author_urn=_read_env("LINKEDIN_AUTHOR_URN"),
        gemini_model=_read_env("GEMINI_MODEL", required=False, default="gemini-2.5-flash"),
        scheduler_time=_read_env("SCHEDULER_TIME", required=False, default="23:11"),
        posting_time=_read_env("POSTING_TIME", required=False, default="10:00"),
    )
