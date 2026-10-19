# ruff: noqa: S603 - the tests run ffmpeg with arguments of their own
"""Stitching dice clips into one sticker (core/stitch.py): the layout, the command, and a real run on small clips."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from writers_bot.core import stitch


def test_layouts_keep_every_clip_inside_the_sticker_and_apart():
    for count in range(2, stitch.MAX_CLIPS + 1):
        boxes = stitch.layout(count)
        assert len(boxes) == len(set(boxes)) == count
        for x, y, size in boxes:
            # a clip's picture has a margin: the die (the middle 60%) is what must be inside the sticker
            assert x + size * 0.2 >= 0 and y + size * 0.2 >= 0
            assert x + size * 0.8 <= stitch.SIZE and y + size * 0.8 <= stitch.SIZE
        centres = [(x + size / 2, y + size / 2) for x, y, size in boxes]
        assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) > 60 for i, a in enumerate(centres) for b in centres[:i])
    two = stitch.layout(2)
    assert two[0][1] == two[1][1] and two[0][0] < two[1][0]  # side by side
    five = stitch.layout(5)
    assert five[3][1] == five[4][1] > five[0][1]  # three above, two below, the last row centred
    assert abs((five[3][0] + five[4][0] + five[4][2]) / 2 - stitch.SIZE / 2) < 40


def test_the_key_is_the_clips_in_order_and_a_hash_when_long():
    assert stitch.key_of(["d20_17", "d4n_1"]) == "d20_17+d4n_1"
    assert stitch.key_of(["d20_17", "d4n_1"]) != stitch.key_of(["d4n_1", "d20_17"])
    long = stitch.key_of(["3d6_1_4_6"] * 12)
    assert long.startswith("st-") and len(long) < 30 and long == stitch.key_of(["3d6_1_4_6"] * 12)


def test_the_command_decodes_alpha_staggers_the_landings_and_stays_under_three_seconds(tmp_path):
    inputs = [tmp_path / f"{i}.webm" for i in range(3)]
    args = stitch.command("ffmpeg", inputs, tmp_path / "mask.png", tmp_path / "out.webm", 40)
    assert args.count("libvpx-vp9") == 3 + 1  # every input and the output; the mask is a picture
    graph = args[args.index("-filter_complex") + 1]
    assert (
        "tpad=start=3" in graph and "tpad=start=6" in graph and "tpad=start=0" not in graph
    )  # each later than the one before
    assert f"trim=end_frame={stitch.FRAMES}" in graph and "alphamerge" in graph and "overlay" in graph
    assert args[-1] == str(tmp_path / "out.webm") and "yuva420p" in args


ffmpeg = stitch.ffmpeg_path()
needs_ffmpeg = pytest.mark.skipif(ffmpeg is None, reason="no ffmpeg")


def make_clip(path: Path, colour: str) -> None:
    """A tiny transparent VP9 clip like the dice clips: a coloured disc on nothing, 512 px, 2.9 s."""
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={colour}:s=200x200:r=30:d=2.9,format=yuva420p",
         "-vf", "pad=512:512:156:156:color=black@0,format=yuva420p", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-b:v", "0", "-crf", "45", "-deadline", "realtime", "-an", str(path)],
        check=True,
    )  # fmt: skip


def probe(path: Path) -> str:
    return subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, check=False
    ).stderr


@needs_ffmpeg
async def test_stitching_makes_a_video_sticker_within_telegrams_limits(tmp_path):
    clips = []
    for i, colour in enumerate(("red", "green", "blue")):
        clips.append(tmp_path / f"{colour}.webm")
        make_clip(clips[i], colour)
    out = tmp_path / "out" / "r+g+b.webm"
    size = await stitch.stitch(ffmpeg, clips, out)
    info = probe(out)
    assert 0 < size <= stitch.MAX_BYTES and "512x512" in info and "00:00:02.9" in info and "vp9" in info


@needs_ffmpeg
async def test_the_stitcher_makes_a_sticker_once_and_keeps_the_newest_files(tmp_path):
    clips = {}
    for colour in ("red", "green"):
        clips[colour] = tmp_path / f"{colour}.webm"
        make_clip(clips[colour], colour)
    stitcher = stitch.Stitcher(ffmpeg, tmp_path / "stitched", at_once=1, keep=2)
    first, second = await asyncio.gather(
        stitcher.make("a", [clips["red"], clips["green"]]), stitcher.make("a", [clips["red"], clips["green"]])
    )  # the same sticker asked for twice at once is made once
    assert first == second == stitcher.path("a") and first.exists()
    before = first.stat().st_mtime_ns
    assert await stitcher.make("a", [clips["red"], clips["green"]]) == first and first.stat().st_mtime_ns == before
    for key in "bcd":
        await stitcher.make(key, [clips["green"], clips["red"]])
    assert sorted(p.stem for p in (tmp_path / "stitched").glob("*.webm")) == ["c", "d"]  # only the newest two are kept


async def test_a_clip_count_outside_two_to_twelve_or_a_bad_ffmpeg_is_an_error(tmp_path):
    with pytest.raises(stitch.StitchError):
        await stitch.stitch("ffmpeg", [tmp_path / "a.webm"], tmp_path / "o.webm")
    with pytest.raises(stitch.StitchError):
        await stitch.stitch("ffmpeg", [tmp_path / f"{i}.webm" for i in range(13)], tmp_path / "o.webm")
    if ffmpeg:
        with pytest.raises(stitch.StitchError):  # the clips do not exist
            await stitch.stitch(ffmpeg, [tmp_path / "nope1.webm", tmp_path / "nope2.webm"], tmp_path / "o.webm")
