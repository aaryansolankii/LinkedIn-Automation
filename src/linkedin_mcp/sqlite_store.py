"""SQLite backend for the content calendar (local mode)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import DATA_DIR, DB_FILE

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


def insert(row: dict[str, str]) -> int:
    """Insert a full post row and return its new id."""
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    with _connect() as conn:
        cursor = conn.execute(f"INSERT INTO posts ({columns}) VALUES ({placeholders})", tuple(row.values()))
    return cursor.lastrowid


def fetch(post_id: int) -> dict[str, Any] | None:
    """Return one post, or None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return dict(row) if row else None


def update(post_id: int, fields: dict[str, str]) -> None:
    """Set the given columns of a post."""
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE posts SET {assignments} WHERE id = ?", (*fields.values(), post_id))


def claim(post_id: int, from_statuses: tuple[str, ...], stamp: str) -> bool:
    """Move a post to 'publishing' only if it is still in one of `from_statuses`."""
    placeholders = ", ".join("?" for _ in from_statuses)
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE posts SET status = 'publishing', updated_at = ? WHERE id = ? AND status IN ({placeholders})",
            (stamp, post_id, *from_statuses),
        )
    return cursor.rowcount == 1


def delete(post_id: int) -> None:
    """Delete a post."""
    with _connect() as conn:
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))


def query(status: str | None) -> list[dict[str, Any]]:
    """Return all posts, or only those with the given status."""
    with _connect() as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM posts").fetchall()
        else:
            rows = conn.execute("SELECT * FROM posts WHERE status = ?", (status,)).fetchall()
    return [dict(row) for row in rows]
