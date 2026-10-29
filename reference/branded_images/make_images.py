"""Branded StatSigma LinkedIn images: pastel background, illustration card left, headline right, logo."""
import base64
import io
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
ATT = Path("/mnt/user-data/attachments/5ac31251-95ad-5ef7-80c7-2ff7bc9a4cef")
ICON_SRC, LOGO_SRC = ATT / "8e3591e3-image.png", ATT / "416b97c4-image.png"

NAVY, BLUE, PURPLE, CYAN, ORANGE, GREY = "#0B2C6F", "#1E88E5", "#5E35B1", "#00BCD4", "#FF8A65", "#5C6B8A"


def white_to_alpha(src: Path) -> Image.Image:
    """Turn the logo's white background transparent (exact un-blend from white) and crop."""
    img = Image.open(src).convert("RGBA")
    flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat.alpha_composite(img)  # a transparent background becomes white first
    a = np.asarray(flat.convert("RGB")).astype(np.float32) / 255
    alpha = 1 - a.min(axis=2)
    alpha[alpha < 0.04] = 0
    safe = np.where(alpha > 0, alpha, 1)
    c = np.clip((a - (1 - alpha)[..., None]) / safe[..., None], 0, 1)
    im = Image.fromarray((np.dstack([c, alpha]) * 255).round().astype(np.uint8), "RGBA")
    return im.crop(im.getbbox())


def uri(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


ICON, LOGO = uri(white_to_alpha(ICON_SRC)), uri(white_to_alpha(LOGO_SRC))


def bell(mean, sd, base, height, x0, x1, n=160):
    return [(x0 + (x1 - x0) * i / n, base - height * math.exp(-0.5 * ((x0 + (x1 - x0) * i / n - mean) / sd) ** 2)) for i in range(n + 1)]


def path(pts, close_to=None):
    d = "M %.1f %.1f " % pts[0] + " ".join("L %.1f %.1f" % p for p in pts[1:])
    if close_to is not None:
        d += " L %.1f %.1f L %.1f %.1f Z" % (pts[-1][0], close_to, pts[0][0], close_to)
    return d


DEFS = f"""<defs>
<linearGradient id="brand" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{CYAN}"/><stop offset=".55" stop-color="{BLUE}"/><stop offset="1" stop-color="{PURPLE}"/></linearGradient>
<linearGradient id="brandSoft" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#90CAF9" stop-opacity=".75"/><stop offset="1" stop-color="#E3F2FD" stop-opacity=".35"/></linearGradient>
<linearGradient id="warm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFB74D"/><stop offset="1" stop-color="#FF7043"/></linearGradient>
<filter id="sh" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#0B2C6F" flood-opacity=".12"/></filter>
</defs>"""

T = 'font-family="Inter, Inter Display, sans-serif"'


def pill(x, y, w, text, fill, stroke, color, size=14):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="36" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{x + w / 2}" y="{y + 23.5}" text-anchor="middle" {T} font-size="{size}" font-weight="700" fill="{color}">{text}</text>')


def svg_tools():
    """Post 1: a browser window holding four StatSigma tools."""
    s = [DEFS, '<circle cx="90" cy="470" r="120" fill="#E0F7FA"/><circle cx="450" cy="90" r="110" fill="#EDE7F6"/>']
    s.append('<g filter="url(#sh)"><rect x="34" y="78" width="452" height="410" rx="20" fill="#fff" stroke="#DCE3F0" stroke-width="2"/></g>')
    s.append('<path d="M34 98 a20 20 0 0 1 20 -20 h412 a20 20 0 0 1 20 20 v26 h-452 z" fill="#F1F5FB"/>')
    s += [f'<circle cx="{x}" cy="101" r="6" fill="{c}"/>' for x, c in ((58, "#FF8A80"), (78, "#FFD180"), (98, "#B9F6CA"))]
    s.append(f'<rect x="124" y="89" width="220" height="24" rx="12" fill="#fff" stroke="#DCE3F0"/><text x="140" y="106" {T} font-size="13" fill="{GREY}">thestatsigma.com</text>')
    tiles = [(52, 140, "Control chart"), (270, 140, "Capability"), (52, 312, "Gauge R&amp;R"), (270, 312, "Fishbone")]
    for x, y, label in tiles:
        s.append(f'<rect x="{x}" y="{y}" width="198" height="160" rx="14" fill="#F7F9FD" stroke="#E6ECF5"/>'
                 f'<text x="{x + 14}" y="{y + 26}" {T} font-size="13" font-weight="700" fill="{NAVY}">{label}</text>')
    x, y = 52, 140  # control chart tile
    s.append(f'<line x1="{x+14}" y1="{y+52}" x2="{x+184}" y2="{y+52}" stroke="#EF5350" stroke-width="2" stroke-dasharray="6 5"/>'
             f'<line x1="{x+14}" y1="{y+95}" x2="{x+184}" y2="{y+95}" stroke="#B0BEC5" stroke-width="2"/>'
             f'<line x1="{x+14}" y1="{y+138}" x2="{x+184}" y2="{y+138}" stroke="#EF5350" stroke-width="2" stroke-dasharray="6 5"/>')
    ys = [95, 82, 108, 90, 116, 78, 100, 44, 92, 110, 86]
    pts = [(x + 20 + i * 16, y + v) for i, v in enumerate(ys)]
    s.append(f'<polyline points="{" ".join(f"{a},{b}" for a, b in pts)}" fill="none" stroke="{BLUE}" stroke-width="2.5"/>')
    s += [f'<circle cx="{a}" cy="{b}" r="{6 if i == 7 else 3.5}" fill="{ORANGE if i == 7 else "#fff"}" stroke="{ORANGE if i == 7 else BLUE}" stroke-width="2"/>' for i, (a, b) in enumerate(pts)]
    x, y = 270, 140  # capability histogram
    heights = [14, 30, 56, 84, 96, 80, 52, 26, 12]
    for i, h in enumerate(heights):
        s.append(f'<rect x="{x + 28 + i * 16}" y="{y + 140 - h}" width="13" height="{h}" rx="3" fill="url(#brand)" opacity=".85"/>')
    s.append(f'<line x1="{x+20}" y1="{y+38}" x2="{x+20}" y2="{y+142}" stroke="#EF5350" stroke-width="2" stroke-dasharray="5 4"/>'
             f'<line x1="{x+178}" y1="{y+38}" x2="{x+178}" y2="{y+142}" stroke="#EF5350" stroke-width="2" stroke-dasharray="5 4"/>'
             f'<rect x="{x+134}" y="{y+10}" width="50" height="22" rx="11" fill="#E8F5E9"/><text x="{x+159}" y="{y+25}" text-anchor="middle" {T} font-size="12" font-weight="700" fill="#2E7D32">Cpk</text>')
    x, y = 52, 312  # gauge R&R bars
    for i, (h, c) in enumerate(((92, CYAN), (58, BLUE), (34, PURPLE))):
        s.append(f'<rect x="{x + 34 + i * 50}" y="{y + 140 - h}" width="34" height="{h}" rx="6" fill="{c}"/>')
    s.append(f'<line x1="{x+20}" y1="{y+141}" x2="{x+184}" y2="{y+141}" stroke="#CFD8DC" stroke-width="2"/>')
    x, y = 270, 312  # fishbone
    s.append(f'<line x1="{x+20}" y1="{y+92}" x2="{x+150}" y2="{y+92}" stroke="{NAVY}" stroke-width="3"/>'
             f'<rect x="{x+150}" y="{y+74}" width="34" height="36" rx="8" fill="url(#brand)"/>')
    for bx in (45, 95):
        s.append(f'<line x1="{x+bx}" y1="{y+48}" x2="{x+bx+28}" y2="{y+92}" stroke="{BLUE}" stroke-width="2.5"/>'
                 f'<line x1="{x+bx}" y1="{y+136}" x2="{x+bx+28}" y2="{y+92}" stroke="{PURPLE}" stroke-width="2.5"/>')
    s.append(f'<g filter="url(#sh)"><circle cx="452" cy="74" r="44" fill="#fff"/></g><image href="{ICON}" x="424" y="44" width="56" height="60" preserveAspectRatio="xMidYMid meet"/>')
    for cx, cy, c in ((60, 52, "#FFB74D"), (486, 300, "#4DD0E1"), (40, 520, "#B39DDB")):
        s.append(f'<path d="M{cx} {cy-10} L{cx+3} {cy-3} L{cx+10} {cy} L{cx+3} {cy+3} L{cx} {cy+10} L{cx-3} {cy+3} L{cx-10} {cy} L{cx-3} {cy-3} Z" fill="{c}"/>')
    return "".join(s)


def svg_pvalue():
    """Post 2: bell curve with a shaded tail and p = 0.03."""
    base, mean, sd, h = 372, 260, 60, 236
    pts = bell(mean, sd, base, h, 30, 490)
    xo = mean + 1.88 * sd
    tail = [p for p in pts if p[0] >= xo]
    tail = [(xo, base - h * math.exp(-0.5 * 1.88 ** 2))] + tail
    s = [DEFS, '<circle cx="470" cy="80" r="120" fill="#FFF3E0"/><circle cx="40" cy="500" r="110" fill="#EDE7F6"/>']
    s.append(f'<path d="{path(pts, base)}" fill="url(#brandSoft)"/><path d="{path(tail, base)}" fill="url(#warm)"/>')
    s.append(f'<path d="{path(pts)}" fill="none" stroke="{BLUE}" stroke-width="4" stroke-linecap="round"/>')
    s.append(f'<line x1="24" y1="{base}" x2="496" y2="{base}" stroke="#B0BEC5" stroke-width="3" stroke-linecap="round"/>')
    s.append(f'<line x1="{mean}" y1="{base}" x2="{mean}" y2="{base - h - 4}" stroke="#B0BEC5" stroke-width="2" stroke-dasharray="4 5"/>'
             f'<text x="{mean}" y="{base - h - 18}" text-anchor="middle" {T} font-size="15" font-weight="700" fill="{GREY}">If nothing changed</text>')
    s.append(f'<line x1="{xo:.1f}" y1="{base}" x2="{xo:.1f}" y2="200" stroke="{PURPLE}" stroke-width="3" stroke-dasharray="7 6"/>'
             f'<text x="{xo:.1f}" y="188" text-anchor="middle" {T} font-size="15" font-weight="700" fill="{PURPLE}">Your result</text>')
    s.append(f'<g filter="url(#sh)"><rect x="384" y="240" width="118" height="54" rx="14" fill="{NAVY}"/></g>'
             f'<text x="443" y="274" text-anchor="middle" font-family="Poppins" font-size="21" font-weight="700" fill="#fff">p = 0.03</text>'
             f'<line x1="443" y1="294" x2="436" y2="352" stroke="{NAVY}" stroke-width="2"/><circle cx="436" cy="356" r="4" fill="{NAVY}"/>')
    s.append(f'<text x="260" y="420" text-anchor="middle" {T} font-size="17" font-weight="600" fill="{NAVY}">p-value = how often you would see</text>'
             f'<text x="260" y="446" text-anchor="middle" {T} font-size="17" font-weight="600" fill="{NAVY}">a gap this big just by luck</text>')
    s.append(pill(150, 474, 220, "3 times in 100", "#FFF3E0", "#FFB74D", "#E65100", 15))
    return "".join(s)


def svg_control():
    """Post 3: control chart with one real signal and plain noise."""
    s = [DEFS, '<circle cx="470" cy="470" r="120" fill="#E8F5E9"/><circle cx="50" cy="60" r="100" fill="#E1F5FE"/>']
    s.append('<rect x="28" y="120" width="464" height="300" rx="18" fill="#F7F9FD" stroke="#E6ECF5"/>')
    ucl, cl, lcl = 165, 270, 375
    for yv, lab, col, dash in ((ucl, "UCL", "#EF5350", "8 6"), (cl, "CL", "#90A4AE", ""), (lcl, "LCL", "#EF5350", "8 6")):
        s.append(f'<line x1="44" y1="{yv}" x2="440" y2="{yv}" stroke="{col}" stroke-width="2.5" {"stroke-dasharray=%s" % repr(dash) if dash else ""}/>'
                 f'<text x="448" y="{yv + 5}" {T} font-size="14" font-weight="700" fill="{col if lab != "CL" else GREY}">{lab}</text>')
    ys = [268, 246, 293, 256, 305, 240, 284, 260, 312, 249, 275, 233, 300, 132, 264, 289, 243, 278]
    pts = [(60 + i * 21.6, v) for i, v in enumerate(ys)]
    s.append(f'<polyline points="{" ".join(f"{a:.1f},{b}" for a, b in pts)}" fill="none" stroke="{BLUE}" stroke-width="3" stroke-linejoin="round"/>')
    for i, (a, b) in enumerate(pts):
        if i == 13:
            s.append(f'<circle cx="{a:.1f}" cy="{b}" r="20" fill="{ORANGE}" opacity=".25"/><circle cx="{a:.1f}" cy="{b}" r="9" fill="{ORANGE}" stroke="#fff" stroke-width="3"/>')
        else:
            s.append(f'<circle cx="{a:.1f}" cy="{b}" r="6" fill="#fff" stroke="{BLUE}" stroke-width="3"/>')
    sx = pts[13][0]
    s.append(pill(sx - 112, 62, 224, "Signal: look into it", "#FFF3E0", "#FFB74D", "#E65100", 15))
    s.append(f'<line x1="{sx:.1f}" y1="98" x2="{sx:.1f}" y2="110" stroke="#FFB74D" stroke-width="2.5"/>')
    s.append(pill(44, 446, 236, "Noise: leave it alone", "#E8F5E9", "#A5D6A7", "#2E7D32", 15))
    return "".join(s)


def svg_capability():
    """Post 4: same spread, centred vs off-centre between spec limits."""
    s = [DEFS, '<circle cx="480" cy="60" r="110" fill="#FCE4EC"/><circle cx="40" cy="520" r="110" fill="#E0F7FA"/>']
    lsl, usl, target, sd, h = 110, 410, 260, 34, 128
    for top, mean, ok in ((36, 260, True), (290, 345, False)):
        base = top + 196
        s.append(f'<rect x="26" y="{top}" width="468" height="236" rx="18" fill="#F7F9FD" stroke="#E6ECF5"/>')
        pts = bell(mean, sd, base, h, mean - 3.6 * sd, mean + 3.6 * sd)
        s.append(f'<path d="{path(pts, base)}" fill="url(#brandSoft)"/>')
        if not ok:
            out = [p for p in pts if p[0] >= usl]
            out = [(usl, base - h * math.exp(-0.5 * ((usl - mean) / sd) ** 2))] + out
            s.append(f'<path d="{path(out, base)}" fill="url(#warm)"/>')
        s.append(f'<path d="{path(pts)}" fill="none" stroke="{BLUE}" stroke-width="3.5"/>'
                 f'<line x1="44" y1="{base}" x2="476" y2="{base}" stroke="#B0BEC5" stroke-width="2.5"/>')
        for xv, lab in ((lsl, "LSL"), (usl, "USL")):
            s.append(f'<line x1="{xv}" y1="{top + 58}" x2="{xv}" y2="{base}" stroke="#EF5350" stroke-width="2.5" stroke-dasharray="7 5"/>'
                     f'<text x="{xv}" y="{base + 24}" text-anchor="middle" {T} font-size="14" font-weight="700" fill="#EF5350">{lab}</text>')
        s.append(f'<line x1="{target}" y1="{top + 58}" x2="{target}" y2="{base}" stroke="#90A4AE" stroke-width="2" stroke-dasharray="3 5"/>'
                 f'<text x="{target}" y="{base + 24}" text-anchor="middle" {T} font-size="14" font-weight="600" fill="{GREY}">Target</text>')
        if ok:
            s.append(pill(40, top + 12, 300, "Centred:  Cp ✓   Cpk ✓", "#E8F5E9", "#A5D6A7", "#2E7D32", 15))
        else:
            s.append(pill(40, top + 12, 320, "Off-centre:  Cp ✓   Cpk ✗", "#FFF3E0", "#FFB74D", "#E65100", 15))
    return "".join(s)


POSTS = [
    dict(name="post1_meet_statsigma", bg="linear-gradient(135deg,#E0F7FA 0%,#EDE7F6 55%,#FCE4EC 100%)", blobs=("#B2EBF2", "#D1C4E9"),
         eyebrow="Meet StatSigma", headline='Six Sigma tools,<br><span class="hl">free in your browser</span>',
         sub="Nothing to install. Free while we are in beta.", cta="thestatsigma.com", svg=svg_tools()),
    dict(name="post2_p_value", bg="linear-gradient(135deg,#FFF3E0 0%,#F3E5F5 60%,#E3F2FD 100%)", blobs=("#FFE0B2", "#BBDEFB"),
         eyebrow="Statistics basics", headline='A small <span class="hl">p-value</span><br>doesn\'t prove<br>you\'re right',
         sub="Here's what it really tells you.", cta="", svg=svg_pvalue()),
    dict(name="post3_control_chart", bg="linear-gradient(135deg,#E8F5E9 0%,#E1F5FE 55%,#EDE7F6 100%)", blobs=("#C8E6C9", "#B3E5FC"),
         eyebrow="Control charts", headline='Stop reacting<br>to every<br><span class="hl">up and down</span>',
         sub="Signal or noise? Here's how to tell.", cta="", svg=svg_control()),
    dict(name="post4_cp_cpk", bg="linear-gradient(135deg,#FCE4EC 0%,#EDE7F6 50%,#E0F7FA 100%)", blobs=("#F8BBD0", "#B2EBF2"),
         eyebrow="Process capability", headline='<span class="hl">Cp</span> looks good.<br><span class="hl">Cpk</span> looks bad.<br>Why?',
         sub="Think of parking a car in a garage.", cta="", svg=svg_capability()),
]

CSS = f"""
html,body{{margin:0;padding:0;background:#fff}}
.canvas{{width:1200px;height:675px;position:relative;overflow:hidden;font-family:Inter,'Inter Display',sans-serif}}
.blob{{position:absolute;border-radius:50%;filter:blur(60px);opacity:.7}}
.card{{position:absolute;left:44px;top:60px;width:520px;height:555px;background:#fff;border-radius:30px;box-shadow:0 26px 60px rgba(11,44,111,.16);overflow:hidden}}
.right{{position:absolute;left:612px;top:0;width:548px;height:600px;display:flex;flex-direction:column;justify-content:center}}
.eyebrow{{font-weight:700;font-size:20px;letter-spacing:3px;text-transform:uppercase;color:#0277BD;margin-bottom:20px;display:flex;align-items:center;gap:14px}}
.eyebrow:before{{content:'';width:40px;height:6px;border-radius:3px;background:linear-gradient(90deg,{CYAN},{PURPLE})}}
.headline{{font-family:Poppins,sans-serif;font-weight:700;font-size:54px;line-height:1.14;color:{NAVY};letter-spacing:-.5px}}
.hl{{background:linear-gradient(90deg,#00A9CE,#3949AB 60%,#6A1B9A);-webkit-background-clip:text;background-clip:text;color:transparent}}
.sub{{font-weight:500;font-size:24px;line-height:1.35;color:#3E4C6E;margin-top:22px}}
.cta{{display:inline-block;margin-top:24px;padding:11px 22px;border-radius:999px;background:{NAVY};color:#fff;font-weight:700;font-size:20px;align-self:flex-start}}
.logo{{position:absolute;right:44px;bottom:34px;height:66px}}
"""


def html(p):
    b1, b2 = p["blobs"]
    cta = f'<div class="cta">{p["cta"]}</div>' if p["cta"] else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="canvas" style="background:{p['bg']}">
<div class="blob" style="width:420px;height:420px;left:-120px;top:-140px;background:{b1}"></div>
<div class="blob" style="width:460px;height:460px;right:-140px;bottom:-180px;background:{b2}"></div>
<div class="card"><svg width="520" height="555" viewBox="0 0 520 555" xmlns="http://www.w3.org/2000/svg">{p['svg']}</svg></div>
<div class="right"><div class="eyebrow">{p['eyebrow']}</div><div class="headline">{p['headline']}</div><div class="sub">{p['sub']}</div>{cta}</div>
<img class="logo" src="{LOGO}">
</div></body></html>"""


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=2)
    for p in POSTS:
        page.set_content(html(p))
        page.evaluate("document.fonts.ready")
        page.locator(".canvas").screenshot(path=str(OUT / f"{p['name']}.png"))
        print("rendered", p["name"])
    browser.close()
