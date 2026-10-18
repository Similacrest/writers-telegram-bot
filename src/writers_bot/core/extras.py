"""Small chat extras carried over from the v2 ``extended`` branch: dice rolls and keyboard smash."""

import html
import random
import re
from dataclasses import dataclass

import d20

from .fmt import Formatter
from .i18n import t

# /d20, /2d6+1, /r 4d6kh3 Ініціатива, /roll d20adv: a command that starts with dice, or /r and /roll. The
# notation is the d20 library's (keep/drop, exploding, rerolls, arithmetic), plus the v2 forms: the Cyrillic
# letter ka for d, "adv"/"dis" for the better/worse of two throws, b5/m3 for +5/-3. (i18n: input syntax)
_DIE = r"\d*[dDкК]\d"  # i18n: input syntax
ROLL_RE = re.compile(rf"^/(?:r(?:oll)?(?:@\w+)?(?:\s+\S|\s*\()|(?:r(?:oll)?(?:@\w+)?)?{_DIE})", re.IGNORECASE)
_PREFIX = re.compile(r"^/(?:r(?:oll)?)?(?:@\w+)?\s*", re.IGNORECASE)  # i18n: input syntax
_MENTION = re.compile(r"^(\S*?)@\w+")
_MODE = re.compile(r"(?<=\d)(adv|dis)\b", re.IGNORECASE)  # i18n: input syntax
_LEGACY_BONUS = re.compile(r"(?<=\d)([bm])(\d+)")
_ROLL20_DROP = re.compile(r"(?<=\d)d([hl]\d+)", re.IGNORECASE)  # 4d6dl1 (Roll20) is d20's 4d6pl1
MAX_DICE = 500
MAX_TOKENS = 12


class _Roller(d20.Roller):
    def __init__(self):
        super().__init__(d20.RollContext(max_rolls=MAX_DICE))


class _Html(d20.MarkdownStringifier):
    """The roll in Telegram HTML: dropped dice struck through, highest bold, ones underlined."""

    def _stringify(self, node):
        if not node.kept and not self._context.in_dropped:
            self._context.in_dropped = True
            inside = d20.SimpleStringifier._stringify(self, node)
            self._context.in_dropped = False
            return f"<s>{inside}</s>"
        handler = self._nodes[type(node)]
        inside = handler(node)
        return f"{inside} {html.escape(node.annotation)}" if node.annotation else inside

    def _str_expression(self, node):
        return f"{self._stringify(node.roll)} = <b>{int(node.total)}</b>"

    def _str_die(self, node):
        out = []
        for val in node.values:
            inside = self._stringify(val)
            if val.number == node.size:
                inside = f"<b>{inside}</b>"
            elif val.number == 1:
                inside = f"<u>{inside}</u>"
            out.append(inside)
        return ", ".join(out)


@dataclass(frozen=True, slots=True)
class Roll:
    expression: str  # in d20 notation
    mode: str | None  # "adv", "dis" or None
    caption: str


def _normalise(text: str) -> str:
    text = text.replace("к", "d").replace("К", "d")  # i18n: input syntax
    text = _ROLL20_DROP.sub(r"p\1", text)
    return _LEGACY_BONUS.sub(lambda m: ("+" if m[1] == "b" else "-") + m[2], text)


def parse_roll(text: str) -> Roll | None:
    """A dice command, or None. A command that looks like dice but isn't a valid roll gives a Roll whose
    expression is empty (the caller says so)."""
    text = (text or "").strip()
    if not ROLL_RE.match(text):
        return None
    rest = _PREFIX.sub("", text, count=1)
    rest = _MENTION.sub(r"\1", rest, count=1)  # /d20@writers_bot
    tokens = rest.split()
    mode = None
    for n in range(min(len(tokens), MAX_TOKENS), 0, -1):
        candidate = _normalise(" ".join(tokens[:n]))
        found = _MODE.search(candidate)
        expression = _MODE.sub("", candidate) if found else candidate
        try:
            d20.parse(expression)
        except d20.RollSyntaxError:
            continue
        mode = found[1].lower() if found else None
        caption = " ".join(tokens[n:]) or t("dice.result")
        return Roll(expression, mode, caption)
    return Roll("", None, "")


def roll_html(f: Formatter, roll: Roll) -> str:
    if not roll.expression:
        return f.t("dice.error")
    caption = f"{f.bold(roll.caption)} "
    roller = _Roller()
    try:
        first = roller.roll(roll.expression, stringifier=_Html())
        if not roll.mode:
            return caption + first.result
        second = roller.roll(roll.expression, stringifier=_Html())
    except d20.TooManyRolls:
        return f.t("dice.too_many", max=MAX_DICE)
    except d20.RollError:
        return f.t("dice.error")
    better = first.total >= second.total if roll.mode == "adv" else first.total <= second.total
    kept, dropped = (first, second) if better else (second, first)
    return f"{caption}\n{f.bold(int(kept.total))}\n{kept.result}\n<s>{dropped.result}</s>"


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
