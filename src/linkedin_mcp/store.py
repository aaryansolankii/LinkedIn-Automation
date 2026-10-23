"""SQLite content calendar: ideas, drafts, scheduled and published posts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from .config import DATA_DIR, DB_FILE
from .errors import LinkedInError

# idea -> draft -> scheduled -> publishing -> published
#                      \-> failed (publish_due could not post it; reschedule to retry)
STATUSES = ("idea", "draft", "scheduled", "publishing", "published", "failed")
EDITABLE_FIELDS = ("title", "hook", "notes", "content", "image_path", "publish_at")
_WRITABLE_COLUMNS = {*EDITABLE_FIELDS, "status", "published_at", "post_urn", "error"}
TIME_FORMAT = "%Y-%m-%d %H:%M"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL DEFAULT '',
    hook         TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    image_path   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'idea',
    publish_at   TEXT NOT NULL DEFAULT '',  -- local time, 'YYYY-MM-DD HH:MM'
    published_at TEXT NOT NULL DEFAULT '',
    post_urn     TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""


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


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open the database (creating it if needed) inside a single transaction."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


def _check_columns(fields: dict[str, str]) -> None:
    """Reject column names that are not writable (they are interpolated into SQL)."""
    unknown = set(fields) - _WRITABLE_COLUMNS
    if unknown:
        raise ValueError(f"Unknown post fields: {sorted(unknown)}")


def add_post(**fields: str) -> dict[str, Any]:
    """Insert a post and return it."""
    _check_columns(fields)
    stamp = now()
    row = {**fields, "created_at": stamp, "updated_at": stamp}
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    with _connect() as conn:
        cursor = conn.execute(f"INSERT INTO posts ({columns}) VALUES ({placeholders})", tuple(row.values()))
        post_id = cursor.lastrowid
    return get_post(post_id)


def get_post(post_id: int) -> dict[str, Any]:
    """Return one post or raise when it does not exist."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        raise LinkedInError(f"Post {post_id} not found. Use list_posts to see post ids.")
    return dict(row)


def update_post(post_id: int, **fields: str) -> dict[str, Any]:
    """Update the given columns of a post and return it."""
    _check_columns(fields)
    get_post(post_id)
    if not fields:
        return get_post(post_id)
    fields["updated_at"] = now()
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE posts SET {assignments} WHERE id = ?", (*fields.values(), post_id))
    return get_post(post_id)


def claim_post(post_id: int, from_statuses: tuple[str, ...]) -> bool:
    """Atomically move a post to 'publishing' so it can never be posted twice."""
    placeholders = ", ".join("?" for _ in from_statuses)
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE posts SET status = 'publishing', updated_at = ? WHERE id = ? AND status IN ({placeholders})",
            (now(), post_id, *from_statuses),
        )
    return cursor.rowcount == 1


def delete_post(post_id: int) -> None:
    """Delete a post from the calendar."""
    get_post(post_id)
    with _connect() as conn:
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))


def list_posts(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List posts by publish time (undated last); without a status, published posts are left out."""
    if status is None:
        where, params = "status != 'published'", ()
    else:
        where, params = "status = ?", (status,)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM posts WHERE {where} ORDER BY publish_at = '', publish_at, id LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def due_posts(as_of: str) -> list[dict[str, Any]]:
    """Return scheduled posts whose publish time is at or before `as_of`."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status = 'scheduled' AND publish_at != '' AND publish_at <= ? ORDER BY publish_at",
            (as_of,),
        ).fetchall()
    return [dict(row) for row in rows]


def status_counts() -> dict[str, int]:
    """Count posts per status."""
    with _connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status").fetchall()
    return {row["status"]: row["n"] for row in rows}
