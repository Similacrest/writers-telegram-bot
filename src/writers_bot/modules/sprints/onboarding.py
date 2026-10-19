"""Getting started, in the private chat with the bot: a few steps (what you write, a goal, whether your
numbers show on the board), each one skippable. Progress is kept per person (``ONBOARDING``), so it can be
continued later from the first sprint card, ``/start`` or ``/profile``, which shows how far it got.

A step also counts as done when it was done elsewhere (a work added in the works menu, a goal set on the
website). Goals need the private chat anyway (the bot tells you there when you reach one), which is why the
steps happen there.
"""

import contextlib
import time
from typing import TYPE_CHECKING

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ...core.i18n import t
from ...core.models import Unit
from ...core.people import HIDE_NUMBERS
from ...core.works import WorkError
from ...telegram_bot.app import display_name
from ...telegram_bot.base import Sent, button
from .worksmenu import parse_goal

if TYPE_CHECKING:
    from .telegram import Module

PLATFORM = "telegram"
ONBOARDING = "onboarding"  # person setting: the steps answered or skipped, comma-separated; "done" at the end
INPUT_TTL = 30 * 60


class _Waiting(filters.MessageFilter):
    """A private text answer to the current step's question: only a reply to that question, so nothing else
    typed in the private chat is taken for a work's name or a goal."""

    def __init__(self, onboarding: "Onboarding"):
        super().__init__(name="onboarding_input")
        self.onboarding = onboarding

    def filter(self, message: Message) -> bool:
        if message.chat.type != ChatType.PRIVATE or message.from_user is None or not message.text:
            return False
        waiting = self.onboarding._waiting.get(message.from_user.id)
        if waiting is None or time.monotonic() - waiting[1] >= INPUT_TTL:
            return False
        replied = message.reply_to_message
        return replied is not None and replied.message_id == waiting[2]


class Onboarding:
    def __init__(self, module: "Module"):
        self.module = module
        self.bot = module.bot
        self._waiting: dict[int, tuple[str, float, int]] = {}  # user -> (step, since, question message)

    def handlers(self):
        return [(MessageHandler(_Waiting(self) & ~filters.COMMAND, self.on_input), 0)]

    def asks(self):
        return {"ob": self.on_button}

    def start_payloads(self):
        return {"onboard": self.on_start}

    @property
    def steps(self) -> list[str]:
        return ["work", "goal", "privacy"] if self.module.works_on else ["privacy"]

    async def _person(self, user) -> int:
        return await self.bot.people.person_id(PLATFORM, str(user.id), display_name(user))

    # --- state -----------------------------------------------------------------------------------

    async def done_steps(self, person: int) -> set[str] | None:
        """The steps behind them, or None when they're through."""
        settings = await self.bot.people.settings(person)
        value = settings.get(ONBOARDING) or ""
        if value == "done":
            return None
        done = {s for s in value.split(",") if s}
        if self.module.works_on:
            works = await self.bot.services.works.for_person(person)
            if works:
                done.add("work")
            if any(w.goal is not None for w in works):
                done.add("goal")
        if settings.get(HIDE_NUMBERS) == "1":
            done.add("privacy")
        return done

    async def finished(self, person: int) -> bool:
        return await self.done_steps(person) is None

    async def progress(self, person: int) -> tuple[int, int] | None:
        """(steps done, steps) while not through, for /profile."""
        done = await self.done_steps(person)
        if done is None:
            return None
        return len([s for s in self.steps if s in done]), len(self.steps)

    async def _mark(self, person: int, step: str) -> None:
        done = await self.done_steps(person)
        if done is None:
            return
        done.add(step)
        value = "done" if all(s in done for s in self.steps) else ",".join(sorted(done))
        await self.bot.people.set(person, ONBOARDING, value)

    # --- the steps -------------------------------------------------------------------------------

    async def on_start(self, update: Update, _context, _rest: str) -> None:
        await self.next_step(update.effective_message.from_user, intro=True)

    async def next_step(self, user, *, intro: bool = False, edit: Sent | None = None) -> None:
        """Ask the first step that isn't behind them, or say what's next when all are."""
        f = self.bot.fmt
        person = await self._person(user)
        done = await self.done_steps(person)
        todo = [s for s in self.steps if done is not None and s not in done]
        uid = user.id
        if not todo:
            if done is not None:
                await self.bot.people.set(person, ONBOARDING, "done")
            await self._show(user, f.t("onboarding.done"), None, edit)
            self._waiting.pop(uid, None)
            return
        step = todo[0]
        number = self.steps.index(step) + 1
        lines = [f.t("onboarding.intro", steps=f.plural(len(self.steps), "steps"))] if intro else []
        rows = []
        if step == "work":
            lines.append(f.t("onboarding.ask_work", n=number, total=len(self.steps)))
        elif step == "goal":
            work = await self._goal_work(person)
            if work is None:  # no work to set it for: nothing to ask
                await self._mark(person, "goal")
                await self.next_step(user, intro=intro, edit=edit)
                return
            lines.append(f.t("onboarding.ask_goal", n=number, total=len(self.steps), work=work.name))
        else:
            lines.append(f.t("onboarding.ask_privacy", n=number, total=len(self.steps)))
            rows.append([button(t("onboarding.show"), uid, "ob:show"), button(t("onboarding.hide"), uid, "ob:hide")])
        rows.append(
            [button(t("onboarding.skip"), uid, f"ob:skip:{step}"), button(t("onboarding.later"), uid, "ob:later")]
        )
        sent = await self._show(user, f.lines(lines), InlineKeyboardMarkup(rows), edit)
        if step in ("work", "goal") and sent is not None:
            self._waiting[uid] = (step, time.monotonic(), sent.message_id)
        else:
            self._waiting.pop(uid, None)

    async def _show(self, user, text, markup, edit: Sent | None) -> Sent | None:
        if edit is not None and edit.message_id:
            await self.bot.asks.edit(edit, text, markup)
            return edit
        try:
            msg = await self.bot.app.bot.send_message(user.id, text, parse_mode="HTML", reply_markup=markup)
        except TelegramError:
            return None
        return Sent(user.id, user.id, message_id=msg.message_id)

    async def _goal_work(self, person: int):
        works = await self.bot.services.works.for_person(person)
        return next((w for w in works if w.unit == Unit.WORDS), None)

    async def on_button(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        user = query.from_user
        person = await self._person(user)
        action = args[0] if args else ""
        if action == "next":
            await query.answer()
            await self.next_step(user, edit=sent if sent and sent.private_chat else None)
            return
        if action == "later":
            await query.answer()
            self._waiting.pop(user.id, None)
            if sent is not None:
                await self.bot.asks.edit(sent, self.bot.fmt.t("onboarding.later_text"))
            return
        if action in ("show", "hide"):
            await self.bot.people.set(person, HIDE_NUMBERS, "1" if action == "hide" else None)
            await self.module.rep.refresh_privacy(person, action == "hide")
            await self._mark(person, "privacy")
        elif action == "skip" and len(args) > 1 and args[1] in self.steps:
            await self._mark(person, args[1])
        else:
            await self.bot.asks.toast(query, "common.stale")
            return
        await query.answer()
        await self.next_step(user, edit=sent)

    async def on_input(self, update: Update, _context) -> None:
        message = update.effective_message
        user = message.from_user
        step, _, question_id = self._waiting.pop(user.id)
        person = await self._person(user)
        works = self.bot.services.works
        text = (message.text or "").strip()
        try:
            if step == "work":
                unit = await self.bot.people.new_work_unit(person) or Unit.WORDS
                await works.create(person, text, unit if self.module.percent_on else Unit.WORDS)
            else:
                work = await self._goal_work(person)
                if work is not None:
                    await works.set_goal(work, parse_goal(text))
        except (WorkError, ValueError) as e:
            self._waiting[user.id] = (step, time.monotonic(), question_id)
            await message.reply_text(t("works.input_error", error=e), parse_mode=None)
            return
        await self._mark(person, step)
        # the answered question goes, the next step is a fresh message under the answer
        with contextlib.suppress(TelegramError):
            await self.bot.app.bot.edit_message_reply_markup(user.id, question_id, reply_markup=None)
        await self.next_step(user)
