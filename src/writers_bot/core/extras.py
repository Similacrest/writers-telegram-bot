"""Small chat extras carried over from the v2 ``extended`` branch: dice rolls, keyboard smash, slogan replies."""

import html
import random
import re
from dataclasses import dataclass
from secrets import SystemRandom

_rng = SystemRandom()

# /d20, /2d6, /r 3d8+2, /roll d20adv Ініціатива, /4к6mi2 (Cyrillic к works too)
ROLL_RE = re.compile(r"^/(?:r(?:oll)?\s*)?(\d*)[dDкК](\d+)(mi-?\d+)?(adv|dis)?([bm+\-]\d+)?(?:@\w+)?(.*)$", re.DOTALL)
MAX_DICE = 500
MAX_SIDES = 2**31 - 1


@dataclass(frozen=True, slots=True)
class Roll:
    count: int
    sides: int
    minimum: int
    mode: str | None  # "adv", "dis" or None
    bonus: int
    caption: str
    notation: str


def parse_roll(text: str) -> Roll | None:
    m = ROLL_RE.match((text or "").strip())
    if not m:
        return None
    count = int(m[1]) if m[1] else 1
    if not 0 < count <= MAX_DICE:
        count = 1
    sides = int(m[2])
    if not 0 < sides <= MAX_SIDES:
        sides = 20
    minimum = int(m[3][2:]) if m[3] else 1
    if not -sides <= minimum <= sides:
        minimum = 1
    bonus = int(m[5].replace("b", "").replace("m", "-")) if m[5] else 0
    notation = f"{m[1]}d{m[2]}{m[3] or ''}{m[4] or ''}{m[5] or ''}"
    return Roll(count, sides, minimum, m[4], bonus, m[6].strip() or "Результат", notation)


def _throw(roll: Roll) -> tuple[int, str]:
    pool = [_rng.randint(roll.minimum, roll.sides) for _ in range(roll.count)]
    shown = ", ".join(
        f"<b>{v}</b>" if v == roll.sides else f"<u>{v}</u>" if v == roll.minimum else str(v) for v in pool
    )
    bonus = f"{roll.bonus:+d}" if roll.bonus else ""
    total = sum(pool) + roll.bonus
    return total, f"<code>{html.escape(roll.notation)}</code> <b>{total}</b>: [{shown}]{bonus}"


def roll_html(roll: Roll) -> str:
    caption = f"<b>{html.escape(roll.caption)}</b> "
    first_total, first = _throw(roll)
    if not roll.mode:
        return caption + first
    second_total, second = _throw(roll)
    keep_first = first_total >= second_total if roll.mode == "adv" else first_total <= second_total
    kept, dropped = (first, second) if keep_first else (second, first)
    best = max(first_total, second_total) if roll.mode == "adv" else min(first_total, second_total)
    return f"{caption}\n<b>{best}</b>\n{kept}\n<s>{dropped}</s>"


_KEYBOARD = ["qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]


def smash() -> str:
    """Random 'keyboard smash': a walk over neighbouring keys, mostly on the home row."""
    row = random.choices(range(3), [0.1, 0.9, 0.1])[0]
    col = int(random.triangular(0, len(_KEYBOARD[row]) - 1))
    direction = random.choice([1, -1])
    out = [_KEYBOARD[row][col]]
    for _ in range(int(random.triangular(8, 20))):
        row = min(2, max(0, row + round(random.normalvariate(0, 0.5))))
        step = max(0, int(random.normalvariate(1, 1.5)))
        if random.random() < 0.2:
            direction = -direction
        col = col + direction * step
        if not 0 <= col < len(_KEYBOARD[row]):
            direction = -direction
            col = min(len(_KEYBOARD[row]) - 1, max(0, col))
        out.append(_KEYBOARD[row][col])
    return "".join(out)


def slogan_reply(text: str) -> str | None:
    lowered = (text or "").lower()
    if "слава україні" in lowered:
        return "<i>Героям Слава!</i>"
    if "слава нації" in lowered:
        return random.choices(["<i>Смерть ворогам!</i>", "Пизда ᵖосійській ᶲедерації!"], weights=[0.8, 0.2])[0]
    return None
