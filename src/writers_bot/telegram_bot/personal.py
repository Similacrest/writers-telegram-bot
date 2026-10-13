"""Personal commands on Telegram: /tz, /goal, /export, /forgetme, and replies only one user should see."""

import contextlib
import logging
import secrets
import time
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, Update
from telegram.constants import ChatType, KeyboardButtonStyle
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from ..core.personal import Personal
from ..core.render import goals_text
from ..core.sprint import utc_now
from .ephemeral import incoming_ephemeral_id, send_ephemeral

if TYPE_CHECKING:
    from .app import WritersTelegramBot

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
FORGET_TTL = 600


class PersonalHandlers:
    def __init__(self, bot: "WritersTelegramBot"):
        self.bot = bot
        self.personal = Personal(bot.storage, PLATFORM, bot.settings.common.timezone)
        self._forget: dict[str, tuple[int, float]] = {}  # token -> (user id, created)

    # --- replies only the sender needs -----------------------------------------------------------

    async def respond(self, message: Message, text: str, *, public_fallback: bool = True, html: bool = True) -> None:
        """Reply to ``message``: ephemerally in groups when possible, else (if allowed) as a normal reply."""
        parse_mode = "HTML" if html else None
        eph = incoming_ephemeral_id(message)
        in_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if self.bot.settings.ephemeral_replies and message.from_user and (eph or in_group):
            thread = message.message_thread_id if message.is_topic_message else None
            if await send_ephemeral(
                message.get_bot(),
                message.chat_id,
                message.from_user.id,
                text,
                reply_to_ephemeral=eph,
                parse_mode=parse_mode,
                message_thread_id=thread,
            ):
                return
        if public_fallback or not in_group:
            await message.reply_text(text, parse_mode=parse_mode)

    # --- /tz, /goal ------------------------------------------------------------------------------

    async def tz(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        text = await self.personal.tz_command(str(message.from_user.id), " ".join(context.args or []), utc_now())
        await self.respond(message, text, html=False)

    async def goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user_id = str(message.from_user.id)
        error = await self.personal.goal_command(user_id, context.args or [], utc_now())
        if error:
            await self.respond(message, error, html=False)
            return
        progress = await self.personal.progress(user_id, utc_now())
        await self.respond(message, goals_text(self.bot.fmt, progress))

    async def report_notes(self, message: Message, sprint_id: int) -> None:
        notes = await self.personal.report_notes(str(message.from_user.id), sprint_id, utc_now())
        if notes:
            await message.reply_text(" ".join(notes), parse_mode=None)

    # --- /export ---------------------------------------------------------------------------------

    async def export(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = message.from_user
        data, count = await self.personal.export_csv(str(user.id))
        if not count:
            await self.respond(message, "У вас ще немає спринтів, нема чого експортувати.", html=False)
            return
        try:  # always to the private chat: the file is personal
            await context.bot.send_document(
                user.id,
                InputFile(data, filename="sprints.csv"),
                caption=f"Ваші спринти: {count}. Кодування UTF-8, відкривається в будь-якій таблиці.",
                parse_mode=None,
            )
        except Forbidden:
            await self.respond(
                message, "Спершу відкрийте особистий чат із ботом (/start), і я надішлю файл туди.", html=False
            )
            return
        if message.chat.type != ChatType.PRIVATE:
            await self.respond(message, "Надіслав файл в особисті повідомлення.", public_fallback=False, html=False)

    # --- /forgetme -------------------------------------------------------------------------------

    async def forgetme(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type != ChatType.PRIVATE:
            await self.respond(message, "Видалити свої дані можна в особистому чаті з ботом: /forgetme.", html=False)
            return
        token = secrets.token_hex(6)
        now = time.monotonic()
        self._forget = {k: v for k, v in self._forget.items() if now - v[1] < FORGET_TTL}
        self._forget[token] = (message.from_user.id, now)
        await message.reply_text(
            "Видалити всі ваші звіти, статистику, цілі й часовий пояс? Це не можна скасувати. "
            "Звіти в спринтах, що ще тривають, лишаться, поки спринт не закінчиться.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Видалити", callback_data=f"fm:go:{token}", style=KeyboardButtonStyle.DANGER
                        ),
                        InlineKeyboardButton("Скасувати", callback_data=f"fm:no:{token}"),
                    ]
                ]
            ),
        )

    async def on_forget_button(self, update: Update, _context) -> None:
        query = update.callback_query
        _, action, token = query.data.split(":", 2)
        pending = self._forget.pop(token, None)
        if pending is None or pending[0] != query.from_user.id or time.monotonic() - pending[1] > FORGET_TTL:
            await query.answer("Це підтвердження вже неактуальне.")
            with contextlib.suppress(TelegramError):
                await query.edit_message_reply_markup(None)
            return
        await query.answer()
        if action == "no":
            await query.edit_message_text("Нічого не видалено.")
            return
        removed = await self.bot.storage.forget_user(PLATFORM, str(query.from_user.id))
        logger.info("user data deleted on request (%d sprint entries)", removed)
        await query.edit_message_text(f"Готово. Видалено записів про спринти: {removed}.")
