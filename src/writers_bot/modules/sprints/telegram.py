"""Sprints on Telegram.

``reports = false`` is the v2 sprint: join/leave buttons, a "finished" message and a repeat button.
``reports = true`` is the redesign (PLAN phase 3): you join with the button and report by replying to the
sprint message or with the buttons at the end; /progress, /profile and /top; works when ``works = true``.
"""

import asyncio
import contextlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReactionTypeEmoji, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from ...core import sprint as sp
from ...core.board import confirmation, goal_dm_text, minutes_top_text, share_line, week_strip, works_card
from ...core.days import bot_day, next_reset
from ...core.i18n import raw, t
from ...core.models import ChatKey, Outcome, Sprint, SprintStatus, Unit
from ...core.people import DATA_NOTICE, HIDE_NUMBERS
from ...core.reporting import ChatOptions, Reporting, Result, Status
from ...core.reportparse import Item, Op, Parsed, parse
from ...core.sources import find_url
from ...core.stats import history, top_minutes
from ...telegram_bot.app import chat_key, command_args_text, display_name, thread_of
from ...telegram_bot.base import Command, ProfilePart, Sent, TelegramModule, button
from .presenter import TelegramSprintPresenter
from .worksmenu import WorksMenu

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
LEGACY_REPEAT_RE = re.compile(r"^repeat_last_sprint_(\d+)(?:_(\d+))?$")
CARD_TTL = 3 * 60
PROMPT_TTL = 5 * 60
PENDING_TTL = 10 * 60
TOP_DAYS = 30


@dataclass(slots=True)
class Pending:
    """A report waiting for the writer to pick a work (or a sprint, for a % correction)."""

    user_id: int
    name: str
    parsed: Parsed
    chat_id: int
    sprint_id: int | None = None  # None: /progress outside a sprint
    item_index: int | None = None
    choice: dict[int, int] = field(default_factory=dict)
    created: float = field(default_factory=time.monotonic)
    origin: Message | None = None


class _SprintReply(filters.MessageFilter):
    """Replies to this bot's sprint messages, so other reply handlers still get the rest."""

    def __init__(self, module: "Module"):
        super().__init__(name="sprint_reply")
        self.module = module

    def filter(self, message: Message) -> bool:
        return message.reply_to_message is not None and self.module._sprint_for_reply(message) is not None


def user_ref(user) -> sp.UserRef:
    return sp.UserRef(str(user.id), display_name(user))


class Module(TelegramModule):
    name = "sprints"

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.reports = bool(options.get("reports"))
        self.works_on = self.reports and bool(options.get("works"))
        self.percent_on = self.reports and bool(options.get("percent"))
        self.personal_data = self.reports
        self.has_profile = self.reports
        self.presenter = TelegramSprintPresenter(bot.fmt, self.reports, panel_button=self.panel_button, now=bot.now)
        self.engine = sp.SprintManager(
            bot.storage,
            self.presenter,
            platform=PLATFORM,
            report_window=sp.LATE_REPORTS if self.reports else bot.settings.common.report_window,
            reports=self.reports,
            now=bot.now,
            sleep=bot.sleep,
        )
        self.rep = Reporting(
            bot.storage, bot.people, bot.services.works, self.engine, bot.links if self.works_on else None,
            works_enabled=self.works_on, now=bot.now,
        )  # fmt: skip
        if self.reports:
            self.engine.after_finish = self.rep.after_finish
        self.menu = WorksMenu(self) if self.works_on else None
        self._pending: dict[str, Pending] = {}
        self._daily: asyncio.Task | None = None

    # --- wiring ----------------------------------------------------------------------------------

    def commands(self) -> list[Command]:
        commands = [Command("sprint", self.sprint, "commands.sprint", uk="aliases.sprint", help="help.sprint")]
        if self.reports:
            commands += [
                Command(
                    "progress",
                    self.progress,
                    "commands.progress",
                    uk="aliases.progress",
                    ephemeral=True,
                    help="help.progress",
                ),
                Command("top", self.top, "commands.top", uk="aliases.top", help="help.top"),
            ]
        return commands

    def handlers(self):
        # Typed input for the works menu goes first: it is also a reply (to the bot's question).
        handlers = self.menu.handlers() if self.menu is not None else []
        handlers += [
            (CallbackQueryHandler(self.on_join, pattern=r"^(sp:join:\d+|join_sprint)$"), 0),
            (CallbackQueryHandler(self.on_leave, pattern=r"^(sp:leave:\d+|leave_or_cancel_sprint)$"), 0),
            (CallbackQueryHandler(self.on_repeat, pattern=r"^(sp:repeat:\d+:\d+|repeat_last_sprint_\d+(_\d+)?)$"), 0),
        ]
        if self.reports:
            handlers += [
                (CallbackQueryHandler(self.on_report_button, pattern=r"^sp:report:\d+$"), 0),
                (CallbackQueryHandler(self.on_outcome_button, pattern=r"^sp:(done|fail):\d+$"), 0),
                (CallbackQueryHandler(self.on_works_button, pattern=r"^sp:works:\d+$"), 0),
                (MessageHandler(_SprintReply(self) & filters.TEXT & ~filters.COMMAND, self.on_reply), 0),
            ]
        return handlers

    def asks(self):
        asks = {
            "jc": self.on_card,
            "as": self.on_choice,
            "ud": self.on_undo,
            "pg": self.on_panel,
            "st": self.on_setting,
        }
        if self.menu is not None:
            asks |= self.menu.asks()
        return asks if self.reports else {}

    def start_payloads(self):
        return self.menu.start_payloads() if self.menu is not None else {}

    async def post_init(self) -> None:
        self.presenter.bot = self.bot.app.bot
        restored = await self.engine.restore()
        if restored:
            logger.info("restored %d open sprints", restored)
        if self.works_on and self.bot.links is not None:
            self._daily = asyncio.create_task(self._daily_loop(), name="daily-recount")

    async def shutdown(self) -> None:
        if self._daily is not None:
            self._daily.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._daily
        await self.engine.shutdown()

    async def _daily_loop(self) -> None:
        while True:
            delay = (next_reset(self.bot.now()) - self.bot.now()).total_seconds()
            await self.bot.sleep(max(60.0, delay + 30))
            try:
                credited = await self.rep.daily_recount()
                logger.info("daily recount credited %d words", credited)
            except Exception:
                logger.exception("daily recount failed")

    def web_routes(self, web) -> None:
        if self.reports:
            from .web import routes

            routes(self, web)

    def web_tabs(self) -> list[str]:
        return ["sprint", "works", "progress", "settings"] if self.reports else []

    def panel_button(self, sprint: Sprint) -> InlineKeyboardButton | None:
        web = self.bot.web
        if web is None or not self.options.get("web", True):
            return None
        return web.sprint_button(self.bot, sprint)

    # --- chat options ------------------------------------------------------------------------------

    async def chat_options(self, key: ChatKey) -> ChatOptions:
        settings = self.bot.services.shared.extra.get("settings")
        if settings is not None:
            return await settings.sprint_options(key, works=self.works_on, percent=self.percent_on)
        unit = await self.bot.storage.sprint_unit(PLATFORM, key.chat_id) if self.percent_on else Unit.WORDS
        return ChatOptions(works=self.works_on, percent=self.percent_on, default_unit=unit)

    async def default_length(self, chat_id: int) -> int:
        settings = self.bot.services.shared.extra.get("settings")
        if settings is None or self.bot.storage is None:
            return sp.DEFAULT_SPRINT
        return int(await settings.get(PLATFORM, str(chat_id), "sprints.default_length") or sp.DEFAULT_SPRINT)

    async def deletes_reports(self, chat_id: int) -> bool:
        settings = self.bot.services.shared.extra.get("settings")
        if settings is not None and not await settings.get_bool(PLATFORM, str(chat_id), "sprints.delete_reports"):
            return False
        return (await self.bot.asks.rights(chat_id))[1]

    # --- starting, joining, leaving ----------------------------------------------------------------

    @staticmethod
    def _int_arg(args: list[str], index: int, default: int) -> int:
        try:
            return int(args[index])
        except (IndexError, ValueError):
            return default

    async def _start(
        self, message: Message, user, duration: int, delay: int, callback_query_id: str | None = None
    ) -> str | None:
        """Start a sprint; returns an error text or None."""
        key = chat_key(message)
        ref = user_ref(user)
        kwargs = {}
        if self.reports:
            opts = await self.chat_options(key)
            kwargs = {"prepare": self.rep.preparer(ref), "refs": {"opts": opts.to_refs()}}
        try:
            sprint = await self.engine.start(key, ref, duration, delay, origin=message, **kwargs)
        except sp.SprintAlreadyRunningError:
            return t("sprint.already_running")
        except sp.InvalidSprintParamsError as e:
            return str(e)
        if self.reports:
            await self.send_card(sprint, user, callback_query_id=callback_query_id)
        return None

    async def sprint(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        args = context.args or []
        duration = self._int_arg(args, 0, await self.default_length(message.chat_id))
        delay = self._int_arg(args, 1, sp.DEFAULT_SPRINT_DELAY)
        if error := await self._start(message, message.from_user, duration, delay):
            await message.reply_text(error)

    @staticmethod
    def _sprint_from_callback(data: str) -> int | None:
        try:
            return int(data.split(":")[2])
        except (IndexError, ValueError):
            return None

    async def on_join(self, update: Update, _context) -> None:
        query = update.callback_query
        sprint_id = self._sprint_from_callback(query.data) if query.data.startswith("sp:") else None
        ref = user_ref(query.from_user)
        prepare = self.rep.preparer(ref) if self.reports else None
        result, sprint = await self.engine.join(chat_key(query.message), ref, sprint_id, prepare=prepare)
        if (
            result == sp.JoinResult.JOINED
            and self.reports
            and await self.send_card(sprint, query.from_user, callback_query_id=query.id)
        ):
            with contextlib.suppress(TelegramError):
                await query.answer()
            return
        if result == sp.JoinResult.ALREADY and self.reports and self.works_on:
            # pressing it again opens the works card
            if await self.send_card(sprint, query.from_user, callback_query_id=query.id, force=True):
                with contextlib.suppress(TelegramError):
                    await query.answer()
                return
        key = {
            sp.JoinResult.JOINED: "sprint.joined",
            sp.JoinResult.ALREADY: "sprint.already_in",
            sp.JoinResult.CLOSED: "sprint.over",
        }[result]
        await self.bot.asks.toast(query, key)

    async def on_leave(self, update: Update, _context) -> None:
        query = update.callback_query
        message = query.message
        sprint_id = self._sprint_from_callback(query.data) if query.data.startswith("sp:") else None
        result, _ = await self.engine.leave_or_cancel(
            chat_key(message),
            user_ref(query.from_user),
            is_admin=lambda: self.bot.is_chat_admin(message.chat, query.from_user.id),
            sprint_id=sprint_id,
        )
        key = {
            sp.LeaveResult.LEFT: "sprint.left",
            sp.LeaveResult.CANCELLED: "sprint.cancelled",
            sp.LeaveResult.STOPPED: "sprint.stopped_reports" if self.reports else "sprint.stopped",
            sp.LeaveResult.NOT_ALLOWED: "sprint.not_allowed",
            sp.LeaveResult.NOT_PARTICIPANT: "sprint.not_in",
            sp.LeaveResult.CLOSED: "sprint.over",
        }[result]
        await self.bot.asks.toast(query, key)

    async def on_repeat(self, update: Update, _context) -> None:
        query = update.callback_query
        if query.data.startswith("sp:repeat:"):
            parts = query.data.split(":")
            duration, delay = int(parts[2]), int(parts[3])
        else:
            m = LEGACY_REPEAT_RE.match(query.data)
            duration = int(m[1]) if m else sp.DEFAULT_SPRINT
            delay = int(m[2]) if m and m[2] else sp.DEFAULT_SPRINT_DELAY
        error = await self._start(query.message, query.from_user, duration, delay, callback_query_id=query.id)
        with contextlib.suppress(TelegramError):
            await query.answer(error or "")

    # --- the join card -------------------------------------------------------------------------------

    async def _card(self, sprint: Sprint, user) -> tuple[str, InlineKeyboardMarkup, bool] | None:
        """The card's text and buttons, and whether it's worth showing (works to pick, or the notice)."""
        p = sprint.participants.get(str(user.id))
        if p is None or p.person_id is None:
            return None
        settings = await self.bot.people.settings(p.person_id)
        first_time = settings.get(DATA_NOTICE) != "1"
        works = await self.bot.services.works.for_person(p.person_id) if self.works_on else []
        opts = ChatOptions.from_refs(sprint.refs)
        works = [w for w in works if opts.percent or w.unit == Unit.WORDS]
        text = works_card(
            self.bot.fmt, sprint, p, bool(works), first_time=first_time, hides=settings.get(HIDE_NUMBERS) == "1"
        )
        rows = []
        for w in works:
            on = w.id in p.works
            key = ("card.work_on" if on else "card.work_off") + ("_percent" if w.unit == Unit.PERCENT else "")
            rows.append([button(t(key, name=w.name), user.id, f"jc:{sprint.id}:{w.id}")])
        last = [button(t("card.ok"), user.id, f"jc:{sprint.id}:ok")]
        if self.works_on and not works:
            last.append(self.bot.dm_button("card.manage", "works"))
        rows.append(last)
        return text, InlineKeyboardMarkup(rows), bool(works) or first_time

    async def send_card(
        self, sprint: Sprint, user, *, callback_query_id: str | None = None, force: bool = False
    ) -> bool:
        card = await self._card(sprint, user)
        if card is None:
            return False
        text, markup, worth = card
        if not worth and not force:
            return False
        p = sprint.participants[str(user.id)]
        has_works = len(markup.inline_keyboard) > 1
        sent = await self.bot.asks.send(
            int(sprint.key.chat_id),
            user.id,
            text,
            markup,
            thread_id=int(sprint.key.thread_id) if sprint.key.thread_id else None,
            callback_query_id=callback_query_id,
            reply_to=sprint.refs.get("msg"),
            ttl=CARD_TTL,
            # Works to pick are worth an ordinary message only that person can press; the one-time notice
            # alone isn't, so it waits for a moment when it can be private.
            public=has_works or force,
        )
        if sent is not None:
            await self.bot.people.set(p.person_id, DATA_NOTICE, "1")
        return sent is not None

    async def on_works_button(self, update: Update, _context) -> None:
        query = update.callback_query
        sprint = self.engine.get(self._sprint_from_callback(query.data) or -1)
        if sprint is None or str(query.from_user.id) not in sprint.participants:
            await self.bot.asks.toast(query, "sprint.not_in" if sprint else "sprint.over")
            return
        if await self.send_card(sprint, query.from_user, callback_query_id=query.id, force=True):
            with contextlib.suppress(TelegramError):
                await query.answer()
        else:
            await self.bot.asks.toast(query, "common.stale")

    async def on_card(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        sprint = self.engine.get(int(args[0])) if args and args[0].isdigit() else None
        if len(args) > 1 and args[1] == "ok":
            await query.answer()
            if sent:
                await self.bot.asks.delete(sent)
            return
        if sprint is None or len(args) < 2 or not args[1].isdigit():
            await self.bot.asks.toast(query, "sprint.over")
            return
        state = await self.rep.toggle_work(sprint, str(query.from_user.id), int(args[1]))
        if state is None:
            await self.bot.asks.toast(query, "common.stale")
            return
        await query.answer()
        card = await self._card(sprint, query.from_user)
        if card is not None and sent is not None:
            await self.bot.asks.edit(sent, card[0], card[1])

    # --- reports -----------------------------------------------------------------------------------

    def _sprint_for_reply(self, message: Message) -> Sprint | None:
        replied = message.reply_to_message
        if (
            replied is None
            or replied.from_user is None
            or self.bot.me is None
            or replied.from_user.id != self.bot.me.id
        ):
            return None
        rid = replied.message_id
        return self.engine.find(
            lambda s: (
                s.key.chat_id == str(message.chat_id)
                and (s.refs.get("finish") == rid or s.refs.get("msg") == rid or str(rid) in s.refs.get("prompts", {}))
            )
        )

    def _parse(self, text: str, *, allow_day: bool = False) -> Parsed | None:
        return parse(text, find_url, today=bot_day(self.bot.now()), allow_day=allow_day)

    async def on_reply(self, update: Update, _context) -> None:
        """A reply to a sprint message counts only when it is nothing but a report, from someone in it."""
        message = update.effective_message
        sprint = self._sprint_for_reply(message)
        if sprint is None or message.from_user is None or str(message.from_user.id) not in sprint.participants:
            return
        rid = message.reply_to_message.message_id
        prompted = str(rid) in sprint.refs.get("prompts", {})
        parsed = self._parse(message.text or "")
        if parsed is None or parsed.day is not None:
            return
        result = await self.rep.report(sprint, user_ref(message.from_user), parsed)
        if result.status in (Status.UNKNOWN_WORK, Status.NOTHING, Status.NOT_JOINED) and not prompted:
            return  # an ordinary reply that happens to start with a number
        if prompted:
            with contextlib.suppress(TelegramError):
                await message.reply_to_message.delete()
            await self.engine.update_refs(sprint, lambda refs: refs.get("prompts", {}).pop(str(rid), None))
        await self.after_report(message, result, parsed, sprint)

    async def after_report(self, message: Message, result: Result, parsed: Parsed, sprint: Sprint | None) -> None:
        """Feedback after a report in a chat: the report message goes, the confirmation is private."""
        user = message.from_user
        chat_id = message.chat_id
        if result.status == Status.AMBIGUOUS:
            await self.ask_choice(message, result, parsed, sprint)
            return
        if not result.ok:
            await self.bot.asks.respond(message, self.error_text(result), html=True, ttl=60)
            return
        deleted = False
        if message.chat.type != ChatType.PRIVATE and await self.deletes_reports(chat_id):
            with contextlib.suppress(TelegramError):
                await message.delete()
                deleted = True
        if not deleted and message.chat.type != ChatType.PRIVATE:
            with contextlib.suppress(TelegramError):
                await message.set_reaction(ReactionTypeEmoji("👍"))
        text = confirmation(self.bot.fmt, result.changes, result.outcome)
        markup = (
            InlineKeyboardMarkup([[button(t("confirm.undo"), user.id, f"ud:{result.undo}")]]) if result.undo else None
        )
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_html(text, reply_markup=markup)
        else:
            # Only privately: an ephemeral message where the bot may send one; else the 👍 is enough.
            await self.bot.asks.send(
                chat_id, user.id, text, markup, thread_id=thread_of(message), reply_to_ephemeral=None, public=False
            )
        await self.dm_goals(user, result, in_sprint=sprint is not None)

    async def dm_goals(self, user, result: Result, *, in_sprint: bool) -> None:
        """Goals reached outside a sprint are told only to the writer, by DM."""
        if in_sprint or not result.goals:
            return
        for g in result.goals:
            with contextlib.suppress(TelegramError):
                await self.bot.app.bot.send_message(user.id, goal_dm_text(self.bot.fmt, g))

    def error_text(self, result: Result) -> str:
        f = self.bot.fmt
        status = result.status
        if status == Status.LINK_ERROR:
            return f.t("common.link_error", error=result.message or "")
        if status == Status.UNKNOWN_WORK:
            return f.t("report.unknown_work", name=result.hint or "")
        key = {
            Status.NOT_JOINED: "report.not_joined",
            Status.CLOSED: "report.closed",
            Status.NOT_STARTED: "report.not_started",
            Status.BAD_PERCENT: "report.bad_percent",
            Status.PERCENT_OFF: "report.percent_off",
            Status.TOO_MANY: "report.too_many",
            Status.LINK_UNKNOWN: "report.link_unknown",
            Status.TOO_OLD: "progress.too_old",
            Status.NO_PERCENT_SPRINT: "progress.no_percent_sprint",
            Status.NOTHING: "report.nothing",
        }.get(status, "report.nothing")
        return f.t(key, name=result.hint or "")

    # --- asking which work ---------------------------------------------------------------------------

    def _remember(self, pending: Pending) -> str:
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if now - v.created < PENDING_TTL}
        token = secrets.token_hex(4)
        self._pending[token] = pending
        return token

    async def ask_choice(self, message: Message, result: Result, parsed: Parsed, sprint: Sprint | None) -> None:
        user = message.from_user
        pending = Pending(
            user.id, display_name(user), parsed, message.chat_id, sprint.id if sprint else None, result.item_index
        )
        token = self._remember(pending)
        item = parsed.items[result.item_index]
        rows = [[button(w.name or "—", user.id, f"as:{token}:{w.id}") for w in result.candidates[:3]]]
        if sprint is not None:
            rows[0].append(button(t("report.split"), user.id, f"as:{token}:split"))
        f = self.bot.fmt
        text = f.t("report.which_work", value=item.value if item.value is not None else "")
        sent = await self.bot.asks.respond(message, text, InlineKeyboardMarkup(rows))
        if sent is not None and message.chat.type != ChatType.PRIVATE and await self.deletes_reports(message.chat_id):
            with contextlib.suppress(TelegramError):
                await message.delete()

    async def on_choice(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        pending = self._pending.pop(args[0], None) if args else None
        if pending is None:
            await self.bot.asks.toast(query, "common.stale")
            return
        await query.answer()
        if args[1] == "split":
            if sent:
                await self.bot.asks.delete(sent)
            sprint = self.engine.get(pending.sprint_id)
            if sprint is not None:
                await self.ask_report(sprint, query.from_user)
            return
        pending.choice[pending.item_index] = int(args[1])
        ref = sp.UserRef(str(query.from_user.id), pending.name)
        if pending.sprint_id is not None:
            sprint = self.engine.get(pending.sprint_id)
            if sprint is None:
                await self.bot.asks.edit(sent, self.bot.fmt.t("report.closed"))
                return
            result = await self.rep.report(sprint, ref, pending.parsed, pending.choice)
        else:
            result = await self.rep.progress(
                ref, pending.parsed, chat_id=str(pending.chat_id), choice=pending.choice, percent=self.percent_on
            )
        if result.status == Status.AMBIGUOUS:  # a second number with the same question
            pending.item_index = result.item_index
            token = self._remember(pending)
            rows = [[button(w.name or "—", query.from_user.id, f"as:{token}:{w.id}") for w in result.candidates[:3]]]
            item = pending.parsed.items[result.item_index]
            await self.bot.asks.edit(
                sent, self.bot.fmt.t("report.which_work", value=item.value or ""), InlineKeyboardMarkup(rows)
            )
            return
        if not result.ok:
            await self.bot.asks.edit(sent, self.error_text(result))
            return
        text = confirmation(self.bot.fmt, result.changes, result.outcome)
        markup = (
            InlineKeyboardMarkup([[button(t("confirm.undo"), query.from_user.id, f"ud:{result.undo}")]])
            if result.undo
            else None
        )
        await self.bot.asks.edit(sent, text, markup)
        await self.dm_goals(query.from_user, result, in_sprint=pending.sprint_id is not None)

    async def on_undo(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        if not args or not await self.rep.undo(args[0], str(query.from_user.id)):
            await self.bot.asks.toast(query, "confirm.undo_expired", alert=True)
            return
        await query.answer()
        await self.bot.asks.edit(sent, self.bot.fmt.t("confirm.undone"))
        if sent and sent.message_id and not sent.private_chat:
            self.bot.asks.expire(sent, 30)

    # --- buttons at the end --------------------------------------------------------------------------

    async def ask_report(self, sprint: Sprint, user) -> None:
        p = sprint.participants.get(str(user.id))
        labels = [w.label for w in p.works.values() if w.label] if p else []
        prompt_id = await self.presenter.ask_for_report(sprint, user.id, display_name(user), labels)
        await self.engine.update_refs(
            sprint, lambda refs: refs.setdefault("prompts", {}).update({str(prompt_id): str(user.id)})
        )
        # An unanswered question shouldn't sit in the chat until the sprint closes.
        self.bot.asks.expire(Sent(int(sprint.key.chat_id), user.id, message_id=prompt_id), PROMPT_TTL)

    async def on_report_button(self, update: Update, _context) -> None:
        query = update.callback_query
        sprint = self.engine.get(self._sprint_from_callback(query.data) or -1)
        if sprint is None or sprint.status != SprintStatus.REPORTING:
            await self.bot.asks.toast(query, "sprint.reports_closed")
            return
        if str(query.from_user.id) not in sprint.participants:
            await self.bot.asks.toast(query, "sprint.not_in")
            return
        if not self.engine.accepting(sprint, str(query.from_user.id)):
            await self.bot.asks.toast(query, "report.closed", alert=True)
            return
        await query.answer()
        await self.ask_report(sprint, query.from_user)

    async def on_outcome_button(self, update: Update, _context) -> None:
        query = update.callback_query
        sprint = self.engine.get(self._sprint_from_callback(query.data) or -1)
        if sprint is None:
            await self.bot.asks.toast(query, "sprint.reports_closed")
            return
        outcome = Outcome.DONE if query.data.startswith("sp:done") else Outcome.FAILED
        result = await self.rep.set_outcome(sprint, user_ref(query.from_user), outcome)
        if result.status == Status.NOT_JOINED:
            await self.bot.asks.toast(query, "sprint.not_in")
        elif not result.ok:
            await self.bot.asks.toast(query, "report.closed")
        else:
            await self.bot.asks.toast(query, "report.done_toast" if outcome == Outcome.DONE else "report.failed_toast")

    # --- /progress ---------------------------------------------------------------------------------

    async def progress(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = message.from_user
        text = command_args_text(message)
        if not text:
            view, markup = await self.panel(user, message.chat.type != ChatType.PRIVATE)
            await self.bot.asks.respond(message, view, markup, delete_command=True)
            return
        parsed = self._parse(text, allow_day=True)
        if parsed is None:
            await self.bot.asks.respond(message, self.bot.fmt.t("progress.usage"), delete_command=True)
            return
        sprint = None if parsed.day is not None else self.engine.current_for(str(user.id), chat_key(message))
        if (
            sprint is not None
            and sprint.status != SprintStatus.RUNNING
            and sprint.participants[str(user.id)].has_report
        ):
            # After the end, /progress is your late report only until you've reported; words written later
            # are ordinary progress, not the sprint's.
            sprint = None
        if sprint is not None:
            result = await self.rep.report(sprint, user_ref(user), parsed)
        else:
            unit = (
                await self.bot.storage.sprint_unit(PLATFORM, str(message.chat_id)) if self.percent_on else Unit.WORDS
            )
            result = await self.rep.progress(
                user_ref(user), parsed, chat_id=str(message.chat_id), default_unit=unit, percent=self.percent_on
            )
        if result.status == Status.PICK_SPRINT:
            await self.ask_sprint(message, result, parsed)
            return
        await self.after_report(message, result, parsed, sprint)

    async def ask_sprint(self, message: Message, result: Result, parsed: Parsed) -> None:
        user = message.from_user
        pending = Pending(user.id, display_name(user), parsed, message.chat_id)
        token = self._remember(pending)
        tz = self.bot.settings.common.timezone
        rows = [
            [button(at.astimezone(tz).strftime("%d.%m %H:%M"), user.id, f"pg:sprint:{token}:{sid}")]
            for sid, at in result.sprints[:5]
        ]
        await self.bot.asks.respond(
            message, self.bot.fmt.t("progress.which_sprint"), InlineKeyboardMarkup(rows), delete_command=True
        )

    async def panel(self, user, in_group: bool) -> tuple[str, InlineKeyboardMarkup]:
        """/progress without arguments: your progress, privately, with a Share button."""
        f = self.bot.fmt
        person = await self.bot.people.person_id(PLATFORM, str(user.id), display_name(user))
        h = await history(self.bot.storage, person)
        today = bot_day(self.bot.now())
        lines = [f.bold(t("progress.title"))]
        start_week = today - timedelta(days=today.weekday())
        lines.append(
            f.t(
                "progress.summary",
                today=f.plural(h.words(today, today), "words"),
                week=f.plural(h.words(start_week, today), "words"),
                month=f.plural(h.words(today.replace(day=1), today), "words"),
            )
        )
        streak = h.streak(today)
        if streak:
            lines.append(f.t("progress.streak", days=f.plural(streak, "days")))
        for line in await self.goal_lines(person, h, today):
            lines.append(line)
        lines += ["", f.t("progress.hint")]
        rows = []
        period = h.share_period(today)
        if in_group and period is not None:
            rows.append([button(t("progress.share"), user.id, "pg:share")])
        rows.append([button(t("common.close"), user.id, "pg:close")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def goal_lines(self, person: int, h, today) -> list[str]:
        f = self.bot.fmt
        lines = []
        if not self.works_on:
            return lines
        for w in await self.bot.services.works.for_person(person):
            if w.goal is None:
                continue
            done = h.work_period(w, today, w.goal.period)
            lines.append(
                f.t(
                    "progress.goal",
                    work=f.t("common.quoted", text=w.name),
                    done=done,
                    goal=f.plural(w.goal.amount, "words"),
                    period=f.t(f"goals.per_{w.goal.period.value}"),
                    percent=min(999, round(100 * done / w.goal.amount)),
                )
            )
        return lines

    async def share_text(self, user) -> str | None:
        f = self.bot.fmt
        person = await self.bot.people.person_id(PLATFORM, str(user.id), display_name(user))
        h = await history(self.bot.storage, person)
        today = bot_day(self.bot.now())
        period = h.share_period(today)
        if period is None:
            return None
        goal = None
        if self.works_on:
            for w in await self.bot.services.works.for_person(person):
                if w.goal is not None and not w.hide_title:
                    done = h.work_period(w, today, w.goal.period)
                    goal = f.t(
                        "share.goal",
                        work=f.t("common.quoted", text=w.name),
                        percent=min(999, round(100 * done / w.goal.amount)),
                    )
                    break
        return share_line(f, display_name(user), period, h.streak(today), goal)

    async def on_panel(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        action = args[0] if args else ""
        if action == "close":
            await query.answer()
            if sent:
                await self.bot.asks.delete(sent)
        elif action == "share":
            text = await self.share_text(query.from_user)
            if text is None or query.message is None:
                await self.bot.asks.toast(query, "progress.nothing_to_share")
                return
            await query.answer()
            message = query.message
            await self.bot.app.bot.send_message(
                message.chat.id,
                text,
                message_thread_id=getattr(message, "message_thread_id", None)
                if getattr(message, "is_topic_message", False)
                else None,
            )
            if sent:
                await self.bot.asks.delete(sent)
        elif action == "fill" and len(args) > 1:
            parsed = Parsed([Item(Op.DONE)], day=date.fromisoformat(args[1]))
            result = await self.rep.progress(user_ref(query.from_user), parsed, percent=self.percent_on)
            await self.bot.asks.toast(query, "progress.filled" if result.ok else "progress.too_old")
            if result.ok and sent:
                private = query.message is not None and query.message.chat.type == ChatType.PRIVATE
                text, markup = await self.bot.profile_view(query.from_user, private)
                await self.bot.asks.edit(sent, text, markup)
        elif action == "open":
            await query.answer()
            in_group = query.message is not None and query.message.chat.type != ChatType.PRIVATE
            text, markup = await self.panel(query.from_user, in_group)
            await self.bot.asks.edit(sent, text, markup)
        elif action == "sprint" and len(args) > 2:
            pending = self._pending.pop(args[1], None)
            if pending is None:
                await self.bot.asks.toast(query, "common.stale")
                return
            await query.answer()
            ref = sp.UserRef(str(query.from_user.id), pending.name)
            result = await self.rep.progress(
                ref, pending.parsed, chat_id=str(pending.chat_id), percent=self.percent_on, sprint_choice=int(args[2])
            )
            text = confirmation(self.bot.fmt, result.changes, result.outcome) if result.ok else self.error_text(result)
            markup = (
                InlineKeyboardMarkup([[button(t("confirm.undo"), query.from_user.id, f"ud:{result.undo}")]])
                if result.undo
                else None
            )
            await self.bot.asks.edit(sent, text, markup)
        else:
            await self.bot.asks.toast(query, "common.stale")

    # --- /top ---------------------------------------------------------------------------------------

    async def top(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        days = min(366, max(1, self._int_arg(context.args or [], 0, TOP_DAYS)))
        settings = self.bot.services.shared.extra.get("settings")
        chats = [str(message.chat_id)]
        if settings is not None:
            if not await settings.get_bool(PLATFORM, str(message.chat_id), "sprints.top"):
                await message.reply_text(t("top.off"))
                return
            chats = await settings.community_chats(PLATFORM, str(message.chat_id))
        entries = await top_minutes(self.bot.storage, PLATFORM, chats, self.bot.now() - timedelta(days=days))
        await message.reply_html(minutes_top_text(self.bot.fmt, entries, days))

    # --- /profile ------------------------------------------------------------------------------------

    async def profile(self, user, person_id: int | None, private: bool) -> ProfilePart | None:
        if not self.reports or person_id is None:
            return None
        f = self.bot.fmt
        h = await history(self.bot.storage, person_id)
        today = bot_day(self.bot.now())
        month_start = today.replace(day=1)
        lines = []
        if self.works_on:
            works = await self.bot.services.works.for_person(person_id)
            for w in works:
                if w.unit == Unit.WORDS:
                    amount = f.plural(h.words(month_start, today, w.id), "words")
                else:
                    amount = f.t("profile.percent_work")
                goal = ""
                if w.goal is not None:
                    done = h.work_period(w, today, w.goal.period)
                    goal = f.t(
                        "profile.goal",
                        done=done,
                        goal=f.plural(w.goal.amount, "words"),
                        period=f.t(f"goals.per_{w.goal.period.value}"),
                    )
                hidden = f.t("profile.hidden_title") if w.hide_title else ""
                lines.append(
                    f.t(
                        "profile.work", work=f.t("common.quoted", text=w.name), amount=amount, goal=goal, hidden=hidden
                    )
                )
            if not works:
                lines.append(f.t("profile.no_works"))
        month_words = h.words(month_start, today)
        lines.append(f.t("profile.month", words=f.bold(f.plural(month_words, "words"))))
        streak = h.streak(today)
        lines.append(f.t("profile.streak", days=f.plural(streak, "days")) if streak else f.t("profile.no_streak"))
        lines.append(week_strip(f, h.last_days(today)))
        done = h.completed()
        lines.append(
            f.t("profile.sprints", sprints=f.plural(len(done), "sprints"), minutes=f.plural(h.minutes(), "minutes"))
        )
        hides = await self.bot.people.hides_numbers(person_id)
        lines.append(f.t("profile.numbers_hidden" if hides else "profile.numbers_shown"))
        buttons = [button(t("profile.button_progress"), user.id, "pg:open")]
        names = raw("profile.weekdays")
        for gap in h.fillable_gaps(today)[:2]:
            buttons.append(button(t("profile.fill", day=names[gap.weekday()]), user.id, f"pg:fill:{gap.isoformat()}"))
        if self.menu is not None:
            buttons.append(self.menu.entry_button(user, private))
        return ProfilePart(lines, buttons)

    async def settings_rows(self, user, person_id: int):
        if not self.reports:
            return [], []
        f = self.bot.fmt
        settings = await self.bot.people.settings(person_id)
        hides = settings.get(HIDE_NUMBERS) == "1"
        lines = ["", f.t("settings.hide_explained")]
        rows = [[button(t("settings.hide_on" if hides else "settings.hide_off"), user.id, "st:hide")]]
        if self.percent_on:
            unit = settings.get("new_work_unit")
            key = {"words": "settings.unit_words", "percent": "settings.unit_percent"}.get(unit, "settings.unit_chat")
            rows.append([button(t(key), user.id, "st:unit")])
        return lines, rows

    async def on_setting(self, query, args: list[str]) -> None:
        user = query.from_user
        person = await self.bot.people.person_id(PLATFORM, str(user.id), display_name(user))
        action = args[0] if args else ""
        if action == "hide":
            hides = not await self.bot.people.hides_numbers(person)
            await self.bot.people.set(person, HIDE_NUMBERS, "1" if hides else None)
            await self.rep.refresh_privacy(person, hides)
            await self.bot.asks.toast(query, "settings.hide_on_toast" if hides else "settings.hide_off_toast")
        elif action == "unit":
            current = (await self.bot.people.settings(person)).get("new_work_unit")
            nxt = {None: "words", "words": "percent", "percent": None}[
                current if current in ("words", "percent") else None
            ]
            await self.bot.people.set(person, "new_work_unit", nxt)
            await query.answer()
        else:
            await self.bot.asks.toast(query, "common.stale")
            return
        await self.bot.refresh_settings(query)
