"""Gemini-based image generation module using the new google-genai SDK."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

from config import get_settings

logger = logging.getLogger(__name__)

# Base directory for generated images — inside the project folder
IMAGE_DIR = Path(__file__).resolve().parent / "generated_images"

# The specific style guide provided by the user
STYLE_GUIDE = (
    "minimal SaaS illustration, flat vector design, pastel colors, "
    "rounded UI cards, soft shadows, modern startup aesthetic, "
    "clean whitespace, professional LinkedIn marketing style. "
    "The layout must be: LEFT SIDE: minimal illustration representing the topic, "
    "RIGHT SIDE: title text of the post. "
    "BACKGROUND: soft gradient or neutral background."
)


def generate_and_save_image(title: str, topic: str, image_prompt: str) -> str:
    """Generates an image via Gemini Flash image generation and saves it to disk."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    full_prompt = (
        f"Generate an image for a LinkedIn post.\n"
        f"Title: {title}\n"
        f"Topic: {topic}\n"
        f"Visual idea: {image_prompt}\n"
        f"Style requirements: {STYLE_GUIDE}"
    )

    logger.info("Generating image for topic: %s", topic)
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp-image-generation",
            contents=full_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Extract image bytes from the response parts
        image_bytes = None
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_bytes = part.inline_data.data
                break

        if not image_bytes:
            raise RuntimeError("Gemini returned no image data.")

        # Save image locally
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        file_name = f"post_{timestamp}_{unique_id}.png"
        file_path = IMAGE_DIR / file_name

        with open(file_path, "wb") as f:
            f.write(image_bytes)

        logger.info("Saved generated image to %s", file_path)
        return str(file_path)

    except Exception:
        logger.exception("Image generation failed.")
        raise

