"""Roll variables: a name for a series of rolls (``$hit`` = ``d20+5 Атака; 2d6+3 Шкода``), kept per person per chat.

A variable is only ever a whole series, rolled with ``/$hit`` (or ``/r $hit``): it is not substituted into a bigger roll, and a
step is a roll like any other, so it cannot name a variable (no nesting). A series has up to 8 steps, different rolls or repeats
of one (``6x 4d6dl1``: six rolls of 4d6 dropping the lowest, for a new character).
"""

import re
from dataclasses import dataclass

from . import extras

MAX_STEPS = 8
MAX_NAME = 32
MAX_STEP = 200
MAX_PER_SCOPE = 50  # variables one person may have in one chat

NAME = re.compile(
    rf"[^\W\d]\w{{0,{MAX_NAME - 1}}}"
)  # a letter or underscore first (Cyrillic too), then word characters
_REF = re.compile(
    r"^[/!](?:r(?:oll)?(?:@\w+)?\s+)?\$(\w+)(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL
)  # i18n: input syntax
_REPEAT = re.compile(r"^(\d{1,2})\s*[x×хХ]\s*(.+)$", re.IGNORECASE)  # i18n: input syntax; 6x, 6×, Cyrillic х
_FLAGS = ("-s", "-q", "-p")  # i18n: input syntax; the roll flags a variable may be rolled with


class VarError(ValueError):
    """A definition that cannot be kept; ``key`` is the locale text that says why."""

    def __init__(self, key: str, **values):
        super().__init__(key)
        self.key = key
        self.values = values


@dataclass(frozen=True, slots=True)
class Reference:
    """``/$hit -p``: the variable's name and the flags it is rolled with."""

    name: str
    flags: tuple[str, ...]


def clean_name(text: str) -> str:
    """A name as written (with or without the ``$``), as it is kept: lower case. Raises VarError."""
    name = text.strip().removeprefix("$")
    if not NAME.fullmatch(name):
        raise VarError("dice.var_bad_name", max=MAX_NAME)
    return name.casefold()


def reference(text: str) -> Reference | None:
    """``/$hit``, ``!$hit -s`` or ``/r $hit`` -> the variable asked for, or None when the text is not one."""
    m = _REF.match((text or "").strip())
    if not m:
        return None
    rest = (m[2] or "").split()
    if any(word.lower() not in _FLAGS for word in rest):
        return None  # something else after the name: not a variable roll (a variable takes no caption)
    try:
        return Reference(clean_name(m[1]), tuple(word.lower() for word in rest))
    except VarError:
        return None


def parse_steps(text: str) -> list[str]:
    """The series of a definition: steps separated by ``;``, each a roll (``d20+5 Атака``) or ``N x`` a roll; at most 8 in
    all after the repeats are counted. Each step is checked to be a valid roll. Raises VarError."""
    steps: list[str] = []
    for part in text.split(";"):
        part = " ".join(part.split())
        if not part:
            continue
        count = 1
        if m := _REPEAT.match(part):
            count, part = int(m[1]), m[2].strip()
            if count < 1:
                raise VarError("dice.var_bad_repeat")
        part = part.lstrip("/!")
        if len(part) > MAX_STEP:
            raise VarError("dice.var_step_too_long", max=MAX_STEP)
        roll = extras.parse_roll("/" + part)
        if roll is None or not roll.expression:
            raise VarError("dice.var_bad_step", step=part)
        if roll.save_as or roll.private or part.lstrip().startswith("$"):
            raise VarError(
                "dice.var_bad_step", step=part
            )  # no variable in a variable, and flags belong to the roll call
        steps += [part] * count
        if len(steps) > MAX_STEPS:
            raise VarError("dice.var_too_many_steps", max=MAX_STEPS)
    if not steps:
        raise VarError("dice.var_empty")
    return steps


def compact(steps: list[str]) -> str:
    """The series as it is shown: ``6x 4d6dl1; d20+5 Атака`` (runs of the same step folded into a repeat)."""
    parts: list[str] = []
    i = 0
    while i < len(steps):
        j = i
        while j < len(steps) and steps[j] == steps[i]:
            j += 1
        parts.append(f"{j - i}x {steps[i]}" if j - i > 1 else steps[i])
        i = j
    return "; ".join(parts)
