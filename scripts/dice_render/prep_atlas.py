"""Rebuild the theme's texture so the numerals are crisp.

The theme's numerals are about 25 px tall in a 512 px JPEG, which turns chalky when a die is drawn at sticker size. This
splits the texture into the marble with the numerals taken out (upscaled 4x) and the numerals as their own coverage
map (upscaled, smoothed and sharpened into clean white shapes). The page draws the marble, then the ink: the rolled
face's numerals at full strength, the others faded. Run by ``fetch_assets.py``.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

SCALE = 4
DARK_INK = {"d12", "d100"}  # light marble with dark numerals; every other die has light numerals


def uv_masks(theme: dict, width: int, height: int) -> dict[str, np.ndarray]:
    """Where each die's faces lie in the texture."""
    masks = {}
    for mesh in theme["meshes"]:
        if mesh["name"].endswith("_collider") or "uvs" not in mesh:
            continue
        uv, idx = mesh["uvs"], mesh["indices"]
        image = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(image)
        for t in range(len(idx) // 3):
            draw.polygon(
                [(uv[idx[t * 3 + k] * 2] * width, (1 - uv[idx[t * 3 + k] * 2 + 1]) * height) for k in range(3)],
                fill=255,
            )
        masks[mesh["name"]] = np.asarray(image) > 127
    return masks


def build(theme_dir: Path) -> None:
    image = Image.open(theme_dir / "diffuse.jpg").convert("RGB")
    width, height = image.size
    rgb = np.asarray(image, dtype=float)
    lum = rgb @ [0.299, 0.587, 0.114]
    theme = json.loads((theme_dir / "smoothDice.json").read_text())

    standing_out = lum - ndimage.gaussian_filter(lum, 6)  # what stands out from its surroundings
    ink = np.zeros((height, width))
    for name, mask in uv_masks(theme, width, height).items():
        inner = ndimage.binary_erosion(mask, iterations=1)
        d = -standing_out if name in DARK_INK else standing_out
        # soft, so the anti-aliasing of the source survives the upscaling
        ink[inner] = np.maximum(ink[inner], np.clip((d[inner] - 30) / 120, 0, 1))

    # the marble: the numerals filled in from what surrounds them (normalised convolution)
    fill = ndimage.binary_dilation(ink > 0.05, iterations=3)
    weight = (~fill).astype(float)
    clean = rgb.copy()
    for c in range(3):
        estimate = ndimage.gaussian_filter(rgb[..., c] * weight, 4) / np.maximum(
            ndimage.gaussian_filter(weight, 4), 1e-3
        )
        clean[..., c] = np.where(fill & (ndimage.gaussian_filter(weight, 4) > 0.05), estimate, rgb[..., c])
    marble = Image.fromarray(np.clip(clean, 0, 255).astype(np.uint8)).resize(
        (width * SCALE, height * SCALE), Image.Resampling.LANCZOS
    )
    marble.save(theme_dir / "marble.png")

    big = Image.fromarray(np.clip(ink * 255, 0, 255).astype(np.uint8)).resize(
        (width * SCALE, height * SCALE), Image.Resampling.BICUBIC
    )
    a = ndimage.gaussian_filter(np.asarray(big, dtype=float) / 255, 1.6)  # takes the stair-steps out of the shape
    a = np.clip((a - 0.30) / 0.30, 0, 1)  # then a clean, slightly anti-aliased edge
    a = a * a * (3 - 2 * a)
    out = np.full((height * SCALE, width * SCALE, 4), 255, np.uint8)
    out[..., 3] = (a * 255).astype(np.uint8)
    Image.fromarray(out, "RGBA").save(theme_dir / "ink.png")
