"""Which sticker a die gets (core/dicestickers.py): the rules for odd dice, crits, advantage and Daggerheart's duality dice."""

import itertools
import json
from importlib.resources import files

import pytest

from writers_bot.core.dicestickers import NEXT_EVEN, SUPPORTED, choose


def stickers(*dice, mode=None):
    return choose(dice, mode)


def manifest_names():
    data = json.loads(files("writers_bot.assets.dice_packs").joinpath("manifest.json").read_text())
    return {row["file"].split("/")[1].removesuffix(".webm") for rows in data.values() for row in rows}


def test_odd_dice_take_the_next_even_one():
    assert stickers((3, 2)) == ["d4_2"]
    assert stickers((5, 5)) == ["d6_5"]
    assert stickers((7, 7)) == ["d8_7"]
    assert stickers((9, 9)) == ["d10_9"]
    assert stickers((11, 11)) == ["d12b_11"]  # alone, so a d12 as in a lone d12 roll
    assert stickers((2, 1)) == ["d2_1"]  # the coin
    assert stickers((3, 1)) == ["d4_1"]


def test_dice_without_stickers_give_none():
    assert stickers() is None
    assert stickers((13, 5)) is None
    assert stickers((20, 5), (13, 2)) is None  # one die without a sticker and the whole roll is just text
    assert stickers((1, 1)) is None
    assert stickers((30, 12)) is None


@pytest.mark.parametrize(
    ("die", "name"),
    [((4, 1), "d4_1"), ((4, 4), "d4_4"), ((6, 1), "d6_1"), ((6, 6), "d6_6"), ((10, 1), "d10_1"), ((10, 10), "d10_10")],
)
def test_d4_d6_d10_alone_have_crits(die, name):
    assert stickers(die) == [name]


def test_the_same_kind_of_dice_keep_their_crits():
    assert stickers((6, 1), (6, 6)) == ["d6_1", "d6_6"]


def test_as_an_addition_to_other_dice_there_are_no_crits():
    assert stickers((20, 7), (4, 1)) == ["d20_7", "d4n_1"]
    assert stickers((20, 7), (4, 4)) == ["d20_7", "d4n_4"]
    assert stickers((20, 7), (6, 6)) == ["d20_7", "d6n_6"]
    assert stickers((20, 7), (10, 10)) == ["d20_7", "d10n_10"]
    assert stickers((20, 7), (10, 1)) == ["d20_7", "d10n_1"]
    assert stickers((20, 7), (4, 3)) == ["d20_7", "d4_3"]  # only the lowest and highest differ
    assert stickers((3, 1), (20, 4)) == ["d4n_1", "d20_4"]  # a d3 is a d4 here too


def test_the_d8_d20_and_d100_always_have_their_crits():
    assert stickers((20, 5), (8, 8)) == ["d20_5", "d8_8"]
    assert stickers((8, 1), (4, 2)) == ["d8_1", "d4_2"]
    assert stickers((20, 20)) == ["d20_20"] and stickers((20, 1)) == ["d20_1"]
    assert stickers((100, 100)) == ["d100_100"]
    assert stickers((20, 20), (4, 4)) == ["d20_20", "d4n_4"]


def test_d20_with_advantage_the_larger_has_the_advantage_look():
    assert stickers((20, 7), (20, 12), mode="adv") == ["d20_7", "d20a_12"]
    assert stickers((20, 12), (20, 7), mode="adv") == ["d20a_12", "d20_7"]  # in the order thrown
    assert stickers((20, 20), (20, 3), mode="adv") == ["d20_20", "d20_3"]  # a 20 is the plain crit
    assert stickers((20, 1), (20, 5), mode="adv") == ["d20_1", "d20a_5"]


def test_d20_with_disadvantage_the_smaller_has_the_disadvantage_look():
    assert stickers((20, 7), (20, 12), mode="dis") == ["d20d_7", "d20_12"]
    assert stickers((20, 1), (20, 5), mode="dis") == ["d20_1", "d20_5"]  # a 1 is the plain crit
    assert stickers((20, 20), (20, 3), mode="dis") == ["d20_20", "d20d_3"]


def test_a_tie_is_plain():
    assert stickers((20, 9), (20, 9), mode="adv") == ["d20_9", "d20_9"]
    assert stickers((20, 9), (20, 9), mode="dis") == ["d20_9", "d20_9"]
    assert stickers((20, 20), (20, 20), mode="adv") == ["d20_20", "d20_20"]


def test_d12_with_advantage_or_disadvantage_works_the_same():
    assert stickers((12, 5), (12, 9), mode="adv") == ["d12r_5", "d12ba_9"]  # the other die is a plain d12
    assert stickers((12, 5), (12, 9), mode="dis") == ["d12rd_5", "d12b_9"]
    assert stickers((12, 12), (12, 3), mode="adv") == ["d12ba_12", "d12r_3"]
    assert stickers((12, 1), (12, 8), mode="dis") == ["d12rd_1", "d12b_8"]
    assert stickers((12, 7), (12, 7), mode="adv") == ["d12b_7", "d12b_7"]


def test_a_mode_on_other_dice_is_nothing_special():
    assert stickers((6, 2), (6, 5), mode="adv") == ["d6_2", "d6_5"]
    assert stickers((20, 7), (20, 12), (4, 3), mode="adv") == ["d20_7", "d20_12", "d4_3"]  # not a plain pair


def test_a_lone_d12_is_red_below_and_blue_above_with_the_crits_from_the_halo_sets():
    assert [stickers((12, v))[0] for v in (1, 2, 6, 7, 11, 12)] == [
        "d12rd_1", "d12r_2", "d12r_6", "d12b_7", "d12b_11", "d12ba_12",
    ]  # fmt: skip


def test_more_d12_than_two_or_with_other_dice_have_no_crit_effects():
    assert stickers((12, 1), (12, 5), (12, 12)) == ["d12r_1", "d12r_5", "d12b_12"]
    assert stickers((20, 8), (12, 12)) == ["d20_8", "d12b_12"]
    assert stickers((12, 1), (4, 2)) == ["d12r_1", "d4_2"]


def test_duality_dice_blue_larger():
    assert stickers((12, 7), (12, 3)) == ["d12ba_7", "d12r_3"]


def test_duality_dice_red_larger():
    assert stickers((12, 3), (12, 9)) == ["d12b_3", "d12rd_9"]


def test_duality_dice_tie_both_have_the_look_of_their_set():
    assert stickers((12, 5), (12, 5)) == ["d12ba_5", "d12rd_5"]


def test_duality_dice_showing_1_or_12_are_crit_less():
    assert stickers((12, 12), (12, 4)) == ["d12b_12", "d12r_4"]  # the larger one is a 12: crit-less
    assert stickers((12, 6), (12, 1)) == ["d12ba_6", "d12r_1"]
    assert stickers((12, 1), (12, 1)) == ["d12b_1", "d12r_1"]
    assert stickers((12, 12), (12, 12)) == ["d12b_12", "d12r_12"]
    assert stickers((12, 1), (12, 12)) == ["d12b_1", "d12r_12"]


def test_duality_dice_with_another_die_for_the_rest():
    assert stickers((12, 4), (12, 9), (6, 6)) == ["d12b_4", "d12rd_9", "d6n_6"]  # an advantage d6 has no crit effect
    assert stickers((12, 4), (12, 9), (6, 3)) == ["d12b_4", "d12rd_9", "d6_3"]


def test_every_sticker_it_can_choose_is_in_the_packs():
    names = manifest_names()
    chosen = set()
    for sides in SUPPORTED:
        for value in range(1, sides + 1):
            chosen.update(choose([(sides, value)]) or [])
            for other in (4, 6, 10, 12, 20):
                chosen.update(choose([(sides, value), (other, 1)]) or [])
                chosen.update(choose([(sides, value), (other, other)]) or [])
    for sides in (12, 20):
        for a, b in itertools.product(range(1, sides + 1), repeat=2):
            for mode in ("adv", "dis", None):
                chosen.update(choose([(sides, a), (sides, b)], mode))
    for a, b, c in itertools.product(range(1, 13), repeat=3):
        chosen.update(choose([(12, a), (12, b), (12, c)]))
        chosen.update(choose([(12, a), (12, b), (6, c % 6 + 1)]))
    assert chosen <= names, sorted(chosen - names)
    assert all(NEXT_EVEN[n] % 2 == 0 for n in NEXT_EVEN)
