"""Settings, credentials and on-disk locations shared by the MCP server and the CLI."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

# One data directory for every entry point (Claude Code plugin, Claude Desktop, the
# publish-due scheduled task) so they all share the same calendar and login.
DATA_DIR = Path(os.environ.get("LINKEDIN_DATA_DIR") or Path.home() / ".linkedin-mcp").expanduser()
ENV_FILE = DATA_DIR / ".env"
DB_FILE = DATA_DIR / "posts.db"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
IMAGES_DIR = DATA_DIR / "images"
LOG_FILE = DATA_DIR / "linkedin-mcp.log"

# Deployment switches: unset locally, set on Cloud Run (see deploy.md).
STORAGE = os.environ.get("STORAGE", "sqlite").strip().lower()  # "sqlite" or "firestore"
IMAGES_BUCKET = os.environ.get("IMAGES_BUCKET", "").strip()  # Cloud Storage bucket for title cards
if STORAGE not in ("sqlite", "firestore"):
    raise RuntimeError(f"STORAGE must be 'sqlite' or 'firestore', not {STORAGE!r}.")


def _env(name: str, default: str = "") -> str:
    """Read a setting from the process environment, then DATA_DIR/.env.

    Blank values count as unset, so an empty variable in an MCP client config
    does not hide the value in the .env file.
    """
    value = os.environ.get(name) or dotenv_values(ENV_FILE).get(name) or default
    return value.strip()


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Only the client id/secret are needed, and only for login."""

    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    api_version: str
    posting_time: str
    author_urn: str
    page_only: bool = False  # only ever post as a company page (urn:li:organization:...)


def get_settings() -> Settings:
    """Load settings fresh on every call so .env edits apply without a restart."""
    return Settings(
        client_id=_env("LINKEDIN_CLIENT_ID"),
        client_secret=_env("LINKEDIN_CLIENT_SECRET"),
        redirect_uri=_env("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback"),
        scopes=_env("LINKEDIN_SCOPES", "openid profile w_member_social"),
        api_version=_env("LINKEDIN_API_VERSION", "202610"),
        posting_time=_env("POSTING_TIME", "10:00"),
        author_urn=_env("LINKEDIN_AUTHOR_URN"),
        page_only=_env("PAGE_ONLY").lower() in ("1", "true", "yes", "on"),
    )


@dataclass(frozen=True)
class Credentials:
    """The access token and the identity posts are published as."""

    access_token: str
    author_urn: str
    name: str = ""
    expires_at: str = ""  # ISO local datetime; empty when unknown

    def is_expired(self) -> bool:
        """Return True when the token has a known expiry that has passed."""
        return bool(self.expires_at) and datetime.fromisoformat(self.expires_at) <= datetime.now()


def load_credentials() -> Credentials | None:
    """Return saved login credentials, with env overrides applied, or None."""
    saved = _saved_credentials()

    # A token or author set explicitly (e.g. an organization page URN) wins over the login.
    token = _env("LINKEDIN_ACCESS_TOKEN") or saved.get("access_token", "")
    author = _env("LINKEDIN_AUTHOR_URN") or saved.get("author_urn", "")
    if not token:
        return None
    from_login = token == saved.get("access_token")
    return Credentials(
        access_token=token,
        author_urn=author,
        name=saved.get("name", "") if from_login else "",
        expires_at=saved.get("expires_at", "") if from_login else "",
    )


def _saved_credentials() -> dict[str, str]:
    """Return what the last login saved: from Firestore in hosted mode, else from DATA_DIR."""
    if STORAGE == "firestore":
        from . import firestore_store

        return firestore_store.load_credentials()
    if CREDENTIALS_FILE.exists():
        return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    return {}


def save_credentials(credentials: Credentials) -> None:
    """Persist login credentials: to Firestore in hosted mode, else to DATA_DIR (owner-only)."""
    if STORAGE == "firestore":
        from . import firestore_store

        firestore_store.save_credentials(asdict(credentials))
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(asdict(credentials), indent=2), encoding="utf-8")
    CREDENTIALS_FILE.chmod(0o600)
