"""LinkedIn API client module for publishing approved posts."""

from __future__ import annotations

import logging

import requests

from config import get_settings

logger = logging.getLogger(__name__)

# NEW REST API endpoint (replaces v2/ugcPosts)
LINKEDIN_POSTS_URL = "https://api.linkedin.com/rest/posts"


def post_to_linkedin(content: str) -> bool:
    """Publish content to LinkedIn using REST Posts API."""
    settings = get_settings()

    # REST API uses urn:li:person: format (OpenID format)
    author_urn = settings.linkedin_author_urn
    
    # Ensure person format (not member) for personal posts
    if author_urn.startswith("urn:li:member:"):
        author_urn = author_urn.replace("urn:li:member:", "urn:li:person:")
    
    if not (author_urn.startswith("urn:li:person:") or author_urn.startswith("urn:li:organization:")):
        logger.error("Invalid author URN format: %s. Must be urn:li:person: or urn:li:organization:", author_urn)
        return False

    # REST API payload structure (different from UGC Posts)
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

    # REST API headers - requires LinkedIn-Version!
    headers = {
        "Authorization": f"Bearer {settings.linkedin_access_token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202602",  # Required for REST API
        "X-Restli-Protocol-Version": "2.0.0"
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