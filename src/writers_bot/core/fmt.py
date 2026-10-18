"""Formatters: how bold text, mentions and times look on each platform. Everything they return is
:class:`~.i18n.Markup`, so :func:`~.i18n.render` doesn't escape it again."""

import html
import re
from datetime import datetime, tzinfo

from .i18n import Markup, plural, render


class Formatter:
    tz: tzinfo

    def __init__(self, tz: tzinfo):
        self.tz = tz

    def escape(self, text) -> str:
        raise NotImplementedError

    def bold(self, text) -> Markup:
        raise NotImplementedError

    def code(self, text) -> Markup:
        raise NotImplementedError

    def mention(self, user_id: str, name: str) -> Markup:
        raise NotImplementedError

    def time(self, when: datetime) -> Markup:
        raise NotImplementedError

    def link(self, url: str, text) -> Markup:
        raise NotImplementedError

    def t(self, key: str, /, **values) -> Markup:
        """A locale text, escaped for this platform (see :func:`~.i18n.render`)."""
        return render(self, key, **values)

    def plural(self, num: int, forms: str) -> Markup:
        return Markup(self.escape(plural(num, forms)))

    def join(self, parts, sep: str = ", ") -> Markup:
        return Markup(self.escape(sep).join(p if isinstance(p, Markup) else self.escape(p) for p in parts))

    def lines(self, parts) -> Markup:
        return self.join([p for p in parts if p is not None], "\n")


class HtmlFormatter(Formatter):
    """Telegram HTML."""

    def escape(self, text) -> str:
        if isinstance(text, Markup):
            return text
        return html.escape(str(text), quote=False)

    def bold(self, text) -> Markup:
        return Markup(f"<b>{self.escape(text)}</b>")

    def italic(self, text) -> Markup:
        return Markup(f"<i>{self.escape(text)}</i>")

    def code(self, text) -> Markup:
        return Markup(f"<code>{self.escape(text)}</code>")

    def mention(self, user_id: str, name: str) -> Markup:
        return Markup(f'<a href="tg://user?id={int(user_id)}">{self.escape(name)}</a>')

    def link(self, url: str, text) -> Markup:
        return Markup(f'<a href="{html.escape(url, quote=True)}">{self.escape(text)}</a>')

    def time(self, when: datetime) -> Markup:
        # Bot API 9.5 date_time entity: each reader sees the time in their own timezone.
        fallback = when.astimezone(self.tz).strftime("%H:%M")
        return Markup(f'<tg-time unix="{int(when.timestamp())}" format="t">{fallback}</tg-time>')


_MD_SPECIAL = re.compile(r"([\\*_`~|>#\[\]()-])")


class MarkdownFormatter(Formatter):
    """Discord Markdown."""

    def escape(self, text) -> str:
        if isinstance(text, Markup):
            return text
        text = _MD_SPECIAL.sub(r"\\\1", str(text))
        return text.replace("@", "@​")  # no @everyone / @here / role pings from user text

    def bold(self, text) -> Markup:
        return Markup(f"**{self.escape(text)}**")

    def italic(self, text) -> Markup:
        return Markup(f"*{self.escape(text)}*")

    def code(self, text) -> Markup:
        return Markup(f"`{str(text).replace('`', 'ˋ')}`")

    def mention(self, user_id: str, name: str) -> Markup:
        return Markup(f"<@{int(user_id)}>")

    def link(self, url: str, text) -> Markup:
        return Markup(f"[{self.escape(text)}](<{url}>)")

    def time(self, when: datetime) -> Markup:
        return Markup(f"<t:{int(when.timestamp())}:t>")  # rendered in each reader's local time


class PlainFormatter(Formatter):
    """No formatting (logs, tests, plain-text messages)."""

    def escape(self, text) -> str:
        return str(text)

    def bold(self, text) -> Markup:
        return Markup(str(text))

    def italic(self, text) -> Markup:
        return Markup(str(text))

    def code(self, text) -> Markup:
        return Markup(str(text))

    def mention(self, user_id: str, name: str) -> Markup:
        return Markup(name)

    def link(self, url: str, text) -> Markup:
        return Markup(f"{text} ({url})")

    def time(self, when: datetime) -> Markup:
        return Markup(when.astimezone(self.tz).strftime("%H:%M"))
