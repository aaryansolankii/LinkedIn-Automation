"""Title-card images for posts, rendered locally with Pillow (no image API needed)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import cache

from PIL import Image, ImageColor, ImageDraw, ImageFont

SIZE = 1200  # square: takes the most room in the LinkedIn feed
MARGIN = 96


@dataclass(frozen=True)
class Theme:
    """Gradient, text and accent colors for a card."""

    top: str
    bottom: str
    text: str
    muted: str
    accent: str


THEMES = {
    "sky": Theme("#E0F2FE", "#EEF2FF", "#0F172A", "#475569", "#2563EB"),
    "mint": Theme("#DCFCE7", "#E0F2FE", "#0F172A", "#334155", "#059669"),
    "peach": Theme("#FFF1E6", "#FDE2E4", "#1C1917", "#57534E", "#EA580C"),
    "lavender": Theme("#EDE9FE", "#FCE7F3", "#1E1B4B", "#4B5563", "#7C3AED"),
    "midnight": Theme("#0F172A", "#1E293B", "#F8FAFC", "#CBD5E1", "#38BDF8"),
}

# Pillow looks these names up in the OS font folders (Windows, macOS, Linux).
_FONT_NAMES = {
    "bold": ("segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
    "semibold": ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"),
    "regular": ("segoeui.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
}


@cache
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return the first available system font of a weight, or Pillow's built-in font."""
    for name in _FONT_NAMES[weight]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    """Greedy word-wrap to a pixel width."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if not line or draw.textlength(candidate, font=font) <= width:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_title(draw: ImageDraw.ImageDraw, title: str, width: int, max_height: int) -> tuple[ImageFont.ImageFont, list[str]]:
    """Pick the largest title size whose wrapped lines fit the available box."""
    for size in range(104, 47, -4):
        font = _font("bold", size)
        lines = _wrap(draw, title, font, width)
        fits_width = all(draw.textlength(line, font=font) <= width for line in lines)
        if fits_width and len(lines) * size * 1.15 <= max_height:
            return font, lines
    return font, lines


def render_title_card(title: str, subtitle: str = "", eyebrow: str = "", author: str = "", theme: str = "sky") -> bytes:
    """Render a square title card and return it as PNG bytes."""
    colors = THEMES[theme]
    width = SIZE - 2 * MARGIN

    # Vertical gradient background with two soft accent circles.
    mask = Image.linear_gradient("L").resize((SIZE, SIZE))
    card = Image.composite(Image.new("RGB", (SIZE, SIZE), colors.bottom), Image.new("RGB", (SIZE, SIZE), colors.top), mask)
    card = card.convert("RGBA")
    shapes = Image.new("RGBA", card.size, (0, 0, 0, 0))
    accent = ImageColor.getrgb(colors.accent)
    shape_draw = ImageDraw.Draw(shapes)
    shape_draw.ellipse((SIZE - 460, SIZE - 460, SIZE + 300, SIZE + 300), fill=(*accent, 30))
    shape_draw.ellipse((SIZE - 330, 90, SIZE - 110, 310), fill=(*accent, 22))
    card = Image.alpha_composite(card, shapes)
    draw = ImageDraw.Draw(card)

    # Accent bar and optional eyebrow label at the top.
    draw.rounded_rectangle((MARGIN, 120, MARGIN + 96, 132), radius=6, fill=colors.accent)
    if eyebrow:
        draw.text((MARGIN, 168), eyebrow.upper(), font=_font("semibold", 34), fill=colors.accent)

    # Title and subtitle, centred vertically between the header and the footer.
    top, bottom = 260, SIZE - 200
    subtitle_font = _font("regular", 42)
    subtitle_lines = _wrap(draw, subtitle, subtitle_font, width)[:3] if subtitle else []
    subtitle_height = len(subtitle_lines) * 42 * 1.35 + (40 if subtitle_lines else 0)
    title_font, title_lines = _fit_title(draw, title, width, int(bottom - top - subtitle_height))
    title_line_height = title_font.size * 1.15
    y = top + (bottom - top - len(title_lines) * title_line_height - subtitle_height) / 2
    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill=colors.text)
        y += title_line_height
    y += 40
    for line in subtitle_lines:
        draw.text((MARGIN, y), line, font=subtitle_font, fill=colors.muted)
        y += 42 * 1.35

    if author:
        draw.ellipse((MARGIN, SIZE - 128, MARGIN + 20, SIZE - 108), fill=colors.accent)
        draw.text((MARGIN + 36, SIZE - 140), author, font=_font("semibold", 34), fill=colors.text)

    buffer = io.BytesIO()
    card.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def preview_jpeg(png: bytes, max_side: int = 640) -> bytes:
    """Return a small JPEG preview of an image (keeps the tool result light)."""
    with Image.open(io.BytesIO(png)) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()
