"""Small chat extras carried over from the v2 ``extended`` branch: dice rolls and keyboard smash."""

import contextlib
import html
import random
import re
from collections.abc import Iterator
from dataclasses import dataclass

import d20
import d20.expression

from .fmt import Formatter
from .i18n import t

# /d20, !к12, /2d6+1, /r 4d6kh3 Ініціатива, /roll d20adv: a command that starts with dice, or /r and /roll. The
# notation is the d20 library's (keep/drop, exploding, rerolls, arithmetic), plus the v2 forms: the Cyrillic
# letter ka for d, "adv"/"dis" for the better/worse of two throws, b5/m3 for +5/-3. (i18n: input syntax)
_DIE = r"\d*[dDкК][\d%]"  # i18n: input syntax; d% is the percentile die
ROLL_RE = re.compile(rf"^[/!](?:r(?:oll)?(?:@\w+)?(?:\s+\S|\s*\()|(?:r(?:oll)?(?:@\w+)?)?{_DIE})", re.IGNORECASE)
_PREFIX = re.compile(r"^[/!](?:r(?:oll)?)?(?:@\w+)?\s*", re.IGNORECASE)  # i18n: input syntax
_MENTION = re.compile(r"^(\S*?)@\w+")
_MODE = re.compile(r"(?<=\d)(adv|dis)\b", re.IGNORECASE)  # i18n: input syntax
_FLAGS = {"-s": True, "-q": False}  # i18n: input syntax; -s: stickers even for 3 or 4 dice, -q: no stickers
# 2d20kh1 / 2d20kl1 (keep the highest / lowest of two), alone or with a flat bonus: the same as d20adv / d20dis
_KEEP_ONE = re.compile(r"2d(\d+|%)k([hl])1([+-]\d+)?", re.IGNORECASE)  # i18n: input syntax
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
    stickers: bool | None = None  # -s: True, -q: False (wins when both are given), nothing: None


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
    tokens = []
    stickers = None
    for word in rest.split():
        if word.lower() in _FLAGS:  # a flag takes no part in the roll or the caption
            stickers = _FLAGS[word.lower()] if stickers is None or word.lower() == "-q" else stickers
        else:
            tokens.append(word)
    mode = None
    for n in range(min(len(tokens), MAX_TOKENS), 0, -1):
        candidate = _normalise(" ".join(tokens[:n]))
        found = _MODE.search(candidate)
        expression = _MODE.sub("", candidate) if found else candidate
        if keep := _KEEP_ONE.fullmatch(expression):
            found, expression = keep, f"d{keep[1]}{keep[3] or ''}"
        try:
            d20.parse(expression)
        except d20.RollSyntaxError:
            continue
        mode = ("adv" if found[2].lower() == "h" else "dis") if keep else found[1].lower() if found else None
        caption = " ".join(tokens[n:]) or t("dice.result")
        return Roll(expression, mode, caption, stickers)
    return Roll("", None, "")


@dataclass(frozen=True, slots=True)
class Outcome:
    html: str
    dice: tuple[tuple[int, int], ...] = ()  # every die thrown, as (sides, value): for the dice stickers
    only_die: bool = False  # one die and nothing to say beyond what it shows (a bare /d20)


def _dice(result) -> list[tuple[int, int]]:
    """Every die of a roll, kept or dropped, rerolled ones included, in order."""
    found = []

    def walk(node):
        if isinstance(node, d20.Dice):
            sides = 100 if node.size == "%" else node.size
            found.extend((sides, die.number) for die in node.values)
        for child in node.children:
            walk(child)

    walk(result.expr)
    return found


@contextlib.contextmanager
def _source(rng: random.Random | None) -> Iterator[None]:
    """The d20 library draws every die from the ``random`` module; for the time of a roll, from ``rng`` (a source of
    true randomness) instead. Nothing awaits inside a roll, so nothing else sees the swap."""
    if rng is None:
        yield
        return
    original = d20.expression.random
    d20.expression.random = rng
    try:
        yield
    finally:
        d20.expression.random = original


def throw(f: Formatter, roll: Roll, rng: random.Random | None = None) -> Outcome:
    """The roll's outcome; its dice come from ``rng`` (see ``core.randomness``) when there is one, else from ``random``."""
    with _source(rng):
        return _throw(f, roll)


def _throw(f: Formatter, roll: Roll) -> Outcome:
    if not roll.expression:
        return Outcome(f.t("dice.error"))
    caption = f"{f.bold(roll.caption)} "
    roller = _Roller()
    try:
        first = roller.roll(roll.expression, stringifier=_Html())
        second = roller.roll(roll.expression, stringifier=_Html()) if roll.mode else None
    except d20.TooManyRolls:
        return Outcome(f.t("dice.too_many", max=MAX_DICE))
    except d20.RollError:
        return Outcome(f.t("dice.error"))
    if second is None:
        bare = re.fullmatch(r"1?d(\d+|%)", roll.expression) is not None and roll.caption == t("dice.result")
        return Outcome(caption + first.result, tuple(_dice(first)), bare)
    better = first.total >= second.total if roll.mode == "adv" else first.total <= second.total
    kept, dropped = (first, second) if better else (second, first)
    html = f"{caption}\n{f.bold(int(kept.total))}\n{kept.result}\n<s>{dropped.result}</s>"
    return Outcome(html, (*_dice(kept), *_dice(dropped)))  # the die that counts first


def roll_html(f: Formatter, roll: Roll) -> str:
    return throw(f, roll).html


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
