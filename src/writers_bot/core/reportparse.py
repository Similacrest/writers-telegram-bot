"""The report grammar shared by sprint replies, the Report prompt and ``/progress``.

    500            a number (in a sprint: your count for it; outside: adds 500)
    +300           adds to it
    =5000          the work's total now; the bot credits the difference from the last counted total
    50%            a % work (share of what you planned for the sprint)
    <link>         counts as a total
    ✓ / готово     Done, no number;  ✗ / не вийшло: didn't make it
    +300 роман +200 есе   several works by name (a name follows its number)
    вчора, позавчора, 17.10   an earlier day, for /progress (up to 3 days back)

``500 слів`` and ``500 words`` are plain numbers. A reply to a sprint message counts only when it is
nothing but this; the caller then checks that every name is one of the writer's works.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from .i18n import words
from .models import Outcome


class Op(StrEnum):
    SET = "set"  # 500
    ADD = "add"  # +500
    TOTAL = "total"  # =5000 or a link
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class Item:
    op: Op
    value: int | None = None
    percent: bool = False
    url: str | None = None
    hint: str | None = None  # a work name, as typed

    @property
    def outcome(self) -> Outcome | None:
        return {Op.DONE: Outcome.DONE, Op.FAILED: Outcome.FAILED}.get(self.op)


@dataclass(slots=True)
class Parsed:
    items: list[Item] = field(default_factory=list)
    day: date | None = None  # an earlier day; None = today

    @property
    def hints(self) -> list[str]:
        return [i.hint for i in self.items if i.hint]


MAX_DIGITS = 9
_NUM_RE = re.compile(r"^([+=])?(\d+(?:[,.'’]\d{3})*)(%)?$")
_GROUP_RE = re.compile(r"^\d{3}%?$")


def _vocab() -> dict[str, set[str] | dict[str, int] | tuple[str, ...]]:
    return {
        "unit": set(words("input.unit_words")),
        "percent": set(words("input.percent_words")),
        "done": set(words("input.done_words")),
        "failed": set(words("input.failed_words")),
        "failed_phrases": tuple(words("input.failed_phrases")),
        "days": {w: 0 for w in words("input.today_words")}
        | {w: 1 for w in words("input.yesterday_words")}
        | {w: 2 for w in words("input.day_before_words")},
    }


_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.?$")
_PLACEHOLDER = "\x00{}\x00"


def _tokens(text: str) -> list[str]:
    raw = text.replace(" ", " ").replace(" ", " ").replace(" ", " ").split()
    merged: list[str] = []
    for tok in raw:
        prev = merged[-1] if merged else None
        # "12 500": a group of exactly three digits continues the previous plain number
        if prev and _GROUP_RE.match(tok) and re.fullmatch(r"[+=]?\d{1,3}(?:\d{3})*", prev):
            merged[-1] = prev + tok
            continue
        # "+ 300", "= 5000"
        if prev in ("+", "=") and re.fullmatch(r"\d[\d,.'’]*%?", tok):
            merged[-1] = prev + tok
            continue
        merged.append(tok)
    return merged


def parse(
    text: str, find_url: Callable[[str], str | None], *, today: date | None = None, allow_day: bool = True
) -> Parsed | None:
    """Parse a report; ``None`` if ``text`` holds no number, link, ✓ or ✗."""
    text = (text or "").strip()
    if not text:
        return None
    urls: list[str] = []
    while (url := find_url(text)) is not None:
        text = text.replace(url, f" {_PLACEHOLDER.format(len(urls))} ", 1)
        urls.append(url)
    vocab = _vocab()
    lowered = text.casefold()
    failed_phrase = False
    for phrase in vocab["failed_phrases"]:
        if phrase in lowered:
            lowered = lowered.replace(phrase, " ✗ ")
            failed_phrase = True
    tokens = _tokens(lowered if failed_phrase else text)

    parsed = Parsed()
    pending_name: list[str] = []  # name words not attached yet
    leading_name: list[str] = []  # name words before the first item

    def flush_name() -> None:
        if not pending_name:
            return
        name = " ".join(pending_name)
        pending_name.clear()
        if parsed.items and parsed.items[-1].hint is None:
            parsed.items[-1].hint = name
        elif not parsed.items:
            leading_name.append(name)
        else:
            parsed.items[-1].hint = f"{parsed.items[-1].hint} {name}"

    for tok in tokens:
        low = tok.casefold()
        if low.startswith("\x00") and low.endswith("\x00"):
            flush_name()
            parsed.items.append(Item(Op.TOTAL, url=urls[int(low.strip("\x00"))]))
            continue
        if m := _NUM_RE.match(tok):
            flush_name()
            digits = re.sub(r"\D", "", m[2])
            if len(digits) > MAX_DIGITS:
                return None
            op = {"+": Op.ADD, "=": Op.TOTAL}.get(m[1] or "", Op.SET)
            parsed.items.append(Item(op, int(digits), percent=bool(m[3])))
            continue
        if low in vocab["unit"] and parsed.items and parsed.items[-1].value is not None and not pending_name:
            continue
        if low in vocab["percent"] and parsed.items and parsed.items[-1].value is not None and not pending_name:
            parsed.items[-1].percent = True
            continue
        if low in vocab["done"]:
            flush_name()
            parsed.items.append(Item(Op.DONE))
            continue
        if low in vocab["failed"]:
            flush_name()
            parsed.items.append(Item(Op.FAILED))
            continue
        if allow_day and low in vocab["days"]:
            flush_name()
            if today is not None:
                parsed.day = today - timedelta(days=vocab["days"][low])
            continue
        if allow_day and (dm := _DATE_RE.match(low)) and today is not None:
            flush_name()
            try:
                when = date(today.year, int(dm[2]), int(dm[1]))
            except ValueError:
                return None
            if when > today:
                when = when.replace(year=today.year - 1)
            parsed.day = when
            continue
        pending_name.append(tok)
    flush_name()
    if not parsed.items:
        return None
    if leading_name and parsed.items[0].hint is None:
        parsed.items[0].hint = " ".join(leading_name)
    elif leading_name:
        parsed.items[0].hint = " ".join(leading_name) + " " + parsed.items[0].hint
    if parsed.day is not None and parsed.day == today:
        parsed.day = None
    return parsed
