"""Content calendar: ideas, drafts, scheduled and published posts.

Posts live in SQLite locally, or in Firestore when STORAGE=firestore (hosted mode).
Both backends store the same row shape and implement the same small interface.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .config import STORAGE
from .errors import LinkedInError

if STORAGE == "firestore":
    from . import firestore_store as _backend
else:
    from . import sqlite_store as _backend

# idea -> draft -> scheduled -> publishing -> published
#                      \-> failed (publish_due could not post it; reschedule to retry)
STATUSES = ("idea", "draft", "scheduled", "publishing", "published", "failed")
EDITABLE_FIELDS = ("title", "hook", "notes", "content", "image_path", "publish_at")
_WRITABLE_COLUMNS = {*EDITABLE_FIELDS, "status", "published_at", "post_urn", "error"}
_EMPTY_POST = {column: "" for column in _WRITABLE_COLUMNS} | {"status": "idea"}
TIME_FORMAT = "%Y-%m-%d %H:%M"


def now() -> str:
    """Return the current local time in the calendar's format."""
    return datetime.now().strftime(TIME_FORMAT)


def normalize_time(value: str, default_time: str) -> str:
    """Turn 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM' or any ISO datetime into local 'YYYY-MM-DD HH:MM'."""
    value = value.strip()
    if not value:
        return ""
    if len(value) == 10:
        value = f"{value} {default_time}"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LinkedInError(f"Invalid date/time {value!r}; use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.strftime(TIME_FORMAT)


def _check_columns(fields: dict[str, str]) -> None:
    """Reject column names that are not writable (the SQLite backend interpolates them into SQL)."""
    unknown = set(fields) - _WRITABLE_COLUMNS
    if unknown:
        raise ValueError(f"Unknown post fields: {sorted(unknown)}")


def _calendar_order(post: dict[str, Any]) -> tuple[bool, str, int]:
    """Sort by publish time, undated posts last."""
    return (post["publish_at"] == "", post["publish_at"], post["id"])


def add_post(**fields: str) -> dict[str, Any]:
    """Insert a post and return it."""
    _check_columns(fields)
    stamp = now()
    post_id = _backend.insert({**_EMPTY_POST, **fields, "created_at": stamp, "updated_at": stamp})
    return get_post(post_id)


def get_post(post_id: int) -> dict[str, Any]:
    """Return one post or raise when it does not exist."""
    post = _backend.fetch(post_id)
    if post is None:
        raise LinkedInError(f"Post {post_id} not found. Use list_posts to see post ids.")
    return post


def update_post(post_id: int, **fields: str) -> dict[str, Any]:
    """Update the given columns of a post and return it."""
    _check_columns(fields)
    get_post(post_id)
    if fields:
        _backend.update(post_id, {**fields, "updated_at": now()})
    return get_post(post_id)


def claim_post(post_id: int, from_statuses: tuple[str, ...]) -> bool:
    """Atomically move a post to 'publishing' so it can never be posted twice."""
    return _backend.claim(post_id, from_statuses, now())


def delete_post(post_id: int) -> None:
    """Delete a post from the calendar."""
    get_post(post_id)
    _backend.delete(post_id)


def list_posts(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List posts by publish time (undated last); without a status, published posts are left out."""
    posts = _backend.query(status)
    if status is None:
        posts = [post for post in posts if post["status"] != "published"]
    return sorted(posts, key=_calendar_order)[:limit]


def due_posts(as_of: str) -> list[dict[str, Any]]:
    """Return approved (scheduled) posts whose publish time is at or before `as_of`. Never drafts."""
    due = [post for post in _backend.query("scheduled") if post["publish_at"] and post["publish_at"] <= as_of]
    return sorted(due, key=_calendar_order)


def status_counts() -> dict[str, int]:
    """Count posts per status."""
    return dict(Counter(post["status"] for post in _backend.query(None)))
