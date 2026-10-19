"""The sticker packs of the dice (scripts/dice_render): what is in them, their layout, and Telegram's limits."""

import json
from collections import Counter
from importlib.resources import files

import pytest

PACK_LIMIT = 120  # Telegram: stickers in a pack
SIZE_LIMIT = 256 * 1024  # Telegram: bytes of a video sticker
LAYOUT = {
    "pack1": 107,
    "pack2": 113,
    "pack3": 102,
    "pack4": 120,
    "pack5": 114,
    "pack6": 120,
    "pack7": 120,
    "pack8": 70,
}


@pytest.fixture(scope="module")
def manifest():
    return json.loads(files("writers_bot.assets.dice_packs").joinpath("manifest.json").read_text())


@pytest.fixture(scope="module")
def names(manifest):
    return {key: [r["file"].split("/")[1].removesuffix(".webm") for r in rows] for key, rows in manifest.items()}


@pytest.fixture(scope="module")
def effects(manifest):
    return {r["file"].split("/")[1].removesuffix(".webm"): r["effect"] for rows in manifest.values() for r in rows}


def test_eight_packs_within_telegrams_limit(manifest):
    assert {k: len(v) for k, v in manifest.items()} == LAYOUT
    assert all(n <= PACK_LIMIT for n in LAYOUT.values())
    listed = [r["file"] for v in manifest.values() for r in v]
    assert len(set(listed)) == len(listed) == 866


def test_every_sticker_is_a_webm_within_limits(manifest):
    folder = files("writers_bot.assets.dice_packs")
    for pack in manifest.values():
        for row in pack:
            data = folder.joinpath(row["file"]).read_bytes()
            assert data[:4] == b"\x1a\x45\xdf\xa3", row["file"]  # WEBM (EBML)
            assert len(data) <= SIZE_LIMIT, (row["file"], len(data))
            assert row["emoji"]


def test_the_singles_of_each_die(manifest):
    singles = Counter(r["die"] for rows in manifest.values() for r in rows if not r["die"][0].isdigit())
    assert singles == {
        "d2": 2, "d3": 3, "d4": 6, "d6": 8, "d8": 10, "d10": 12, "d12": 14, "d20": 22, "d100": 102,
    }  # fmt: skip
    values = lambda die, variant: sorted(  # noqa: E731
        r["value"] for rows in manifest.values() for r in rows if r["die"] == die and r["variant"] == variant
    )
    assert values("d20", "") == list(range(1, 21)) and values("d20", "n") == [1, 20]
    assert values("d12", "") == list(range(1, 13)) and values("d12", "n") == [1, 12]
    assert values("d100", "") == list(range(1, 101)) and values("d100", "n") == [1, 100]
    assert values("d3", "") == [1, 2, 3]
    for die in ("d4", "d6", "d8", "d10"):
        assert values(die, "n") == [1, int(die[1:])]


def test_the_groups_of_dice(manifest):
    groups = Counter(r["die"] for rows in manifest.values() for r in rows if r["die"][0].isdigit())
    assert groups == {"2d4": 10, "3d4": 20, "2d6": 21, "3d6": 56, "2d8": 36, "2d12": 144, "2d20": 400}
    twenty = Counter(r["variant"] for rows in manifest.values() for r in rows if r["die"] == "2d20")
    assert twenty == {"a": 190, "d": 190, "t": 20}
    for rows in manifest.values():
        for r in rows:
            if r["die"] in ("2d4", "3d4", "2d6", "3d6", "2d8"):
                assert r["value"] == sorted(r["value"]), r["file"]  # unordered: always ascending


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


def test_emoji_and_keywords_are_within_telegrams_limits(manifest):
    for row in (r for pack in manifest.values() for r in pack):
        assert 1 <= len(row["emoji"]) <= 20, row["file"]
        assert len(row["keywords"]) <= 20 and sum(len(k) for k in row["keywords"]) <= 64, row["file"]
        first = row["value"] if isinstance(row["value"], int) else row["value"][0]
        assert row["keywords"][:2] == [row["die"], str(first)], row["file"]  # "d20", "17": what the search finds
    # the first emoji is all the bot can read back from a sticker: it is the die's marker, the same for a die's
    # singles and its groups, and no two dice share one
    markers = {}
    for row in (r for pack in manifest.values() for r in pack):
        markers.setdefault(row["die"].lstrip("23"), set()).add(row["emoji"][0])
    assert all(len(m) == 1 for m in markers.values()), markers
    assert len({next(iter(m)) for m in markers.values()}) == len(markers)


def test_the_packs_are_in_order_by_die_with_the_crits_at_the_ends(names):
    one = names["pack1"]
    assert one[:7] == ["d2_1", "d2_2", "d3_1", "d3_2", "d3_3", "d4_1", "d4n_1"]
    assert one[one.index("d20_1") : one.index("d20_1") + 4] == ["d20_1", "d20n_1", "d20_2", "d20_3"]
    assert one[one.index("d20_19") :][:4] == ["d20_19", "d20n_20", "d20_20", "2d4_1_1"]
    assert one[-1] == "3d4_4_4_4" and one.index("2d4_1_1") == 77
    two = names["pack2"]
    assert two[0] == "2d6_1_1" and two[21] == "3d6_1_1_1" and two[77] == "2d8_1_1" and two[-1] == "2d8_8_8"
    three = names["pack3"]
    assert three[:3] == ["d100_1", "d100n_1", "d100_2"] and three[-3:] == ["d100_99", "d100n_100", "d100_100"]
    assert (
        names["pack4"][0] == "2d12_1_1" and names["pack4"][-1] == "2d12_10_12"
    )  # the blue die 1-10, the red die 1-12
    five = names["pack5"]
    assert five[0] == "2d12_11_1" and five[24] == "2d20t_1" and five[44] == "2d20a_1_2" and five[-1] == "2d20a_4_20"
    assert names["pack6"][0] == "2d20a_5_6" and names["pack6"][-1] == "2d20a_19_20"
    assert names["pack7"][0] == "2d20d_1_2" and names["pack8"][-1] == "2d20d_19_20"


def test_credits_travel_with_the_stickers():
    text = files("writers_bot.assets.dice_packs").joinpath("CREDITS.md").read_text()
    assert "Frank Ali" in text and "PD-UA-exempt" in text and "Babylon.js" in text
