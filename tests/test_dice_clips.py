"""The dice clips (scripts/dice_render): what is in the catalogue, and Telegram's limits for a video sticker."""

import json
from collections import Counter
from importlib.resources import files

import pytest

SIZE_LIMIT = 256 * 1024  # Telegram: bytes of a video sticker
FOLDER = files("writers_bot.assets.dice_clips")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(FOLDER.joinpath("manifest.json").read_text())


@pytest.fixture(scope="module")
def effects(manifest):
    return {name: row["effect"] for name, row in manifest.items()}


def test_the_catalogue_has_866_clips_each_with_a_file_and_nothing_else_in_the_folder(manifest):
    assert len(manifest) == 866
    on_disk = {p.name for p in FOLDER.iterdir() if p.name.endswith(".webm")}
    assert on_disk == {f"{name}.webm" for name in manifest}
    assert all(set(row) == {"die", "variant", "value", "effect"} for row in manifest.values())  # no emoji, no keywords


def test_every_clip_is_a_webm_within_the_limits_of_a_video_sticker(manifest):
    for name in manifest:
        data = FOLDER.joinpath(f"{name}.webm").read_bytes()
        assert data[:4] == b"\x1a\x45\xdf\xa3", name  # WEBM (EBML)
        assert len(data) <= SIZE_LIMIT, (name, len(data))


def test_the_singles_of_each_die(manifest):
    singles = Counter(row["die"] for row in manifest.values() if not row["die"][0].isdigit())
    assert singles == {
        "d2": 2, "d3": 3, "d4": 6, "d6": 8, "d8": 10, "d10": 12, "d12": 14, "d20": 22, "d100": 102,
    }  # fmt: skip
    values = lambda die, variant: sorted(  # noqa: E731
        row["value"] for row in manifest.values() if row["die"] == die and row["variant"] == variant
    )
    assert values("d20", "") == list(range(1, 21)) and values("d20", "n") == [1, 20]
    assert values("d12", "") == list(range(1, 13)) and values("d12", "n") == [1, 12]
    assert values("d100", "") == list(range(1, 101)) and values("d100", "n") == [1, 100]
    assert values("d3", "") == [1, 2, 3]
    for die in ("d4", "d6", "d8", "d10"):
        assert values(die, "n") == [1, int(die[1:])]


def test_the_groups_of_dice(manifest):
    groups = Counter(row["die"] for row in manifest.values() if row["die"][0].isdigit())
    assert groups == {"2d4": 10, "3d4": 20, "2d6": 21, "3d6": 56, "2d8": 36, "2d12": 144, "2d20": 400}
    twenty = Counter(row["variant"] for row in manifest.values() if row["die"] == "2d20")
    assert twenty == {"a": 190, "d": 190, "t": 20}
    for row in manifest.values():
        if row["die"] in ("2d4", "3d4", "2d6", "3d6", "2d8"):
            assert row["value"] == sorted(row["value"])  # unordered: always ascending


def test_effects_only_where_meant(effects):
    assert effects["d20_20"] == "crit" and effects["d20_1"] == "fumble" and effects["d20_9"] is None
    assert effects["d20n_20"] is None and effects["d20n_1"] is None
    assert effects["d12_1"] == "fumble" and effects["d12_12"] == "crit" and effects["d12n_1"] is None
    assert [effects[f"d3_{v}"] for v in (1, 2, 3)] == [None] * 3 and effects["d2_1"] is None
    assert not [n for n in effects if n.startswith("2d12") and effects[n]]  # Daggerheart's crits work differently
    # a group of dice: the highest roll of any die is a crit, else the lowest a fumble
    assert effects["3d6_1_4_6"] == "crit" and effects["3d6_1_4_5"] == "fumble" and effects["3d6_2_4_5"] is None
    assert effects["2d4_1_4"] == "crit" and effects["2d8_8_8"] == "crit" and effects["2d6_1_1"] == "fumble"
    # with advantage the higher die counts, with disadvantage the lower one
    assert effects["2d20a_5_20"] == "crit" and effects["2d20a_1_19"] is None and effects["2d20a_5_17"] is None
    assert effects["2d20d_1_7"] == "fumble" and effects["2d20d_5_20"] is None
    assert effects["2d20t_20"] == "crit" and effects["2d20t_1"] == "fumble" and effects["2d20t_9"] is None


def test_credits_travel_with_the_clips():
    text = FOLDER.joinpath("CREDITS.md").read_text()
    assert "Frank Ali" in text and "PD-UA-exempt" in text and "Babylon.js" in text
