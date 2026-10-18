"""Pictures of achievements, in the style of a game's "achievement unlocked" (PLAN phase 5, v4.2).

Outside creating one, the raw sticker is never posted: awarding posts :func:`award_card`, a person's list is
:func:`collection` and the community's board is :func:`leaderboard`. The sticker (a static sticker's picture,
or the thumbnail of an animated one) is the icon; without one a drawn medal stands in.

Everything here is synchronous Pillow work on bytes; callers run it in a thread. All texts come from the
caller (the locale), names included, so this module has no strings of its own.
"""

import io
import math
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

S = 2  # drawn at twice the size and scaled down, for smooth edges

BG_TOP = (27, 31, 46)
BG_BOTTOM = (44, 30, 66)
PANEL = (255, 255, 255, 18)
GOLD = (245, 196, 81)
GOLD_DARK = (176, 128, 30)
SILVER = (196, 204, 214)
BRONZE = (205, 127, 50)
WHITE = (245, 245, 250)
MUTED = (168, 172, 190)
LOCKED = (90, 94, 112)


@cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    data = files("writers_bot.assets.fonts").joinpath(name).read_bytes()
    return ImageFont.truetype(io.BytesIO(data), size * S)


def printable(text: str) -> str:
    """Drop what the font can't draw (emoji, pictographs), so no empty boxes appear."""
    out = []
    for ch in text or "":
        cat = unicodedata.category(ch)
        if ord(ch) > 0xFFFF or cat in ("So", "Cs", "Co", "Cn") or 0x2600 <= ord(ch) <= 0x27BF:
            continue
        if cat.startswith("C") and ch not in "\n":
            continue
        out.append(ch)
    return " ".join("".join(out).split())


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int, lines: int) -> list[str]:
    """Up to ``lines`` lines of at most ``width`` (in drawing units); the last one ends with … if cut."""
    words = printable(text).split()
    out: list[str] = []
    current = ""
    for i, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if f.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            out.append(current)
        current = word
        if len(out) == lines:
            current = ""
            words = words[i:]
            break
    else:
        words = []
    if current:
        out.append(current)
    out = out[:lines]
    if words or (out and f.getlength(out[-1]) > width):
        last = out[-1] if out else ""
        while last and f.getlength(last + "…") > width:
            last = last[:-1]
        out[-1:] = [last.rstrip() + "…"]
    return out


def _background(w: int, h: int) -> Image.Image:
    top, bottom = Image.new("RGB", (1, 2)), None
    top.putpixel((0, 0), BG_TOP)
    top.putpixel((0, 1), BG_BOTTOM)
    bottom = top.resize((w, h), Image.Resampling.BILINEAR)
    return bottom.convert("RGBA")


def _rrect(img: Image.Image, box, radius: int, fill=None, outline=None, width: int = 0) -> None:
    """A rounded rectangle blended onto ``img`` (ImageDraw replaces pixels of an RGBA image instead)."""
    x0, y0, x1, y1 = (int(v) for v in box)
    layer = Image.new("RGBA", (x1 - x0 + 1, y1 - y0 + 1), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((0, 0, x1 - x0, y1 - y0), radius, fill=fill, outline=outline, width=width)
    img.alpha_composite(layer, (x0, y0))


def _finish(img: Image.Image) -> bytes:
    small = img.resize((img.width // S, img.height // S), Image.Resampling.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    small.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _medal(size: int, color=GOLD) -> Image.Image:
    """The stand-in icon: a medal with a star."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    pad = size // 10
    d.ellipse((pad, pad, size - pad, size - pad), fill=(*color, 255) if len(color) == 3 else color)
    inner = size // 5
    d.ellipse((inner, inner, size - inner, size - inner), outline=(255, 255, 255, 120), width=max(2, size // 40))
    cx = cy = size / 2
    r1, r2 = size * 0.22, size * 0.09
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = r1 if i % 2 == 0 else r2
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    d.polygon(points, fill=(255, 255, 255, 230))
    return img


def _icon(data: bytes | None, size: int, *, locked: bool = False) -> Image.Image:
    """A sticker picture fitted into a square (or the medal), greyed out when locked."""
    img = None
    if data:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            img = img.convert("RGBA")
        except Exception:  # an unreadable picture: the medal instead
            img = None
    if img is None:
        img = _medal(size, LOCKED if locked else GOLD)
    else:
        img = ImageOps.contain(img, (size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
        img = canvas
    if locked:
        alpha = img.getchannel("A").point(lambda a: a * 45 // 100)
        grey = ImageOps.grayscale(img.convert("RGB")).convert("RGBA")
        grey.putalpha(alpha)
        img = grey
    return img


def _tile(size: int, ring, *, glow: bool) -> Image.Image:
    """The rounded square behind an icon, with a coloured ring (and a soft glow for a fresh award)."""
    margin = size // 8 if glow else 0
    img = Image.new("RGBA", (size + 2 * margin, size + 2 * margin), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    radius = size // 5
    box = (margin, margin, margin + size, margin + size)
    if glow:
        halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle(box, radius, fill=(*ring, 150))
        img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(margin / 2)))
    d.rounded_rectangle(box, radius, fill=(16, 18, 28, 255), outline=(*ring, 255), width=max(3, size // 30))
    return img


def _place_icon(img: Image.Image, data: bytes | None, x: int, y: int, size: int, ring, *, glow=False, locked=False):
    tile = _tile(size, ring, glow=glow)
    margin = (tile.width - size) // 2
    img.alpha_composite(tile, (x - margin, y - margin))
    inner = int(size * 0.8)
    icon = _icon(data, inner, locked=locked)
    off = (size - inner) // 2
    img.alpha_composite(icon, (x + off, y + off))


# --- the award ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class AwardCard:
    label: str  # "ДОСЯГНЕННЯ ОТРИМАНО"
    name: str
    description: str | None
    footer: str  # "Taras · 18.10.2026 · вже має 3 людини"
    icon: bytes | None = None


def award_card(card: AwardCard) -> bytes:
    w, h = 1280 * S, 440 * S
    img = _background(w, h)
    d = ImageDraw.Draw(img, "RGBA")
    _rrect(img, (24 * S, 24 * S, w - 24 * S, h - 24 * S), 36 * S, fill=PANEL, outline=(*GOLD, 90), width=2 * S)
    icon_size = 300 * S
    _place_icon(img, card.icon, 70 * S, (h - icon_size) // 2, icon_size, GOLD, glow=True)
    x = 430 * S
    width = w - x - 70 * S
    d = ImageDraw.Draw(img, "RGBA")
    y = 78 * S
    d.text((x, y), printable(card.label).upper(), font=font(26, True), fill=GOLD)
    y += 52 * S
    for line in wrap(card.name, font(58, True), width, 2):
        d.text((x, y), line, font=font(58, True), fill=WHITE)
        y += 70 * S
    if card.description:
        y += 6 * S
        for line in wrap(card.description, font(30), width, 3):
            d.text((x, y), line, font=font(30), fill=MUTED)
            y += 40 * S
    footer = wrap(card.footer, font(26), width, 1)
    _rrect(img, (x, h - 112 * S, x + width, h - 110 * S), 0, fill=(255, 255, 255, 40))
    d.text((x, h - 92 * S), footer[0] if footer else "", font=font(26), fill=MUTED)
    return _finish(img)


# --- a person's achievements ---------------------------------------------------------------------------


@dataclass(slots=True)
class Item:
    name: str
    description: str | None = None
    note: str | None = None  # the date it was awarded, or "???" for a locked one
    icon: bytes | None = None
    locked: bool = False


@dataclass(slots=True)
class Collection:
    title: str  # "Досягнення · Taras"
    subtitle: str  # "4 з 9"
    items: list[Item] = field(default_factory=list)
    earned: int = 0
    total: int = 0


COLS = 2
TILE_W, TILE_H = 600, 150


def collection(c: Collection) -> bytes:
    rows = max(1, (len(c.items) + COLS - 1) // COLS)
    w = (40 + COLS * TILE_W + (COLS - 1) * 20 + 40) * S
    h = (170 + rows * (TILE_H + 20) + 30) * S
    img = _background(w, h)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((40 * S, 36 * S), printable(c.title), font=font(40, True), fill=WHITE)
    d.text((40 * S, 92 * S), printable(c.subtitle), font=font(26), fill=GOLD)
    if c.total:
        bar_x, bar_w = 320 * S, w - 360 * S
        _rrect(img, (bar_x, 100 * S, bar_x + bar_w, 116 * S), 8 * S, fill=(255, 255, 255, 30))
        done = max(16 * S, bar_w * c.earned // c.total) if c.earned else 0
        if done:
            d.rounded_rectangle((bar_x, 100 * S, bar_x + done, 116 * S), 8 * S, fill=GOLD)
    for i, item in enumerate(c.items):
        col, row = i % COLS, i // COLS
        x = (40 + col * (TILE_W + 20)) * S
        y = (150 + row * (TILE_H + 20)) * S
        _rrect(
            img,
            (x, y, x + TILE_W * S, y + TILE_H * S),
            22 * S,
            fill=(255, 255, 255, 10 if item.locked else 22),
            outline=(255, 255, 255, 30) if item.locked else (*GOLD, 110),
            width=2 * S,
        )
        size = 110 * S
        _place_icon(img, item.icon, x + 20 * S, y + 20 * S, size, LOCKED if item.locked else GOLD, locked=item.locked)
        d = ImageDraw.Draw(img, "RGBA")
        tx = x + 150 * S
        tw = TILE_W * S - 170 * S
        ty = y + 22 * S
        name_font = font(28, True)
        d.text(
            (tx, ty),
            (wrap(item.name, name_font, tw, 1) or [""])[0],
            font=name_font,
            fill=MUTED if item.locked else WHITE,
        )
        ty += 42 * S
        if item.description:
            for line in wrap(item.description, font(22), tw, 2):
                d.text((tx, ty), line, font=font(22), fill=LOCKED if item.locked else MUTED)
                ty += 28 * S
        if item.note:
            d.text(
                (tx, y + TILE_H * S - 40 * S),
                printable(item.note),
                font=font(20),
                fill=LOCKED if item.locked else GOLD,
            )
    return _finish(img)


# --- the community's board -------------------------------------------------------------------------------


@dataclass(slots=True)
class Row:
    rank: int
    name: str
    count: str  # "7"
    icons: list[bytes | None] = field(default_factory=list)


@dataclass(slots=True)
class Board:
    title: str  # "Дошка досягнень"
    subtitle: str  # the community or chat, and how many achievements there are
    rows: list[Row] = field(default_factory=list)
    empty: str = ""  # shown when nobody has one yet


ROW_H = 96


def leaderboard(b: Board) -> bytes:
    w = 1280 * S
    h = (170 + max(1, len(b.rows)) * (ROW_H + 14) + 30) * S
    img = _background(w, h)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((40 * S, 36 * S), printable(b.title), font=font(42, True), fill=WHITE)
    d.text((40 * S, 96 * S), (wrap(b.subtitle, font(26), w - 80 * S, 1) or [""])[0], font=font(26), fill=GOLD)
    if not b.rows:
        d.text((40 * S, 170 * S), printable(b.empty), font=font(28), fill=MUTED)
    for i, row in enumerate(b.rows):
        y = (160 + i * (ROW_H + 14)) * S
        _rrect(img, (40 * S, y, w - 40 * S, y + ROW_H * S), 24 * S, fill=PANEL)
        color = {1: GOLD, 2: SILVER, 3: BRONZE}.get(row.rank, LOCKED)
        cx, cy, r = 92 * S, y + ROW_H * S // 2, 30 * S
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        rank = str(row.rank)
        f = font(28, True)
        d.text((cx - f.getlength(rank) / 2, cy - 17 * S), rank, font=f, fill=(20, 20, 30))
        name_x = 150 * S
        name_w = 420 * S
        d.text(
            (name_x, cy - 20 * S),
            (wrap(row.name, font(32, True), name_w, 1) or [""])[0],
            font=font(32, True),
            fill=WHITE,
        )
        cf = font(34, True)
        count_x = 600 * S
        d.text((count_x, cy - 22 * S), row.count, font=cf, fill=GOLD)
        ix = count_x + 90 * S
        size = 64 * S
        room = (w - 64 * S - ix) // (size + 10 * S)
        shown = row.icons[:room] if len(row.icons) <= room else row.icons[: room - 1]
        for data in shown:
            _place_icon(img, data, ix, cy - size // 2, size, GOLD)
            ix += size + 10 * S
        d = ImageDraw.Draw(img, "RGBA")
        if len(row.icons) > len(shown):
            d.text((ix + 4 * S, cy - 16 * S), f"+{len(row.icons) - len(shown)}", font=font(26, True), fill=MUTED)
    return _finish(img)
