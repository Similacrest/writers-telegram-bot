"""Find, for every face of the theme's dice, the turn that puts its numeral upright (``roll_offsets.json``).

    uv run --group render python scripts/dice_render/calibrate.py 12 20     # recalibrate the d12 and the d20

How a face is turned is the texture's business: the theme draws every numeral at its own tilt. So each face is drawn
head-on with the theme's own texture, its numeral is matched against a serif font at every angle, and the result is
checked by drawing the face again. It is right for most faces; a few (the d12's 10 and 11, the d100's 10 and 20) did
not match and were set by eye, so check the output pictures (``build/dice_render/out/cal``) before replacing the file.
Needs numpy and scipy, and a serif font: ``DICE_FONT=/path/to/Serif.ttf`` (default DejaVu Serif).
"""

import base64
import io
import json
import math
import os
import sys

import numpy as np
import render
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright
from scipy import ndimage

FONT = os.environ.get("DICE_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
FACES = {6: range(1, 7), 8: range(1, 9), 10: range(10), 12: range(1, 13), 20: range(1, 21), 100: range(0, 100, 10)}
RADIUS = 100


def text_of(sides: int, value: int) -> str:
    if sides == 100:
        return f"{value:02d}"
    return str(value) + ("." if value in (6, 9) else "")


def normalised(mask: np.ndarray, size: int = 72):
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return None
    crop = Image.fromarray(mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].astype(np.uint8) * 255)
    k = (size - 8) / max(crop.size)
    crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))), Image.Resampling.LANCZOS)
    canvas = Image.new("L", (size, size), 0)
    canvas.paste(crop, ((size - crop.width) // 2, (size - crop.height) // 2))
    a = np.asarray(canvas.filter(ImageFilter.GaussianBlur(1.5)), dtype=float)
    a -= a.mean()
    return a / (np.linalg.norm(a) or 1), (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)


def template(text: str):
    image = Image.new("L", (400, 200), 0)
    ImageDraw.Draw(image).text((20, 20), text, font=ImageFont.truetype(FONT, 120), fill=255)
    return normalised(np.asarray(image) > 128)


def glyph_mask(image: Image.Image) -> np.ndarray:
    """The numeral nearest the middle: ink (light or dark) standing out from the marble, merged into one blob."""
    grey = image.convert("L")
    signed = np.asarray(grey, dtype=float) - np.asarray(grey.filter(ImageFilter.GaussianBlur(14)), dtype=float)
    top = np.abs(signed) > np.percentile(np.abs(signed), 99.3)
    ink = np.clip(signed * (1 if np.median(signed[top]) > 0 else -1), 0, None)
    h, w = ink.shape
    yy, xx = np.mgrid[:h, :w]
    near = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 < (RADIUS * 1.8) ** 2
    mask = (ink > 0.4 * np.percentile(ink[near], 99.5)) & near
    labels, n = ndimage.label(ndimage.binary_dilation(mask, iterations=26))
    best, nearest = 0, 1e9
    for i in range(1, n + 1):
        ys, xs = np.nonzero(labels == i)
        distance = math.hypot(ys.mean() - h / 2, xs.mean() - w / 2)
        if distance < nearest and len(xs) > 400:
            best, nearest = i, distance
    return mask & (labels == best) if best else mask


def tilt(image: Image.Image, sides: int, value: int) -> tuple[int, float]:
    """Degrees to turn the picture counter-clockwise to make its numeral upright, and how well it matched."""
    mask = Image.fromarray(glyph_mask(image).astype(np.uint8) * 255)
    wanted, wanted_aspect = template(text_of(sides, value))
    best = (-2.0, 0)
    for angle in range(-180, 180, 4):
        got = normalised(np.asarray(mask.rotate(angle, expand=True, resample=Image.Resampling.BILINEAR)) > 100)
        if got is None:
            continue
        score = float((got[0] * wanted).sum()) - 0.5 * abs(math.log(got[1] / wanted_aspect))
        if score > best[0]:
            best = (score, angle)
    return best[1], best[0]


def shot(page, sides: int, value: int, roll: float) -> Image.Image:
    page.evaluate(
        "spec => window.setup(spec)", [dict(sides=sides, value=value, seed=1, scale=3.0, roll=roll, dim=False)]
    )
    data = page.evaluate("window.frame(26)")
    return Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGBA").resize((512, 512))


def main() -> None:
    sides_wanted = [int(x) for x in sys.argv[1:]] or list(FACES)
    path = render.HERE / "roll_offsets.json"
    table = json.loads(path.read_text())
    cal = render.OUT / "cal"
    cal.mkdir(parents=True, exist_ok=True)
    server = render.serve()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
        )
        page = browser.new_page(viewport={"width": render.BIG, "height": render.BIG})
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/render.html")
        for sides in sides_wanted:
            for value in FACES[sides]:
                first, _ = tilt(shot(page, sides, value, 0.0), sides, value)
                best = None
                for sign in (1, -1):  # the measurement has a sign; the check says which one is right
                    roll = sign * math.radians(first)
                    picture = shot(page, sides, value, roll)
                    residual, score = tilt(picture, sides, value)
                    if best is None or abs(residual) < abs(best[1]):
                        best = (roll, residual, score, picture)
                    if abs(residual) <= 4:
                        break
                table[f"{sides}_{value}"] = best[0]
                print(sides, value, "turn", first, "residual", best[1], "score", round(best[2], 2), flush=True)
                tile = Image.new("RGB", (512, 512), (236, 238, 244))
                tile.paste(best[3], (0, 0), best[3])
                tile.crop((96, 96, 416, 416)).save(cal / f"{sides}_{value}.png")
        browser.close()
    path.write_text(
        json.dumps(dict(sorted(table.items(), key=lambda kv: tuple(map(int, kv[0].split("_"))))), indent=1)
    )


if __name__ == "__main__":
    main()
