"""LinkedIn REST API client: profile lookup, image upload and post creation."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from . import images
from .config import Credentials, get_settings, load_credentials
from .errors import LinkedInError

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com"
TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# The Posts API reads `commentary` as LinkedIn's "little" text format, where these
# characters are reserved and must be backslash-escaped; unescaped ones can make
# LinkedIn cut the post short. A '#' that starts a word is left alone because
# '#word' is the format's native hashtag element.
_LITTLE_TEXT = re.compile(r"(#(?=[^\W_]))|([|{}@\[\]()<>#\\*_~])")


@dataclass(frozen=True)
class PublishedPost:
    """Identifiers of a post that is live on LinkedIn."""

    urn: str
    url: str


def to_little_text(text: str) -> str:
    """Escape plain text for the `commentary` field, keeping #hashtags clickable."""
    return _LITTLE_TEXT.sub(lambda m: m.group(1) or "\\" + m.group(2), text)


def post_url(post_urn: str) -> str:
    """Return the public URL of a post."""
    return f"https://www.linkedin.com/feed/update/{post_urn}/"


def request(method: str, url: str, action: str, **kwargs: Any) -> httpx.Response:
    """Send one HTTP request and turn every failure into a readable LinkedInError."""
    try:
        response = httpx.request(method, url, timeout=TIMEOUT, **kwargs)
    except httpx.TransportError as exc:
        raise LinkedInError(f"Could not reach LinkedIn during {action}: {exc}") from exc
    if response.is_success:
        return response

    try:
        body = response.json()
        detail = body.get("message") or body.get("error_description") or response.text
    except ValueError:
        detail = response.text
    hints = {
        401: " The access token is invalid or expired: run linkedin_login to reconnect.",
        403: " The token is not allowed to do this: check the app's products/scopes and LINKEDIN_AUTHOR_URN.",
        426: " LINKEDIN_API_VERSION is no longer supported: set a newer YYYYMM version.",
    }
    logger.error("LinkedIn %s failed (%s): %s", action, response.status_code, response.text)
    raise LinkedInError(
        f"LinkedIn {action} failed ({response.status_code}): {str(detail)[:300]}{hints.get(response.status_code, '')}",
        status_code=response.status_code,
    )


def get_profile(access_token: str) -> dict[str, Any]:
    """Return the OpenID Connect userinfo (sub, name, ...) for a token."""
    response = request(
        "GET", f"{API_BASE}/v2/userinfo", "profile lookup", headers={"Authorization": f"Bearer {access_token}"}
    )
    return response.json()


def _require_credentials() -> Credentials:
    """Return usable credentials or explain how to get them."""
    credentials = load_credentials()
    if credentials is None:
        raise LinkedInError("Not connected to LinkedIn yet. Run linkedin_login first.")
    if credentials.is_expired():
        raise LinkedInError(f"The LinkedIn token expired on {credentials.expires_at}. Run linkedin_login to reconnect.")
    if not credentials.author_urn:
        raise LinkedInError("No author URN is known. Run linkedin_login, or set LINKEDIN_AUTHOR_URN.")
    return credentials


def _author_urn(credentials: Credentials) -> str:
    """Return the author as the person/organization URN the Posts API expects."""
    author = credentials.author_urn
    if author.startswith("urn:li:member:"):
        author = "urn:li:person:" + author.removeprefix("urn:li:member:")
    if not author.startswith(("urn:li:person:", "urn:li:organization:")):
        raise LinkedInError(f"Invalid author URN {author!r}: expected urn:li:person:... or urn:li:organization:...")
    return author


def _upload_image(headers: dict[str, str], owner: str, image: bytes) -> str:
    """Upload an image and return its URN for use in a post."""
    init = request(
        "POST",
        f"{API_BASE}/rest/images",
        "image upload",
        params={"action": "initializeUpload"},
        headers=headers,
        json={"initializeUploadRequest": {"owner": owner}},
    )
    value = init.json()["value"]
    request(
        "PUT",
        value["uploadUrl"],
        "image upload",
        headers={"Authorization": headers["Authorization"]},
        content=image,
    )
    return value["image"]


def publish(text: str, image_path: str = "") -> PublishedPost:
    """Publish text (and an optional image) as a public LinkedIn post."""
    settings = get_settings()
    credentials = _require_credentials()
    author = _author_urn(credentials)
    if settings.page_only and not author.startswith("urn:li:organization:"):
        raise LinkedInError(
            f"PAGE_ONLY is on, so posts can only go out as a company page (urn:li:organization:...), "
            f"not as {author}. Set LINKEDIN_AUTHOR_URN to the page's URN."
        )
    image = images.load(images.validate(image_path)) if image_path else None
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "LinkedIn-Version": settings.api_version,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    payload: dict[str, Any] = {
        "author": author,
        "commentary": to_little_text(text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if image:
        payload["content"] = {"media": {"id": _upload_image(headers, author, image)}}

    response = request("POST", f"{API_BASE}/rest/posts", "post creation", headers=headers, json=payload)
    urn = response.headers.get("x-restli-id", "")
    logger.info("Published LinkedIn post %s as %s", urn, author)
    return PublishedPost(urn=urn, url=post_url(urn) if urn else "")
