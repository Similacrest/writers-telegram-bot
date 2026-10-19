"""Check the rendered clips against Telegram's limits for a video sticker and put them into the bot's assets.

    uv run --group render python scripts/dice_render/assets.py

Writes every clip of the catalogue (``clips.py``) to ``src/writers_bot/assets/dice_clips`` and ``manifest.json``: for every clip
its die, variant, value (a list for dice in a group) and effect (crit, fumble or none). Nothing is written unless every clip
passes: WEBM with VP9, 512 px, 2.9 s, at most 256 KB.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import clips
import imageio_ffmpeg
import render
from PIL import Image, ImageDraw

DEST = Path(__file__).resolve().parents[2] / "src" / "writers_bot" / "assets" / "dice_clips"


def check(clip: Path) -> str | None:
    """What is wrong with a clip, if anything."""
    if not clip.exists():
        return "missing"
    info = subprocess.run(  # noqa: S603 - our own arguments
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(clip)], capture_output=True, text=True, check=False
    ).stderr
    if clip.stat().st_size > render.MAX_BYTES:
        return f"{clip.stat().st_size} bytes"
    if "vp9" not in info or "512x512" not in info or "00:00:02.9" not in info:
        return "not VP9, 512x512 and 2.9 s"
    return None


def contact_sheet(names: list[str], path: Path) -> None:
    cols, tile = 16, 128
    sheet = Image.new("RGB", (cols * tile, ((len(names) + cols - 1) // cols) * tile), (236, 238, 244))
    for i, name in enumerate(names):
        picture = Image.open(render.OUT / f"{name}.png").resize((tile, tile))
        ImageDraw.Draw(picture).text((3, 2), name, fill=(60, 60, 70))
        sheet.paste(picture, ((i % cols) * tile, (i // cols) * tile))
    sheet.save(path)


def main() -> None:
    names = clips.ALL
    problems = [f"{len(names)} names, {len(set(names))} different"] if len(set(names)) != len(names) else []
    problems += [f"{n}: {why}" for n in names if (why := check(render.OUT / f"{n}.webm"))]
    if problems:
        sys.exit("not written:\n" + "\n".join(problems))
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    manifest = {}
    for name in names:
        shutil.copy(render.OUT / f"{name}.webm", DEST / f"{name}.webm")
        clip = clips.parse(name)
        manifest[name] = {
            "die": f"d{clip.sides}" if clip.kind == "single" else f"{len(clip.pieces)}d{clip.sides}",
            "variant": clip.variant,
            "value": clip.values[0] if clip.kind == "single" else list(clip.values),
            "effect": clip.effect,
        }
    contact_sheet(names, render.OUT / "contact-sheet.png")
    (DEST / "__init__.py").write_text("")
    shutil.copy(render.HERE / "CREDITS.md", DEST / "CREDITS.md")  # the credits of what the stickers are made of
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    total = sum(p.stat().st_size for p in DEST.glob("*.webm"))
    print(f"{len(manifest)} clips, {total // 1024} KB, in {DEST}")


if __name__ == "__main__":
    main()
