"""What is in the dice clips: every clip's name, the dice in it, their looks and its effect.

The names are a small grammar, shared with the bot's own picking (``core/dicestickers.py``):

    d20_17        a single die showing 17, with the crit effects on its lowest and highest roll
    d20n_20       the same without the crit effects (a die rolled with others; also d4n, d6n, d8n, d10n, d12n, d100n)
    d3_2          a d3 (no crit effects), d2_1 / d2_2 the coin
    d12_5         a d12: blue, except its 1, which is red; d12_12 the blue crit, d12_1 the red fumble
    3d6_1_4_4     the dice of a roll of 2 or 3 of the same die, unordered (values ascending): 2d4 3d4 2d6 3d6 2d8
    2d12_9_4      Daggerheart's duality dice, ordered: the blue die 9, the red die 4
    2d20a_5_17    two d20s with advantage (the higher one, 17, counts), 2d20d_5_17 with disadvantage (5 counts)
    2d20t_9       two d20s showing the same (the tie of an advantage or disadvantage roll)

Effects: the highest roll of a die gets golden rays, the lowest a red halo, a dull die and dust; in a group of dice, when
any die shows its highest (the one that counts, with advantage or disadvantage); the lowest otherwise; the highest
wins. The d12 pairs have none (Daggerheart's crits work differently), but halos on the dice that count: green on the blue die when
it is the larger, magenta on the red die when it is.
"""

from dataclasses import dataclass, field

DICE = (2, 3, 4, 6, 8, 10, 12, 20, 100)
POOLS = {4: (2, 3), 6: (2, 3), 8: (2,)}  # the dice with pair / triple stickers, and how many dice fit in one


@dataclass(frozen=True, slots=True)
class Piece:
    """One die in a clip: its sides, value and look (``colour``: b / r for the d12, ``halo``: adv / dis / None)."""

    sides: int
    value: int
    colour: str = ""
    halo: str | None = None
    gold: bool = False  # the face of a crit
    dull: bool = False


@dataclass(frozen=True, slots=True)
class Clip:
    name: str
    kind: str  # single | pool | duality | advantage
    sides: int
    pieces: tuple[Piece, ...]
    effect: str | None  # crit | fumble | None
    critless: bool = False
    variant: str = ""
    values: tuple[int, ...] = field(default=())


def _kind(name: str) -> tuple[str, list[int]]:
    head, *numbers = name.split("_")
    return head, [int(n) for n in numbers]


def _effect(values: list[int], sides: int, counted: list[int] | None = None) -> str | None:
    """The effect of a group: by the dice that count (all of them when ``counted`` is None)."""
    dice = values if counted is None else counted
    if any(v == sides for v in dice):
        return "crit"
    if any(v == 1 for v in dice):
        return "fumble"
    return None


def parse(name: str) -> Clip:
    """The clip a name stands for. Raises ValueError for a name outside the grammar."""
    head, v = _kind(name)
    if head.startswith(("2d12", "2d20")) or (head[0] in "23" and head[1] == "d"):
        return _group(name, head, v)
    sides = int("".join(c for c in head[1:] if c.isdigit()))
    n = head.endswith("n")
    if sides not in DICE or len(v) != 1 or not 1 <= v[0] <= sides:
        raise ValueError(name)
    value = v[0]
    if sides in (2, 3):
        return Clip(name, "single", sides, (Piece(sides, value),), None, values=(value,))
    if n and value not in (1, sides):
        raise ValueError(name)
    if sides == 12:
        colour = "r" if value == 1 else "b"
        effect = None if n else _effect([value], sides)
        return Clip(
            name, "single", sides, (Piece(12, value, colour, gold=effect == "crit"),), effect, n, "n" * n, (value,)
        )
    effect = None if n else _effect([value], sides)
    return Clip(name, "single", sides, (Piece(sides, value, gold=effect == "crit"),), effect, n, "n" * n, (value,))


def _group(name: str, head: str, v: list[int]) -> Clip:
    count, sides = int(head[0]), int("".join(c for c in head[2:] if c.isdigit()))
    kind = head[2 + len(str(sides)) :]
    if sides == 12 and kind == "" and count == 2 and len(v) == 2:  # duality: blue first, red second
        blue, red = v
        halos = (
            "adv" if blue >= red and blue not in (1, 12) else None,
            "dis" if red >= blue and red not in (1, 12) else None,
        )
        pieces = (Piece(12, blue, "b", halos[0]), Piece(12, red, "r", halos[1]))
        return Clip(name, "duality", 12, pieces, None, False, "dh", (blue, red))
    if sides == 20 and count == 2 and kind in ("a", "d", "t"):
        if kind == "t":
            (value,) = v
            effect = _effect([value], 20)
            return Clip(
                name,
                "advantage",
                20,
                (Piece(20, value, gold=effect == "crit"),) * 2,
                effect,
                False,
                "t",
                (value, value),
            )
        lo, hi = v
        if not 1 <= lo < hi <= 20:
            raise ValueError(name)
        counted = hi if kind == "a" else lo
        effect = _effect([counted], 20)
        pieces = [Piece(20, lo), Piece(20, hi)]
        i = 1 if kind == "a" else 0
        pieces[i] = Piece(20, counted, halo="adv" if kind == "a" else "dis", gold=effect == "crit")
        return Clip(name, "advantage", 20, tuple(pieces), effect, False, kind, (lo, hi))
    if kind == "" and sides in POOLS and count in POOLS[sides] and len(v) == count and v == sorted(v):
        if not all(1 <= x <= sides for x in v):
            raise ValueError(name)
        effect = _effect(v, sides)
        pieces = tuple(Piece(sides, x, gold=effect == "crit" and x == sides) for x in v)
        return Clip(name, "pool", sides, pieces, effect, False, "", tuple(v))
    raise ValueError(name)


# --- the catalogue ----------------------------------------------------------------------------------------------------


def ladder(sides: int) -> list[str]:
    """A die's single clips: ``d20_1`` ... with the crit-less ``d20n_1`` and ``d20n_20`` for the ends (the d2 and d3 have no effects)."""
    if sides in (2, 3):
        return [f"d{sides}_{v}" for v in range(1, sides + 1)]
    d = f"d{sides}"
    return [f"{d}_1", f"{d}n_1", *[f"{d}_{v}" for v in range(2, sides)], f"{d}n_{sides}", f"{d}_{sides}"]


def pools(count: int, sides: int) -> list[str]:
    """Every unordered roll of ``count`` dice of ``sides`` sides, values ascending."""
    rolls = [[]]
    for _ in range(count):
        rolls = [[*r, x] for r in rolls for x in range(r[-1] if r else 1, sides + 1)]
    return [f"{count}d{sides}_" + "_".join(map(str, r)) for r in rolls]


def advantage(kind: str) -> list[str]:
    return [f"2d20{kind}_{lo}_{hi}" for lo in range(1, 20) for hi in range(lo + 1, 21)]


ALL: list[str] = (
    [n for s in (2, 3, 4, 6, 8, 10, 12, 20, 100) for n in ladder(s)]
    + pools(2, 4) + pools(3, 4) + pools(2, 6) + pools(3, 6) + pools(2, 8)
    + [f"2d12_{blue}_{red}" for blue in range(1, 13) for red in range(1, 13)]  # Daggerheart's duality dice, ordered
    + advantage("a") + advantage("d") + [f"2d20t_{v}" for v in range(1, 21)]
)  # fmt: skip
