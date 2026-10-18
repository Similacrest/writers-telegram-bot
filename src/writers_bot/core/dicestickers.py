"""Which sticker each die of a roll gets. The stickers are named as in ``assets/dice_packs/manifest.json``
(``d20_17``, ``d12ba_5``, ``d4n_1``; ``scripts/dice_render`` makes them).

- A die with an odd number of sides takes the sticker of the next even one (d3 is a d4, d11 a d12); d2 is the coin.
- The highest and lowest rolls of a d4, d6, d10, d12 and d20 have crit effects (the d8 and d100 always do). A d4, d6 or
  d10 rolled together with dice of another kind has the crit-less version of its lowest and highest; so has a d12 that
  is not alone.
- A d20 or d12 rolled with advantage (``adv``) or disadvantage (``dis``): the die that counts has its own look
  (the larger one of an advantage, the smaller one of a disadvantage); the other die and a tie are plain.
- Two d12 are Daggerheart's duality dice: the first is blue, the second red. The blue one is larger: it is from the
  blue advantage set. The red one is larger: it is from the red disadvantage set. A tie: both are. A die showing
  1 or 12 is crit-less. Crits work differently there, so the crit effects stay off.
- A d12 is blue (each die type has its own colour) except for its red 1; on its own the 1 is the red disadvantage 1 and its 12 the blue
  advantage 12, for the crit effects.
"""

from collections.abc import Sequence

NEXT_EVEN = {3: 4, 5: 6, 7: 8, 9: 10, 11: 12}
SUPPORTED = frozenset({2, 4, 6, 8, 10, 12, 20, 100, *NEXT_EVEN})


def plain_d12(value: int) -> str:
    """A d12 without crit effects: blue, except for the red 1."""
    return "d12r_1" if value == 1 else f"d12b_{value}"


def lone_d12(value: int) -> str:
    """A d12 on its own: the crit effects come from the disadvantage 1 (red) and the advantage 12 (blue); the rest is blue."""
    if value == 1:
        return "d12rd_1"
    if value == 12:
        return "d12ba_12"
    return plain_d12(value)


def duality(blue: int, red: int) -> tuple[str, str]:
    """Daggerheart's two d12: the sticker of the blue die and of the red one."""
    if blue in (1, 12):
        first = f"d12b_{blue}"
    else:
        first = f"d12ba_{blue}" if blue >= red else f"d12b_{blue}"
    if red in (1, 12):
        second = f"d12r_{red}"
    else:
        second = f"d12rd_{red}" if red >= blue else f"d12r_{red}"
    return first, second


def _one(sides: int, value: int, *, alone: bool, mixed: bool) -> str:
    sides = NEXT_EVEN.get(sides, sides)
    if sides in (4, 6, 10) and mixed and value in (1, sides):
        return f"d{sides}n_{value}"
    if sides == 12:
        return lone_d12(value) if alone else plain_d12(value)
    return f"d{sides}_{value}"


def _with_mode(sides: int, values: tuple[int, int], mode: str) -> list[str]:
    """Two d20 or d12 rolled for the better (``adv``) or worse (``dis``) one."""
    if values[0] == values[1]:
        return [_one(sides, v, alone=True, mixed=False) for v in values]
    counted = max(values) if mode == "adv" else min(values)
    names = []
    for v in values:
        if v != counted:
            names.append(_one(sides, v, alone=True, mixed=False))
        elif sides == 20:
            own = "a" if mode == "adv" else "d"
            names.append(f"d20{own}_{v}" if 2 <= v <= 19 else f"d20_{v}")  # a 1 or a 20 is the plain d20's crit
        else:
            names.append(f"d12{'ba' if mode == 'adv' else 'rd'}_{v}")
    return names


def choose(dice: Sequence[tuple[int, int]], mode: str | None = None) -> list[str] | None:
    """The stickers for the dice of a roll, as (sides, value) in the order thrown; None when a die has none."""
    if not dice or any(sides not in SUPPORTED for sides, _ in dice):
        return None
    if mode in ("adv", "dis") and len(dice) == 2 and dice[0][0] == dice[1][0] in (12, 20):
        return _with_mode(dice[0][0], (dice[0][1], dice[1][1]), mode)
    mixed = len({sides for sides, _ in dice}) > 1
    names: list[str | None] = [None] * len(dice)
    twelves = [i for i, (sides, _) in enumerate(dice) if sides == 12]
    if len(twelves) == 2:
        names[twelves[0]], names[twelves[1]] = duality(dice[twelves[0]][1], dice[twelves[1]][1])
    return [
        name or _one(sides, value, alone=len(dice) == 1, mixed=mixed)
        for name, (sides, value) in zip(names, dice, strict=True)
    ]
