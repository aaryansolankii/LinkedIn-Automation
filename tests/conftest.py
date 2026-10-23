"""Point the app at a throwaway data dir (local SQLite mode) before anything imports it."""

import os
import tempfile

os.environ["LINKEDIN_DATA_DIR"] = tempfile.mkdtemp(prefix="linkedin-mcp-tests-")
for name in ("STORAGE", "IMAGES_BUCKET", "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN"):
    os.environ.pop(name, None)

import pytest  # noqa: E402

from linkedin_mcp.config import DB_FILE  # noqa: E402


@pytest.fixture(autouse=True)
def empty_calendar():
    """Start every test with an empty calendar."""
    DB_FILE.unlink(missing_ok=True)
    yield
    DB_FILE.unlink(missing_ok=True)
