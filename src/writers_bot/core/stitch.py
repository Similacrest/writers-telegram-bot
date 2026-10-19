"""Stitch dice sticker clips into one sticker with ffmpeg: any set of dice in a single picture.

The clips (``assets/dice_packs``) are transparent VP9 videos of one die, a pair or a triple, each 512 px, 2.9 s. A roll that needs
several stickers (``d20+d4``, 8d6, six mixed dice) gets one instead, six dice at most, more dice more stickers: its clips are scaled down, put on a grid in the order thrown and
overlaid with alpha, each landing a few frames after the one before (dice never land together). The result is encoded to Telegram's
limits for a video sticker (512 px, at most 3 s, at most 256 KB), and takes a few seconds of one core.

ffmpeg must decode VP9 with alpha, which only libvpx does (``-c:v libvpx-vp9`` before each input); the static binary of the
``imageio-ffmpeg`` package has it.
"""

import asyncio
import contextlib
import hashlib
import logging
import math
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

SIZE = 512
FPS = 30
FRAMES = 87  # 2.9 s: under Telegram's 3 s
MAX_BYTES = 256 * 1024
CRFS = (40, 46, 52, 58)  # tried in turn until the sticker fits
MAX_CLIPS = 6  # clips in one sticker: with 3 dice at most in a clip of a pool, readable on a grid of 3 x 2 (see dicestickers.group)
STAGGER = 3  # frames between one clip landing and the next
MAX_DELAY = 15
TIMEOUT = 90  # seconds for one ffmpeg run


def ffmpeg_path(configured: str = "") -> str | None:
    """The ffmpeg to use: the one configured, the ``imageio-ffmpeg`` package's, or one on the PATH."""
    if configured:
        return configured
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return shutil.which("ffmpeg")


def key_of(names: list[str]) -> str:
    """The name of a stitched sticker: its clips in the order thrown (a hash when that is long)."""
    key = "+".join(names)
    return key if len(key) <= 60 else "st-" + hashlib.sha1(key.encode()).hexdigest()[:20]  # noqa: S324 - a name, not security


def layout(count: int) -> list[tuple[int, int, int]]:
    """Where each of ``count`` clips goes: (x, y, size) of its square, in pixels of the 512 px sticker.

    A grid with as many columns as the square root asks for (3 x 2 for six); a clip's picture has a margin, so its square is bigger than its cell.
    """
    columns = math.ceil(math.sqrt(count))
    if count == 2:
        columns = 2
    rows = math.ceil(count / columns)
    cell_w, cell_h = SIZE / columns, SIZE / rows
    size = round(min(cell_w, cell_h) * 1.35)
    out = []
    for i in range(count):
        row, col = divmod(i, columns)
        in_row = columns if row < rows - 1 else count - columns * (rows - 1)  # the last row is centred
        x = (col + 0.5 + (columns - in_row) / 2) * cell_w - size / 2
        y = (row + 0.5) * cell_h - size / 2
        out.append((round(x), round(y), size))
    return out


def feather(path: Path) -> Path:
    """The soft mask every clip is multiplied by (opaque in the middle, fading to nothing at the edges), made once: the glow
    of a crit runs to the edge of its clip, and a clip scaled down would show a square."""
    if not path.exists():
        from PIL import Image, ImageOps

        edge = ImageOps.invert(Image.radial_gradient("L").resize((SIZE, SIZE), Image.Resampling.BICUBIC))
        edge.point(lambda v: min(255, v * 3 // 2)).save(path)
    return path


def command(ffmpeg: str, inputs: list[Path], mask: Path, out: Path, crf: int) -> list[str]:
    """The ffmpeg command line that stitches ``inputs`` into ``out``; ``mask`` is the feathering picture."""
    count = len(inputs)
    boxes = layout(count)
    args = [ffmpeg, "-y", "-loglevel", "error"]
    for clip in inputs:
        args += ["-c:v", "libvpx-vp9", "-i", str(clip)]  # libvpx decodes the alpha channel
    args += ["-loop", "1", "-framerate", str(FPS), "-i", str(mask)]
    splits = "".join(f"[k{i}]" for i in range(count))
    parts = [f"[{count}:v]format=gray,split={count}{splits}"]
    for i, (_, _, size) in enumerate(boxes):
        delay = min(i * STAGGER, MAX_DELAY)
        pad = f",tpad=start={delay}:start_mode=clone" if delay else ""
        parts += [
            f"[{i}:v]format=yuva420p,scale={size}:{size},split[v{i}][a{i}]",
            f"[a{i}]alphaextract[x{i}]",
            f"[k{i}]scale={size}:{size},format=gray[m{i}]",
            f"[x{i}][m{i}]blend=all_mode=multiply:shortest=1[y{i}]",
            f"[v{i}][y{i}]alphamerge{pad}[c{i}]",
        ]
    parts.append(f"color=c=black@0:s={SIZE}x{SIZE}:r={FPS},format=yuva420p[base]")
    last = "base"
    for i, (x, y, _) in enumerate(boxes):
        parts.append(f"[{last}][c{i}]overlay=x={x}:y={y}:format=auto:shortest=1[o{i}]")
        last = f"o{i}"
    parts.append(f"[{last}]trim=end_frame={FRAMES},format=yuva420p[out]")
    args += ["-filter_complex", ";".join(parts), "-map", "[out]", "-frames:v", str(FRAMES)]
    args += [
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf),
        "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-an", str(out),
    ]  # fmt: skip
    return args


class StitchError(RuntimeError):
    pass


async def stitch(ffmpeg: str, inputs: list[Path], out: Path) -> int:
    """Stitch the clips into ``out`` (a WEBM sticker); its size in bytes. Raises StitchError."""
    if not 2 <= len(inputs) <= MAX_CLIPS:
        raise StitchError(f"{len(inputs)} clips")
    out.parent.mkdir(parents=True, exist_ok=True)
    mask = feather(out.parent / "feather.png")
    tmp = out.with_suffix(".part.webm")
    try:
        for crf in CRFS:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command(ffmpeg, inputs, mask, tmp, crf),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as e:  # no such program, not allowed to run it
                raise StitchError(f"cannot run {ffmpeg}: {e}") from None
            try:
                _, error = await asyncio.wait_for(process.communicate(), TIMEOUT)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise StitchError("ffmpeg timed out") from None
            if process.returncode != 0:
                raise StitchError(error.decode(errors="replace")[-300:] or f"ffmpeg exited {process.returncode}")
            size = await asyncio.to_thread(lambda: tmp.stat().st_size)
            if size <= MAX_BYTES:
                await asyncio.to_thread(tmp.replace, out)
                return size
        raise StitchError(f"over {MAX_BYTES} bytes even at crf {CRFS[-1]}")
    finally:
        await asyncio.to_thread(tmp.unlink, missing_ok=True)


class Stitcher:
    """Makes stitched stickers on demand into a folder, one at a time per sticker and a few at once, and forgets the oldest files."""

    def __init__(self, ffmpeg: str, folder: Path, *, at_once: int = 2, keep: int = 400):
        self.ffmpeg = ffmpeg
        self.folder = folder
        self.keep = keep
        self._slots = asyncio.Semaphore(at_once)
        self._busy: dict[str, asyncio.Future] = {}

    def path(self, key: str) -> Path:
        return self.folder / f"{key}.webm"

    async def make(self, key: str, inputs: list[Path]) -> Path:
        """The file of a stitched sticker, made if it is not there; the same sticker asked for twice at once is made once."""
        out = self.path(key)
        if await asyncio.to_thread(out.exists):
            return out
        if key in self._busy:
            return await asyncio.shield(self._busy[key])
        future = asyncio.get_running_loop().create_future()
        self._busy[key] = future
        try:
            async with self._slots:
                await stitch(self.ffmpeg, inputs, out)
            self._tidy()
            future.set_result(out)
            return out
        except BaseException as e:
            future.set_exception(e if isinstance(e, Exception) else StitchError("cancelled"))
            future.exception()  # nobody else may be waiting: do not warn about it
            raise
        finally:
            self._busy.pop(key, None)

    def _tidy(self) -> None:
        files = sorted(self.folder.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        for old in files[: max(0, len(files) - self.keep)]:
            with contextlib.suppress(OSError):
                old.unlink()
