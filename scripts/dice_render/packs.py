"""Check the rendered clips against Telegram's limits and put the packs into the bot's assets.

    uv run --group render python scripts/dice_render/packs.py

Writes ``src/writers_bot/assets/dice_packs/pack1`` ... ``pack8`` (at most 120 stickers each, Telegram's limit for a pack;
the layout is in ``clips.py``) and ``manifest.json``: for every sticker its file, die, variant, value (a list for dice in a
group) and effect (crit, fumble or none), its emoji and its search keywords (see ``tags.py``).
Nothing is written unless every clip passes: WEBM with VP9, 512 px, 2.9 s, at most 256 KB.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import clips
import imageio_ffmpeg
import render
import tags
from PIL import Image, ImageDraw

DEST = Path(__file__).resolve().parents[2] / "src" / "writers_bot" / "assets" / "dice_packs"
PACK_SIZE = clips.PACK_SIZE


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
    problems = []
    for key, names in render.PACKS.items():
        if not 0 < len(names) <= PACK_SIZE or len(set(names)) != len(names):
            problems.append(f"{key}: {len(names)} names ({len(set(names))} different), at most {PACK_SIZE}")
        problems += [f"{n}: {why}" for n in names if (why := check(render.OUT / f"{n}.webm"))]
    if problems:
        sys.exit("not written:\n" + "\n".join(problems))
    if DEST.exists():
        shutil.rmtree(DEST)
    manifest = {}
    for key, names in render.PACKS.items():
        (DEST / key).mkdir(parents=True)
        rows = []
        for name in names:
            shutil.copy(render.OUT / f"{name}.webm", DEST / key / f"{name}.webm")
            clip = clips.parse(name)
            emoji, keywords = tags.tags(name)
            if why := tags.check(name, emoji, keywords):
                sys.exit(f"{name}: {why}")
            rows.append(
                {
                    "file": f"{key}/{name}.webm",
                    "die": f"d{clip.sides}" if clip.kind == "single" else f"{len(clip.pieces)}d{clip.sides}",
                    "variant": clip.variant,
                    "value": clip.values[0] if clip.kind == "single" else list(clip.values),
                    "effect": clip.effect,
                    "emoji": emoji,
                    "keywords": keywords,
                }
            )
        manifest[key] = rows
        contact_sheet(names, render.OUT / f"contact-sheet-{key}.png")
    (DEST / "__init__.py").write_text("")
    shutil.copy(render.HERE / "CREDITS.md", DEST / "CREDITS.md")  # the credits of what the stickers are made of
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    total = sum(p.stat().st_size for p in DEST.rglob("*.webm"))
    print(f"{sum(len(v) for v in manifest.values())} stickers, {total // 1024} KB, in {DEST}")


if __name__ == "__main__":
    main()
