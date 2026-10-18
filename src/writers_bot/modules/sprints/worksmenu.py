"""Managing works in the private chat with the bot (the web app has the same). In groups the works
button opens the private chat (``/start works``), since adding a work means typing its name."""

import re
import time
from typing import TYPE_CHECKING

from telegram import ForceReply, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType, KeyboardButtonStyle
from telegram.ext import MessageHandler, filters

from ...core.i18n import t, words
from ...core.links import LinkError
from ...core.models import Goal, GoalPeriod, Unit, Work
from ...core.sources import find_url
from ...core.works import WorkError
from ...telegram_bot.app import display_name
from ...telegram_bot.base import Sent, button

if TYPE_CHECKING:
    from .telegram import Module

PLATFORM = "telegram"
INPUT_TTL = 10 * 60


class _Waiting(filters.MessageFilter):
    """Private text messages from someone the menu asked to type something."""

    def __init__(self, menu: "WorksMenu"):
        super().__init__(name="works_input")
        self.menu = menu

    def filter(self, message: Message) -> bool:
        if message.chat.type != ChatType.PRIVATE or message.from_user is None or not message.text:
            return False
        waiting = self.menu._waiting.get(message.from_user.id)
        return waiting is not None and time.monotonic() - waiting[2] < INPUT_TTL


class WorksMenu:
    def __init__(self, module: "Module"):
        self.module = module
        self.bot = module.bot
        self._waiting: dict[int, tuple[str, int | None, float]] = {}  # user -> (action, work id, since)

    def handlers(self):
        return [(MessageHandler(_Waiting(self) & ~filters.COMMAND, self.on_input), 0)]

    def asks(self):
        return {"wk": self.on_button}

    def start_payloads(self):
        return {"works": self.on_start}

    def entry_button(self, user, private: bool):
        if private:
            return button(t("profile.button_works"), user.id, "wk:list")
        return self.bot.dm_button("profile.button_works", "works")

    async def _person(self, user) -> int:
        return await self.bot.people.person_id(PLATFORM, str(user.id), display_name(user))

    @property
    def works(self):
        return self.bot.services.works

    # --- views -----------------------------------------------------------------------------------

    async def list_view(self, user) -> tuple[str, InlineKeyboardMarkup]:
        f = self.bot.fmt
        person = await self._person(user)
        works = await self.works.for_person(person)
        lines = [f.bold(t("works.title"))]
        lines.append(f.t("works.intro") if works else f.t("works.none"))
        rows = [[button(self._label(w), user.id, f"wk:w:{w.id}")] for w in works]
        if len(works) < 5:
            rows.append([button(t("works.add"), user.id, "wk:new")])
        rows.append([button(t("common.back"), user.id, "pf:back")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def _label(self, w: Work) -> str:
        unit = t("works.unit_words_short") if w.unit == Unit.WORDS else t("works.unit_percent_short")
        return t("works.list_item", name=w.name, unit=unit, kept="" if w.keep else t("works.not_kept_mark"))

    async def work_view(self, user, work: Work) -> tuple[str, InlineKeyboardMarkup]:
        f = self.bot.fmt
        lines = [f.bold(work.name)]
        lines.append(
            f.t("works.unit_line", unit=t("works.unit_words") if work.unit == Unit.WORDS else t("works.unit_percent"))
        )
        lines.append(f.t("works.link_line", link=work.url) if work.url else f.t("works.no_link"))
        if work.total is not None and work.unit == Unit.WORDS:
            lines.append(f.t("works.total_line", total=f.plural(work.total, "words")))
        if work.goal:
            lines.append(
                f.t(
                    "works.goal_line",
                    goal=f.plural(work.goal.amount, "words"),
                    period=f.t(f"goals.per_{work.goal.period.value}"),
                )
            )
        lines.append(f.t("works.hidden_on" if work.hide_title else "works.hidden_off"))
        lines.append(f.t("works.keep_on" if work.keep else "works.keep_off"))
        uid, wid = user.id, work.id
        rows = [
            [button(t("works.rename"), uid, f"wk:rename:{wid}"), button(t("works.set_link"), uid, f"wk:link:{wid}")],
        ]
        second = []
        if work.unit == Unit.WORDS:
            second.append(button(t("works.set_goal"), uid, f"wk:goal:{wid}"))
        if self.module.percent_on:
            second.append(button(t("works.switch_unit"), uid, f"wk:unit:{wid}"))
        if second:
            rows.append(second)
        rows.append(
            [
                button(t("works.toggle_hidden"), uid, f"wk:hide:{wid}"),
                button(t("works.toggle_keep"), uid, f"wk:keep:{wid}"),
            ]
        )
        rows.append(
            [
                button(t("works.delete"), uid, f"wk:del:{wid}", style=KeyboardButtonStyle.DANGER),
                button(t("common.back"), uid, "wk:list"),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    # --- buttons ---------------------------------------------------------------------------------

    async def on_start(self, update: Update, _context, _rest: str) -> None:
        message = update.effective_message
        text, markup = await self.list_view(message.from_user)
        await message.reply_html(text, reply_markup=markup)

    async def _own_work(self, user, work_id: str) -> Work | None:
        if not work_id.isdigit():
            return None
        work = await self.works.get(int(work_id))
        if work is None or work.person_id != await self._person(user) or work.unnamed:
            return None
        return work

    async def on_button(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        user = query.from_user
        action = args[0] if args else ""
        if action == "list":
            await query.answer()
            text, markup = await self.list_view(user)
            await self.bot.asks.edit(sent, text, markup)
            return
        if action == "new":
            await query.answer()
            await self.ask(user, "new", None, "works.ask_name")
            return
        work = await self._own_work(user, args[1]) if len(args) > 1 else None
        if work is None:
            await self.bot.asks.toast(query, "common.stale")
            return
        if action == "w":
            await query.answer()
        elif action in ("rename", "link", "goal"):
            await query.answer()
            await self.ask(
                user,
                action,
                work.id,
                {"rename": "works.ask_name", "link": "works.ask_link", "goal": "works.ask_goal"}[action],
            )
            return
        elif action == "unit":
            work = await self.works.set_unit(work, Unit.PERCENT if work.unit == Unit.WORDS else Unit.WORDS)
            await query.answer()
        elif action == "hide":
            work = await self.works.set_flag(work, "hide_title", not work.hide_title)
            await self.module.rep.refresh_work(work)
            await query.answer()
        elif action == "keep":
            work = await self.works.set_flag(work, "keep", not work.keep)
            await query.answer()
        elif action == "del":
            await query.answer()
            uid = user.id
            markup = InlineKeyboardMarkup(
                [
                    [
                        button(t("works.delete_yes"), uid, f"wk:delok:{work.id}", style=KeyboardButtonStyle.DANGER),
                        button(t("common.cancel"), uid, f"wk:w:{work.id}"),
                    ]
                ]
            )
            await self.bot.asks.edit(sent, self.bot.fmt.t("works.delete_confirm", name=work.name), markup)
            return
        elif action == "delok":
            await self.works.delete(work)
            await self.bot.asks.toast(query, "works.deleted")
            text, markup = await self.list_view(user)
            await self.bot.asks.edit(sent, text, markup)
            return
        else:
            await self.bot.asks.toast(query, "common.stale")
            return
        text, markup = await self.work_view(user, work)
        await self.bot.asks.edit(sent, text, markup)

    # --- typed input -------------------------------------------------------------------------------

    async def ask(self, user, action: str, work_id: int | None, key: str) -> None:
        self._waiting[user.id] = (action, work_id, time.monotonic())
        await self.bot.app.bot.send_message(
            user.id, self.bot.fmt.t(key), reply_markup=ForceReply(input_field_placeholder=t(key + "_placeholder"))
        )

    async def on_input(self, update: Update, _context) -> None:
        message = update.effective_message
        user = message.from_user
        action, work_id, _ = self._waiting.pop(user.id)
        text = (message.text or "").strip()
        person = await self._person(user)
        try:
            if action == "new":
                unit = await self.bot.people.new_work_unit(person) or Unit.WORDS
                work = await self.works.create(person, text, unit if self.module.percent_on else Unit.WORDS)
            else:
                work = await self._own_work(user, str(work_id))
                if work is None:
                    await message.reply_text(t("common.stale"))
                    return
                if action == "rename":
                    work = await self.works.rename(work, text)
                    await self.module.rep.refresh_work(work)
                elif action == "link":
                    work = await self._set_link(work, text)
                elif action == "goal":
                    work = await self.works.set_goal(work, parse_goal(text))
        except (WorkError, ValueError, LinkError) as e:
            await message.reply_text(t("works.input_error", error=e), parse_mode=None)
            self._waiting[user.id] = (action, work_id, time.monotonic())  # let them try again
            return
        view, markup = await self.work_view(user, work)
        await message.reply_html(view, reply_markup=markup)

    async def _set_link(self, work: Work, text: str) -> Work:
        if text.casefold() in words("works.no_words"):
            return await self.works.set_link(work, None, None)
        url = find_url(text)
        if url is None:
            raise ValueError(t("works.not_a_link"))
        count = await self.bot.links.count(url) if self.bot.links is not None else None
        return await self.works.set_link(work, url, count)


def parse_goal(text: str) -> Goal | None:
    """ "1000 на день", "5 000 на тиждень", "20000 на місяць"; "ні" removes the goal."""
    low = " ".join(text.casefold().split())
    if low in words("works.no_words") or low == "0":
        return None
    m = re.match(r"^(\d[\d  ]*)\s*(.*)$", low)
    if not m:
        raise ValueError(t("works.goal_format"))
    amount = int(re.sub(r"\D", "", m[1]))
    rest = m[2].strip()
    for period in GoalPeriod:
        if (not rest and period == GoalPeriod.DAY) or rest in words(f"works.period_{period.value}"):
            return Goal(amount, period)
    raise ValueError(t("works.goal_format"))
