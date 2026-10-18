"""The two sticker packs of the new dice (scripts/dice_render): what is in them, and Telegram's limits."""

import json
from collections import Counter
from importlib.resources import files

import pytest

PACK_LIMIT = 120  # Telegram: stickers in a pack
SIZE_LIMIT = 256 * 1024  # Telegram: bytes of a video sticker


@pytest.fixture(scope="module")
def manifest():
    return json.loads(files("writers_bot.assets.dice_packs").joinpath("manifest.json").read_text())


def rows(manifest, pack, die, variant=""):
    return [r for r in manifest[pack] if r["die"] == die and r["variant"] == variant]


def test_two_packs_within_telegrams_limit(manifest):
    assert set(manifest) == {"pack1", "pack2"}
    assert {k: len(v) for k, v in manifest.items()} == {"pack1": PACK_LIMIT, "pack2": PACK_LIMIT}
    files_listed = [r["file"] for v in manifest.values() for r in v]
    assert len(set(files_listed)) == len(files_listed)


def test_every_sticker_is_a_webm_within_limits(manifest):
    folder = files("writers_bot.assets.dice_packs")
    for pack in manifest.values():
        for row in pack:
            data = folder.joinpath(row["file"]).read_bytes()
            assert data[:4] == b"\x1a\x45\xdf\xa3", row["file"]  # WEBM (EBML)
            assert len(data) <= SIZE_LIMIT, (row["file"], len(data))
            assert row["emoji"]


def test_pack_one_holds_d20_d12_and_d10(manifest):
    values = lambda die, variant="": sorted(r["value"] for r in rows(manifest, "pack1", die, variant))  # noqa: E731
    assert values("d20") == list(range(1, 21))
    # the higher / lower of two: no tied 1 (advantage) or 20 (disadvantage), and no crits (the plain d20 has them)
    assert values("d20", "a") == values("d20", "d") == list(range(2, 20))
    for variant in ("r", "b", "rd", "ba"):
        assert values("d12", variant) == list(range(1, 13))
    assert values("d10") == list(range(1, 11))
    assert {r["die"] for r in manifest["pack1"]} == {"d20", "d12", "d10", "d4", "d6"}


def test_crit_effects_only_where_meant(manifest):
    effects = {r["file"].split("/")[1][:-5]: r["effect"] for v in manifest.values() for r in v}
    assert effects["d20_20"] == "crit" and effects["d20_1"] == "fumble" and effects["d20_9"] is None
    assert effects["d20a_9"] == "adv" and effects["d20d_9"] == "dis"
    # a d12 for Xd12 has no crit effects (they work differently there); the red / blue pair for a lone d12 has them
    for name in ("d12r_1", "d12r_12", "d12b_1", "d12b_12"):
        assert effects[name] is None, name
    assert effects["d12rd_12"] == effects["d12ba_12"] == "crit"
    assert effects["d12rd_1"] == effects["d12ba_1"] == "fumble"
    assert effects["d12rd_5"] == "dis" and effects["d12ba_5"] == "adv"
    # the non-crit lowest and highest of the small dice; the d8 has only the crit ones
    for name in ("d4n_1", "d4n_4", "d6n_1", "d6n_6", "d10n_1", "d10n_10"):
        assert effects[name] is None, name
    assert not [n for n in effects if n.startswith("d8n")]


def test_pack_two_holds_d100_small_dice_and_the_coin(manifest):
    pack = manifest["pack2"]
    assert sorted(r["value"] for r in pack if r["die"] == "d100") == list(range(1, 101))
    assert Counter(r["die"] for r in pack if r["die"] != "d100") == {"d4": 4, "d6": 6, "d8": 8, "d2": 2}
    crit = {r["die"]: r["value"] for r in pack if r["effect"] == "crit"}
    assert crit == {"d100": 100, "d4": 4, "d6": 6, "d8": 8}
    fumbles = [r for r in pack if r["effect"] == "fumble"]
    assert {r["die"] for r in fumbles} == {"d100", "d4", "d6", "d8"} and {r["value"] for r in fumbles} == {1}


def test_emoji_and_keywords_are_within_telegrams_limits(manifest):
    for row in (r for pack in manifest.values() for r in pack):
        assert 1 <= len(row["emoji"]) <= 20, row["file"]
        assert len(row["keywords"]) <= 20 and sum(len(k) for k in row["keywords"]) <= 64, row["file"]
        assert row["keywords"][:2] == [row["die"], str(row["value"])], row[
            "file"
        ]  # "d20", "17": what the search finds
    # the first emoji is all the bot can read back from a sticker: it is the die's marker, and the same for a whole die
    markers = {}
    for row in (r for pack in manifest.values() for r in pack):
        markers.setdefault(row["die"], set()).add(row["emoji"][0])
    assert all(len(m) == 1 for m in markers.values()), markers
    assert len({next(iter(m)) for m in markers.values()}) == len(markers)  # and no two dice share one


def test_the_packs_are_in_order_by_die_with_the_crits_at_the_ends(manifest):
    names = {key: [r["file"].split("/")[1].removesuffix(".webm") for r in rows] for key, rows in manifest.items()}
    one = names["pack1"]
    assert one[:2] == ["d20_1", "d20_2"] and one[19] == "d20_20" and one[20] == "d20a_2"
    blue = one[one.index("d12ba_1") :]
    # crit 1 < crit-less 1 < 2 ... 11 (plain before halo) < crit-less 12 < crit 12; then the red die the same way
    assert blue[:5] == ["d12ba_1", "d12b_1", "d12b_2", "d12ba_2", "d12b_3"]
    assert one[one.index("d12ba_12") - 1 : one.index("d12ba_12") + 2] == ["d12b_12", "d12ba_12", "d12rd_1"]
    assert one[one.index("d12rd_1") : one.index("d12rd_1") + 3] == ["d12rd_1", "d12r_1", "d12r_2"]
    assert one[one.index("d12r_12") : one.index("d12r_12") + 3] == ["d12r_12", "d12rd_12", "d10_1"]
    assert one[one.index("d10_1") : one.index("d10_1") + 4] == ["d10_1", "d10n_1", "d10_2", "d10_3"]
    assert one[one.index("d10_9") :] == ["d10_9", "d10n_10", "d10_10", "d4n_1", "d4n_4", "d6n_1", "d6n_6"]
    two = names["pack2"]
    assert two[0] == "d100_1" and two[99] == "d100_100" and two[100:] == [
        *[f"d4_{v}" for v in range(1, 5)], *[f"d6_{v}" for v in range(1, 7)], *[f"d8_{v}" for v in range(1, 9)], "d2_1", "d2_2",
    ]  # fmt: skip


def test_credits_travel_with_the_stickers():
    text = files("writers_bot.assets.dice_packs").joinpath("CREDITS.md").read_text()
    assert "Frank Ali" in text and "PD-UA-exempt" in text and "Babylon.js" in text
