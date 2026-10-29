"""Branded images: size, highlight and CTA, headline fitting, illustration safety, and attaching to a post."""

import base64
import io
from pathlib import Path

import pytest
from mcp.server.mcpserver import Image as MCPImage
from PIL import Image

from linkedin_mcp import branded, server, store

ILLUSTRATION = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 555">
<circle cx="450" cy="90" r="110" fill="#EDE7F6"/>
<rect x="28" y="120" width="464" height="300" rx="18" fill="#F7F9FD" stroke="#E6ECF5"/>
<line x1="44" y1="165" x2="440" y2="165" stroke="#EF5350" stroke-width="2.5" stroke-dasharray="8 6"/>
<line x1="44" y1="270" x2="440" y2="270" stroke="#90A4AE" stroke-width="2.5"/>
<line x1="44" y1="375" x2="440" y2="375" stroke="#EF5350" stroke-width="2.5" stroke-dasharray="8 6"/>
<polyline points="60,268 100,246 140,293 180,256 220,132 260,284 300,243" fill="none" stroke="#1E88E5" stroke-width="3"/>
<circle cx="220" cy="132" r="9" fill="#FF8A65"/>
<text x="448" y="170" font-family="Inter" font-size="14" font-weight="700" fill="#EF5350">UCL</text>
<use href="#statsigma-icon" x="420" y="460" width="56" height="60"/>
</svg>"""


def png_image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def gradient_pixels(image: Image.Image) -> int:
    """Pixels in the headline area tinted like the highlight gradient (cyan through purple), not navy."""
    headline_area = image.crop((1224, 380, 2340, 1000)).resize((279, 155))
    pixels = zip(*(band.tobytes() for band in headline_area.split()))
    return sum(1 for r, g, b in pixels if (g > 120 and b > 150 and r < 80) or (r > 70 and b > 130 and g < 60))


@pytest.mark.parametrize("highlight, cta", [("control chart", "thestatsigma.com"), ("", "")])
def test_renders_2400_by_1350(highlight, cta):
    png = branded.render("Read a control chart in 30 seconds", "Control charts", ILLUSTRATION,
                         highlight=highlight, subtitle="Signal or noise?", cta=cta, palette="mint")
    assert png.startswith(b"\x89PNG")
    assert png_image(png).size == (2400, 1350)


def test_highlight_and_cta_change_the_image():
    kwargs = dict(headline="Stop reacting to every up and down", eyebrow="Control charts", illustration_svg=ILLUSTRATION)
    plain = png_image(branded.render(**kwargs))
    highlighted = png_image(branded.render(**kwargs, highlight="up and down"))
    with_cta = png_image(branded.render(**kwargs, cta="thestatsigma.com"))

    assert gradient_pixels(highlighted) > gradient_pixels(plain) + 50
    assert with_cta.tobytes() != plain.tobytes()


def test_long_headlines_shrink_to_four_lines():
    size, lines, _ = branded._headline_lines("Six Sigma tools")
    assert size == 54 and len(lines) == 1
    size, lines, _ = branded._headline_lines("Why your control chart keeps crying wolf and what you should do about it before Monday")
    assert 40 <= size < 54 and len(lines) == 4
    size, lines, _ = branded._headline_lines("Measurement system analysis: repeatability, reproducibility and bias explained without the jargon")
    assert size == 40  # never smaller, even if it then needs a fifth line
    size, lines, words = branded._headline_lines("Cp looks good.\nCpk looks bad.\nWhy?")
    assert [[words[i] for i in line] for line in lines] == [["Cp", "looks", "good."], ["Cpk", "looks", "bad."], ["Why?"]]


def test_highlight_matches_phrases_then_words():
    words = "Cp looks good. Cpk looks bad.".split()
    assert branded._highlighted(words, "Cp, Cpk") == [True, False, False, True, False, False]
    assert branded._highlighted(words, "looks good") == [False, True, True, False, False, False]
    with pytest.raises(branded.LinkedInError):
        branded._highlighted(words, "p-value")


@pytest.mark.parametrize(
    "svg",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect onClick="x()" width="5" height="5"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="/etc/passwd"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///C:/secret.png"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/svg+xml;base64,PHN2Zy8+"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="other.svg#a"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(http://evil.example/p)" width="5" height="5"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><style>@import "http://evil.example/a.css";</style></svg>',
        '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
        '<?xml-stylesheet href="http://evil.example/a.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="5"',
        '<html><body/></html>',
        '<svg xmlns="http://www.w3.org/2000/svg">' + '<rect width="1" height="1"/>' * 1500 + "</svg>",
    ],
    ids=["script", "foreignObject", "onload", "onclick", "http-href", "file-path", "file-url", "svg-data-uri",
         "xlink-external", "css-url", "css-import", "entity", "xml-stylesheet", "malformed", "not-svg", "over-30KB"],
)
def test_unsafe_illustrations_are_rejected(svg):
    with pytest.raises(branded.LinkedInError, match="illustration_svg"):
        branded.render("Headline", "Topic", svg)


def test_allowed_links_and_unknown_fonts_render():
    tiny_png = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(tiny_png, "PNG")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 555">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#00BCD4"/></linearGradient></defs>'
        '<rect width="100" height="100" fill="url(#g)"/>'
        f'<image x="200" y="0" width="40" height="40" href="data:image/png;base64,{base64.b64encode(tiny_png.getvalue()).decode()}"/>'
        '<text x="10" y="300" font-family="Arial" font-size="40">Arial falls back to Inter</text></svg>'
    )
    png = branded._render(branded._prepare_illustration(branded.check_illustration(svg)), 1040, 1110)
    illustration = Image.open(io.BytesIO(png))
    assert illustration.getpixel((440, 40)) == (255, 0, 0, 255)  # the embedded PNG
    assert illustration.getchannel("A").crop((0, 520, 1040, 620)).getbbox() is not None  # the text was drawn


def test_image_is_saved_and_attached_to_the_post():
    post = store.add_post(title="Control charts", content="Text", status="draft")
    result = server.make_branded_image(
        headline="Stop reacting to every up and down", eyebrow="Control charts", illustration_svg=ILLUSTRATION,
        highlight="up and down", post_id=post["id"],
    )
    message, preview = result
    image_path = store.get_post(post["id"])["image_path"]
    assert image_path and image_path in message and f"post {post['id']}" in message
    assert png_image(Path(image_path).read_bytes()).size == (2400, 1350)
    assert isinstance(preview, MCPImage)
