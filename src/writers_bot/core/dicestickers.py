"""Which stickers a roll gets. The stickers are named as in ``assets/dice_clips/manifest.json`` (``d20_17``, ``3d6_1_4_4``,
``2d20a_5_17``; ``scripts/dice_render/clips.py`` has the grammar and makes them).

One sticker per die, except that dice of the same kind share stickers:

- **Pools.** A roll of d4s or d6s (up to three dice per sticker) or d8s (two): ``3d6_1_4_4``, the values ascending. Bigger
  rolls are split into the fewest groups, as even as can be: 8d6 is 3 + 3 + 2, so three stickers; 7d6 is 3 + 2 + 2.
- **d10s** go in twos on the d100 stickers: a pair (a, b) shows a tens die (a, 10 being 00) and a ones die (b, 10 being 0),
  so ``d100_{tens * 10 + ones}``; the 1 and 100 of it are crit-less (``d100n``). A d10 left over is a single.
- **Two d12** (also among other dice) are Daggerheart's duality dice, ordered: ``2d12_9_4`` is the blue 9 and the red 4. The larger die has its
  advantage / disadvantage halo (blue / red) unless it shows 1 or 12; a tie has both. They have no crit effects. With
  advantage or disadvantage (``adv``, ``dis``) the die that counts is the blue one.
- **Two d20 with advantage or disadvantage**: ``2d20a_5_17`` (the higher die counts, with a quiet green halo) and
  ``2d20d_5_17`` (the lower one, magenta), the dice ascending; two alike are ``2d20t_9``.

Every other die is a single sticker: a die with an odd number of sides takes the sticker of the next even one (d5 is a d6,
d11 a d12; the d3 has its own, and the d2 is the coin). A group of dice has the crit effects of the clip (the highest roll
of any die is golden, otherwise the lowest is red; with advantage, the die that counts decides). A single die alone has
them on its lowest and highest roll; with other dice not grouped with it, it has the crit-less version (``d6n_6``,
``d20n_1``; the d3 and the coin never have effects, a d12's 1 is its red fumble).
"""

from collections.abc import Sequence

NEXT_EVEN = {5: 6, 7: 8, 9: 10, 11: 12}
SUPPORTED = frozenset({2, 3, 4, 6, 8, 10, 12, 20, 100, *NEXT_EVEN})
POOL_SIZE = {4: 3, 6: 3, 8: 2}  # the most dice of a kind in one sticker
MOST_PER_STICKER = max(POOL_SIZE.values())
STITCHED_DICE = 6  # dice in one stitched sticker (a 3 x 2 grid), see ``core.stitch``


def dice_in(name: str) -> int:
    """How many dice a clip shows: ``3d6_1_4_4`` three, ``2d20a_5_17`` two, ``d100_73`` two (a tens die and a ones die), else one."""
    if name[0].isdigit():
        return int(name[0])
    return 2 if name.startswith("d100") else 1


def single(sides: int, value: int, *, alone: bool) -> str:
    """The sticker of one die; with the crit-less look for its lowest and highest unless it is rolled alone."""
    sides = NEXT_EVEN.get(sides, sides)
    if sides in (2, 3):
        return f"d{sides}_{value}"
    if not alone and value in (1, sides):
        return f"d{sides}n_{value}"
    return f"d{sides}_{value}"


def split(count: int, size: int) -> list[int]:
    """The sizes of the fewest groups of at most ``size`` dice for ``count`` dice, as even as can be, the larger first."""
    groups = -(-count // size)
    return [count // groups + (1 if i < count % groups else 0) for i in range(groups)]


def d100(first: int, second: int) -> str:
    """Two d10 as a d100: a tens die and a ones die (10 is 00 and 0), crit-less at the ends."""
    value = (first % 10) * 10 + second % 10 or 100
    return f"d100n_{value}" if value in (1, 100) else f"d100_{value}"


def duality(blue: int, red: int) -> str:
    return f"2d12_{blue}_{red}"


def d10_clips(values: list[int]) -> list[str]:
    """d10s in twos on the d100 stickers, an odd one left on its own."""
    out = [d100(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]
    return out + ([single(10, values[-1], alone=False)] if len(values) % 2 else [])


def pool_clips(sides: int, values: list[int]) -> list[str]:
    """The clips of dice of one kind (d4, d6, d8): the fewest, as even as can be, each with its values ascending."""
    out, start = [], 0
    for size in split(len(values), POOL_SIZE[sides]):
        group = sorted(values[start : start + size])
        # a lone die is left over only from an odd number of d8s
        out.append(
            f"{size}d{sides}_" + "_".join(map(str, group)) if size > 1 else single(sides, group[0], alone=False)
        )
        start += size
    return out


def balanced(names: list[str], per: int = STITCHED_DICE) -> list[list[str]]:
    """Clips in order as stickers of at most ``per`` dice, as even as can be: 9 single dice are 5 + 4, not 6 + 3."""
    weights = [dice_in(name) for name in names]
    stickers = -(-sum(weights) // per)
    target = sum(weights) / stickers
    groups: list[list[str]] = [[]]
    shown = 0
    for name, weight in zip(names, weights, strict=True):
        if groups[-1] and (shown + weight > per or (shown + weight / 2 > target and len(groups) < stickers)):
            groups.append([])
            shown = 0
        groups[-1].append(name)
        shown += weight
    return groups


def grouped(
    dice: Sequence[tuple[int, int]], mode: str | None = None, per: int = STITCHED_DICE
) -> list[list[str]] | None:
    """The stickers of a roll when clips are stitched, each a list of clips (one clip is the clip itself): up to ``per`` dice
    to a sticker, the dice shared out evenly (8d6 is 4 + 4, not 6 + 2). None when a die has no sticker."""
    names = choose(dice, mode)
    if not names:
        return None
    sides_of = {NEXT_EVEN.get(sides, sides) for sides, _ in dice}
    if len(sides_of) == 1 and len(dice) > per:
        (sides,) = sides_of
        values = [value for _, value in dice]
        if sides == 10 or sides in POOL_SIZE:  # dice of one kind: share the dice out, then make each share's clips
            shares = -(-len(values) // per)  # how many stickers, and the dice shared out evenly among them
            out, start = [], 0
            for size in [len(values) // shares + (1 if i < len(values) % shares else 0) for i in range(shares)]:
                share = values[start : start + size]
                out.append(d10_clips(share) if sides == 10 else pool_clips(sides, share))
                start += size
            return out
    return balanced(names, per)


def choose(dice: Sequence[tuple[int, int]], mode: str | None = None) -> list[str] | None:
    """The stickers for the dice of a roll, as (sides, value) in the order thrown (with advantage or disadvantage the die
    that counts first); None when a die has none."""
    if not dice or any(sides not in SUPPORTED for sides, _ in dice):
        return None
    sides_of = {NEXT_EVEN.get(sides, sides) for sides, _ in dice}
    if len(dice) == 1:
        return [single(*dice[0], alone=True)]
    if len(sides_of) > 1:  # different dice: each on its own, crit-less; two d12 among them are still the duality dice
        twelves = [i for i, (sides, _) in enumerate(dice) if NEXT_EVEN.get(sides, sides) == 12]
        pair = (twelves[0], twelves[1]) if len(twelves) == 2 else None
        out = []
        for i, (sides, value) in enumerate(dice):
            if pair and i == pair[0]:
                out.append(duality(value, dice[pair[1]][1]))
            elif not pair or i != pair[1]:
                out.append(single(sides, value, alone=False))
        return out
    (sides,) = sides_of
    values = [value for _, value in dice]
    if mode in ("adv", "dis") and len(dice) == 2 and sides == 20:
        lo, hi = sorted(values)
        return [f"2d20t_{lo}" if lo == hi else f"2d20{'a' if mode == 'adv' else 'd'}_{lo}_{hi}"]
    if sides == 12 and len(dice) == 2:  # also adv / dis: the die that counts first, so the blue one
        return [duality(*values)]
    if sides == 10:
        return d10_clips(values)
    if sides in POOL_SIZE:
        return pool_clips(sides, values)
    return [single(sides, value, alone=False) for value in values]  # d3, d12 (not two), d20, d100, the coin
