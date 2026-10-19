"""What Telegram modules are made of: commands, the module interface, and private asks.

**Private asks** are messages meant for one person in a group (a join card, a confirmation, a question).
They go out as ephemeral messages where Telegram allows (a button press or an ephemeral command in the
last 15 seconds, or any time when the bot is a chat admin). Otherwise they are ordinary messages whose
buttons only that person can press, deleted after the tap or after a few minutes. Never a DM.

Buttons on asks carry their owner in the callback data (``u:<user id>:<data>``), so the check survives
restarts and needs no state.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import BaseHandler

from ..core.i18n import t
from .ephemeral import NO_PREVIEW, delete_ephemeral, edit_ephemeral, incoming_ephemeral_id, send_ephemeral

if TYPE_CHECKING:
    from .app import TelegramBot

logger = logging.getLogger(__name__)

ASK_TTL = 5 * 60  # a fallback ask in a group is deleted after this long
RIGHTS_TTL = 10 * 60

Callback = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class HelpContext:
    """Who asks for /help (or whom a command menu is for), and where: what decides the lines and values."""

    bot: "TelegramBot"
    chat_id: int | None  # None: no particular chat (the bot-wide menus)
    private: bool
    chat_admin: bool = False  # of this group; in the private chat: of any group with the bot
    super_admin: bool = False
    _values: dict | None = None

    async def setting(self, key: str):
        """This chat's value of a chat setting (the default outside a group or without settings)."""
        from ..core.settings import BY_KEY

        default = BY_KEY[key].default if key in BY_KEY else None
        settings = self.bot.services.shared.extra.get("settings")
        if settings is None or self.chat_id is None or self.private:
            return default
        if self._values is None:
            resolved = await settings.resolve("telegram", str(self.chat_id))
            self._values = {k: v for k, (v, _source) in resolved.items()}
        return self._values.get(key, default)


@dataclass(frozen=True, slots=True)
class Command:
    """A command a module offers. ``uk`` is its Ukrainian alias (written as ``!команда`` or ``/команда``, matched as
    text, never in the menu).

    The command menus and /help are generated from these, per chat and person: ``description`` is the menu text and
    the /help line (``menu=False`` keeps a command out of the menu but not out of /help), ``usage`` the arguments
    shown after the name, ``module`` the /help submenu it sits in (set from the module that offers it), ``when``
    hides a command where its feature is off (a chat setting), and ``values`` fills the texts of its description and
    usage (this chat's sprint length, its prompt categories)."""

    name: str
    callback: Callback
    description: str | None = None  # locale key; None = neither in the menu nor in /help
    uk: str | None = None  # locale key of the Ukrainian alias
    aliases: tuple[str, ...] = ()  # hidden Latin aliases (/wc for /count)
    # all | private (private chats only) | chat_admin (group admins' menu and /help) | admin (super-admins)
    scope: str = "all"
    ephemeral: bool = False  # marked is_ephemeral in the group menu (EPHEMERAL_COMMANDS)
    menu: bool = True  # in Telegram's command menu (not only in /help)
    usage: str | None = None  # locale key of its arguments: "[хвилин] [затримка]"
    module: str = ""  # the /help submenu: a module's name, or you | chat | admin (see app.HELP_GROUPS)
    when: Callable[[HelpContext], Awaitable[bool]] | None = None
    values: Callable[[HelpContext], Awaitable[dict]] | None = None


@dataclass(slots=True)
class ProfilePart:
    """A module's section of /profile."""

    lines: list[str] = field(default_factory=list)
    buttons: list[InlineKeyboardButton] = field(default_factory=list)


class TelegramModule:
    """Base of a module's Telegram part. Subclasses are named ``Module`` in ``modules/<name>/telegram.py``."""

    name = ""

    def __init__(self, bot: "TelegramBot", options: dict):
        self.bot = bot
        self.options = options

    def commands(self) -> list[Command]:
        return []

    def handlers(self) -> list[tuple[BaseHandler, int]]:
        """Extra handlers (buttons, messages) with their handler group."""
        return []

    async def help_lines(self, ctx: HelpContext) -> list[tuple[str, str, dict]]:
        """/help lines that don't belong to a command, for the module's submenu: (module, locale key, values)."""
        return []

    def asks(self) -> dict[str, Callback]:
        """Owner-only button prefixes this module handles: ``prefix -> handler(query, args)``."""
        return {}

    def start_payloads(self) -> dict[str, Callback]:
        """``/start <payload>`` deep links: ``prefix -> handler(update, context, rest)``."""
        return {}

    async def profile(self, user, person_id: int | None, private: bool) -> ProfilePart | None:
        return None

    async def private_start(self, message: Message) -> None:
        """``/start`` without a payload in the private chat, after the help text."""

    async def post_init(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


# --- private asks ----------------------------------------------------------------------------------


def owned(owner_id: int | str, data: str) -> str:
    return f"u:{owner_id}:{data}"


def button(text: str, owner_id: int | str, data: str, **kwargs) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=owned(owner_id, data), **kwargs)


@dataclass(slots=True)
class Sent:
    """Where a private ask went, to edit or delete it later."""

    chat_id: int
    user_id: int
    message_id: int | None = None  # an ordinary message
    ephemeral_id: int | None = None  # an ephemeral message (-1: sent, id unknown)
    thread_id: int | None = None

    @property
    def private_chat(self) -> bool:
        return self.chat_id == self.user_id

    @classmethod
    def from_query(cls, query: CallbackQuery) -> "Sent | None":
        message = query.message
        if message is None:
            return None
        eph = incoming_ephemeral_id(message) if isinstance(message, Message) else None
        if eph or not message.message_id:
            return cls(message.chat.id, query.from_user.id, ephemeral_id=eph)
        return cls(message.chat.id, query.from_user.id, message_id=message.message_id)


class Asks:
    def __init__(self, tbot: "TelegramBot"):
        self.tbot = tbot
        self._rights: dict[int, tuple[float, bool, bool]] = {}  # chat -> (checked, admin, can_delete)
        self._timers: dict[tuple[int, int], asyncio.Task] = {}

    @property
    def bot(self):
        return self.tbot.app.bot

    async def rights(self, chat_id: int) -> tuple[bool, bool]:
        """(the bot is an admin here, it may delete messages), cached."""
        if chat_id > 0:
            return False, False
        cached = self._rights.get(chat_id)
        if cached and time.monotonic() - cached[0] < RIGHTS_TTL:
            return cached[1], cached[2]
        admin = can_delete = False
        try:
            member = await self.bot.get_chat_member(chat_id, self.bot.id)
            admin = member.status in ("administrator", "creator")
            can_delete = admin and bool(getattr(member, "can_delete_messages", False) or member.status == "creator")
        except TelegramError as e:
            logger.debug("no rights info for %s: %s", chat_id, e)
        self._rights[chat_id] = (time.monotonic(), admin, can_delete)
        return admin, can_delete

    def forget_rights(self, chat_id: int) -> None:
        self._rights.pop(chat_id, None)

    async def send(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        *,
        thread_id: int | None = None,
        callback_query_id: str | None = None,
        reply_to_ephemeral: int | None = None,
        reply_to: int | None = None,
        ttl: float | None = ASK_TTL,
        html: bool = True,
        public: bool = True,
    ) -> Sent | None:
        """Send a private ask. ``public=False``: give up (None) rather than post an ordinary message."""
        parse_mode = "HTML" if html else None
        if chat_id > 0:  # a private chat
            msg = await self.bot.send_message(
                chat_id, text, parse_mode=parse_mode, reply_markup=markup, link_preview_options=NO_PREVIEW
            )
            return Sent(chat_id, user_id, message_id=msg.message_id)
        if self.tbot.settings.ephemeral_replies:
            allowed = bool(callback_query_id or reply_to_ephemeral) or (await self.rights(chat_id))[0]
            if allowed:
                eph = await send_ephemeral(
                    self.bot,
                    chat_id,
                    user_id,
                    text,
                    callback_query_id=callback_query_id,
                    reply_to_ephemeral=reply_to_ephemeral,
                    parse_mode=parse_mode,
                    message_thread_id=thread_id,
                    reply_markup=markup,
                )
                if eph:
                    return Sent(chat_id, user_id, ephemeral_id=eph, thread_id=thread_id)
        if not public:
            return None
        msg = await self.bot.send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            reply_markup=markup,
            link_preview_options=NO_PREVIEW,
            message_thread_id=thread_id,
            reply_parameters=ReplyParameters(reply_to, allow_sending_without_reply=True) if reply_to else None,
        )
        sent = Sent(chat_id, user_id, message_id=msg.message_id, thread_id=thread_id)
        if ttl:
            self.expire(sent, ttl)
        return sent

    def expire(self, sent: Sent, ttl: float) -> None:
        """Delete an ordinary message after ``ttl`` seconds."""
        if sent.message_id is None:
            return
        key = (sent.chat_id, sent.message_id)
        old = self._timers.pop(key, None)
        if old:
            old.cancel()

        async def later():
            await self.tbot.sleep(ttl)
            self._timers.pop(key, None)
            await self.delete(sent)

        self._timers[key] = asyncio.create_task(later(), name=f"expire-{sent.chat_id}-{sent.message_id}")

    async def edit(
        self, sent: Sent, text: str, markup: InlineKeyboardMarkup | None = None, *, html: bool = True
    ) -> None:
        parse_mode = "HTML" if html else None
        if sent.ephemeral_id:
            if sent.ephemeral_id > 0:
                await edit_ephemeral(
                    self.bot,
                    sent.chat_id,
                    sent.user_id,
                    sent.ephemeral_id,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=markup,
                )
            return
        if sent.message_id:
            try:
                await self.bot.edit_message_text(
                    text, chat_id=sent.chat_id, message_id=sent.message_id, parse_mode=parse_mode, reply_markup=markup
                )
            except TelegramError as e:
                if "not modified" not in str(e).lower():
                    logger.debug("cannot edit ask %s: %s", sent, e)

    async def delete(self, sent: Sent) -> None:
        if sent.ephemeral_id:
            if sent.ephemeral_id > 0:
                await delete_ephemeral(self.bot, sent.chat_id, sent.user_id, sent.ephemeral_id)
            return
        if sent.message_id:
            timer = self._timers.pop((sent.chat_id, sent.message_id), None)
            if timer and timer is not asyncio.current_task():
                timer.cancel()
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(sent.chat_id, sent.message_id)

    async def shutdown(self) -> None:
        for task in list(self._timers.values()):
            task.cancel()
        self._timers.clear()

    # --- replies to one person's command ------------------------------------------------------------

    async def respond(
        self,
        message: Message,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        *,
        html: bool = True,
        delete_command: bool = False,
        ttl: float | None = ASK_TTL,
    ) -> Sent | None:
        """Answer a command only its sender needs: in groups ephemeral where possible, else a reply that
        is deleted after a while. ``delete_command`` also removes the command (when the bot may)."""
        chat = message.chat
        user = message.from_user
        if chat.type == ChatType.PRIVATE or user is None:
            msg = await message.reply_text(text, parse_mode="HTML" if html else None, reply_markup=markup)
            return Sent(chat.id, chat.id, message_id=msg.message_id)
        thread = message.message_thread_id if message.is_topic_message else None
        eph = incoming_ephemeral_id(message)
        sent = await self.send(
            chat.id,
            user.id,
            text,
            markup,
            thread_id=thread,
            reply_to_ephemeral=eph,
            reply_to=None if eph else message.message_id,
            ttl=ttl,
            html=html,
        )
        if delete_command and not eph and (await self.rights(chat.id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()
        elif sent is not None and sent.message_id and ttl and not eph:
            self.expire_with(message, ttl)
        return sent

    async def respond_private(
        self,
        message: Message,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        *,
        dm_head: str | None = None,
        delete_command: bool = True,
    ) -> tuple[Sent | None, str]:
        """Answer only the sender, never in public: ephemeral where Telegram allows, else in the private chat with the
        bot (``dm_head`` above the text says which chat it is about). Returns where it went: ``"chat"`` (the
        private chat itself, or an ephemeral message), ``"dm"``, or ``"none"`` (the person never started the bot: a
        short hint with a link goes in the group instead). The command is deleted where the bot may."""
        chat, user = message.chat, message.from_user
        if chat.type == ChatType.PRIVATE or user is None:
            msg = await message.reply_text(text, parse_mode="HTML", reply_markup=markup)
            return Sent(chat.id, chat.id, message_id=msg.message_id), "chat"
        thread = message.message_thread_id if message.is_topic_message else None
        eph = incoming_ephemeral_id(message)
        sent = await self.send(chat.id, user.id, text, markup, thread_id=thread, reply_to_ephemeral=eph, public=False)
        where = "chat"
        if sent is None:
            body = f"{dm_head}\n{text}" if dm_head else text
            try:
                msg = await self.bot.send_message(
                    user.id, body, parse_mode="HTML", reply_markup=markup, link_preview_options=NO_PREVIEW
                )
                sent, where = Sent(user.id, user.id, message_id=msg.message_id), "dm"
            except TelegramError:
                where = "none"
                await self.dm_hint(message)
        if delete_command and not eph and (await self.rights(chat.id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()
        return sent, where

    async def dm_hint(self, message: Message) -> None:
        """A short-lived note in the group: the answer is private, so start a private chat with the bot first."""
        thread = message.message_thread_id if message.is_topic_message else None
        link = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t("common.open_dm_button"), url=self.tbot.deep_link("dm"))]]
        )
        with contextlib.suppress(TelegramError):
            note = await self.bot.send_message(
                message.chat.id, t("common.open_dm"), reply_markup=link, message_thread_id=thread, parse_mode=None
            )
            self.expire(Sent(message.chat.id, message.from_user.id, message_id=note.message_id), 60)

    def expire_with(self, message: Message, ttl: float) -> None:
        """Delete the user's command together with the bot's fallback reply, if the bot may."""

        async def later():
            await self.tbot.sleep(ttl)
            if (await self.rights(message.chat.id))[1]:
                with contextlib.suppress(TelegramError):
                    await message.delete()

        task = asyncio.create_task(later(), name=f"expire-cmd-{message.chat.id}-{message.message_id}")
        self._timers[(message.chat.id, -message.message_id)] = task

    async def toast(self, query: CallbackQuery, key: str, *, alert: bool = False, **values) -> None:
        with contextlib.suppress(TelegramError):
            await query.answer(t(key, **values), show_alert=alert)
