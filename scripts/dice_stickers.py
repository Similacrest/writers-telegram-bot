"""Make the dice sticker animations: one short video per result of d4, d6, d8, d10, d12, d20 and d100.

Each sticker is a real polyhedron that tumbles in and lands with the rolled face towards the viewer, its
number upright. The highest result gets golden rays and sparkles, the lowest a heavy landing with a shake, a
red glow and a puff of dust. A d100 is the two percentile dice landing together:
a tens d10 (00-90) and a ones d10 (0-9), 00 and 0 being 100; its two d10s are plain except on 01 and 00
(the d100's lowest and highest), while a d10 on its own has the effects on 1 and 10. The bot uploads them once as its own sticker packs
(``modules/dice``) and then answers a roll with the sticker of its result.

Telegram's rules for video stickers: WEBM with VP9, 512 px, at most 3 s and 30 fps, at most 256 KB, no
sound. Needs ffmpeg with libvpx-vp9 (the ``art`` dependency group brings one):

    uv run --group art python scripts/dice_stickers.py            # all that are missing (--force: all)
    uv run --group art python scripts/dice_stickers.py 20 7       # just d20 showing 7
    uv run --group art python scripts/dice_stickers.py 100        # all the d100 ones
    uv run --group art python scripts/dice_stickers.py --preview  # a PNG contact sheet of last frames
"""

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "src" / "writers_bot" / "assets" / "dice"
SIZE = 512
SS = 2  # supersampling
FPS = 30
FRAMES = 87  # 2.9 s: safely under Telegram's 3 s
LAND = 48  # the frame the die comes to rest on
MAX_BYTES = 256 * 1024

COLORS = {
    4: (46, 160, 103),
    6: (214, 69, 65),
    8: (52, 120, 214),
    10: (142, 82, 204),
    12: (232, 136, 38),
    20: (40, 52, 92),
}
TENS_COLOR = (168, 44, 70)  # the d100's two d10s
ONES_COLOR = (30, 118, 138)
ALL_SIDES = (*sorted(COLORS), 100)
LIGHT = (-0.45, 0.6, 0.66)  # x right, y up, z towards the viewer
GOLD = (255, 205, 80)
RED = (235, 70, 60)
DUST = (120, 112, 104)


# --- geometry --------------------------------------------------------------------------------------------


def _norm(v):
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v)


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _hull_faces(points, sides: int):
    """Faces of a convex polyhedron: every plane through three vertices that has all the others behind it,
    with its vertices in order around the face (counter-clockwise seen from outside)."""
    faces = {}
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                normal = _cross(_sub(points[j], points[i]), _sub(points[k], points[i]))
                if _dot(normal, normal) < 1e-9:
                    continue
                normal = _norm(normal)
                d = _dot(normal, points[i])
                if d < 0:
                    normal, d = tuple(-c for c in normal), -d
                if all(_dot(normal, p) <= d + 1e-6 for p in points):
                    key = tuple(round(c, 4) for c in normal)
                    on = [m for m in range(n) if abs(_dot(normal, points[m]) - d) < 1e-6]
                    faces[key] = (normal, on)
    out = []
    for normal, on in faces.values():
        center = tuple(sum(points[m][c] for m in on) / len(on) for c in range(3))
        ref = _norm(_sub(points[on[0]], center))
        side = _cross(normal, ref)

        def angle(m, center=center, ref=ref, side=side):
            v = _sub(points[m], center)
            return math.atan2(_dot(v, side), _dot(v, ref))

        out.append({"normal": normal, "verts": sorted(on, key=angle), "center": center})
    if len(out) != sides:
        raise ValueError(f"d{sides}: found {len(out)} faces")
    return out


def polyhedron(sides: int):
    phi = (1 + math.sqrt(5)) / 2
    if sides == 4:
        pts = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]
    elif sides == 6:
        pts = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    elif sides == 8:
        pts = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    elif sides == 10:  # pentagonal trapezohedron: two apexes and a zigzag ring
        h = 1.0
        c = math.cos(math.pi / 5)
        z = h * (1 - c) / (1 + c)  # what keeps each kite flat
        pts = [(0, 0, h), (0, 0, -h)]
        for i in range(10):
            a = i * math.pi / 5
            pts.append((math.cos(a), math.sin(a), z if i % 2 == 0 else -z))
    elif sides == 12:
        pts = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        for a in (-1, 1):
            for b in (-1, 1):
                pts += [(0, a / phi, b * phi), (a / phi, b * phi, 0), (a * phi, 0, b / phi)]
    elif sides == 20:
        pts = []
        for a in (-1, 1):
            for b in (-1, 1):
                pts += [(0, a, b * phi), (a, b * phi, 0), (a * phi, 0, b)]
    else:
        raise ValueError(sides)
    radius = max(math.sqrt(_dot(p, p)) for p in pts)
    pts = [tuple(c / radius for c in p) for p in pts]
    faces = _hull_faces(pts, sides)
    if sides == 10:  # the kites' "up" is towards their apex, like the numbers on a real d10
        for f in faces:
            apex = 0 if f["normal"][2] > 0 else 1
            f["up"] = _norm(_sub(pts[apex], f["center"]))
    else:
        for f in faces:
            f["up"] = _norm(_sub(pts[f["verts"][0]], f["center"]))
    _number(faces, sides)
    return pts, faces


def _number(faces, sides: int) -> None:
    """Numbers 1..sides; opposite faces add up to sides + 1, as on real dice (a d4 has no opposite faces)."""
    left = list(range(len(faces)))
    low, high = 1, sides
    while left:
        i = left.pop(0)
        opposite = next((j for j in left if _dot(faces[i]["normal"], faces[j]["normal"]) < -0.999), None)
        faces[i]["value"] = low
        low += 1
        if opposite is not None:
            left.remove(opposite)
            faces[opposite]["value"] = high
            high -= 1
    if sides == 4:
        for value, f in enumerate(faces, 1):
            f["value"] = value


# --- rotations -------------------------------------------------------------------------------------------


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _axis_angle(axis, angle):
    x, y, z = _norm(axis)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ]


def _facing(face, tilt, yaw):
    """The rotation that turns ``face`` towards the viewer with its number upright, tipped back (``tilt``)
    and turned (``yaw``) a little so the die looks three-dimensional at rest."""
    n, u = face["normal"], face["up"]
    r = _cross(u, n)
    # rows: where n, u and r go -> z, y, x
    base = [list(r), list(u), list(n)]
    return _matmul(_axis_angle((0, 1, 0), yaw), _matmul(_axis_angle((1, 0, 0), tilt), base))


# --- drawing -------------------------------------------------------------------------------------------


def _font(size):
    data = files("writers_bot.assets.fonts").joinpath("DejaVuSans-Bold.ttf").read_bytes()
    return ImageFont.truetype(BytesIO(data), size)


def _glyph(value: int, sides: int, text: str | None = None) -> Image.Image:
    """The number for a face, white on transparent; 6 and 9 underlined so they can't be confused."""
    text = text or str(value)
    size = 150 if len(text) == 1 else 118
    if sides in (4,):
        size = 130
    f = _font(size)
    box = f.getbbox(text)
    w, h = box[2] - box[0], box[3] - box[1]
    img = Image.new("L", (w + 40, h + 60), 0)
    d = ImageDraw.Draw(img)
    d.text((20 - box[0], 20 - box[1]), text, font=f, fill=255)
    if text in ("6", "9") and sides > 6:
        d.rectangle((20, h + 34, 20 + w, h + 44), fill=255)
    return img


def _shade(color, normal, *, lift=0.0):
    light = _norm(LIGHT)
    k = 0.38 + 0.62 * max(0.0, _dot(normal, light)) + lift
    return tuple(max(0, min(255, int(c * k))) for c in color)


def _star(d, x, y, r, fill):
    """A four-pointed sparkle."""
    pts = []
    for k in range(8):
        a = k * math.pi / 4
        rr = r if k % 2 == 0 else r * 0.22
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def _effects(img, effect, since, cx, cy, radius, seed, *, front: bool) -> None:
    """The highest roll: slowly turning golden rays behind the die and sparkles around it. The lowest: a puff
    of dust from where it hit. ``since`` counts frames from the landing (negative before)."""
    s = img.width
    if effect is None or since < 0:
        return
    ramp = min(1.0, since / 8)
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    if effect == "crit" and not front:
        turn = since * 0.02
        for k in range(12):
            a = turn + k * math.pi / 6
            w = math.pi / 30
            far = radius * 2.3
            d.polygon(
                [(cx, cy), (cx + far * math.cos(a - w), cy + far * math.sin(a - w)),
                 (cx + far * math.cos(a + w), cy + far * math.sin(a + w))],
                fill=(*GOLD, int(70 * ramp)),
            )  # fmt: skip
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(s / 160)))
    elif effect == "crit":
        for k in range(7):
            a = seed * 0.37 + k * 2 * math.pi / 7
            dist = radius * (1.08 + 0.3 * ((k * 5 + seed) % 4) / 3)
            twinkle = 0.5 + 0.5 * math.sin(since * 0.45 + k * 1.7)
            size = radius * 0.11 * (0.4 + 0.6 * twinkle) * ramp
            if size > 1:
                _star(d, cx + dist * math.cos(a), cy + dist * math.sin(a), size, (255, 246, 205, int(230 * twinkle)))
        img.alpha_composite(layer)
    elif effect == "fumble" and not front:
        for k in range(6):
            start = k * 1.5
            t = since - start
            if t < 0 or t > 26:
                continue
            side = -1 if k % 2 == 0 else 1
            px = cx + side * radius * (0.5 + 0.12 * k)
            py = cy + radius * (0.75 - 0.03 * t)
            pr = radius * (0.14 + 0.012 * t)
            alpha = int(130 * (1 - t / 26))
            d.ellipse((px - pr, py - pr, px + pr, py + pr), fill=(*DUST, alpha))
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(s / 90)))


def frame(
    sides, pts, faces, glyphs, rot, *, scale, dx, dy, glow, highlight, color=None, effect=None, since=-1, seed=0,
    fx=True,
):  # fmt: skip
    """One frame of one die. ``fx`` False leaves the halo, rays, sparkles and dust to the caller (the d100
    draws them once behind both dice)."""
    s = SIZE * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    radius = s * 0.36 * scale
    cx, cy = s / 2 + dx * s, s / 2 + dy * s

    def screen(p):
        q = _apply(rot, p)
        return (cx + q[0] * radius, cy - q[1] * radius), q[2]

    # soft shadow on the "floor", smaller when the die is in the air
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    lift = max(0.0, -dy) * 3
    sw, sh = radius * (1.1 - 0.4 * lift), radius * 0.22
    sy = s / 2 + radius * 1.05
    ImageDraw.Draw(shadow).ellipse(
        (s / 2 + dx * s - sw, sy - sh, s / 2 + dx * s + sw, sy + sh), fill=(0, 0, 0, int(90 * (1 - 0.6 * lift)))
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s / 50)))
    if fx:
        _effects(img, effect, since, cx, cy, radius, seed, front=False)
    tone = RED if effect == "fumble" else GOLD
    if glow > 0 and fx:
        halo = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        r = radius * (1.25 + 0.1 * glow)
        ImageDraw.Draw(halo).ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*tone, int(150 * glow)))
        img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(s / 18)))
    d = ImageDraw.Draw(img)
    color = color or COLORS[sides]
    if effect == "fumble" and glow > 0:  # the lowest roll: the die goes dull and dark
        k = min(1.0, glow)
        grey = sum(color) / 3
        color = tuple(int((c * (1 - 0.55 * k) + grey * 0.55 * k) * (1 - 0.3 * k)) for c in color)
    for face in faces:
        normal = _apply(rot, face["normal"])
        if normal[2] <= 0.02:
            continue
        poly = [screen(pts[v])[0] for v in face["verts"]]
        is_result = face is highlight
        fill = _shade(color, normal, lift=0.08 if is_result and glow > 0 else 0)
        d.polygon(poly, fill=(*fill, 255))
        d.line(
            [*poly, poly[0]], fill=(*_shade(color, normal, lift=0.35), 255), width=max(2, int(s / 160)), joint="curve"
        )
        if normal[2] < 0.3:
            continue
        # the number, mapped onto the face plane
        g = glyphs[face["value"]]
        c = _apply(rot, face["center"])
        up = _apply(rot, face["up"])
        right = _cross(up, normal)
        inradius = min(
            math.dist(face["center"], tuple((a + b) / 2 for a, b in zip(pts[v1], pts[v2], strict=True)))
            for v1, v2 in zip(face["verts"], face["verts"][1:] + face["verts"][:1], strict=True)
        )
        span = inradius * (1.45 if len(face["verts"]) == 3 else 1.15) * radius / max(g.size)
        # glyph pixel (gx, gy) -> screen: centre + right * (gx - w/2) * span + up * (h/2 - gy) * span
        w, h = g.size
        ox = cx + c[0] * radius
        oy = cy - c[1] * radius
        a, b = right[0] * span, -up[0] * span
        cc, dd = -right[1] * span, up[1] * span
        tx = ox - a * w / 2 - b * h / 2
        ty = oy - cc * w / 2 - dd * h / 2
        det = a * dd - b * cc
        if abs(det) < 1e-6:
            continue
        # only the face's bounding box is drawn into
        x0, y0 = int(min(p[0] for p in poly)), int(min(p[1] for p in poly))
        x1, y1 = int(max(p[0] for p in poly)) + 1, int(max(p[1] for p in poly)) + 1
        tx, ty = tx - x0, ty - y0
        inv = (dd / det, -b / det, (b * ty - dd * tx) / det, -cc / det, a / det, (cc * tx - a * ty) / det)
        mask = g.transform((x1 - x0, y1 - y0), Image.Transform.AFFINE, inv, resample=Image.Resampling.BILINEAR)
        fade = min(1.0, (normal[2] - 0.3) / 0.35)  # numbers fade out towards the edge of the die
        alpha = mask.point(lambda v, k=fade: int(v * k))
        ink = GOLD if is_result and glow > 0 and effect == "crit" else (255, 255, 255)
        layer = Image.new("RGBA", mask.size, (*ink, 0))
        layer.putalpha(alpha)
        img.alpha_composite(layer, (x0, y0))
        d = ImageDraw.Draw(img)
    if fx:
        _effects(img, effect, since, cx, cy, radius, seed, front=True)
    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def _ease_out(t):
    return 1 - (1 - t) ** 3


def frames(sides: int, value: int, only: range | None = None):
    if sides == 100:
        yield from _percentile(value, only)
        return
    effect = "crit" if value == sides else "fumble" if value == 1 else None
    yield from _die(sides, value, only, seed=sides * 31 + value * 7, effect=effect)


def _percentile(value: int, only: range | None):
    """Two d10s side by side: tens (00-90) and ones (0-9); a face value of 10 is the 0 / 00 face."""
    tens, ones = (value // 10) % 10, value % 10
    labels_tens = {v: f"{(v % 10) * 10:02d}" for v in range(1, 11)}
    labels_ones = {v: str(v % 10) for v in range(1, 11)}
    # only the d100's own extremes get the effects, not a d10 that happens to show 0 or 1
    effect = "crit" if value == 100 else "fumble" if value == 1 else None
    left = _die(10, tens or 10, only, seed=value * 13 + 1, effect=effect, labels=labels_tens, color=TENS_COLOR,
                scale=0.72, x=-0.235, fx=False)  # fmt: skip
    right = _die(10, ones or 10, only, seed=value * 17 + 5, effect=effect, labels=labels_ones, color=ONES_COLOR,
                 scale=0.72, x=0.235, fx=False)  # fmt: skip
    size = SIZE * SS
    radius = size * 0.36 * 1.3  # around both dice
    for i, a, b in zip(only or range(FRAMES), left, right, strict=True):
        since = i - LAND
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        if effect and since >= 0:
            glow = min(1.0, since / 10) * (0.75 + 0.25 * math.sin(since / 5))
            halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            r = radius * (1.1 + 0.1 * glow)
            c = size / 2
            tone = RED if effect == "fumble" else GOLD
            ImageDraw.Draw(halo).ellipse((c - r, c - r * 0.75, c + r, c + r * 0.75), fill=(*tone, int(130 * glow)))
            out.alpha_composite(halo.filter(ImageFilter.GaussianBlur(size / 18)))
        _effects(out, effect, since, size / 2, size / 2, radius, value, front=False)
        out = out.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
        out.alpha_composite(a)
        out.alpha_composite(b)
        front = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        _effects(front, effect, since, size / 2, size / 2, radius, value, front=True)
        out.alpha_composite(front.resize((SIZE, SIZE), Image.Resampling.LANCZOS))
        yield out


def _die(sides, value, only, *, seed, effect=None, labels=None, color=None, scale=1.0, x=0.0, fx=True):
    pts, faces = polyhedron(sides)
    glyphs = {f["value"]: _glyph(f["value"], sides, (labels or {}).get(f["value"])) for f in faces}
    target = next(f for f in faces if f["value"] == value)
    tilt, yaw = {4: (-0.55, 0.45), 20: (-0.25, 0.2)}.get(sides, (-0.38, 0.28))
    rest = _facing(target, tilt, yaw)
    # a different tumble for every result, the same every time the script runs
    axis = _norm((math.sin(seed), math.cos(seed * 1.3), 0.35 * math.sin(seed * 0.7)))
    turns = 2.2 + (seed % 5) * 0.15
    for i in only or range(FRAMES):
        t = min(1.0, i / LAND)
        angle = (1 - _ease_out(t)) * turns * 2 * math.pi
        rot = _matmul(rest, _axis_angle(axis, angle))
        # falls in from above with two small bounces, then rests
        if t < 0.55:
            dy = -0.14 * (1 - (t / 0.55) ** 2)
        elif t < 1:
            u = (t - 0.55) / 0.45
            dy = -0.07 * abs(math.sin(u * 2 * math.pi)) * (1 - u)
        else:
            dy = 0.0
        dx = 0.08 * (1 - _ease_out(t)) * math.sin(seed)
        since = i - LAND
        settle = max(0.0, since / 10)
        pop = 1 + 0.06 * math.sin(min(1.0, settle) * math.pi) if i >= LAND and effect != "fumble" else 1.0
        if effect == "fumble" and since >= 0:  # a heavy landing: it shudders instead of popping
            dx += 0.03 * math.sin(since * 2.4) * math.exp(-since / 5)
            dy += 0.012 * abs(math.sin(since * 1.9)) * math.exp(-since / 4)
        glow = 0.0
        if effect and since >= 0:
            glow = min(1.0, settle) * (0.75 + 0.25 * math.sin(since / 5))
        yield frame(
            sides, pts, faces, glyphs, rot, scale=pop * scale, dx=dx + x, dy=dy, glow=glow, highlight=target,
            color=color, effect=effect, since=since, seed=seed, fx=fx,
        )  # fmt: skip


# --- encoding ------------------------------------------------------------------------------------------


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        found = shutil.which("ffmpeg")
        if not found:
            sys.exit("ffmpeg not found: run with `uv run --group art`")
        return found


def encode(sides: int, value: int, out: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for i, img in enumerate(frames(sides, value)):
            img.save(Path(tmp, f"{i:03d}.png"))
        for crf in (40, 46, 52, 58):
            subprocess.run(  # noqa: S603 - our own arguments
                [
                    ffmpeg(),
                    "-y",
                    "-loglevel",
                    "error",
                    "-framerate",
                    str(FPS),
                    "-i",
                    str(Path(tmp, "%03d.png")),
                    "-c:v",
                    "libvpx-vp9",
                    "-pix_fmt",
                    "yuva420p",
                    "-b:v",
                    "0",
                    "-crf",
                    str(crf),
                    "-deadline",
                    "good",
                    "-cpu-used",
                    "2",
                    "-row-mt",
                    "1",
                    "-an",
                    str(out),
                ],
                check=True,
                timeout=300,
            )
            if out.stat().st_size <= MAX_BYTES:
                return out.stat().st_size
    raise RuntimeError(f"{out} is over {MAX_BYTES} bytes")


def preview(path: Path) -> None:
    sheet = Image.new("RGBA", (len(ALL_SIDES) * 256, 2 * 256), (230, 230, 235, 255))
    for col, sides in enumerate(ALL_SIDES):
        for row, value in enumerate((sides, 1)):
            last = next(frames(sides, value, range(FRAMES - 1, FRAMES))).resize((256, 256), Image.Resampling.LANCZOS)
            sheet.alpha_composite(last, (col * 256, row * 256))
    sheet.save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sides", nargs="?", type=int)
    parser.add_argument("value", nargs="?", type=int)
    parser.add_argument("--preview", type=Path, nargs="?", const=Path("dice-preview.png"))
    parser.add_argument("--force", action="store_true", help="make them again even if the file exists")
    args = parser.parse_args()
    if args.preview:
        preview(args.preview)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    todo = (
        [(args.sides, v) for v in ([args.value] if args.value else range(1, args.sides + 1))]
        if args.sides
        else [(s, v) for s in ALL_SIDES for v in range(1, s + 1)]
    )
    for sides, value in todo:
        out = OUT / f"d{sides}_{value}.webm"
        if out.exists() and not args.force:
            continue
        size = encode(sides, value, out)
        print(f"{out.name}: {size // 1024} KB", flush=True)


if __name__ == "__main__":
    main()
