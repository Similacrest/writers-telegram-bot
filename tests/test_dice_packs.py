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
