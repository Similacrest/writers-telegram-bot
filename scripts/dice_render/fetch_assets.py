"""Download the third-party files the dice renderer needs into ``build/dice_render/web`` and prepare the texture.

    uv run --group render python scripts/dice_render/fetch_assets.py

Each download is checked against a pinned SHA-256, so the renders stay reproducible:

- Babylon.js 5.57.1 (npm ``babylonjs``; Apache-2.0): the 3D engine the page runs.
- The "Dice of Rolling" theme by Frank Ali, from github.com/3d-dice/dice-themes (the repository is MIT; its README
  calls the models and themes CC0): the dice meshes and their texture.
- A scan of a 1992 Ukrainian 5 kopiyok coin, Wikimedia Commons "5-kopiyok-Ukraine.jpg" (tagged PD-UA-exempt: coins
  are not subject to copyright in Ukraine): the sides of the d2.
"""

import hashlib
import io
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import prep_atlas

BUILD = Path(__file__).resolve().parents[2] / "build" / "dice_render"
WEB = BUILD / "web"
THEME_COMMIT = "0e166e5cd927a1f0a0581f2460a865b134ef1d9e"
SOURCES = {
    "babylon": (
        "https://registry.npmjs.org/babylonjs/-/babylonjs-5.57.1.tgz",
        "fd62dca170652231f68a3590496eda5712441f9677b39f6e10691e8bc8c1faac",
    ),
    "theme": (
        f"https://raw.githubusercontent.com/3d-dice/dice-themes/{THEME_COMMIT}/downloads/diceOfRolling.zip",
        "55765e96e3ff4a80f89dc36b2edd1038324464758b85de88b0f06214871d7987",
    ),
    "coin": (
        "https://upload.wikimedia.org/wikipedia/commons/7/7c/5-kopiyok-Ukraine.jpg",
        "8251f0e39477c845654db9e80392ef2a9c25a596a780c9fedf6a8cdbeee62745",
    ),
}


def download(name: str) -> bytes:
    url, sha256 = SOURCES[name]
    request = urllib.request.Request(url, headers={"User-Agent": "writers-bot-dice-render/1.0"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - pinned https URLs above
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"{name}: {url} is not the file this script was written for (checksum differs)")
    return data


def main() -> None:
    theme = WEB / "theme"
    theme.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(download("babylon"))) as tar:
        member = tar.extractfile("package/babylon.js")
        if member is None:
            raise SystemExit("babylon.js is not in the npm package")
        (WEB / "babylon.js").write_bytes(member.read())
    with zipfile.ZipFile(io.BytesIO(download("theme"))) as archive:
        for name in ("diffuse.jpg", "smoothDice.json"):
            (theme / name).write_bytes(archive.read(name))
    (theme / "5kop.jpg").write_bytes(download("coin"))
    prep_atlas.build(theme)
    print("ready in", WEB)


if __name__ == "__main__":
    main()
