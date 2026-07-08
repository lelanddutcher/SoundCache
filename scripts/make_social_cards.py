"""Generate 16:9 social-media announcement cards for Sound Cache features.

Pure PIL so it renders the exact bundled Unbounded/Quicksand fonts (variable —
weight pinned per call) with no emoji-font dependency; all motifs are drawn as
vector shapes. Outputs 1920x1080 PNGs into the OUT dir.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "src" / "sound_vault" / "ui" / "fonts"
ICON = Path("/Users/LelandDutcher/Developer/soundcache-web/assets/icons/icon-squircle-1024.png")
OUT = Path("/private/tmp/claude-501/-Users-LelandDutcher-Developer-TiktokSoundVault/"
           "a3bbf34a-6a0f-4a5b-b0ef-af7b7339bf0a/scratchpad/social")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080

NIGHT = (10, 5, 24)
NIGHT2 = (28, 12, 62)
INK = (251, 237, 255)
MUTED = (199, 181, 232)
DIM = (150, 132, 190)
PINK = (255, 106, 213)
CYAN = (102, 236, 255)
LILAC = (183, 147, 255)
GOLD = (255, 216, 107)
MINT = (135, 232, 176)


def _font(name, size, weight):
    f = ImageFont.truetype(str(FONTS / name), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:  # noqa: BLE001
        pass
    return f


def unb(size, weight=700):
    return _font("Unbounded.ttf", size, weight)


def qs(size, weight=600):
    return _font("Quicksand.ttf", size, weight)


_ICON = Image.open(ICON).convert("RGBA") if ICON.exists() else None
_SHOT_PATH = ROOT / "docs" / "images" / "app-screenshot.png"
_SHOT = Image.open(_SHOT_PATH).convert("RGBA") if _SHOT_PATH.exists() else None


def gradient_bg(accent):
    """Night gradient with a faint accent bloom in the lower-right + sparkles."""
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        belly = 1 - abs(t - 0.42) * 1.7
        belly = max(0.0, belly)
        r = int(NIGHT[0] + (NIGHT2[0] - NIGHT[0]) * belly)
        g = int(NIGHT[1] + (NIGHT2[1] - NIGHT[1]) * belly)
        b = int(NIGHT[2] + (NIGHT2[2] - NIGHT[2]) * belly)
        for x in range(W):
            px[x, y] = (r, g, b)
    img = img.convert("RGBA")
    # accent bloom
    bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bloom)
    bd.ellipse([W - 760, H - 620, W + 260, H + 260], fill=(*accent, 46))
    bd.ellipse([-260, -260, 420, 420], fill=(*LILAC, 24))
    img.alpha_composite(bloom.filter(ImageFilter.GaussianBlur(120)))
    # sparkles
    d = ImageDraw.Draw(img, "RGBA")
    seeds = [(0.06, 0.16), (0.14, 0.62), (0.24, 0.30), (0.33, 0.82), (0.46, 0.12),
             (0.55, 0.70), (0.68, 0.22), (0.78, 0.60), (0.86, 0.36), (0.93, 0.78),
             (0.40, 0.50), (0.62, 0.44), (0.20, 0.88), (0.72, 0.86), (0.90, 0.14)]
    cols = [CYAN, PINK, LILAC, GOLD, INK]
    for i, (fx, fy) in enumerate(seeds):
        star(d, int(fx * W), int(fy * H), 4 if i % 3 else 6, cols[i % len(cols)], 150)
    return img


def star(d, x, y, r, color, alpha=255):
    """A soft 4-point sparkle."""
    d.line([x - r, y, x + r, y], fill=(*color, alpha), width=2)
    d.line([x, y - r, x, y + r], fill=(*color, alpha), width=2)
    d.line([x - r // 2, y - r // 2, x + r // 2, y + r // 2], fill=(*color, alpha // 2), width=1)
    d.line([x - r // 2, y + r // 2, x + r // 2, y - r // 2], fill=(*color, alpha // 2), width=1)
    rr = max(1, r // 3)
    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*color, alpha))


def soft_panel(img, box, radius, accent, fill_alpha=16, glow=60):
    """A translucent glass panel with an accent glow behind it."""
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow_layer).rounded_rectangle(box, radius=radius, fill=(*accent, 40))
    img.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(glow)))
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(box, radius=radius, fill=(255, 255, 255, fill_alpha),
                                            outline=(*accent, 120), width=2)
    img.alpha_composite(panel)


def brand_lockup(img, d):
    if _ICON is not None:
        ic = _ICON.resize((74, 74), Image.LANCZOS)
        img.alpha_composite(ic, (96, 72))
    d.text((186, 88), "Sound Cache", font=unb(40, 700), fill=INK)


def footer(d, accent):
    d.text((96, H - 96), "soundcache.io", font=qs(34, 700), fill=accent)
    txt = "free · local-first · Apple Silicon"
    bb = d.textbbox((0, 0), txt, font=qs(30, 500))
    d.text((W - 96 - (bb[2] - bb[0]), H - 92), txt, font=qs(30, 500), fill=DIM)


def kicker(d, x, y, text, accent):
    star(d, x + 10, y + 22, 9, accent)
    d.text((x + 34, y), text, font=qs(34, 700), fill=accent)


def wrap(d, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textbbox((0, 0), t, font=font)[2] <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def headline(d, x, y, text, accent, size=104, max_w=1000):
    f = unb(size, 800)
    lines = wrap(d, text, f, max_w)
    for i, ln in enumerate(lines):
        d.text((x, y + i * int(size * 1.12)), ln, font=f, fill=accent)
    return y + len(lines) * int(size * 1.12)


def subline(d, x, y, text, max_w=980, size=40):
    f = qs(size, 500)
    lines = wrap(d, text, f, max_w)
    for i, ln in enumerate(lines):
        d.text((x, y + i * int(size * 1.4)), ln, font=f, fill=MUTED)
    return y + len(lines) * int(size * 1.4)


# ---------- motifs ----------
def m_phone(d, cx, cy, s=1.0):
    w, h = int(150 * s), int(300 * s)
    x, y = cx - w // 2, cy - h // 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=int(34 * s), fill=(20, 12, 46), outline=(*CYAN, 210), width=4)
    d.rounded_rectangle([cx - 26, y + 16, cx + 26, y + 27], radius=6, fill=(6, 3, 16))
    ny = cy - 6
    d.ellipse([cx - 34, ny + 14, cx - 8, ny + 40], fill=PINK)
    d.rectangle([cx + 3, ny - 44, cx + 7, ny + 30], fill=PINK)
    d.polygon([(cx + 5, ny - 44), (cx + 36, ny - 58), (cx + 36, ny - 34), (cx + 5, ny - 20)], fill=PINK)
    bx, by = x + w - 34, y + 36
    d.ellipse([bx - 20, by - 20, bx + 20, by + 20], fill=CYAN)
    d.line([bx, by + 10, bx, by - 11], fill=(6, 3, 16), width=4)
    d.polygon([(bx, by - 16), (bx - 8, by - 5), (bx + 8, by - 5)], fill=(6, 3, 16))


def m_relay(d, cx, cy):
    for i, rad in enumerate((78, 56, 34)):
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(*[LILAC, CYAN, PINK][i], 220), width=4)
    d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=GOLD)
    for a in range(0, 360, 60):
        px = cx + int(78 * math.cos(math.radians(a)))
        py = cy + int(78 * math.sin(math.radians(a)))
        d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=CYAN)


def m_desktop(d, cx, cy):
    w, h = 340, 210
    x, y = cx - w // 2, cy - h // 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=20, fill=(20, 12, 46), outline=(*LILAC, 210), width=4)
    d.rounded_rectangle([x + 16, y + 16, x + w - 16, y + 44], radius=8, fill=(12, 8, 30))
    for i in range(4):
        ry = y + 60 + i * 34
        d.rounded_rectangle([x + 18, ry, x + w - 96, ry + 22], radius=7, fill=(46, 30, 92))
        d.rounded_rectangle([x + w - 86, ry, x + w - 18, ry + 22], radius=7, fill=(*CYAN, 90))
    d.rectangle([cx - 9, y + h, cx + 9, y + h + 22], fill=(*LILAC, 170))
    d.rounded_rectangle([cx - 52, y + h + 22, cx + 52, y + h + 32], radius=5, fill=(*LILAC, 170))


def arrow(base, x1, x2, y, c1=PINK, c2=CYAN):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    span = max(1, x2 - x1)
    for x in range(x1, x2 - 18):
        t = (x - x1) / span
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        d.line([x, y, x + 1, y], fill=(*col, 240), width=7)
    d.polygon([(x2, y), (x2 - 22, y - 15), (x2 - 22, y + 15)], fill=(*c2, 240))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(4)))
    base.alpha_composite(layer)


def varrow(base, x, y1, y2, c1=PINK, c2=CYAN):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    span = max(1, y2 - y1)
    for y in range(y1, y2 - 16):
        t = (y - y1) / span
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        d.line([x, y, x + 1, y], fill=(*col, 240), width=7)
    d.polygon([(x, y2), (x - 15, y2 - 22), (x + 15, y2 - 22)], fill=(*c2, 240))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(4)))
    base.alpha_composite(layer)


def m_sound_card(d, x, y, w=520, accent=PINK):
    h = 300
    d.rounded_rectangle([x, y, x + w, y + h], radius=24, fill=(255, 255, 255, 14), outline=(*accent, 130), width=2)
    d.rounded_rectangle([x + 26, y + 26, x + 26 + 130, y + 26 + 130], radius=18,
                        fill=(40, 24, 78), outline=(*LILAC, 120), width=2)
    # music note in artwork
    ax, ay = x + 26 + 88, y + 26 + 64
    d.ellipse([ax - 20, ay + 16, ax - 2, ay + 34], fill=accent)
    d.rectangle([ax + 3, ay - 30, ax + 6, ay + 24], fill=accent)
    d.polygon([(ax + 5, ay - 30), (ax + 26, ay - 40), (ax + 26, ay - 22), (ax + 5, ay - 14)], fill=accent)
    tx = x + 26 + 130 + 26
    d.text((tx, y + 34), "brainrot anthem", font=unb(30, 700), fill=INK)
    d.text((tx, y + 82), "original sound · creator", font=qs(26, 500), fill=MUTED)
    for i in range(3):
        d.rounded_rectangle([tx, y + 128 + i * 20, tx + 300 - i * 60, y + 140 + i * 20], radius=6, fill=(60, 40, 110))
    # tag chips
    cx = x + 26
    for label, col in (("audio", CYAN), ("artwork", PINK), ("transcript", LILAC), ("videos", GOLD)):
        f = qs(24, 600)
        tw = d.textbbox((0, 0), label, font=f)[2]
        d.rounded_rectangle([cx, y + h - 58, cx + tw + 32, y + h - 20], radius=19, outline=(*col, 180), width=2,
                            fill=(*col, 28))
        d.text((cx + 16, y + h - 52), label, font=f, fill=INK)
        cx += tw + 32 + 14


def m_platforms(d, cx, cy):
    labels = [("TikTok", CYAN), ("Reels", PINK), ("Shorts", GOLD)]
    n = len(labels)
    bw, gap = 150, 34
    total = n * bw + (n - 1) * gap
    x0 = cx - total // 2
    for i, (lab, col) in enumerate(labels):
        x = x0 + i * (bw + gap)
        d.rounded_rectangle([x, cy - 75, x + bw, cy + 75], radius=28, fill=(20, 12, 46), outline=(*col, 200), width=4)
        # simple play glyph
        d.polygon([(x + bw // 2 - 16, cy - 30), (x + bw // 2 - 16, cy + 6), (x + bw // 2 + 20, cy - 12)], fill=col)
        f = qs(28, 700)
        tw = d.textbbox((0, 0), lab, font=f)[2]
        d.text((x + bw // 2 - tw // 2, cy + 26), lab, font=f, fill=INK)


def m_search_transcript(d, x, y, w=760, accent=LILAC):
    # search pill
    d.rounded_rectangle([x, y, x + w, y + 78], radius=39, fill=(16, 10, 38), outline=(*accent, 170), width=3)
    d.ellipse([x + 26, y + 24, x + 56, y + 54], outline=CYAN, width=4)
    d.line([x + 52, y + 50, x + 66, y + 64], fill=CYAN, width=4)
    d.text((x + 84, y + 20), '"it’s giving...\"', font=qs(34, 600), fill=INK)
    # transcript lines with a highlighted phrase
    ly = y + 122
    widths = [w, w - 120, w - 40, w - 200]
    for i, ww in enumerate(widths):
        hot = i == 1
        d.rounded_rectangle([x, ly + i * 46, x + ww, ly + i * 46 + 26], radius=8,
                            fill=(*accent, 70) if hot else (52, 34, 100))
    # waveform
    wy = ly + len(widths) * 46 + 30
    for i in range(48):
        bh = int(8 + 44 * abs(math.sin(i * 0.6)) * (0.5 + 0.5 * abs(math.cos(i * 0.22))))
        bx = x + i * 15
        d.rounded_rectangle([bx, wy + (44 - bh), bx + 8, wy + 44], radius=4,
                            fill=[CYAN, PINK, LILAC, GOLD][i % 4])


def m_data_import(d, cx, cy):
    # export file
    fx, fy, fw, fh = cx - 300, cy - 130, 190, 250
    d.rounded_rectangle([fx, fy, fx + fw, fy + fh], radius=18, fill=(20, 12, 46), outline=(*GOLD, 200), width=4)
    d.polygon([(fx + fw - 46, fy), (fx + fw, fy + 46), (fx + fw - 46, fy + 46)], fill=(*GOLD, 90))
    for i in range(4):
        d.rounded_rectangle([fx + 26, fy + 80 + i * 34, fx + fw - 30 - i * 24, fy + 96 + i * 34], radius=6, fill=(60, 44, 30))
    d.text((fx + 26, fy + fh - 52), "TikTok", font=qs(26, 700), fill=GOLD)
    d.text((fx + 26, fy + fh - 24), "data.json", font=qs(22, 500), fill=MUTED)
    arrow_base_add(d)  # noop placeholder to keep signature simple


def arrow_base_add(d):
    pass


def m_share_sheet(img, cx, top):
    """A single iOS-share-sheet illustration with the 'Save to Sound Cache' action."""
    w, h = 640, 470
    x = cx - w // 2
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([x, top, x + w, top + h], radius=36, fill=(*CYAN, 42))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(70)))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([x, top, x + w, top + h], radius=36, fill=(24, 15, 52), outline=(*CYAN, 130), width=2)
    d.rounded_rectangle([cx - 44, top + 20, cx + 44, top + 29], radius=5, fill=(96, 74, 150))
    # app share row
    ry = top + 66
    for i, c in enumerate((PINK, GOLD, LILAC, MINT)):
        ix = x + 44 + i * 96
        d.rounded_rectangle([ix, ry, ix + 68, ry + 68], radius=17, fill=(40, 26, 78), outline=(*c, 150), width=2)
        d.ellipse([ix + 23, ry + 23, ix + 45, ry + 45], fill=c)
    d.line([x + 30, ry + 104, x + w - 30, ry + 104], fill=(60, 44, 96), width=2)
    # the highlighted Shortcut action
    ay = ry + 132
    d.rounded_rectangle([x + 24, ay, x + w - 24, ay + 88], radius=18, fill=(*CYAN, 34), outline=(*CYAN, 190), width=3)
    if _ICON is not None:
        img.alpha_composite(_ICON.resize((62, 62), Image.LANCZOS), (x + 46, ay + 13))
    d = ImageDraw.Draw(img, "RGBA")
    d.text((x + 126, ay + 28), "Save to Sound Cache", font=qs(34, 700), fill=INK)
    # dim actions
    for j in range(2):
        yy = ay + 112 + j * 76
        d.rounded_rectangle([x + 24, yy, x + w - 24, yy + 62], radius=15, fill=(34, 22, 66))
        d.ellipse([x + 44, yy + 15, x + 78, yy + 49], fill=(72, 54, 116))
        d.rounded_rectangle([x + 98, yy + 23, x + 98 + 260, yy + 41], radius=6, fill=(64, 46, 104))


def m_laptop(img, cx, cy, shot):
    """A laptop mockup with the app screenshot fitted (cover-crop) into the screen."""
    sw, sh = 780, 492
    x, y = cx - sw // 2, cy - sh // 2
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([x, y, x + sw, y + sh], radius=22, fill=(12, 8, 26), outline=(*LILAC, 170), width=3)
    ix0, iy0 = x + 16, y + 16
    iw, ih = sw - 32, sh - 32
    if shot is not None:
        r = max(iw / shot.width, ih / shot.height)
        rs = shot.resize((int(shot.width * r) + 1, int(shot.height * r) + 1), Image.LANCZOS)
        ox, oy = (rs.width - iw) // 2, (rs.height - ih) // 2
        crop = rs.crop((ox, oy, ox + iw, oy + ih)).convert("RGBA")
        mask = Image.new("L", (iw, ih), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, iw, ih], radius=10, fill=255)
        img.paste(crop, (ix0, iy0), mask)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([x - 64, y + sh, x + sw + 64, y + sh + 26], radius=13, fill=(30, 20, 58), outline=(*LILAC, 120), width=2)
    d.rounded_rectangle([cx - 64, y + sh + 6, cx + 64, y + sh + 18], radius=6, fill=(52, 36, 96))


def m_tiktok_file(d, cx, cy):
    """A single 'TikTok data.json' export-file illustration."""
    fw, fh = 300, 384
    x, y = cx - fw // 2, cy - fh // 2
    d.rounded_rectangle([x, y, x + fw, y + fh], radius=22, fill=(22, 14, 44), outline=(*GOLD, 210), width=4)
    fold = 72
    d.polygon([(x + fw - fold, y), (x + fw, y + fold), (x + fw - fold, y + fold)], fill=(*GOLD, 120))
    d.line([(x + fw - fold, y), (x + fw - fold, y + fold)], fill=(*GOLD, 210), width=3)
    d.line([(x + fw - fold, y + fold), (x + fw, y + fold)], fill=(*GOLD, 210), width=3)
    for i in range(6):
        d.rounded_rectangle([x + 34, y + 118 + i * 34, x + fw - 40 - (i % 3) * 40, y + 134 + i * 34], radius=6, fill=(72, 54, 34))
    d.text((x + 34, y + fh - 86), "TikTok", font=unb(36, 700), fill=GOLD)
    d.text((x + 34, y + fh - 42), "data.json", font=qs(30, 500), fill=MUTED)


# ---------- cards ----------
def base(accent):
    img = gradient_bg(accent)
    d = ImageDraw.Draw(img, "RGBA")
    brand_lockup(img, d)
    footer(d, accent)
    return img, ImageDraw.Draw(img, "RGBA")


def card1_shortcut():
    accent = CYAN
    img, d = base(accent)
    kicker(d, 96, 274, "one tap, done", accent)
    y = headline(d, 96, 324, "Share once. It lands in your vault.", INK, size=90, max_w=920)
    subline(d, 96, y + 26,
            "Add the iOS Shortcut, then just hit Share on any sound. It flies through the relay "
            "to your Mac: downloaded, tagged, offline. No login, no cloud.", max_w=900)
    m_share_sheet(img, 1470, 300)
    img.convert("RGB").save(OUT / "01-shortcut.png")


def card2_richmedia():
    accent = PINK
    img, d = base(accent)
    kicker(d, 96, 262, "not just an mp3", accent)
    y = headline(d, 96, 312, "Every sound, fully loaded.", INK, size=94, max_w=880)
    subline(d, 96, y + 24,
            "From TikTok, Instagram, or YouTube, Sound Cache grabs the real audio plus artwork, "
            "artist, popularity, transcript, and example videos.", max_w=860)
    m_laptop(img, 1400, 636, _SHOT)
    img.convert("RGB").save(OUT / "02-rich-media.png")


def card3_transcripts():
    accent = LILAC
    img, d = base(accent)
    kicker(d, 96, 220, "search superpower", accent)
    y = headline(d, 96, 270, "Find it by the meme. Not the creator.", INK, size=90, max_w=1000)
    subline(d, 96, y + 20,
            "On-device GPU transcripts make every spoken word searchable. "
            "Type the line you remember, and get the sound.", max_w=920)
    m_search_transcript(d, 1010, 330, w=800, accent=accent)
    img.convert("RGB").save(OUT / "03-transcripts.png")


def card4_bulk():
    accent = GOLD
    img, d = base(accent)
    kicker(d, 96, 210, "bring your whole history", accent)
    y = headline(d, 96, 258, "Import your entire TikTok, at once.", INK, size=86, max_w=1120)
    subline(d, 96, y + 16,
            "Point Sound Cache at your exported TikTok data and bulk-import every favorite sound.", max_w=1120)
    # how-to strip
    box = [96, 720, W - 96, 940]
    soft_panel(img, box, 24, accent, fill_alpha=12, glow=50)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((130, 748), "How to get your export", font=unb(32, 700), fill=INK)
    steps = [
        "1     In the TikTok app, open your Profile, then the menu, then Settings and privacy",
        "2     Go to Account, tap Download your data, and request it in JSON format",
        "3     When it arrives, download the file, then hit Bulk import in Sound Cache",
    ]
    for i, s in enumerate(steps):
        d.text((130, 812 + i * 42), s, font=qs(29, 500), fill=MUTED)
    m_tiktok_file(d, 1470, 456)
    img.convert("RGB").save(OUT / "04-bulk-import.png")


def m_sound_card_mini(d, x, y, accent):
    d.rounded_rectangle([x, y, x + 230, y + 70], radius=14, fill=(30, 18, 60), outline=(*accent, 150), width=2)
    d.rounded_rectangle([x + 12, y + 12, x + 58, y + 58], radius=10, fill=(56, 38, 100))
    for i in range(2):
        d.rounded_rectangle([x + 74, y + 18 + i * 24, x + 210 - i * 40, y + 34 + i * 24], radius=5, fill=(60, 42, 110))


def card5_packs():
    accent = MINT
    img, d = base(accent)
    kicker(d, 96, 240, "start instantly", accent)
    y = headline(d, 96, 290, "Grab a pack. Get a whole vibe.", INK, size=96, max_w=1000)
    subline(d, 96, y + 20,
            "Curated sound packs at soundcache.io: phonk, lofi, gym hype, y2k and more. "
            "Import a whole set in one click.", max_w=900)
    # overlapping album-style cascade (staggered down-right so every label shows)
    packs = [("y2k", PINK), ("lofi", CYAN), ("gym", GOLD), ("phonk", LILAC)]
    pw, ph = 300, 300
    x0, y0, dx, dy = 1010, 296, 150, 64
    for i, (lab, col) in enumerate(packs):
        x, yy = x0 + i * dx, y0 + i * dy
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x + 6, yy + 14, x + pw + 6, yy + ph + 14], radius=26, fill=(0, 0, 0, 130))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20)))
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle([x, yy, x + pw, yy + ph], radius=26, fill=(20, 12, 46), outline=(*col, 225), width=3)
        d.rounded_rectangle([x + 20, yy + 20, x + pw - 20, yy + ph - 76], radius=16, fill=(*col, 235))
        nx, ny = x + pw // 2, yy + (ph - 76) // 2 + 4
        d.ellipse([nx - 20, ny + 16, nx - 2, ny + 34], fill=(14, 8, 32))
        d.rectangle([nx + 3, ny - 30, nx + 6, ny + 26], fill=(14, 8, 32))
        d.polygon([(nx + 5, ny - 30), (nx + 28, ny - 40), (nx + 28, ny - 22), (nx + 5, ny - 14)], fill=(14, 8, 32))
        d.text((x + 22, yy + ph - 62), lab, font=unb(30, 700), fill=INK)
    img.convert("RGB").save(OUT / "05-packs.png")


card1_shortcut()
card2_richmedia()
card3_transcripts()
card4_bulk()
card5_packs()
print("wrote 5 cards to", OUT)
