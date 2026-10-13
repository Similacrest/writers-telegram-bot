"""Shows sprints in Telegram chats: one pinned, live-updated sprint message and a results message."""

import contextlib
import html
import logging
from datetime import datetime, tzinfo

from telegram import Bot, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters
from telegram.constants import KeyboardButtonStyle
from telegram.error import BadRequest, Forbidden, TelegramError

from ..core.models import Sprint, SprintStatus, SprintUnit
from ..core.render import report_text, sprint_text
from ..core.sprint import SprintEvent, utc_now

logger = logging.getLogger(__name__)


class HtmlFormatter:
    def __init__(self, tz: tzinfo):
        self.tz = tz

    def escape(self, text: str) -> str:
        return html.escape(str(text), quote=False)

    def bold(self, text: str) -> str:
        return f"<b>{self.escape(text)}</b>"

    def code(self, text: str) -> str:
        return f"<code>{self.escape(text)}</code>"

    def mention(self, user_id: str, name: str) -> str:
        return f'<a href="tg://user?id={int(user_id)}">{self.escape(name)}</a>'

    def time(self, when: datetime) -> str:
        # Bot API 9.5 date_time entity: each reader sees the time in their own timezone.
        fallback = when.astimezone(self.tz).strftime("%H:%M")
        return f'<tg-time unix="{int(when.timestamp())}" format="t">{fallback}</tg-time>'


def _thread(sprint: Sprint) -> int | None:
    return int(sprint.key.thread_id) if sprint.key.thread_id else None


def sprint_keyboard(sprint: Sprint) -> InlineKeyboardMarkup | None:
    if sprint.is_active:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Долучитися", callback_data=f"sp:join:{sprint.id}", style=KeyboardButtonStyle.SUCCESS
                    ),
                    InlineKeyboardButton(
                        "Вийти/скасувати", callback_data=f"sp:leave:{sprint.id}", style=KeyboardButtonStyle.DANGER
                    ),
                ]
            ]
        )
    if sprint.status == SprintStatus.CANCELLED:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Повторити", callback_data=f"sp:repeat:{sprint.planned_duration}:{sprint.delay}")]]
        )
    return None


def report_keyboard(sprint: Sprint) -> InlineKeyboardMarkup:
    repeat = InlineKeyboardButton("Ще раз", callback_data=f"sp:repeat:{sprint.planned_duration}:0")
    if sprint.status == SprintStatus.REPORTING:
        report = InlineKeyboardButton(
            "Звітувати", callback_data=f"sp:report:{sprint.id}", style=KeyboardButtonStyle.PRIMARY
        )
        return InlineKeyboardMarkup([[report, repeat]])
    return InlineKeyboardMarkup([[repeat]])


class TelegramSprintPresenter:
    def __init__(self, fmt: HtmlFormatter):
        self.fmt = fmt
        self.bot: Bot | None = None

    async def show(self, sprint: Sprint, event: SprintEvent, origin=None) -> None:
        handler = {
            SprintEvent.CREATED: self._created,
            SprintEvent.STARTED: self._started,
            SprintEvent.TICK: self._tick,
            SprintEvent.PARTICIPANTS_CHANGED: self._edit_main,
            SprintEvent.CANCELLED: self._cancelled,
            SprintEvent.FINISHED: self._finished,
            SprintEvent.REPORT_UPDATED: self._edit_report,
            SprintEvent.CLOSED: self._closed,
        }[event]
        await handler(sprint, origin)

    # --- helpers ---------------------------------------------------------------------------------

    async def _send(self, sprint: Sprint, text: str, markup=None, reply_to: int | None = None, **kwargs):
        return await self.bot.send_message(
            chat_id=int(sprint.key.chat_id),
            message_thread_id=_thread(sprint),
            text=text,
            reply_markup=markup,
            reply_parameters=ReplyParameters(reply_to, allow_sending_without_reply=True) if reply_to else None,
            **kwargs,
        )

    async def _edit(self, sprint: Sprint, message_id: int | None, text: str, markup=None) -> None:
        if not message_id:
            return
        try:
            await self.bot.edit_message_text(
                chat_id=int(sprint.key.chat_id), message_id=message_id, text=text, reply_markup=markup
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logger.warning("cannot edit sprint %s message %s: %s", sprint.id, message_id, e)

    async def _pin(self, sprint: Sprint, message_id: int) -> None:
        with contextlib.suppress(BadRequest, Forbidden):
            await self.bot.pin_chat_message(int(sprint.key.chat_id), message_id, disable_notification=True)

    async def _unpin(self, sprint: Sprint, message_id: int | None) -> None:
        if message_id:
            with contextlib.suppress(BadRequest, Forbidden):
                await self.bot.unpin_chat_message(int(sprint.key.chat_id), message_id)

    def _main_text(self, sprint: Sprint) -> str:
        return sprint_text(self.fmt, sprint, utc_now())

    # --- events ----------------------------------------------------------------------------------

    async def _created(self, sprint: Sprint, origin) -> None:
        reply_to = getattr(origin, "message_id", None)
        msg = await self._send(sprint, self._main_text(sprint), sprint_keyboard(sprint), reply_to=reply_to)
        sprint.refs["msg"] = msg.message_id
        await self._pin(sprint, msg.message_id)

    async def _started(self, sprint: Sprint, _origin) -> None:
        # A fresh message (instead of an edit) notifies the participants that writing has started.
        old = sprint.refs.get("msg")
        msg = await self._send(sprint, self._main_text(sprint), sprint_keyboard(sprint), reply_to=old)
        sprint.refs["msg"] = msg.message_id
        if old:
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(int(sprint.key.chat_id), old)
        await self._pin(sprint, msg.message_id)

    async def _edit_main(self, sprint: Sprint, _origin=None) -> None:
        await self._edit(sprint, sprint.refs.get("msg"), self._main_text(sprint), sprint_keyboard(sprint))

    async def _tick(self, sprint: Sprint, _origin) -> None:
        await self._edit_main(sprint)

    async def _cancelled(self, sprint: Sprint, _origin) -> None:
        await self._edit_main(sprint)
        await self._unpin(sprint, sprint.refs.get("msg"))

    async def _finished(self, sprint: Sprint, _origin) -> None:
        main = sprint.refs.get("msg")
        await self._edit(sprint, main, self._main_text(sprint), None)
        await self._unpin(sprint, main)
        msg = await self._send(
            sprint, report_text(self.fmt, sprint, utc_now()), report_keyboard(sprint), reply_to=main
        )
        sprint.refs["finish"] = msg.message_id

    async def _edit_report(self, sprint: Sprint, _origin=None) -> None:
        await self._edit(
            sprint, sprint.refs.get("finish"), report_text(self.fmt, sprint, utc_now()), report_keyboard(sprint)
        )

    async def _closed(self, sprint: Sprint, _origin) -> None:
        await self._edit_report(sprint)
        for prompt_id in list(sprint.refs.get("prompts", {})):
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(int(sprint.key.chat_id), int(prompt_id))

    # --- report prompt ---------------------------------------------------------------------------

    async def ask_for_report(self, sprint: Sprint, user_id: int, name: str) -> int:
        who = self.fmt.mention(str(user_id), name)
        if sprint.unit == SprintUnit.PERCENT:
            text = f"{who}, скільки відсотків завдання ви зробили? Відповідайте на це повідомлення числом."
            placeholder = "40%"
        else:
            text = f"{who}, скільки слів ви написали? Відповідайте на це повідомлення числом або посиланням на текст."
            placeholder = "500 або посилання"
        msg = await self._send(
            sprint,
            text,
            ForceReply(selective=True, input_field_placeholder=placeholder),
            reply_to=sprint.refs.get("finish"),
        )
        return msg.message_id
