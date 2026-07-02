"""Generate the README banner + relay-flow graphic in the Sound Cache brand style.

Pure PIL so it renders the exact bundled Unbounded/Quicksand fonts (loaded straight
from the TTF, no system install) with no emoji-font dependency. Both fonts are
variable, so we pin the weight axis to match the web wordmark (Unbounded 700).
Outputs:
  docs/images/banner.png       - squircle icon + "Sound Cache" wordmark + tagline
  docs/images/relay-flow.png   - phone -> relay -> vault, the sharing story
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "src" / "sound_vault" / "ui" / "fonts"
ICON = Path("/Users/LelandDutcher/Developer/soundcache-web/assets/icons/icon-squircle-1024.png")
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)

# brand palette
NIGHT = (10, 5, 24)
NIGHT2 = (26, 11, 58)
INK = (251, 237, 255)
MUTED = (197, 179, 230)
PINK = (255, 106, 213)
CYAN = (102, 236, 255)
LILAC = (183, 147, 255)
GOLD = (255, 216, 107)


def _font(name, size, weight):
    f = ImageFont.truetype(str(FONTS / name), size)
    try:
        f.set_variation_by_axes([weight])  # both bundled fonts are variable (wght axis)
    except Exception:  # noqa: BLE001 - static fallback
        pass
    return f


def unb(size, weight=700):
    return _font("Unbounded.ttf", size, weight)


def qs(size, weight=600):
    return _font("Quicksand.ttf", size, weight)


def gradient_bg(w, h):
    """Vertical night gradient with a scatter of sparkle dots (the brand motif)."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        belly = 1 - abs(t - 0.5) * 2  # soft belly of colour in the middle
        r = int(NIGHT[0] + (NIGHT2[0] - NIGHT[0]) * belly)
        g = int(NIGHT[1] + (NIGHT2[1] - NIGHT[1]) * belly)
        b = int(NIGHT[2] + (NIGHT2[2] - NIGHT[2]) * belly)
        for x in range(w):
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(img, "RGBA")
    seeds = [(0.07, 0.20), (0.16, 0.66), (0.28, 0.34), (0.39, 0.80), (0.52, 0.16),
             (0.61, 0.58), (0.72, 0.30), (0.80, 0.74), (0.88, 0.44), (0.94, 0.22),
             (0.34, 0.12), (0.46, 0.90), (0.66, 0.86), (0.22, 0.50), (0.90, 0.64)]
    cols = [CYAN, PINK, LILAC, GOLD, INK]
    for i, (fx, fy) in enumerate(seeds):
        x, y = int(fx * w), int(fy * h)
        c = cols[i % len(cols)]
        rad = 2 if i % 3 else 3
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(*c, 190))
        d.line([x - rad * 3, y, x + rad * 3, y], fill=(*c, 70), width=1)
        d.line([x, y - rad * 3, x, y + rad * 3], fill=(*c, 70), width=1)
    return img


def soft_shadow(size, box, radius, color, blur=18, alpha=150):
    """Return an RGBA layer with a blurred rounded-rect glow, for depth under cards/nodes."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, fill=(*color, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def make_banner():
    W, H = 1240, 340
    img = gradient_bg(W, H).convert("RGBA")
    d = ImageDraw.Draw(img)

    icon_sz = 208
    iy = (H - icon_sz) // 2
    ix = 92
    # soft glow behind the icon
    img.alpha_composite(soft_shadow((W, H), [ix + 8, iy + 14, ix + icon_sz - 8, iy + icon_sz + 6],
                                    54, LILAC, blur=26, alpha=90))
    icon = Image.open(ICON).convert("RGBA").resize((icon_sz, icon_sz), Image.LANCZOS)
    img.alpha_composite(icon, (ix, iy))

    tx = ix + icon_sz + 52
    # wordmark + tagline, vertically centered as a block next to the icon
    word_font = unb(94, weight=700)  # match the web wordmark weight (was rendering thin at 400)
    wbb = d.textbbox((tx, 0), "Sound Cache", font=word_font)
    wh = wbb[3] - wbb[1]
    tag_font = qs(38, weight=600)
    tbb = d.textbbox((tx, 0), "hoard your favorite sounds.", font=tag_font)
    th = tbb[3] - tbb[1]
    gap = 20
    block_h = wh + gap + th
    top = (H - block_h) // 2 - wbb[1]
    d.text((tx, top), "Sound Cache", font=word_font, fill=INK)
    d.text((tx + 3, top + wh + gap - (tbb[1] - 0)), "hoard your favorite sounds.", font=tag_font, fill=MUTED)

    img.convert("RGB").save(OUT / "banner.png")
    print("wrote", OUT / "banner.png")


def _phone(d, cx, cy):
    w, h = 124, 164
    x, y = cx - w // 2, cy - h // 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=24, fill=(20, 12, 44), outline=(*CYAN, 200), width=3)
    d.rounded_rectangle([x + w // 2 - 20, y + 12, x + w // 2 + 20, y + 21], radius=5, fill=(6, 3, 16))
    nx, ny = x + w // 2, y + h // 2 - 4
    d.ellipse([nx - 24, ny + 12, nx - 6, ny + 30], fill=PINK)
    d.rectangle([nx + 3, ny - 30, nx + 5, ny + 22], fill=PINK)
    d.polygon([(nx + 4, ny - 30), (nx + 26, ny - 39), (nx + 26, ny - 23), (nx + 4, ny - 14)], fill=PINK)
    bx, by = x + w - 26, y + 26  # share badge
    d.ellipse([bx - 15, by - 15, bx + 15, by + 15], fill=CYAN)
    d.line([bx, by + 7, bx, by - 8], fill=(6, 3, 16), width=3)
    d.polygon([(bx, by - 12), (bx - 6, by - 4), (bx + 6, by - 4)], fill=(6, 3, 16))


def _relay(d, cx, cy):
    for i, rad in enumerate((52, 38, 24)):
        col = [LILAC, CYAN, PINK][i]
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(*col, 210), width=3)
    d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=GOLD)
    for fx, fy in ((0, -52), (45, 26), (-45, 26)):
        d.ellipse([cx + fx - 5, cy + fy - 5, cx + fx + 5, cy + fy + 5], fill=CYAN)


def _desktop(d, cx, cy, icon):
    w, h = 232, 148
    x, y = cx - w // 2, cy - h // 2 - 8
    d.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=(20, 12, 44), outline=(*LILAC, 200), width=3)
    d.rounded_rectangle([x + 12, y + 12, x + w - 12, y + 32], radius=6, fill=(12, 8, 30))
    for i in range(3):
        ry = y + 46 + i * 25
        d.rounded_rectangle([x + 14, ry, x + w - 58, ry + 15], radius=5, fill=(46, 30, 92))
        d.rounded_rectangle([x + w - 50, ry, x + w - 14, ry + 15], radius=5, fill=(*CYAN, 90))
    d.rectangle([cx - 7, y + h, cx + 7, y + h + 16], fill=(*LILAC, 160))
    d.rounded_rectangle([cx - 38, y + h + 16, cx + 38, y + h + 24], radius=4, fill=(*LILAC, 160))
    ic = icon.resize((32, 32), Image.LANCZOS)
    return ic, (x + w - 50, y + 44)


def _arrow(base, x1, x2, y):
    """A soft gradient signal-arrow (pink -> cyan) drawn on its own layer for a subtle glow."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    span = max(1, x2 - x1)
    for x in range(x1, x2 - 12):
        t = (x - x1) / span
        col = tuple(int(PINK[i] + (CYAN[i] - PINK[i]) * t) for i in range(3))
        d.line([x, y, x + 1, y], fill=(*col, 235), width=5)
    d.polygon([(x2, y), (x2 - 15, y - 10), (x2 - 15, y + 10)], fill=(*CYAN, 235))
    glow = layer.filter(ImageFilter.GaussianBlur(4))
    base.alpha_composite(glow)
    base.alpha_composite(layer)


def make_relay_flow():
    W, H = 1360, 470
    img = gradient_bg(W, H).convert("RGBA")
    d = ImageDraw.Draw(img)
    icon = Image.open(ICON).convert("RGBA")

    title = unb(37, weight=700)
    ttl = "Save a sound anywhere. It lands in your vault."
    tb = d.textbbox((0, 0), ttl, font=title)
    d.text(((W - (tb[2] - tb[0])) // 2, 38), ttl, font=title, fill=INK)

    centers = (232, 680, 1128)
    card_w, card_h, card_top = 300, 302, 110
    node_cy = card_top + 100
    accents = (CYAN, LILAC, PINK)

    # soft coloured glow behind each card
    for cx, accent in zip(centers, accents):
        box = [cx - card_w // 2, card_top, cx + card_w // 2, card_top + card_h]
        img.alpha_composite(soft_shadow((W, H), box, 26, accent, blur=34, alpha=42))
    # translucent glass cards drawn on their own layer so the fill actually blends
    # (drawing an alpha fill straight onto the RGBA base then convert('RGB') = solid white)
    card_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(card_layer)
    for cx, accent in zip(centers, accents):
        box = [cx - card_w // 2, card_top, cx + card_w // 2, card_top + card_h]
        cd.rounded_rectangle(box, radius=26, fill=(255, 255, 255, 13), outline=(*accent, 120), width=2)
    img.alpha_composite(card_layer)
    d = ImageDraw.Draw(img)

    _phone(d, centers[0], node_cy)
    _relay(d, centers[1], node_cy)
    ic, pos = _desktop(d, centers[2], node_cy, icon)

    # gradient signal-arrows in the gaps BETWEEN cards (outside the card edges)
    for a, b in ((0, 1), (1, 2)):
        _arrow(img, centers[a] + card_w // 2 + 16, centers[b] - card_w // 2 - 2, node_cy)
    img.alpha_composite(ic, pos)
    d = ImageDraw.Draw(img)

    def centered(cx, y, text, font, fill):
        bb = d.textbbox((0, 0), text, font=font)
        d.text((cx - (bb[2] - bb[0]) // 2, y), text, font=font, fill=fill)

    label = unb(19, weight=700)
    sub = qs(16, weight=500)
    ly = card_top + card_h - 92
    steps = [
        ("1 · Tap Share", CYAN, ["on any TikTok,", "in any app"]),
        ("2 · Through the relay", LILAC, ["just a link + your code.", "no account, no tracking."]),
        ("3 · Into your vault", PINK, ["your Mac grabs the audio,", "into a folder that's yours."]),
    ]
    for cx, (head, col, lines) in zip(centers, steps):
        centered(cx, ly, head, label, col)
        for j, line in enumerate(lines):
            centered(cx, ly + 32 + j * 22, line, sub, MUTED)

    img.convert("RGB").save(OUT / "relay-flow.png")
    print("wrote", OUT / "relay-flow.png")


make_banner()
make_relay_flow()
