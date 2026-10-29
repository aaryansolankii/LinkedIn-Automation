"""StatSigma-branded post images: pastel background, illustration card on the left, headline on the right, logo.

A port of reference/branded_images/make_images.py, which laid the design out in HTML and took a
headless-browser screenshot. Here the layout is computed in Python and drawn as one SVG that resvg
renders with the bundled fonts: no browser, no system fonts and no network access.

Claude supplies the illustration as SVG. It is checked (size, no scripts, no external or file
references) and rendered on its own, so its ids and <style> rules can't touch the rest of the image.
"""

from __future__ import annotations

import base64
import io
import re
import string
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import resvg_py
from PIL import Image, ImageFont

from .errors import LinkedInError

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
FONT_FILES = [str(ASSETS_DIR / "fonts" / name) for name in (
    "Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf", "Poppins-Bold.ttf",
)]
LOGO_FILE = ASSETS_DIR / "brand" / "logo.png"
ICON_FILE = ASSETS_DIR / "brand" / "icon.png"

# The layout is designed at 1200x675 and rendered at 2x.
WIDTH, HEIGHT, SCALE = 1200, 675, 2
CARD_X, CARD_Y, CARD_W, CARD_H, CARD_R = 44, 60, 520, 555, 30
COLUMN_X, COLUMN_W, COLUMN_H = 612, 548, 600  # text column, vertically centred in the top 600px
LOGO_H, LOGO_RIGHT, LOGO_BOTTOM = 66, 44, 34

NAVY, EYEBROW_BLUE, SUBTITLE_GREY = "#0B2C6F", "#0277BD", "#3E4C6E"
BAR_GRADIENT = ("#00BCD4", "#5E35B1")
HIGHLIGHT_GRADIENT = (("0", "#00A9CE"), (".6", "#3949AB"), ("1", "#6A1B9A"))

HEADLINE_FONT, HEADLINE_SIZES, HEADLINE_LINE_HEIGHT, HEADLINE_SPACING, HEADLINE_MAX_LINES = (
    "Poppins-Bold.ttf", range(54, 39, -2), 1.14, -0.5, 4,
)
EYEBROW_SIZE, EYEBROW_SPACING, EYEBROW_BAR_W, EYEBROW_GAP, EYEBROW_MARGIN = 20, 3, 40, 14, 20
SUBTITLE_SIZE, SUBTITLE_LINE_HEIGHT, SUBTITLE_MARGIN = 24, 1.35, 22
CTA_SIZE, CTA_PAD_X, CTA_PAD_Y, CTA_MARGIN = 20, 22, 11, 24

MAX_SVG_BYTES = 30 * 1024
SVG_NS, XLINK_NS = "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"
ICON_ID = "statsigma-icon"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


@dataclass(frozen=True)
class Palette:
    """Diagonal background gradient (start, middle at `middle_at`, end) and the two blurred blobs."""

    start: str
    middle: str
    end: str
    middle_at: float
    blobs: tuple[str, str]


PALETTES = {
    "sky": Palette("#E0F7FA", "#EDE7F6", "#FCE4EC", 0.55, ("#B2EBF2", "#D1C4E9")),
    "peach": Palette("#FFF3E0", "#F3E5F5", "#E3F2FD", 0.60, ("#FFE0B2", "#BBDEFB")),
    "mint": Palette("#E8F5E9", "#E1F5FE", "#EDE7F6", 0.55, ("#C8E6C9", "#B3E5FC")),
    "rose": Palette("#FCE4EC", "#EDE7F6", "#E0F7FA", 0.50, ("#F8BBD0", "#B2EBF2")),
    "lavender": Palette("#F3E5F5", "#EDE7F6", "#E8EAF6", 0.55, ("#E1BEE7", "#C5CAE9")),
}


# --- Text measurement ---------------------------------------------------------------------------


@cache
def _font(file: str, size: int) -> ImageFont.FreeTypeFont:
    # Basic layout on every platform, so wrapping is the same locally and on Cloud Run.
    return ImageFont.truetype(str(ASSETS_DIR / "fonts" / file), size, layout_engine=ImageFont.Layout.BASIC)


def _width(text: str, file: str, size: int, spacing: float = 0) -> float:
    return _font(file, size).getlength(text) + spacing * len(text)


@cache
def _metrics(file: str) -> tuple[float, float]:
    """Ascent and descent as a fraction of the font size."""
    ascent, descent = _font(file, 1000).getmetrics()
    return ascent / 1000, descent / 1000


def _baseline(file: str, size: int, line_top: float, line_height: float) -> float:
    """Where CSS puts the baseline of a line box: the glyphs' ascent+descent centred in the line."""
    ascent, descent = _metrics(file)
    return line_top + (line_height - (ascent + descent) * size) / 2 + ascent * size


def _wrap(words: list[str], file: str, size: int, width: float, spacing: float = 0) -> list[list[int]]:
    """Greedy word wrap; returns the indexes of the words on each line."""
    lines: list[list[int]] = []
    for index, word in enumerate(words):
        if lines and _width(" ".join(words[i] for i in [*lines[-1], index]), file, size, spacing) <= width:
            lines[-1].append(index)
        else:
            lines.append([index])
    return lines


# --- Headline -----------------------------------------------------------------------------------


def _normalize(word: str) -> str:
    return word.strip(string.punctuation + "“”‘’").lower()


def _highlighted(words: list[str], highlight: str) -> list[bool]:
    """Mark the headline words to draw in the gradient: each comma-separated phrase where it appears
    as a whole, otherwise its individual words."""
    flags = [False] * len(words)
    normalized = [_normalize(word) for word in words]
    for phrase in highlight.split(","):
        target = [word for word in map(_normalize, phrase.split()) if word]
        if not target:
            continue
        starts = [i for i in range(len(words) - len(target) + 1) if normalized[i : i + len(target)] == target]
        for start in starts:
            flags[start : start + len(target)] = [True] * len(target)
        if not starts:
            matches = [i for i, word in enumerate(normalized) if word in target]
            if not matches:
                raise LinkedInError(f"highlight {phrase.strip()!r} is not part of the headline.")
            for i in matches:
                flags[i] = True
    return flags


def _headline_lines(headline: str) -> tuple[int, list[list[int]], list[str]]:
    """Pick the largest size (54 down to 40) at which the headline fits the column in 4 lines or fewer.

    A newline in the headline forces a line break."""
    paragraphs = [line.split() for line in headline.splitlines() if line.split()]
    words = [word for paragraph in paragraphs for word in paragraph]
    for size in HEADLINE_SIZES:
        lines, offset = [], 0
        for paragraph in paragraphs:
            lines += [[offset + i for i in line] for line in _wrap(paragraph, HEADLINE_FONT, size, COLUMN_W, HEADLINE_SPACING)]
            offset += len(paragraph)
        fits = all(_width(" ".join(words[i] for i in line), HEADLINE_FONT, size, HEADLINE_SPACING) <= COLUMN_W for line in lines)
        if fits and len(lines) <= HEADLINE_MAX_LINES:
            break
    return size, lines, words


def _headline_svg(lines: list[list[int]], words: list[str], flags: list[bool], size: int, top: float) -> tuple[str, str]:
    """One <text> per line; each highlighted run gets its own left-to-right gradient. Returns (defs, body)."""
    defs, body = [], []
    line_height = size * HEADLINE_LINE_HEIGHT
    for row, line in enumerate(lines):
        baseline = _baseline(HEADLINE_FONT, size, top + row * line_height, line_height)
        runs: list[tuple[bool, list[str]]] = []
        for i in line:
            if runs and runs[-1][0] == flags[i]:
                runs[-1][1].append(words[i])
            else:
                runs.append((flags[i], [words[i]]))
        spans, x = [], float(COLUMN_X)
        for n, (highlight, run_words) in enumerate(runs):
            text = " ".join(run_words) + (" " if n < len(runs) - 1 else "")
            fill = ""
            if highlight:
                gradient_id = f"ss-hl-{row}-{n}"
                run_width = _width(" ".join(run_words), HEADLINE_FONT, size, HEADLINE_SPACING)
                stops = "".join(f'<stop offset="{at}" stop-color="{color}"/>' for at, color in HIGHLIGHT_GRADIENT)
                defs.append(
                    f'<linearGradient id="{gradient_id}" gradientUnits="userSpaceOnUse" x1="{x:.1f}" y1="0" '
                    f'x2="{x + run_width:.1f}" y2="0">{stops}</linearGradient>'
                )
                fill = f' fill="url(#{gradient_id})"'
            spans.append(f"<tspan{fill}>{escape(text)}</tspan>")
            x += _width(text, HEADLINE_FONT, size, HEADLINE_SPACING)
        body.append(
            f'<text x="{COLUMN_X}" y="{baseline:.1f}" font-family="Poppins" font-weight="700" font-size="{size}" '
            f'letter-spacing="{HEADLINE_SPACING}" fill="{NAVY}" xml:space="preserve">{"".join(spans)}</text>'
        )
    return "".join(defs), "".join(body)


# --- Illustration -------------------------------------------------------------------------------

_GENERIC_FAMILY = re.compile(r"\b(sans-serif|serif|monospace|cursive|fantasy|system-ui)\b", re.I)
_FONT_FAMILY_CSS = re.compile(r"(font-family\s*:\s*)([^;}\"']+)", re.I)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_PROCESSING_INSTRUCTION = re.compile(r"<\?\s*([\w.:-]+)")


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _with_fallback(families: str) -> str:
    """Only the bundled fonts exist when rendering, and an unknown family draws nothing: end every
    font-family list with a generic family (rendered in Inter)."""
    families = families.strip().rstrip(",")
    return families if _GENERIC_FAMILY.search(families) else f"{families}, sans-serif"


def _reject(reason: str) -> LinkedInError:
    return LinkedInError(f"illustration_svg was rejected: {reason}")


def check_illustration(svg: str) -> ET.Element:
    """Parse the illustration and refuse anything that could run code or load an external or local file."""
    size = len(svg.encode())
    if size > MAX_SVG_BYTES:
        raise _reject(f"it is {size / 1024:.1f} KB; the limit is 30 KB. Use fewer elements and round coordinates.")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", svg, re.I):
        raise _reject("DOCTYPE and entity declarations are not allowed.")
    if any(name.lower() != "xml" for name in _PROCESSING_INSTRUCTION.findall(svg)):
        raise _reject("processing instructions such as <?xml-stylesheet?> are not allowed.")
    if re.search(r"@import", svg, re.I):
        raise _reject("CSS @import is not allowed.")
    for _, target in _CSS_URL.findall(svg):
        if not target.strip().startswith("#"):
            raise _reject(f"url({target.strip()[:60]}) points outside the SVG; only url(#id) is allowed.")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise _reject(f"it is not well-formed XML ({exc}). Start it with <svg xmlns=\"{SVG_NS}\" ...>.") from exc
    if _local(root.tag) != "svg":
        raise _reject("the root element must be <svg>.")

    for element in root.iter():
        tag = _local(element.tag)
        if tag.lower() in ("script", "foreignobject"):
            raise _reject(f"<{tag}> is not allowed.")
        for name, value in element.attrib.items():
            attribute = _local(name).lower()
            if attribute.startswith("on"):
                raise _reject(f"event attribute {attribute!r} is not allowed.")
            if attribute == "href" and not (value.startswith("#") or value.startswith("data:image/png;base64,")):
                raise _reject(f"{_local(name)}={value[:60]!r}: links may only point to #ids or data:image/png;base64.")
    return root


def _prepare_illustration(root: ET.Element) -> str:
    """Size the illustration to the card, give every font a fallback and add the S mark if it is used."""
    uses_icon = False
    for element in root.iter():
        if not element.tag.startswith("{"):  # forgot xmlns: treat the elements as SVG anyway
            element.tag = f"{{{SVG_NS}}}{element.tag}"
        if "font-family" in element.attrib:
            element.set("font-family", _with_fallback(element.get("font-family", "")))
        if "style" in element.attrib:
            element.set("style", _FONT_FAMILY_CSS.sub(lambda m: m[1] + _with_fallback(m[2]), element.get("style", "")))
        if _local(element.tag) == "style" and element.text:
            element.text = _FONT_FAMILY_CSS.sub(lambda m: m[1] + _with_fallback(m[2]), element.text)
        uses_icon |= any(_local(k) == "href" and v == f"#{ICON_ID}" for k, v in element.attrib.items())

    root.attrib.pop("x", None)
    root.attrib.pop("y", None)
    root.set("width", str(CARD_W))
    root.set("height", str(CARD_H))
    root.attrib.setdefault("viewBox", f"0 0 {CARD_W} {CARD_H}")
    if uses_icon:
        icon_w, icon_h = _png_size(ICON_FILE)
        symbol = ET.SubElement(ET.SubElement(root, f"{{{SVG_NS}}}defs"), f"{{{SVG_NS}}}symbol",
                               {"id": ICON_ID, "viewBox": f"0 0 {icon_w} {icon_h}"})
        ET.SubElement(symbol, f"{{{SVG_NS}}}image", {"width": str(icon_w), "height": str(icon_h), "href": _data_uri(ICON_FILE)})
    return ET.tostring(root, encoding="unicode")


def _render(svg: str, width: int, height: int) -> bytes:
    try:
        return resvg_py.svg_to_bytes(
            svg_string=svg,
            width=width,
            height=height,
            skip_system_fonts=True,
            font_files=FONT_FILES,
            font_family="Inter",
            sans_serif_family="Inter",
            serif_family="Inter",
            monospace_family="Inter",
            cursive_family="Inter",
            fantasy_family="Inter",
        )
    except ValueError as exc:
        raise LinkedInError(f"Could not render the image: {exc}") from exc


# --- Brand assets -------------------------------------------------------------------------------


@cache
def _png_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


@cache
def _data_uri(path: Path, height: int | None = None) -> str:
    """A PNG as a data URI, optionally pre-scaled to `height` pixels (sharper than letting resvg scale it)."""
    data = path.read_bytes()
    if height is not None:
        with Image.open(path) as image:
            image = image.resize((round(image.width * height / image.height), height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            data = buffer.getvalue()
    return "data:image/png;base64," + base64.b64encode(data).decode()


# --- Layout -------------------------------------------------------------------------------------


@cache
def _backdrop(palette_name: str) -> str:
    """The palette's background, blurred blobs and the card's shadow, as a full-size PNG data URI.

    These layers are smooth, so they are rendered at 1/5 size and scaled up: the blurs take
    seconds at full size. The result only depends on the palette, so it is built once per process.
    """
    palette = PALETTES[palette_name]
    # CSS linear-gradient(135deg, ...): the gradient line runs through the centre and is (W+H)/sqrt(2)
    # long, so its ends are (W+H)/4 from the centre along each axis.
    half, cx, cy = (WIDTH + HEIGHT) / 4, WIDTH / 2, HEIGHT / 2
    blob1, blob2 = palette.blobs
    svg = (
        f'<svg xmlns="{SVG_NS}" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}"><defs>'
        f'<linearGradient id="bg" gradientUnits="userSpaceOnUse" x1="{cx - half}" y1="{cy - half}" x2="{cx + half}" '
        f'y2="{cy + half}"><stop offset="0" stop-color="{palette.start}"/><stop offset="{palette.middle_at}" '
        f'stop-color="{palette.middle}"/><stop offset="1" stop-color="{palette.end}"/></linearGradient>'
        '<filter id="blob" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="60"/></filter>'
        '<filter id="shadow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="30"/></filter>'
        f'</defs><rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>'
        f'<circle cx="90" cy="70" r="210" fill="{blob1}" opacity=".7" filter="url(#blob)"/>'
        f'<circle cx="1110" cy="625" r="230" fill="{blob2}" opacity=".7" filter="url(#blob)"/>'
        # CSS box-shadow: 0 26px 60px rgba(11,44,111,.16) under the card.
        f'<rect x="{CARD_X}" y="{CARD_Y + 26}" width="{CARD_W}" height="{CARD_H}" rx="{CARD_R}" fill="{NAVY}" '
        'fill-opacity=".16" filter="url(#shadow)"/></svg>'
    )
    small = Image.open(io.BytesIO(_render(svg, WIDTH * SCALE // 5, HEIGHT * SCALE // 5))).convert("RGB")
    buffer = io.BytesIO()
    small.resize((WIDTH * SCALE, HEIGHT * SCALE), Image.Resampling.BICUBIC).save(buffer, "PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def render(
    headline: str,
    eyebrow: str,
    illustration_svg: str,
    highlight: str = "",
    subtitle: str = "",
    cta: str = "",
    palette: str = "sky",
) -> bytes:
    """Render the branded image and return it as a 2400x1350 PNG."""
    headline, eyebrow, subtitle, cta = headline.strip(), eyebrow.strip().upper(), " ".join(subtitle.split()), cta.strip()
    if not headline or not eyebrow:
        raise LinkedInError("Pass a headline and an eyebrow (a short topic label).")
    if len(headline.split()) > 16:
        raise LinkedInError("The headline is too long: keep it to about 10 words.")
    if palette not in PALETTES:
        raise LinkedInError(f"Unknown palette {palette!r}; use one of {', '.join(PALETTES)}.")
    eyebrow_x = COLUMN_X + EYEBROW_BAR_W + EYEBROW_GAP
    if _width(eyebrow, "Inter-Bold.ttf", EYEBROW_SIZE, EYEBROW_SPACING) > COLUMN_X + COLUMN_W - eyebrow_x:
        raise LinkedInError("The eyebrow is too long for one line: use a short topic label (1-3 words).")
    cta_w = _width(cta, "Inter-Bold.ttf", CTA_SIZE) + 2 * CTA_PAD_X
    if cta and cta_w > COLUMN_W:
        raise LinkedInError("The cta is too long for one line: use a short label such as a domain.")
    illustration = _render(_prepare_illustration(check_illustration(illustration_svg)), CARD_W * SCALE, CARD_H * SCALE)

    size, lines, words = _headline_lines(headline)
    flags = _highlighted(words, highlight) if highlight.strip() else [False] * len(words)
    inter_ascent, inter_descent = _metrics("Inter-Bold.ttf")
    eyebrow_h = (inter_ascent + inter_descent) * EYEBROW_SIZE  # CSS line-height: normal
    subtitle_words = subtitle.split()
    subtitle_lines = [
        " ".join(subtitle_words[i] for i in line)
        for line in _wrap(subtitle_words, "Inter-Medium.ttf", SUBTITLE_SIZE, COLUMN_W)
    ]
    subtitle_line_h = SUBTITLE_SIZE * SUBTITLE_LINE_HEIGHT
    cta_h = (inter_ascent + inter_descent) * CTA_SIZE + 2 * CTA_PAD_Y
    column_h = (
        eyebrow_h + EYEBROW_MARGIN + len(lines) * size * HEADLINE_LINE_HEIGHT
        + (SUBTITLE_MARGIN + len(subtitle_lines) * subtitle_line_h if subtitle_lines else 0)
        + (CTA_MARGIN + cta_h if cta else 0)
    )
    y = (COLUMN_H - column_h) / 2

    defs = [
        f'<clipPath id="ss-card"><rect x="{CARD_X}" y="{CARD_Y}" width="{CARD_W}" height="{CARD_H}" rx="{CARD_R}"/></clipPath>',
        f'<linearGradient id="ss-bar" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{BAR_GRADIENT[0]}"/>'
        f'<stop offset="1" stop-color="{BAR_GRADIENT[1]}"/></linearGradient>',
    ]
    body = [
        f'<image width="{WIDTH}" height="{HEIGHT}" href="{_backdrop(palette)}"/>',
        # The white card with the illustration clipped to its rounded corners.
        f'<rect x="{CARD_X}" y="{CARD_Y}" width="{CARD_W}" height="{CARD_H}" rx="{CARD_R}" fill="#fff"/>',
        f'<image x="{CARD_X}" y="{CARD_Y}" width="{CARD_W}" height="{CARD_H}" clip-path="url(#ss-card)" '
        f'href="data:image/png;base64,{base64.b64encode(illustration).decode()}"/>',
        # Eyebrow: gradient bar, then the label.
        f'<rect x="{COLUMN_X}" y="{y + eyebrow_h / 2 - 3:.1f}" width="{EYEBROW_BAR_W}" height="6" rx="3" fill="url(#ss-bar)"/>',
        f'<text x="{eyebrow_x}" y="{_baseline("Inter-Bold.ttf", EYEBROW_SIZE, y, eyebrow_h):.1f}" font-family="Inter" '
        f'font-weight="700" font-size="{EYEBROW_SIZE}" letter-spacing="{EYEBROW_SPACING}" fill="{EYEBROW_BLUE}">{escape(eyebrow)}</text>',
    ]
    y += eyebrow_h + EYEBROW_MARGIN
    headline_defs, headline_body = _headline_svg(lines, words, flags, size, y)
    defs.append(headline_defs)
    body.append(headline_body)
    y += len(lines) * size * HEADLINE_LINE_HEIGHT
    if subtitle_lines:
        y += SUBTITLE_MARGIN
        for line in subtitle_lines:
            body.append(
                f'<text x="{COLUMN_X}" y="{_baseline("Inter-Medium.ttf", SUBTITLE_SIZE, y, subtitle_line_h):.1f}" '
                f'font-family="Inter" font-weight="500" font-size="{SUBTITLE_SIZE}" fill="{SUBTITLE_GREY}">{escape(line)}</text>'
            )
            y += subtitle_line_h
    if cta:
        y += CTA_MARGIN
        body.append(
            f'<rect x="{COLUMN_X}" y="{y:.1f}" width="{cta_w:.1f}" height="{cta_h:.1f}" rx="{cta_h / 2:.1f}" fill="{NAVY}"/>'
            f'<text x="{COLUMN_X + cta_w / 2:.1f}" y="{_baseline("Inter-Bold.ttf", CTA_SIZE, y + CTA_PAD_Y, cta_h - 2 * CTA_PAD_Y):.1f}" '
            f'text-anchor="middle" font-family="Inter" font-weight="700" font-size="{CTA_SIZE}" fill="#fff">{escape(cta)}</text>'
        )

    logo_w, logo_h = _png_size(LOGO_FILE)
    logo_w = LOGO_H * logo_w / logo_h
    body.append(
        f'<image x="{WIDTH - LOGO_RIGHT - logo_w:.1f}" y="{HEIGHT - LOGO_BOTTOM - LOGO_H}" width="{logo_w:.1f}" '
        f'height="{LOGO_H}" href={quoteattr(_data_uri(LOGO_FILE, LOGO_H * SCALE))}/>'
    )
    svg = (
        f'<svg xmlns="{SVG_NS}" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
        f'<defs>{"".join(defs)}</defs>{"".join(body)}</svg>'
    )
    return _render(svg, WIDTH * SCALE, HEIGHT * SCALE)
