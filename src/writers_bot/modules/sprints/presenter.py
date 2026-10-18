"""Shows sprints in Telegram chats: one pinned, live-updated sprint message and, at the end, the board.

Two flows: ``reports=False`` is the v2 sprint (a "finished" message with a repeat button, no reports);
the redesigned flow posts a board that takes late reports and is edited as they arrive.
"""

import contextlib
import logging
from collections.abc import Callable

from telegram import Bot, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters
from telegram.constants import KeyboardButtonStyle
from telegram.error import BadRequest, Forbidden, TelegramError

from ...core.board import board_text, sprint_message
from ...core.fmt import HtmlFormatter
from ...core.i18n import t
from ...core.models import Sprint, SprintStatus
from ...core.render import sprint_text
from ...core.reporting import ChatOptions
from ...core.sprint import SprintEvent, utc_now

logger = logging.getLogger(__name__)


def _thread(sprint: Sprint) -> int | None:
    return int(sprint.key.thread_id) if sprint.key.thread_id else None


class TelegramSprintPresenter:
    def __init__(
        self,
        fmt: HtmlFormatter,
        reports: bool = True,
        *,
        panel_button: Callable[[Sprint], InlineKeyboardButton | None] | None = None,
        now: Callable = utc_now,
    ):
        self.fmt = fmt
        self.reports = reports
        self.panel_button = panel_button or (lambda _sprint: None)
        self.now = now
        self.bot: Bot | None = None

    async def show(self, sprint: Sprint, event: SprintEvent, origin=None) -> None:
        handler = {
            SprintEvent.CREATED: self._created,
            SprintEvent.STARTED: self._started,
            SprintEvent.TICK: self._edit_main,
            SprintEvent.PARTICIPANTS_CHANGED: self._edit_main,
            SprintEvent.CANCELLED: self._cancelled,
            SprintEvent.FINISHED: self._finished,
            SprintEvent.REPORT_UPDATED: self._edit_board,
            SprintEvent.CLOSED: self._closed,
        }[event]
        await handler(sprint, origin)

    # --- keyboards -------------------------------------------------------------------------------

    def main_keyboard(self, sprint: Sprint) -> InlineKeyboardMarkup | None:
        if sprint.is_active:
            rows = [
                [
                    InlineKeyboardButton(
                        t("sprint.button_join"),
                        callback_data=f"sp:join:{sprint.id}",
                        style=KeyboardButtonStyle.SUCCESS,
                    ),
                    InlineKeyboardButton(
                        t("sprint.button_leave"),
                        callback_data=f"sp:leave:{sprint.id}",
                        style=KeyboardButtonStyle.DANGER,
                    ),
                ]
            ]
            if self.reports:
                extra = []
                if ChatOptions.from_refs(sprint.refs).works:
                    extra.append(
                        InlineKeyboardButton(t("sprint.button_my_works"), callback_data=f"sp:works:{sprint.id}")
                    )
                if (panel := self.panel_button(sprint)) is not None:
                    extra.append(panel)
                if extra:
                    rows.append(extra)
            return InlineKeyboardMarkup(rows)
        if sprint.status == SprintStatus.CANCELLED:
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t("sprint.button_repeat_cancelled"),
                            callback_data=f"sp:repeat:{sprint.planned_duration}:{sprint.delay}",
                        )
                    ]
                ]
            )
        return None

    def board_keyboard(self, sprint: Sprint) -> InlineKeyboardMarkup:
        repeat = InlineKeyboardButton(
            t("sprint.button_repeat"), callback_data=f"sp:repeat:{sprint.planned_duration}:0"
        )
        if sprint.status == SprintStatus.REPORTING and self.reports:
            rows = [
                [
                    InlineKeyboardButton(
                        t("sprint.button_report"),
                        callback_data=f"sp:report:{sprint.id}",
                        style=KeyboardButtonStyle.PRIMARY,
                    ),
                    InlineKeyboardButton(t("sprint.button_done"), callback_data=f"sp:done:{sprint.id}"),
                    InlineKeyboardButton(t("sprint.button_failed"), callback_data=f"sp:fail:{sprint.id}"),
                ],
                [repeat],
            ]
            if (panel := self.panel_button(sprint)) is not None:
                rows[1].append(panel)
            return InlineKeyboardMarkup(rows)
        return InlineKeyboardMarkup([[repeat]])

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

    def main_text(self, sprint: Sprint) -> str:
        if self.reports:
            return sprint_message(self.fmt, sprint, self.now())
        return sprint_text(self.fmt, sprint, self.now())

    def board(self, sprint: Sprint) -> str:
        return board_text(self.fmt, sprint, self.now(), mention=True)

    # --- events ----------------------------------------------------------------------------------

    async def _created(self, sprint: Sprint, origin) -> None:
        reply_to = getattr(origin, "message_id", None)
        msg = await self._send(sprint, self.main_text(sprint), self.main_keyboard(sprint), reply_to=reply_to)
        sprint.refs["msg"] = msg.message_id
        await self._pin(sprint, msg.message_id)

    async def _started(self, sprint: Sprint, _origin) -> None:
        # A fresh message (instead of an edit) notifies the participants that writing has started.
        old = sprint.refs.get("msg")
        msg = await self._send(sprint, self.main_text(sprint), self.main_keyboard(sprint), reply_to=old)
        sprint.refs["msg"] = msg.message_id
        if old:
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(int(sprint.key.chat_id), old)
        await self._pin(sprint, msg.message_id)

    async def _edit_main(self, sprint: Sprint, _origin=None) -> None:
        await self._edit(sprint, sprint.refs.get("msg"), self.main_text(sprint), self.main_keyboard(sprint))

    async def _cancelled(self, sprint: Sprint, _origin) -> None:
        await self._edit_main(sprint)
        await self._unpin(sprint, sprint.refs.get("msg"))

    async def _finished(self, sprint: Sprint, _origin) -> None:
        main = sprint.refs.get("msg")
        if not self.reports:  # v2: a stopped sprint keeps its message, a finished one gets a new message
            stopped = sprint.stopped_early
            await self._edit(sprint, main, self.main_text(sprint), self.board_keyboard(sprint) if stopped else None)
            await self._unpin(sprint, main)
            if not stopped:
                msg = await self._send(sprint, self.main_text(sprint), self.board_keyboard(sprint), reply_to=main)
                sprint.refs["finish"] = msg.message_id
            return
        # The board takes the sprint message's place, so a finished sprint leaves one message in the chat.
        await self._unpin(sprint, main)
        msg = await self._send(sprint, self.board(sprint), self.board_keyboard(sprint))
        sprint.refs["finish"] = msg.message_id
        if main:
            try:
                await self.bot.delete_message(int(sprint.key.chat_id), main)
            except TelegramError:
                await self._edit(sprint, main, self.main_text(sprint), None)
            else:
                sprint.refs.pop("msg", None)

    async def _edit_board(self, sprint: Sprint, _origin=None) -> None:
        if not sprint.refs.get("finish"):
            return
        await self._edit(sprint, sprint.refs.get("finish"), self.board(sprint), self.board_keyboard(sprint))

    async def _closed(self, sprint: Sprint, _origin) -> None:
        await self._edit_board(sprint)
        for prompt_id in list(sprint.refs.get("prompts", {})):
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(int(sprint.key.chat_id), int(prompt_id))

    # --- the Report button's question ----------------------------------------------------------------

    async def ask_for_report(self, sprint: Sprint, user_id: int, name: str, works: list[str]) -> int:
        who = self.fmt.mention(str(user_id), name)
        if len(works) > 1:
            text = self.fmt.t("report.ask_works", who=who, works=self.fmt.join(works))
        else:
            text = self.fmt.t("report.ask", who=who)
        msg = await self._send(
            sprint,
            text,
            ForceReply(selective=True, input_field_placeholder=t("report.placeholder")),
            reply_to=sprint.refs.get("finish"),
        )
        return msg.message_id
