"""Gemini-based full LinkedIn post generation."""

from __future__ import annotations

import logging

import google.generativeai as genai

from config import get_settings

logger = logging.getLogger(__name__)

CONTENT_PROMPT_TEMPLATE = """You are an expert LinkedIn ghostwriter known for highly engaging, viral content.

Using this information:
Title: {title}
Hook: {hook}
About: {about}

Write a high-quality LinkedIn post (120-200 words). Follow these STRICT rules:
1. Start directly with the hook.
2. Use very short, punchy paragraphs (1-2 sentences max) with double line breaks between them.
3. Use emojis strategically (maximum 3-4 total).
4. Use lists with standard bullet points (•) for readability.
5. Emphasize key terms by using ALL CAPS. 
6. NEVER use Markdown asterisks (** or *) or underscores (_) anywhere in the response. LinkedIn cannot render markdown.
7. Include 8-10 highly relevant hashtags at the very end of the post (e.g., #AI #SoftwareEngineering).
8. End with a strong, single-sentence question to drive comments."""


def _build_model() -> genai.GenerativeModel:
    """Create an authenticated Gemini model instance for post writing."""
    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(settings.gemini_model)


def generate_post(title: str, hook: str, about: str) -> str:
    """Generate a polished LinkedIn post from title, hook, and context."""
    if not title or not hook or not about:
        raise ValueError("title, hook, and about are required to generate post content.")

    prompt = CONTENT_PROMPT_TEMPLATE.format(title=title, hook=hook, about=about)
    logger.info("Generating LinkedIn post for title: %s", title)

    model = _build_model()
    response = model.generate_content(prompt)
    generated_post = (response.text or "").strip()

    if not generated_post:
        raise RuntimeError("Gemini returned empty post content.")
    return generated_post
