"""Gemini-based idea generation for LinkedIn content planning."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google import genai

from config import get_settings

logger = logging.getLogger(__name__)

IDEA_PROMPT_TEMPLATE = """You are a LinkedIn content strategist.

Generate {days} LinkedIn post ideas for the niche: {niche}

Each idea must include:
"title"
"hook"
"about"

You MUST return the result EXACTLY as a raw JSON array of objects. Do not wrap the JSON in markdown code blocks. Do not add any conversational text.

Example format:
[
  {{"title": "Idea 1", "hook": "Hook 1", "about": "About 1"}}
]
"""


def _build_client() -> genai.Client:
    """Create an authenticated Gemini client."""
    settings = get_settings()
    return genai.Client(api_key=settings.gemini_api_key)


def _parse_idea_json(raw_text: str) -> list[dict[str, str]]:
    """Parse Gemini output into a normalized list of idea dictionaries."""
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    candidates = [cleaned]
    array_match = re.search(r"\[[\s\S]*\]", cleaned)
    object_match = re.search(r"\{[\s\S]*\}", cleaned)
    if array_match:
        candidates.append(array_match.group(0))
    if object_match:
        candidates.append(object_match.group(0))

    parsed: Any = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    if parsed is None:
        raise ValueError("Gemini returned non-JSON response for ideas.")

    if isinstance(parsed, dict):
        ideas_source = parsed.get("ideas", [])
    elif isinstance(parsed, list):
        ideas_source = parsed
    else:
        ideas_source = []

    normalized_ideas: list[dict[str, str]] = []
    for item in ideas_source:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("Title") or "").strip()
        hook = str(item.get("hook") or item.get("Hook") or "").strip()
        about = str(item.get("about") or item.get("About") or item.get("brief_about") or "").strip()
        if title and hook and about:
            normalized_ideas.append({"title": title, "hook": hook, "about": about})

    if not normalized_ideas:
        raise ValueError("No valid ideas found in Gemini response.")
    return normalized_ideas


def generate_ideas(niche: str, days: int = 30) -> list[dict[str, str]]:
    """Generate LinkedIn idea rows (title/hook/about) for a niche."""
    if days <= 0:
        raise ValueError("days must be greater than 0.")

    prompt = IDEA_PROMPT_TEMPLATE.format(niche=niche, days=days)
    client = _build_client()
    logger.info("Generating %s ideas for niche: %s", days, niche)

    response = client.models.generate_content(
        model=get_settings().gemini_model,
        contents=prompt,
    )
    raw_text = (response.text or "").strip()
    ideas = _parse_idea_json(raw_text)

    if len(ideas) < days:
        logger.warning("Gemini returned %s ideas, fewer than requested %s.", len(ideas), days)

    return ideas[:days]
