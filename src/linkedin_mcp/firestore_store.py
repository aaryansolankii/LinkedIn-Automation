"""Firestore backend for the content calendar and the LinkedIn login (hosted mode).

Posts are documents in `posts/<id>` with the same fields as the SQLite rows. Ids stay
small integers (allocated from `meta/counters`) so tools keep using numeric post ids.
"""

from __future__ import annotations

import os
from functools import cache
from typing import Any

from google.cloud import firestore

_COUNTER = ("meta", "counters")
_CREDENTIALS = ("settings", "linkedin")


@cache
def _db() -> firestore.Client:
    """Return a client for the configured database (uses the Cloud Run service account)."""
    return firestore.Client(database=os.environ.get("FIRESTORE_DATABASE", "(default)"))


def _post_ref(post_id: int) -> firestore.DocumentReference:
    return _db().collection("posts").document(str(post_id))


def insert(row: dict[str, str]) -> int:
    """Allocate the next id and create the post in one transaction."""
    counter = _db().collection(_COUNTER[0]).document(_COUNTER[1])

    @firestore.transactional
    def allocate(transaction: firestore.Transaction) -> int:
        snapshot = counter.get(transaction=transaction)
        post_id = (snapshot.to_dict() or {}).get("posts", 0) + 1
        transaction.set(counter, {"posts": post_id}, merge=True)
        transaction.set(_post_ref(post_id), {**row, "id": post_id})
        return post_id

    return allocate(_db().transaction())


def fetch(post_id: int) -> dict[str, Any] | None:
    """Return one post, or None."""
    snapshot = _post_ref(post_id).get()
    return snapshot.to_dict() if snapshot.exists else None


def update(post_id: int, fields: dict[str, str]) -> None:
    """Set the given fields of a post."""
    _post_ref(post_id).update(fields)


def claim(post_id: int, from_statuses: tuple[str, ...], stamp: str) -> bool:
    """Move a post to 'publishing' only if it is still in one of `from_statuses`."""
    ref = _post_ref(post_id)

    @firestore.transactional
    def move(transaction: firestore.Transaction) -> bool:
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists or snapshot.get("status") not in from_statuses:
            return False
        transaction.update(ref, {"status": "publishing", "updated_at": stamp})
        return True

    return move(_db().transaction())


def delete(post_id: int) -> None:
    """Delete a post."""
    _post_ref(post_id).delete()


def query(status: str | None) -> list[dict[str, Any]]:
    """Return all posts, or only those with the given status."""
    posts = _db().collection("posts")
    if status is not None:
        posts = posts.where(filter=firestore.FieldFilter("status", "==", status))
    return [snapshot.to_dict() for snapshot in posts.stream()]


def load_credentials() -> dict[str, str]:
    """Return the saved LinkedIn login, or an empty dict."""
    snapshot = _db().collection(_CREDENTIALS[0]).document(_CREDENTIALS[1]).get()
    return snapshot.to_dict() or {}


def save_credentials(data: dict[str, str]) -> None:
    """Replace the saved LinkedIn login."""
    _db().collection(_CREDENTIALS[0]).document(_CREDENTIALS[1]).set(data)
