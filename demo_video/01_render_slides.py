"""Render 9 PNG slides at 1920x1080. Tech-conference dark style with brand
gradient — matching Pythia's actual UI palette.
"""
import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from script import SLIDES  # noqa: E402

# ─── Canvas + colors ───────────────────────────────────────────────
W, H = 1920, 1080
BG = (7, 7, 11)               # near-black, matches body bg
PANEL = (14, 14, 20)
PANEL_2 = (22, 22, 32)
BORDER = (42, 42, 58)
TEXT_1 = (244, 244, 248)
TEXT_2 = (184, 184, 200)
TEXT_3 = (122, 122, 144)
TEXT_4 = (74, 74, 96)
PURPLE = (124, 92, 255)
BLUE = (91, 141, 239)
ACCENT = (25, 232, 184)
WARN = (255, 181, 71)
NEG = (255, 93, 122)

# ─── Fonts ─────────────────────────────────────────────────────────
def font(size, bold=False, mono=False):
    if mono:
        path = "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf"
    else:
        path = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
    return ImageFont.truetype(path, size)

# ─── Helpers ────────────────────────────────────────────────────────
def rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def text_w(draw, text, fnt):
    bbox = draw.textbbox((0, 0), text, font=fnt)
    return bbox[2] - bbox[0]


def grad_underline(draw, x1, y, x2, thickness=4):
    """Brand gradient underline (purple → blue → accent)."""
    x1, x2, y = int(x1), int(x2), int(y)
    length = max(1, x2 - x1)
    for i in range(length):
        t = i / length
        if t < 0.5:
            k = t * 2
            r = int(PURPLE[0] * (1 - k) + BLUE[0] * k)
            g = int(PURPLE[1] * (1 - k) + BLUE[1] * k)
            b = int(PURPLE[2] * (1 - k) + BLUE[2] * k)
        else:
            k = (t - 0.5) * 2
            r = int(BLUE[0] * (1 - k) + ACCENT[0] * k)
            g = int(BLUE[1] * (1 - k) + ACCENT[1] * k)
            b = int(BLUE[2] * (1 - k) + ACCENT[2] * k)
        draw.line([(x1 + i, y), (x1 + i, y + thickness)], fill=(r, g, b))


def make_glow_layer(intensity=1.0):
    """Soft purple-blue radial glow as a separate RGBA layer."""
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    # Purple top-left
    for r in range(700, 0, -40):
        a = int(20 * intensity * (1 - r / 700))
        gd.ellipse((300 - r, -200 - r, 300 + r, -200 + r),
                   fill=(PURPLE[0], PURPLE[1], PURPLE[2], a))
    # Accent bottom-right
    for r in range(600, 0, -30):
        a = int(15 * intensity * (1 - r / 600))
        gd.ellipse((W - 200 - r, H + 100 - r, W - 200 + r, H + 100 + r),
                   fill=(ACCENT[0], ACCENT[1], ACCENT[2], a))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    return glow


def draw_grid(draw, alpha=12):
    """Subtle grid background like the Pythia hero section."""
    for x in range(0, W, 64):
        draw.line([(x, 0), (x, H)], fill=(255, 255, 255, alpha), width=1)
    for y in range(0, H, 64):
        draw.line([(0, y), (W, y)], fill=(255, 255, 255, alpha), width=1)


def base_canvas():
    img = Image.new("RGB", (W, H), BG)
    # Add subtle grid layer
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 80):
        gd.line([(x, 0), (x, H)], fill=(255, 255, 255, 8), width=1)
    for y in range(0, H, 80):
        gd.line([(0, y), (W, y)], fill=(255, 255, 255, 8), width=1)
    img.paste(grid, (0, 0), grid)
    img = img.convert("RGBA")
    img.alpha_composite(make_glow_layer())
    return img.convert("RGB")


def draw_progress_bar(draw, current, total):
    """Bottom progress bar in brand gradient."""
    y = H - 14
    # Track
    draw.line([(0, y), (W, y)], fill=BORDER, width=2)
    # Fill
    fill_w = int(W * current / total)
    for i in range(fill_w):
        t = i / max(W, 1)
        if t < 0.5:
            k = t * 2
            r = int(PURPLE[0] * (1 - k) + BLUE[0] * k)
            g = int(PURPLE[1] * (1 - k) + BLUE[1] * k)
            b = int(PURPLE[2] * (1 - k) + BLUE[2] * k)
        else:
            k = (t - 0.5) * 2
            r = int(BLUE[0] * (1 - k) + ACCENT[0] * k)
            g = int(BLUE[1] * (1 - k) + ACCENT[1] * k)
            b = int(BLUE[2] * (1 - k) + ACCENT[2] * k)
        draw.line([(i, y), (i, y + 4)], fill=(r, g, b))


def draw_chrome(img, slide_idx, total, slide_title):
    """Add page number, project tag, progress bar."""
    draw = ImageDraw.Draw(img)
    # Top-left brand mark
    rounded_rect(draw, (60, 50, 120, 110), 12,
                 fill=(PURPLE[0], PURPLE[1], PURPLE[2]))
    f = font(36, bold=True)
    tw = text_w(draw, "P", f)
    draw.text((60 + (60 - tw) / 2, 50 + 8), "P", fill=TEXT_1, font=f)
    draw.text((140, 64), "Pythia", fill=TEXT_1, font=font(28, bold=True))
    draw.text((140, 96), "Prediction DEX on Arc", fill=TEXT_3, font=font(16))

    # Top-right page number
    page = f"{slide_idx} / {total}"
    f2 = font(20, mono=True)
    pw = text_w(draw, page, f2)
    draw.text((W - 60 - pw, 70), page, fill=TEXT_3, font=f2)

    # Bottom-left slide title in gray
    f3 = font(18)
    draw.text((60, H - 60), slide_title.upper(), fill=TEXT_3, font=f3)

    # Bottom-right hackathon tag
    tag = "AGORA HACKATHON · 2026"
    f4 = font(16, mono=True)
    tw = text_w(draw, tag, f4)
    draw.text((W - 60 - tw, H - 60), tag, fill=TEXT_4, font=f4)

    # Progress bar
    draw_progress_bar(draw, slide_idx, total)


def title_block(draw, title, eyebrow, x=120, y=180):
    """Big title with gradient underline + eyebrow above."""
    if eyebrow:
        eb_color = (181, 166, 255)  # light purple
        draw.text((x, y), eyebrow.upper(), fill=eb_color, font=font(22, bold=True, mono=True))
        y += 38
    f = font(72, bold=True)
    draw.text((x, y), title, fill=TEXT_1, font=f)
    tw = text_w(draw, title, f)
    grad_underline(draw, x, y + 92, x + min(tw, 480), thickness=6)
    return y + 130  # next y


def stat_tile(draw, x, y, w, h, value, label):
    """Glassy data tile."""
    rounded_rect(draw, (x, y, x + w, y + h), 14, fill=PANEL, outline=BORDER, width=1)
    # value
    f1 = font(64, bold=True)
    draw.text((x + 24, y + 18), value, fill=ACCENT, font=f1)
    # label
    f2 = font(18, bold=True, mono=True)
    draw.text((x + 24, y + 18 + 78), label.upper(), fill=TEXT_3, font=f2)


def bullet_row(draw, x, y, head, sub, w):
    """Yellow dot + headline + subtext below."""
    # Dot
    dot_r = 8
    draw.ellipse((x, y + 14, x + dot_r * 2, y + 14 + dot_r * 2),
                 fill=ACCENT, outline=None)
    # Headline
    f1 = font(32, bold=True)
    draw.text((x + 30, y), head, fill=TEXT_1, font=f1)
    # Sub
    if sub:
        f2 = font(22)
        draw.text((x + 30, y + 46), sub, fill=TEXT_2, font=f2)
    return y + 90


def col_card(draw, x, y, w, h, label, name, lines):
    """Three-column architecture card."""
    rounded_rect(draw, (x, y, x + w, y + h), 18, fill=PANEL, outline=BORDER, width=1)
    # Tiny label badge
    f0 = font(16, bold=True, mono=True)
    badge_w = text_w(draw, label, f0) + 24
    rounded_rect(draw, (x + 24, y + 24, x + 24 + badge_w, y + 24 + 32), 8,
                 fill=PANEL_2, outline=PURPLE, width=1)
    draw.text((x + 24 + 12, y + 30), label, fill=PURPLE, font=f0)
    # Name
    f1 = font(30, bold=True, mono=True)
    draw.text((x + 24, y + 80), name, fill=TEXT_1, font=f1)
    # Lines
    f2 = font(22)
    yy = y + 145
    for line in lines:
        # accent dot
        draw.ellipse((x + 24, yy + 11, x + 24 + 8, yy + 19), fill=ACCENT)
        draw.text((x + 44, yy + 0), line, fill=TEXT_2, font=f2)
        yy += 50


def footer_box(draw, text):
    """Subtle footer card across bottom."""
    box_y1, box_y2 = H - 130, H - 80
    rounded_rect(draw, (60, box_y1, W - 60, box_y2), 10,
                 fill=PANEL, outline=BORDER, width=1)
    f = font(20, mono=True)
    tw = text_w(draw, text, f)
    draw.text(((W - tw) / 2, box_y1 + 14), text, fill=TEXT_2, font=f)


# ─── Slide renderers ───────────────────────────────────────────────
def render_cover(slide, draw, img):
    # giant centered title
    f1 = font(160, bold=True)
    title = slide["title"]
    tw = text_w(draw, title, f1)
    cx = (W - tw) / 2
    draw.text((cx, 280), title, fill=TEXT_1, font=f1)
    # gradient underline
    grad_underline(draw, cx, 280 + 200, cx + tw, thickness=8)
    # eyebrow under title
    f2 = font(36)
    eb = slide["eyebrow"]
    ew = text_w(draw, eb, f2)
    draw.text(((W - ew) / 2, 510), eb, fill=TEXT_2, font=f2)
    # 4 stat tiles
    stats = slide["stats"]
    n = len(stats)
    tile_w = 320
    tile_h = 140
    gap = 30
    total_w = n * tile_w + (n - 1) * gap
    start_x = (W - total_w) // 2
    for i, (val, lbl) in enumerate(stats):
        stat_tile(draw, start_x + i * (tile_w + gap), 640, tile_w, tile_h, val, lbl)
    # footer
    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_bullets(slide, draw, img):
    y = title_block(draw, slide["title"], slide["eyebrow"])
    y += 30
    for head, sub in slide["bullets"]:
        y = bullet_row(draw, 140, y, head, sub, W - 280)
        y += 8
    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_three_cols(slide, draw, img):
    title_block(draw, slide["title"], slide["eyebrow"])
    cols = slide["cols"]
    n = len(cols)
    gap = 30
    margin = 120
    avail = W - 2 * margin - (n - 1) * gap
    cw = avail // n
    ch = 500
    cy = 380
    for i, c in enumerate(cols):
        col_card(draw, margin + i * (cw + gap), cy, cw, ch,
                 c["label"], c["name"], c["lines"])
    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_screen(slide, draw, img):
    """Bullets on the left, big screenshot on the right."""
    title_block(draw, slide["title"], slide["eyebrow"])
    # Bullets — left half
    y = 360
    for head, sub in slide["bullets"]:
        y = bullet_row(draw, 120, y, head, sub, 720)
        y += 4
    # Screenshot placeholder — right half (will be replaced by capture step)
    sx, sy, sw, sh = 920, 340, 880, 540
    rounded_rect(draw, (sx, sy, sx + sw, sy + sh), 18,
                 fill=PANEL, outline=BORDER, width=2)
    # Watermark text inside placeholder; capture script overlays the real PNG
    f = font(22, mono=True)
    draw.text((sx + 24, sy + 24),
              f"[ {slide['screen']}.png will overlay here ]",
              fill=TEXT_4, font=f)
    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_screen_evidence(slide, draw, img):
    """Screenshot left, big stat tiles right."""
    title_block(draw, slide["title"], slide["eyebrow"])
    # Screenshot placeholder — left
    sx, sy, sw, sh = 100, 340, 880, 540
    rounded_rect(draw, (sx, sy, sx + sw, sy + sh), 18,
                 fill=PANEL, outline=ACCENT, width=2)
    f = font(22, mono=True)
    draw.text((sx + 24, sy + 24),
              f"[ {slide['screen']}.png will overlay here ]",
              fill=TEXT_4, font=f)
    # Stat tiles — right (2x2)
    sx, sy = 1020, 340
    tw, th = 380, 130
    gap = 20
    stats = slide["stats"]
    for i, (val, lbl) in enumerate(stats):
        x = sx + (i % 2) * (tw + gap)
        y = sy + (i // 2) * (th + gap)
        stat_tile(draw, x, y, tw, th, val, lbl)
    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_outro(slide, draw, img):
    title_block(draw, slide["title"], slide["eyebrow"], x=120, y=200)
    # Big "Thanks" line
    f = font(48)
    draw.text((120, 380), "Source · Demo · Settlement — all linked below.",
              fill=TEXT_2, font=f)
    # Link list
    y = 480
    f1 = font(28, bold=True, mono=True)
    f2 = font(22)
    for url, lbl in slide["links"]:
        # Bullet dot
        draw.ellipse((130, y + 14, 142, y + 26), fill=PURPLE)
        draw.text((160, y - 2), url, fill=TEXT_1, font=f1)
        tw = text_w(draw, url, f1)
        draw.text((160 + tw + 24, y + 8), f"— {lbl}", fill=TEXT_3, font=f2)
        y += 60
    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_demo(slide, draw, img):
    """Demo slide — small title + side text on right, big screen-recording slot on left."""
    # Compact title (smaller, upper area)
    f_eb = font(20, bold=True, mono=True)
    draw.text((120, 170), slide["eyebrow"].upper(), fill=(181, 166, 255), font=f_eb)
    f_t = font(56, bold=True)
    draw.text((120, 200), slide["title"], fill=TEXT_1, font=f_t)
    tw = text_w(draw, slide["title"], f_t)
    grad_underline(draw, 120, 270, 120 + min(tw, 400), thickness=5)

    # Left: big screen recording placeholder (1280x800 → fits 1100x690 area)
    sx, sy = 110, 320
    sw, sh = 1100, 690
    rounded_rect(draw, (sx, sy, sx + sw, sy + sh), 16,
                 fill=(0, 0, 0), outline=BORDER, width=2)
    # Window-chrome dots (mac-style) for visual depth
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = sx + 24 + i * 24
        draw.ellipse((cx, sy + 14, cx + 14, sy + 28), fill=c)
    # Watermark — composer overlays the captured frames
    f = font(20, mono=True)
    draw.text((sx + 24, sy + 60),
              f"[ recording: {slide['screen_clip']}.mp4 will overlay here ]",
              fill=TEXT_4, font=f)

    # Right: bullet text column
    rx = sx + sw + 50
    rw = W - rx - 80
    y = 360
    f_b = font(22)
    for line in slide["right_text"]:
        # Accent dot
        draw.ellipse((rx, y + 10, rx + 10, y + 20), fill=ACCENT)
        # Wrap text within rw
        wrap_text(draw, line, rx + 24, y, rw - 30, f_b, fill=TEXT_2,
                  line_height=32)
        y += 90

    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def wrap_text(draw, text, x, y, max_w, fnt, fill, line_height=30):
    """Simple word-wrapper to fit max_w."""
    words = text.split()
    line = ""
    cur_y = y
    for w in words:
        candidate = (line + " " + w).strip()
        bw = text_w(draw, candidate, fnt)
        if bw > max_w and line:
            draw.text((x, cur_y), line, fill=fill, font=fnt)
            line = w
            cur_y += line_height
        else:
            line = candidate
    if line:
        draw.text((x, cur_y), line, fill=fill, font=fnt)


def render_honesty(slide, draw, img):
    """Two-column honest framing: WHAT'S REAL (green) | WHAT'S SIMULATED (amber)."""
    title_block(draw, slide["title"], slide["eyebrow"])

    col_y = 380
    col_h = 540
    margin = 120
    gap = 40
    cw = (W - 2 * margin - gap) // 2

    # Real column — green tint
    rx = margin
    rounded_rect(draw, (rx, col_y, rx + cw, col_y + col_h), 18,
                 fill=PANEL, outline=ACCENT, width=2)
    f0 = font(20, bold=True, mono=True)
    draw.text((rx + 24, col_y + 24), "WHAT'S REAL", fill=ACCENT, font=f0)
    f1 = font(24)
    yy = col_y + 90
    for line in slide["real"]:
        draw.ellipse((rx + 24, yy + 11, rx + 34, yy + 21), fill=ACCENT)
        wrap_text(draw, line, rx + 50, yy, cw - 80, f1, fill=TEXT_1,
                  line_height=32)
        yy += 90

    # Simulated column — amber tint
    sx = margin + cw + gap
    rounded_rect(draw, (sx, col_y, sx + cw, col_y + col_h), 18,
                 fill=PANEL, outline=WARN, width=2)
    draw.text((sx + 24, col_y + 24), "WHAT'S SIMULATED (YET)", fill=WARN, font=f0)
    yy = col_y + 90
    for line in slide["simulated"]:
        draw.ellipse((sx + 24, yy + 11, sx + 34, yy + 21), fill=WARN)
        wrap_text(draw, line, sx + 50, yy, cw - 80, f1, fill=TEXT_1,
                  line_height=32)
        yy += 90

    if slide.get("footer"):
        footer_box(draw, slide["footer"])


def render_roadmap(slide, draw, img):
    """5-phase horizontal timeline."""
    title_block(draw, slide["title"], slide["eyebrow"])

    # Timeline
    phases = slide["phases"]
    n = len(phases)
    margin_x = 100
    avail_w = W - 2 * margin_x
    step_w = avail_w // n
    timeline_y = 470

    # Connecting line
    draw.line([(margin_x + step_w // 2, timeline_y),
               (margin_x + step_w * (n - 1) + step_w // 2, timeline_y)],
              fill=BORDER, width=2)

    f_label = font(16, bold=True, mono=True)
    f_name = font(24, bold=True)
    f_desc = font(16)

    for i, (label, name, desc) in enumerate(phases):
        cx = margin_x + step_w * i + step_w // 2

        # Numbered dot
        r = 30
        draw.ellipse((cx - r, timeline_y - r, cx + r, timeline_y + r),
                     fill=PANEL, outline=PURPLE, width=2)
        f_num = font(22, bold=True)
        num_txt = str(i + 1)
        ntw = text_w(draw, num_txt, f_num)
        draw.text((cx - ntw / 2, timeline_y - 14), num_txt, fill=PURPLE, font=f_num)

        # Label above
        lw = text_w(draw, label, f_label)
        draw.text((cx - lw / 2, timeline_y - 80), label, fill=PURPLE, font=f_label)

        # Name below
        nw = text_w(draw, name, f_name)
        draw.text((cx - nw / 2, timeline_y + 50), name, fill=TEXT_1, font=f_name)

        # Desc below name (wrap if needed) — use short single line, center
        dw = text_w(draw, desc, f_desc)
        # If too wide, just show — descriptions are short
        if dw > step_w - 30:
            # split on " · "
            parts = desc.split(" · ")
            yy = timeline_y + 90
            for p in parts:
                pw = text_w(draw, p, f_desc)
                draw.text((cx - pw / 2, yy), p, fill=TEXT_3, font=f_desc)
                yy += 24
        else:
            draw.text((cx - dw / 2, timeline_y + 90), desc, fill=TEXT_3, font=f_desc)

    if slide.get("footer"):
        footer_box(draw, slide["footer"])


KIND_RENDERERS = {
    "cover": render_cover,
    "bullets": render_bullets,
    "three_cols": render_three_cols,
    "screen": render_screen,
    "screen_evidence": render_screen_evidence,
    "outro": render_outro,
    "demo": render_demo,
    "honesty": render_honesty,
    "roadmap": render_roadmap,
}


def render_one(slide, total):
    img = base_canvas().convert("RGB")
    draw = ImageDraw.Draw(img)
    KIND_RENDERERS[slide["kind"]](slide, draw, img)
    draw_chrome(img, slide["id"], total, slide["title"])
    return img


def main():
    out_dir = ROOT / "slides"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(SLIDES)
    for s in SLIDES:
        img = render_one(s, total)
        out_path = out_dir / f"slide_{s['id']:02d}.png"
        img.save(out_path, "PNG", optimize=True)
        print(f"  OK {out_path.name}")
    print(f"\n  Rendered {total} slides to {out_dir}")


if __name__ == "__main__":
    main()
