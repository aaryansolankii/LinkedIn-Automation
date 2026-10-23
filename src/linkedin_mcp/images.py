"""Where post images live: the local images folder, or a Cloud Storage bucket in hosted mode.

A post's `image_path` is either a local file path or a `gs://<IMAGES_BUCKET>/...` URI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from .config import IMAGES_BUCKET, IMAGES_DIR
from .errors import LinkedInError

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif"}


@cache
def _bucket() -> Any:
    """Return the configured Cloud Storage bucket (imported lazily: local mode doesn't need it)."""
    from google.cloud import storage

    return storage.Client().bucket(IMAGES_BUCKET)


def _check_type(name: str) -> None:
    if PurePosixPath(name).suffix.lower() not in IMAGE_TYPES:
        raise LinkedInError(f"Unsupported image type for {name!r}; use PNG, JPG or GIF.")


def save_card(png: bytes) -> str:
    """Store a rendered title card and return the reference to save on the post."""
    filename = f"card_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.png"
    if IMAGES_BUCKET:
        _bucket().blob(f"cards/{filename}").upload_from_string(png, content_type="image/png")
        return f"gs://{IMAGES_BUCKET}/cards/{filename}"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGES_DIR / filename
    path.write_bytes(png)
    return str(path)


def validate(image_ref: str) -> str:
    """Check that an image can be attached to a post and return its normalized reference."""
    if image_ref.startswith("gs://"):
        bucket, _, name = image_ref.removeprefix("gs://").partition("/")
        if not IMAGES_BUCKET or bucket != IMAGES_BUCKET:
            raise LinkedInError(f"Only images in gs://{IMAGES_BUCKET or '<IMAGES_BUCKET>'}/ can be attached.")
        _check_type(name)
        if not _bucket().blob(name).exists():
            raise LinkedInError(f"Image not found: {image_ref}")
        return image_ref
    if IMAGES_BUCKET:
        raise LinkedInError(
            "The hosted server can't read files from your computer. Create the image with make_post_image instead."
        )
    path = Path(image_ref).expanduser().resolve()
    if not path.is_file():
        raise LinkedInError(f"Image not found: {path}")
    _check_type(path.name)
    return str(path)


def load(image_ref: str) -> bytes:
    """Return the bytes of a validated image reference."""
    if image_ref.startswith("gs://"):
        _, _, name = image_ref.removeprefix("gs://").partition("/")
        return _bucket().blob(name).download_as_bytes()
    return Path(image_ref).read_bytes()
