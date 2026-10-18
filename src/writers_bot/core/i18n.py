"""User-facing texts, from ``writers_bot/locale/uk.toml``.

Every text the bot shows lives in that file, with a comment saying where it appears, so it can be edited
without touching the code. Keys are dotted paths of the TOML tables (``sprint.started``). Placeholders
use ``str.format`` syntax: ``{name}``, ``{wpm:.1f}``.

``t(key, **values)`` gives plain text. ``render(f, key, **values)`` is for formatted messages (HTML on
Telegram, Markdown on Discord): the template's own text is escaped for the format, and so is every value
unless it is :class:`Markup` (what the formatter's ``bold``/``mention``/… return), so user input can
never inject formatting.
"""

import random
import string
import tomllib
from functools import cache
from importlib import resources
from typing import Any, Protocol

LOCALE = "uk"


class Markup(str):
    """Text that is already formatted for the target (HTML or Markdown) and must not be escaped again."""

    __slots__ = ()


class Escaper(Protocol):
    def escape(self, text: str) -> str: ...


def _flatten(table: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in table.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out |= _flatten(value, path + ".")
        else:
            out[path] = value
    return out


@cache
def texts(locale: str = LOCALE) -> dict[str, Any]:
    raw = resources.files("writers_bot.locale").joinpath(f"{locale}.toml").read_text(encoding="utf-8")
    return _flatten(tomllib.loads(raw))


def raw(key: str) -> Any:
    """The value as stored: a string, or a list (plural forms, alternatives, word lists)."""
    try:
        return texts()[key]
    except KeyError:
        raise KeyError(f"no text {key!r} in locale/{LOCALE}.toml") from None


def words(key: str) -> list[str]:
    """A list of words from the file (input vocabulary such as day names), lower-cased."""
    value = raw(key)
    return [str(v).casefold() for v in (value if isinstance(value, list) else [value])]


def _template(key: str) -> str:
    value = raw(key)
    if isinstance(value, list):
        return random.choice(value)
    return str(value)


def t(key: str, /, **values: Any) -> str:
    """Plain text (no formatting)."""
    return _template(key).format(**values)


def render(f: Escaper, key: str, /, **values: Any) -> Markup:
    """Formatted text: escapes the template's text and every value that isn't :class:`Markup`."""
    return format_markup(f, _template(key), **values)


def format_markup(f: Escaper, template: str, /, **values: Any) -> Markup:
    out = []
    for literal, field, spec, conversion in string.Formatter().parse(template):
        if literal:
            out.append(f.escape(literal))
        if field is None:
            continue
        value = values[field]
        if conversion:
            value = {"r": repr, "s": str, "a": ascii}[conversion](value)
        if isinstance(value, Markup):
            out.append(value)
        else:
            out.append(f.escape(format(value, spec or "")))
    return Markup("".join(out))


def plural(num: int, forms: str | tuple[str, str, str]) -> str:
    """``num`` with the Ukrainian plural form: 1 слово, 2 слова, 5 слів. ``forms`` is a key under
    ``[plural]`` in the locale file (or the three forms themselves)."""
    if isinstance(forms, str):
        forms = tuple(raw(f"plural.{forms}"))
    n = abs(num)
    if n % 10 == 1 and n % 100 != 11:
        form = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{num} {form}"


# Shortcuts kept from v3 (they name the [plural] keys)
MINUTES = "minutes"
WORDS = "words"
CHARACTERS = "characters"
LETTERS = "letters"
SPRINTS = "sprints"
WORKS = "works"
