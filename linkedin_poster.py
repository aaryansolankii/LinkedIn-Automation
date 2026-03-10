"""LinkedIn API client module for publishing approved posts."""

from __future__ import annotations

import logging
import os

import requests

from config import get_settings

logger = logging.getLogger(__name__)

# NEW REST API endpoint (replaces v2/ugcPosts)
LINKEDIN_POSTS_URL = "https://api.linkedin.com/rest/posts"


def _upload_image(image_path: str, author_urn: str, headers: dict) -> str | None:
    """Upload an image to LinkedIn and return its URN."""
    if not image_path or not os.path.isfile(image_path):
        return None

    init_url = "https://api.linkedin.com/rest/images?action=initializeUpload"
    init_payload = {
        "initializeUploadRequest": {
            "owner": author_urn
        }
    }

    logger.info("Initializing image upload for %s", image_path)
    init_res = requests.post(init_url, headers=headers, json=init_payload)
    if init_res.status_code not in (200, 201):
        logger.error("Failed to initialize image upload. Status: %s, Response: %s", init_res.status_code, init_res.text)
        return None

    init_data = init_res.json()
    upload_url = init_data["value"]["uploadUrl"]
    image_urn = init_data["value"]["image"]

    logger.info("Uploading image data to LinkedIn URL.")
    try:
        with open(image_path, "rb") as img_file:
            put_headers = {"Authorization": headers["Authorization"]}
            put_res = requests.put(upload_url, headers=put_headers, data=img_file)
            if put_res.status_code not in (200, 201):
                logger.error("Failed to upload image data. Status: %s, Response: %s", put_res.status_code, put_res.text)
                return None
    except Exception:
        logger.exception("Exception during image data upload.")
        return None

    return image_urn


def post_to_linkedin(content: str, image_path: str = "") -> bool:
    """Publish content and an optional image to LinkedIn using REST Posts API."""
    settings = get_settings()

    # REST API uses urn:li:person: format (OpenID format)
    author_urn = settings.linkedin_author_urn
    
    # Ensure person format (not member) for personal posts
    if author_urn.startswith("urn:li:member:"):
        author_urn = author_urn.replace("urn:li:member:", "urn:li:person:")
    
    if not (author_urn.startswith("urn:li:person:") or author_urn.startswith("urn:li:organization:")):
        logger.error("Invalid author URN format: %s. Must be urn:li:person: or urn:li:organization:", author_urn)
        return False

    # REST API headers - requires LinkedIn-Version!
    headers = {
        "Authorization": f"Bearer {settings.linkedin_access_token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202602",  # Required for REST API
        "X-Restli-Protocol-Version": "2.0.0"
    }

    image_urn = _upload_image(image_path, author_urn, headers)

    # REST API payload structure
    payload = {
        "author": author_urn,
        "commentary": content,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False
    }

    if image_urn:
        payload["content"] = {
            "media": {
                "id": image_urn
            }
        }
    
    logger.info("Posting to LinkedIn REST API with author: %s", author_urn)
    
    response = requests.post(
        LINKEDIN_POSTS_URL,
        headers=headers,
        json=payload
    )
    
    if response.status_code in (200, 201):
        post_id = response.headers.get('x-restli-id', 'unknown')
        logger.info("LinkedIn post published successfully. Post ID: %s", post_id)
        return True
    else:
        logger.error("LinkedIn response code: %s", response.status_code)
        logger.error("LinkedIn response body: %s", response.text)
        return False