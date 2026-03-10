"""Gemini-based full LinkedIn post generation."""

from __future__ import annotations

import json
import logging
import re

from google import genai
from google.genai import types

from config import get_settings

logger = logging.getLogger(__name__)

CONTENT_PROMPT_TEMPLATE = """You are a seasoned LinkedIn creator who writes like a real human, not a robot.

Using this information:
Title: {title}
Hook: {hook}
About: {about}

Write a high-quality LinkedIn post (150-220 words) and an image prompt. Follow these STRICT rules:

WRITING STYLE:
- Write in first person ("I", "we", "my") — personal and direct
- Sound like a real professional sharing genuine insight, NOT a marketing copy
- Use conversational, plain language — no corporate buzzwords or AI-sounding phrases
- Vary sentence length naturally — mix short punchy lines with longer ones
- Show personality: be a little bold, honest, even slightly contrarian if fits the topic

FORMATTING (LinkedIn does NOT render markdown — follow this exactly):
- NEVER use ** or * or _ anywhere — these show as literal characters on LinkedIn
- Separate each paragraph with a blank line (double line break)
- Use bullet points with • symbol only for lists (max 4-5 bullets)
- For emphasis on key words, use CAPS sparingly (1-2 words max per post) or just strong word choice
- Start with a short, punchy hook line (1 sentence, no intro fluff)
- End with a direct question to the reader OR a call-to-action on a line by itself

HASHTAGS:
- Write exactly 7-9 hashtags at the very end
- Format: #Word (e.g. #AIStrategy not "hashtag#AIStrategy")
- Put all hashtags on a single line separated by spaces
- Choose hashtags relevant to the niche, not generic ones like #Business or #Life

Also generate a short image prompt that visually represents the core topic.

You MUST return the result EXACTLY as a raw JSON object. Do not wrap it in markdown. No extra text.

Required JSON format:
{{
  "title": "...",
  "post": "...",
  "hashtags": "#Tag1 #Tag2 #Tag3 ...",
  "image_prompt": "..."
}}
"""


def _build_client() -> genai.Client:
    """Create an authenticated Gemini client for post writing."""
    settings = get_settings()
    return genai.Client(api_key=settings.gemini_api_key)


def _parse_post_json(raw_text: str) -> dict[str, str]:
    """Parse Gemini output into a normalized output dictionary."""
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    
    try:
        parsed = json.loads(cleaned)
        return parsed
    except json.JSONDecodeError:
        # Fallback regex extraction if exact JSON parsing fails
        logger.warning("Failed standard JSON parsing. Attempting regex extract.")
        object_match = re.search(r"\{[\s\S]*\}", cleaned)
        if object_match:
            try:
                return json.loads(object_match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError("Gemini returned non-JSON response for post generation.")


def generate_post(title: str, hook: str, about: str) -> tuple[str, str]:
    """Generate a polished LinkedIn post and image prompt from title, hook, and context.
    Returns: (post_content, image_prompt)
    """
    if not title or not hook or not about:
        raise ValueError("title, hook, and about are required to generate post content.")

    prompt = CONTENT_PROMPT_TEMPLATE.format(title=title, hook=hook, about=about)
    logger.info("Generating LinkedIn post for title: %s", title)

    client = _build_client()
    response = client.models.generate_content(
        model=get_settings().gemini_model,
        contents=prompt,
    )
    raw_text = (response.text or "").strip()

    if not raw_text:
        raise RuntimeError("Gemini returned empty post content.")
        
    parsed = _parse_post_json(raw_text)
    
    gen_title = str(parsed.get("title", title)).strip()
    gen_post = str(parsed.get("post", "")).strip()
    gen_hashtags = str(parsed.get("hashtags", "")).strip()
    image_prompt = str(parsed.get("image_prompt", "")).strip()
    
    # Combine the text attributes into one cohesive string
    final_post = f"{gen_title}\n\n{gen_post}"
    if gen_hashtags:
        final_post += f"\n\n{gen_hashtags}"
        
    return final_post, image_prompt
