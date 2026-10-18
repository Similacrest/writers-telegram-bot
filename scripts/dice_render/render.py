"""Render the dice sticker clips: the "Dice of Rolling" theme drawn frame by frame with Babylon.js in headless Chromium.

Nothing is recorded: each die is posed so the wanted face looks at the camera, so every result comes out exactly as
asked. The page is ``page/render.html``; this script drives it, adds the effects around the die, and encodes WEBM.

    uv run --group render python scripts/dice_render/render.py --pack 1 --pack 2     # both packs (240 clips)
    uv run --group render python scripts/dice_render/render.py d20_17 d12rd_5        # just these
    uv run --group render python scripts/dice_render/render.py --pack 2 --shard 1/2  # half of pack 2; run 2/2 beside it

Set it up first with ``fetch_assets.py`` (see README.md). Clips that already exist are skipped (``--force`` redoes them).

Clip names: ``d20_17`` a d20 showing 17; ``d20a_17`` / ``d20d_17`` the higher / lower of two d20s (a quiet green / magenta
halo); ``d12r_9`` / ``d12b_9`` a red / blue d12; ``d12rd_9`` red, for the lower of two, ``d12ba_9`` blue, for the higher;
``d4n_1`` (also ``d6n``, ``d10n``) the lowest and highest without the crit effects; ``d100_73`` a tens die and a d10;
``d2_1`` / ``d2_2`` the coin, coat of arms / "5 копійок". The highest roll of a die gets golden rays and sparkles, the lowest
a red halo, a dull die, a shudder and a puff of dust.
"""

import argparse
import base64
import functools
import http.server
import importlib.util
import io
import json
import math
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BUILD = HERE.parents[1] / "build" / "dice_render"
WEB = BUILD / "web"
OUT = BUILD / "out"

FPS, FRAMES, LAND, SETTLE, SIZE = 30, 87, 24, 8, 512  # Telegram: at most 3 s, 30 fps, 512 px
BIG = 1024  # the page renders at twice the sticker size
MAX_BYTES = 256 * 1024
CRFS = (40, 46, 52, 58)  # tried in turn until the clip fits

# the bot's own effects code: here only the puff of dust and the colours
_spec = importlib.util.spec_from_file_location("dice_stickers", HERE.parent / "dice_stickers.py")
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)
GOLD, RED = ds.GOLD, ds.RED
ADV, DIS = (96, 200, 140), (190, 96, 150)  # the quiet halos for the higher / lower of two d20s (and d12s)
AMBER = (0.78, 0.56, 0.24)  # the d100's tens die: its light marble made deeper, so white numerals read
TINTS = {"r": (0.86, 0.26, 0.26), "b": (0.28, 0.42, 0.9)}  # the d12's red and blue
SLATE = (0.42, 0.46, 0.56)  # a d12 without a colour of its own
OFFSETS = json.loads((HERE / "roll_offsets.json").read_text())  # per face: the turn that puts its numeral upright

# --- what goes into the packs ----------------------------------------------------------------------------------------

PACK1 = (
    [f"d20_{v}" for v in range(1, 21)]  # a plain d20 with its crits ...
    + [f"d20{c}_{v}" for c in ("a", "d") for v in range(2, 20)]  # ... and the higher / lower of two, without the ties
    + [
        f"d12{c}_{v}" for c in ("r", "b", "rd", "ba") for v in range(1, 13)
    ]  # r, b: no crits (Xd12); rd, ba: with (1d12)
    + [f"d10_{v}" for v in range(1, 11)]
    + ["d10n_1", "d10n_10", "d4n_1", "d4n_4", "d6n_1", "d6n_6"]  # the lowest and highest without the crit effects
)
PACK2 = (
    [f"d100_{v}" for v in range(1, 101)]
    + [f"d4_{v}" for v in range(1, 5)]
    + [f"d6_{v}" for v in range(1, 7)]
    + [f"d8_{v}" for v in range(1, 9)]
    + ["d2_1", "d2_2"]
)
PACKS = {"pack1": PACK1, "pack2": PACK2}


def die(sides: int, value: int, seed: int, **kw) -> dict:
    key = f"{sides}_{value % 10 if sides == 10 else value}"
    return {"sides": sides, "value": value, "seed": seed, "roll": OFFSETS.get(key, 0.0), **kw}


def job(name: str) -> tuple[list[dict], int, int, str]:
    """The dice of a clip, its sides and value (of the total, for a d100) and its variant letters."""
    kind, number = name.split("_")
    value = int(number)
    sides = int("".join(c for c in kind[1:] if c.isdigit()))
    variant = kind[1 + len(str(sides)) :]
    if sides == 100:  # a tens die (00-90) and a d10 for the ones (0 counts as 10), landing a moment apart
        tens, ones = value // 10 % 10 * 10, value % 10 or 10
        dice = [
            die(100, tens, 100 + value, x=-0.68, scale=0.7, tint=list(AMBER)),
            die(10, ones, 101 + value, x=0.68, scale=0.7, delay=4),
        ]
        return dice, sides, value, ""
    if sides == 2:  # a coin: 1 = the coat of arms, 2 = "5 копійок"; no fading, no crit effects
        return [die(2, value, 20 + value, scale=1.5, dim=False)], sides, value, ""
    extra = {}
    if sides == 12:  # light marble with dark numerals: coloured, the numerals made white
        extra = {"tint": list(TINTS.get(variant[:1], SLATE))}
    return [die(sides, value, sides * 7 + value, scale=1.1, **extra)], sides, value, variant


def effect_of(sides: int, value: int, variant: str) -> str | None:
    if sides == 2:
        return None
    if variant == "n" or (sides == 12 and variant in ("r", "b")):  # no crits (Daggerheart's work differently)
        return None
    if value == sides:
        return "crit"
    if value == 1:
        return "fumble"
    if sides == 20 or len(variant) == 2:  # 'a' / 'd' on a d20; the d12's 'rd' (red, lower) and 'ba' (blue, higher)
        return {"a": "adv", "d": "dis"}.get(variant[-1:])
    return None


# --- the effects: there from the first frame to the last, quieter at both ends so the loop closes ----------------------

QUIET, RISE, FALL = 0.25, LAND + SETTLE, 64
RAY_BLUR = 40


def amp(i: int) -> float:
    def smooth(x: float) -> float:
        return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, x)))

    if i < RISE:
        return QUIET + (1 - QUIET) * smooth(i / RISE)
    if i < FALL:
        return 1.0
    return QUIET + (1 - QUIET) * (1 - smooth((i - FALL) / (FRAMES - 1 - FALL)))


def star(d: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple) -> None:
    d.polygon(
        [
            (x + (r if k % 2 == 0 else r * 0.22) * math.cos(k * math.pi / 4),
             y + (r if k % 2 == 0 else r * 0.22) * math.sin(k * math.pi / 4))
            for k in range(8)
        ],
        fill=fill,
    )  # fmt: skip


def crit_back(out: Image.Image, a: float, i: int, c: float, radius: float) -> None:
    layer = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    turn = i * 2 * (math.pi / 6) / FRAMES  # two ray-widths per loop, so it comes round to where it began
    for k in range(12):
        ang, w, far = turn + k * math.pi / 6, math.pi / 30, radius * 2.3
        d.polygon(
            [(c, c), (c + far * math.cos(ang - w), c + far * math.sin(ang - w)),
             (c + far * math.cos(ang + w), c + far * math.sin(ang + w))],
            fill=(*GOLD, int(38 * a)),
        )  # fmt: skip
    out.alpha_composite(layer.filter(ImageFilter.GaussianBlur(BIG / RAY_BLUR)))


def crit_front(out: Image.Image, a: float, i: int, c: float, radius: float, seed: int) -> None:
    d = ImageDraw.Draw(out, "RGBA")
    for k in range(7):
        ang = seed * 0.37 + k * 2 * math.pi / 7
        dist = radius * (1.08 + 0.3 * ((k * 5 + seed) % 4) / 3)
        twinkle = 0.5 + 0.5 * math.sin(i * 2 * math.pi * 5 / FRAMES + k * 1.7)  # five twinkles per loop
        size = radius * 0.11 * (0.4 + 0.6 * twinkle) * a
        if size > 1:
            star(d, c + dist * math.cos(ang), c + dist * math.sin(ang), size, (255, 246, 205, int(200 * twinkle * a)))


def dull(die_img: Image.Image, k: float) -> Image.Image:
    """The lowest roll: the die goes dull and dark."""
    a = np.asarray(die_img, dtype=float)
    rgb = a[..., :3]
    grey = rgb.mean(axis=2, keepdims=True)
    a[..., :3] = (rgb * (1 - 0.55 * k) + grey * 0.55 * k) * (1 - 0.3 * k)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def halo(out: Image.Image, tone: tuple, alpha: float, radius: float, pair: bool, blur: float) -> None:
    layer = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    c = BIG / 2
    r = radius * (1.1 if pair else 1.25)
    ry = r * 0.75 if pair else r
    ImageDraw.Draw(layer).ellipse((c - r, c - ry, c + r, c + ry), fill=(*tone, int(alpha)))
    out.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def compose(die_img: Image.Image, effect: str | None, i: int, pair: bool, seed: int) -> Image.Image:
    out = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    radius = BIG * 0.36 * (1.3 if pair else 1.0)
    c = BIG / 2
    a = amp(i)
    if effect == "crit":
        halo(out, GOLD, (75 if pair else 85) * a, radius, pair, BIG / 14)
        crit_back(out, a, i, c, radius)
    elif effect == "fumble":
        halo(out, RED, (130 if pair else 150) * a, radius, pair, BIG / 18)
        ds._effects(out, "fumble", i - LAND, c, c, radius, seed, front=False)  # the puff of dust where it landed
        die_img = dull(die_img, a)
    elif effect in ("adv", "dis"):
        halo(out, ADV if effect == "adv" else DIS, 62 * a, radius, pair, BIG / 14)
    out.alpha_composite(die_img)
    if effect == "crit":
        front = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
        crit_front(front, a, i, c, radius, seed)
        out.alpha_composite(front)
    return out.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


# --- rendering and encoding ----------------------------------------------------------------------------------------


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves build/dice_render/web, with the page itself taken from the repository."""

    def translate_path(self, path: str) -> str:
        if path.split("?")[0] == "/render.html":
            return str(HERE / "page" / "render.html")
        return super().translate_path(path)

    def log_message(self, *args) -> None:
        pass


def serve() -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def render_dice(page, spec: list[dict], effect: str | None) -> list[Image.Image]:
    """All 87 frames of a clip: the dice as the page draws them, with the effects around them."""
    for d in spec:  # the rolled face goes gold on a crit, the die shudders on a fumble
        d["gold"] = effect == "crit"
        d["fumble"] = effect == "fumble"
    page.evaluate("spec => window.setup(spec)", spec)
    last_move = LAND + SETTLE + max(d.get("delay", 0) for d in spec)  # after this the dice lie still
    dice = []
    for i in range(last_move + 1):
        data = page.evaluate(f"window.frame({i})")
        dice.append(Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGBA"))
    seed = sum(d["value"] for d in spec)
    return [compose(dice[min(i, last_move)], effect, i, len(spec) > 1, seed) for i in range(FRAMES)]


def encode(frames: list[Image.Image], out: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for i, frame in enumerate(frames):
            frame.save(Path(tmp, f"{i:03d}.png"))
        for crf in CRFS:
            subprocess.run(  # noqa: S603 - our own arguments
                [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(FPS),
                 "-i", str(Path(tmp, "%03d.png")), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
                 "-crf", str(crf), "-deadline", "good", "-cpu-used", "2", "-row-mt", "1", "-an", str(out)],
                check=True,
                timeout=300,
            )  # fmt: skip
            if out.stat().st_size <= MAX_BYTES:
                return out.stat().st_size
    raise RuntimeError(f"{out.name} is over {MAX_BYTES} bytes even at crf {CRFS[-1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", help="clips to render, e.g. d20_17")
    parser.add_argument("--pack", action="append", choices=("1", "2"), help="all the clips of pack 1 or 2")
    parser.add_argument("--shard", default="1/1", help="I/N: only every Nth clip, starting at the Ith")
    parser.add_argument("--force", action="store_true", help="render clips that already exist too")
    args = parser.parse_args()
    names = list(args.names) + [n for p in args.pack or [] for n in PACKS[f"pack{p}"]]
    index, count = (int(x) for x in args.shard.split("/"))
    names = names[index - 1 :: count]
    if not names:
        parser.error("no clips given (names, or --pack 1 / --pack 2)")
    if not (WEB / "babylon.js").exists():
        sys.exit("run scripts/dice_render/fetch_assets.py first")
    OUT.mkdir(parents=True, exist_ok=True)
    server = serve()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
        )
        page = browser.new_page(viewport={"width": BIG, "height": BIG})
        page.on("pageerror", lambda e: print("page error:", e, file=sys.stderr))
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/render.html")
        for name in names:
            clip = OUT / f"{name}.webm"
            if clip.exists() and not args.force:
                print(name, "already rendered", flush=True)
                continue
            spec, sides, value, variant = job(name)
            frames = render_dice(page, spec, effect_of(sides, value, variant))
            still = frames[LAND + SETTLE + max(d.get("delay", 0) for d in spec)]
            sheet = Image.new("RGBA", still.size, (236, 238, 244, 255))
            sheet.alpha_composite(still)
            sheet.convert("RGB").save(OUT / f"{name}.png")  # the resting frame, for the contact sheets
            print(name, encode(frames, clip), "bytes", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
