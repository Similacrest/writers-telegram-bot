"""Which stickers a roll gets (core/dicestickers.py): singles, pools of d4 / d6 / d8, d10 pairs as d100, duality dice and advantage."""

import itertools
import json
from importlib.resources import files

import pytest

from writers_bot.core.dicestickers import NEXT_EVEN, POOL_SIZE, SUPPORTED, choose, split


def stickers(*dice, mode=None):
    return choose(dice, mode)


def manifest_names():
    return set(json.loads(files("writers_bot.assets.dice_clips").joinpath("manifest.json").read_text()))


def test_odd_dice_take_the_next_even_one_but_the_d3_has_its_own():
    assert stickers((3, 2)) == ["d3_2"]
    assert stickers((5, 5)) == ["d6_5"]  # not a 6: no crit
    assert stickers((7, 7)) == ["d8_7"]
    assert stickers((9, 9)) == ["d10_9"]
    assert stickers((11, 11)) == ["d12_11"]
    assert stickers((2, 1)) == ["d2_1"]  # the coin
    assert stickers((3, 1)) == ["d3_1"] and stickers((3, 3)) == ["d3_3"]  # never a crit


def test_dice_without_stickers_give_none():
    assert stickers() is None
    assert stickers((13, 5)) is None
    assert stickers((20, 5), (13, 2)) is None  # one die without a sticker and the whole roll is just text
    assert stickers((1, 1)) is None
    assert stickers((30, 12)) is None


@pytest.mark.parametrize(
    ("die", "name"),
    [
        ((4, 1), "d4_1"), ((4, 4), "d4_4"), ((6, 1), "d6_1"), ((6, 6), "d6_6"), ((8, 8), "d8_8"), ((10, 10), "d10_10"),
        ((12, 1), "d12_1"), ((12, 12), "d12_12"), ((20, 1), "d20_1"), ((20, 20), "d20_20"), ((100, 100), "d100_100"),
    ],
)  # fmt: skip
def test_a_die_alone_has_its_crits(die, name):
    assert stickers(die) == [name]


def test_with_other_dice_a_single_is_crit_less_at_its_ends():
    assert stickers((20, 7), (4, 1)) == ["d20_7", "d4n_1"]
    assert stickers((20, 7), (4, 4)) == ["d20_7", "d4n_4"]
    assert stickers((20, 7), (6, 6)) == ["d20_7", "d6n_6"]
    assert stickers((20, 7), (10, 10)) == ["d20_7", "d10n_10"]
    assert stickers((20, 7), (10, 1)) == ["d20_7", "d10n_1"]
    assert stickers((20, 7), (4, 3)) == ["d20_7", "d4_3"]  # only the lowest and highest differ
    assert stickers((20, 20), (4, 4)) == ["d20n_20", "d4n_4"]
    assert stickers((20, 5), (8, 8)) == ["d20_5", "d8n_8"]
    assert stickers((100, 100), (6, 2)) == ["d100n_100", "d6_2"]
    assert stickers((12, 1), (4, 2), (20, 20)) == ["d12n_1", "d4_2", "d20n_20"]  # a d12 here is not a duality pair
    assert stickers((3, 1), (20, 4)) == ["d3_1", "d20_4"]  # a d3 never has them


def test_two_or_three_dice_of_a_kind_share_a_sticker_values_ascending():
    assert stickers((6, 5), (6, 2)) == ["2d6_2_5"]
    assert stickers((6, 5), (6, 2), (6, 6)) == ["3d6_2_5_6"]
    assert stickers((4, 4), (4, 1), (4, 1)) == ["3d4_1_1_4"]
    assert stickers((4, 3), (4, 2)) == ["2d4_2_3"]
    assert stickers((8, 8), (8, 1)) == ["2d8_1_8"]
    assert stickers((5, 5), (5, 1)) == ["2d6_1_5"]  # d5s on d6 stickers


def test_bigger_pools_are_split_into_the_fewest_even_groups():
    assert split(8, 3) == [3, 3, 2] and split(7, 3) == [3, 2, 2] and split(4, 3) == [2, 2] and split(3, 2) == [2, 1]
    assert stickers(*[(6, v) for v in (3, 1, 6, 2, 2, 5, 4, 4)]) == ["3d6_1_3_6", "3d6_2_2_5", "2d6_4_4"]  # 8d6
    assert stickers(*[(6, 1)] * 7) == ["3d6_1_1_1", "2d6_1_1", "2d6_1_1"]
    assert len(stickers(*[(4, 2)] * 9)) == 3 and len(stickers(*[(4, 2)] * 10)) == 4
    assert stickers((8, 3), (8, 1), (8, 8)) == ["2d8_1_3", "d8n_8"]  # an odd d8 is left on its own, crit-less
    assert stickers((8, 5), (8, 8), (8, 2), (8, 7)) == ["2d8_5_8", "2d8_2_7"]


def test_d10s_go_in_twos_on_the_d100_stickers():
    assert stickers((10, 7), (10, 3)) == ["d100_73"]  # a tens die 70 and a ones die 3
    assert stickers((10, 10), (10, 5)) == ["d100_5"]  # 10 is 00 on the tens die
    assert stickers((10, 4), (10, 10)) == ["d100_40"]  # and 0 on the ones die
    assert stickers((10, 10), (10, 10)) == ["d100n_100"] and stickers((10, 10), (10, 1)) == ["d100n_1"]  # no d100 crit
    assert stickers((10, 7), (10, 3), (10, 10), (10, 10), (10, 1)) == ["d100_73", "d100n_100", "d10n_1"]
    # every pair has its own picture: 100 pairs, 100 stickers
    assert len({stickers((10, a), (10, b))[0] for a in range(1, 11) for b in range(1, 11)}) == 100


def test_two_d12_are_the_duality_dice_blue_then_red():
    assert stickers((12, 9), (12, 4)) == ["2d12_9_4"]
    assert stickers((12, 4), (12, 9)) == ["2d12_4_9"]  # ordered: not the same sticker
    assert stickers((12, 4), (12, 9), (6, 6)) == ["2d12_4_9", "d6n_6"]  # an advantage d6, crit-less
    assert stickers((6, 3), (12, 4), (12, 9)) == ["d6_3", "2d12_4_9"]
    assert stickers((12, 1), (12, 5), (12, 12)) == ["d12n_1", "d12_5", "d12n_12"]  # three: not duality


def test_d12_with_advantage_or_disadvantage_uses_the_duality_pair_kept_die_first():
    assert stickers((12, 9), (12, 5), mode="adv") == ["2d12_9_5"]
    assert stickers((12, 5), (12, 9), mode="dis") == ["2d12_5_9"]


def test_two_d20_with_advantage_or_disadvantage():
    assert stickers((20, 17), (20, 5), mode="adv") == ["2d20a_5_17"]  # the dice ascending, the higher one counts
    assert stickers((20, 5), (20, 17), mode="adv") == ["2d20a_5_17"]
    assert stickers((20, 17), (20, 5), mode="dis") == ["2d20d_5_17"]
    assert stickers((20, 9), (20, 9), mode="adv") == ["2d20t_9"] and stickers((20, 9), (20, 9), mode="dis") == [
        "2d20t_9"
    ]
    assert stickers((20, 1), (20, 20), mode="adv") == ["2d20a_1_20"]


def test_two_d20_without_a_mode_are_two_crit_less_singles():
    assert stickers((20, 20), (20, 3)) == ["d20n_20", "d20_3"]
    assert stickers((20, 1), (20, 1)) == ["d20n_1", "d20n_1"]


def test_a_mode_on_other_dice_is_nothing_special():
    assert stickers((6, 2), (6, 5), mode="adv") == ["2d6_2_5"]
    assert stickers((20, 7), (20, 12), (4, 3), mode="adv") == ["d20_7", "d20_12", "d4_3"]  # not a plain pair
    assert stickers((20, 5), (20, 7), (20, 12), mode="adv") == ["d20_5", "d20_7", "d20_12"]


def test_every_sticker_it_can_choose_is_in_the_packs_and_every_sticker_in_the_packs_can_be_chosen():
    names = manifest_names()
    chosen = set()
    for sides in SUPPORTED:
        for value in range(1, sides + 1):
            chosen.update(choose([(sides, value)]))
            for other in (4, 6, 10, 12, 20):
                chosen.update(choose([(sides, value), (other, 1)]))
                chosen.update(choose([(sides, value), (other, other)]))
    for sides in (3, 12, 20, 100):  # the dice that are not pooled: singles, crit-less together
        for a, b in itertools.product(range(1, min(sides, 20) + 1), repeat=2):
            chosen.update(choose([(sides, a), (sides, b)]))
    for sides in (12, 20):
        for a, b in itertools.product(range(1, sides + 1), repeat=2):
            for mode in ("adv", "dis", None):
                chosen.update(choose([(sides, a), (sides, b)], mode))
    for sides, most in POOL_SIZE.items():
        for count in range(2, most * 4 + 1):
            for roll in itertools.combinations_with_replacement(range(1, sides + 1), min(count, 4)):
                chosen.update(choose([(sides, v) for v in roll] + [(sides, 1)] * (count - len(roll))))
    for a, b in itertools.product(range(1, 11), repeat=2):
        chosen.update(choose([(10, a), (10, b)]))
    for sides in POOL_SIZE:
        for roll in itertools.combinations_with_replacement(range(1, sides + 1), 3):
            chosen.update(choose([(sides, v) for v in roll]))
    assert chosen <= names, sorted(chosen - names)
    assert names - chosen == set(), sorted(names - chosen)  # no clip that no roll gets
    assert all(NEXT_EVEN[n] % 2 == 0 for n in NEXT_EVEN)


def test_stitched_stickers_hold_up_to_six_dice_shared_out_evenly():
    from writers_bot.core.dicestickers import dice_in, grouped

    assert (
        dice_in("d20_17") == 1 and dice_in("3d6_1_4_4") == 3 and dice_in("2d20a_5_17") == 2 and dice_in("d100_73") == 2
    )
    assert grouped([(6, 3), (6, 1), (6, 6)]) == [["3d6_1_3_6"]]  # three dice: the clip itself
    assert grouped([(6, v) for v in (3, 1, 6, 2, 2, 5)]) == [
        ["3d6_1_3_6", "3d6_2_2_5"]
    ]  # six dice: one sticker of two clips
    # more than six dice of a kind: the dice are shared out evenly, then each share is made into clips
    eight = grouped([(6, v) for v in (3, 1, 6, 2, 2, 5, 4, 4)])
    assert eight == [["2d6_1_3", "2d6_2_6"], ["2d6_2_5", "2d6_4_4"]]  # 4 + 4, not 6 + 2
    assert [sum(dice_in(n) for n in part) for part in grouped([(6, 1)] * 7)] == [4, 3]
    assert [sum(dice_in(n) for n in part) for part in grouped([(6, 1)] * 13)] == [5, 4, 4]
    assert [sum(dice_in(n) for n in part) for part in grouped([(8, 2)] * 9)] == [5, 4]
    assert grouped([(10, v) for v in range(1, 9)]) == [
        ["d100_12", "d100_34"],
        ["d100_56", "d100_78"],
    ]  # d10s as d100s, 4 + 4
    # mixed dice and singles are shared out evenly too
    assert [len(part) for part in grouped([(20, v) for v in range(1, 10)])] == [5, 4]
    assert [len(part) for part in grouped([(20, v) for v in range(1, 8)])] == [4, 3]
    assert grouped([(20, 7), (4, 2), (6, 6), (10, 3)]) == [["d20_7", "d4_2", "d6n_6", "d10_3"]]
    assert grouped([(12, 4), (12, 9), (6, 6)]) == [["2d12_4_9", "d6n_6"]]
    assert grouped([(20, 17), (20, 5)], "adv") == [["2d20a_5_17"]]
    assert grouped([(13, 5)]) is None
