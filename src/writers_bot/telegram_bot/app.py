"""One Telegram bot: the small runtime every bot has, plus its modules.

The runtime holds the config, the command menu and /help (assembled from the modules), Ukrainian command
aliases, the chat allowlist, /start deep links, private asks, and, when the bot has storage, the chat
registry, /profile, your data (/export, /forgetme) and the super-admin tools.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    MessageEntity,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationHandlerStop,
    BaseHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import BaseRequest

from .. import __version__
from ..botconfig import BotConfig
from ..core.fmt import HtmlFormatter
from ..core.i18n import t
from ..core.models import ChatKey
from ..core.sprint import utc_now
from ..services import BotServices
from .admin import AdminTools
from .base import Asks, Command, ProfilePart, TelegramModule, button
from .ephemeral import set_commands
from .yourdata import YourData

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
ALIAS_RE = re.compile(r"^/([^\W\d_A-Za-z][\w'ʼ’]*)(?:@(\w+))?(?:\s+(.*))?$", re.DOTALL)
COMMAND_RE = re.compile(r"^/(\w+)(?:@\w+)?(?:\s|$)")


def chat_key(message: Message) -> ChatKey:
    # message_thread_id is also set for reply threads in ordinary groups, so only topics count (issue #3).
    thread = message.message_thread_id if getattr(message, "is_topic_message", False) else None
    # InaccessibleMessage (buttons on messages older than ~48h) has .chat but no .chat_id.
    return ChatKey(PLATFORM, str(message.chat.id), str(thread) if thread else None)


def thread_of(message: Message) -> int | None:
    return message.message_thread_id if getattr(message, "is_topic_message", False) else None


def command_args_text(message: Message) -> str:
    """Text after the leading /command (works for captions and for Ukrainian aliases too)."""
    text = message.text or message.caption or ""
    entities = message.entities if message.text else message.caption_entities
    for entity in entities or ():
        if entity.type == MessageEntity.BOT_COMMAND and entity.offset == 0:
            # Entity offsets are in UTF-16 code units.
            encoded = text.encode("utf-16-le")
            return encoded[2 * entity.length :].decode("utf-16-le").strip()
    if text.startswith("/"):
        return text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
    return text.strip()


def display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


class TelegramBot:
    def __init__(
        self,
        config: BotConfig,
        services: BotServices,
        *,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if config.telegram is None:
            raise ValueError(f"bot {config.name} has no telegram target")
        self.config = config
        self.settings = config.telegram
        self.services = services
        self.storage = services.storage
        self.people = services.people
        self.links = services.links
        self.now = now
        self.sleep = sleep  # injected by tests, with ``now``, to drive time
        self.fmt = HtmlFormatter(self.settings.common.timezone)
        self.asks = Asks(self)
        self.app: Application | None = None
        self.me = None
        self.started = time.monotonic()
        self.last_update: float | None = None
        self.admin = AdminTools(self.storage, self.settings.super_admins, self) if self.storage is not None else None
        self.modules: list[TelegramModule] = []
        for spec in config.specs():
            part = spec.part(PLATFORM)
            if part is not None:
                self.modules.append(part(self, config.options(spec.name)))
        self.data = YourData(self) if self.storage is not None and self.personal_data else None
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, Command] = {}
        self._asks: dict[str, Callable] = {}
        self._payloads: dict[str, Callable] = {}

    # --- module access -----------------------------------------------------------------------------

    def module(self, name: str) -> TelegramModule | None:
        return next((m for m in self.modules if m.name == name), None)

    @property
    def personal_data(self) -> bool:
        """Does this bot keep data about people that they can export and delete?"""
        return any(getattr(m, "personal_data", False) for m in self.modules)

    @property
    def web(self):
        return self.services.web

    # --- building ----------------------------------------------------------------------------------

    def build(self, *, request: BaseRequest | None = None, updates_request: BaseRequest | None = None) -> Application:
        builder = (
            Application.builder()
            .token(self.settings.token)
            .defaults(Defaults(parse_mode=ParseMode.HTML, tzinfo=self.settings.common.timezone))
        )
        if request is not None:
            builder = builder.request(request).get_updates_request(updates_request or request)
        else:
            builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
        app = builder.build()
        self.app = app
        app.bot_data["writers_bot"] = self
        self.register(app)
        return app

    def all_commands(self) -> list[Command]:
        commands = [
            Command("start", self.start, help=None),
            Command("help", self.help, "commands.help", uk="aliases.help"),
            Command("debuginfo", self.debuginfo),
        ]
        for module in self.modules:
            commands += module.commands()
        if self.profile_enabled:
            commands.append(
                Command(
                    "profile",
                    self.profile,
                    "commands.profile",
                    uk="aliases.profile",
                    aliases=("mystats",),
                    ephemeral=True,
                    help="help.profile",
                )
            )
        if self.data is not None:
            commands += self.data.commands()
        if self.admin is not None and self.config.admin:
            commands += self.admin.commands()
        return commands

    @property
    def profile_enabled(self) -> bool:
        return self.storage is not None and any(getattr(m, "has_profile", False) for m in self.modules)

    def register(self, app: Application) -> None:
        app.add_handler(TypeHandler(Update, self.note_update), group=-3)
        app.add_handler(TypeHandler(Update, self.gate), group=-2)
        if self.storage is not None:
            # Chat registry: runs before everything else and never stops other handlers.
            app.add_handler(TypeHandler(Update, self.admin.track), group=-1)
            app.add_handler(ChatMemberHandler(self.admin.on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER), group=1)

        handlers: list[tuple[BaseHandler, int]] = []
        for command in self.all_commands():
            self._commands[command.name] = command
            names = [command.name, *command.aliases]
            handlers.append((CommandHandler(names, command.callback), 0))
            if command.uk:
                self._aliases[t(command.uk).casefold()] = command
        if self._aliases:
            handlers.append((MessageHandler(filters.TEXT & filters.Regex(ALIAS_RE), self.on_alias), 0))
        for module in self.modules:
            handlers += module.handlers()
            self._asks |= module.asks()
            self._payloads |= module.start_payloads()
        if self.profile_enabled:
            self._asks |= self.profile_asks()
        if self.data is not None:
            self._asks |= self.data.asks()
        handlers.append((CallbackQueryHandler(self.on_owned, pattern=r"^u:-?\d+:"), 0))
        if self.admin is not None and self.config.admin:
            handlers += self.admin.handlers()
        for module in self.modules:
            handlers += getattr(module, "late_handlers", lambda: [])()
        for handler, group in handlers:
            app.add_handler(handler, group=group)
        app.add_error_handler(self.on_error)

    # --- lifecycle -------------------------------------------------------------------------------

    async def post_init(self, app: Application) -> None:
        self.me = await app.bot.get_me()
        await self.set_menus()
        for module in self.modules:
            await module.post_init()

    def menus(self) -> tuple[list[BotCommand], list[BotCommand], list[BotCommand]]:
        """Command menus for groups, private chats and super-admins."""
        group, private, admin = [], [], []
        for c in self.all_commands():
            if c.description is None:
                continue
            entry = BotCommand(c.name, t(c.description))
            if c.scope == "all":
                group.append(entry)
                private.append(entry)
            elif c.scope == "private":
                private.append(entry)
            elif c.scope == "admin":
                admin.append(entry)
        return group, private, [*private, *admin]

    async def set_menus(self) -> None:
        group, private, admin = self.menus()
        bot = self.app.bot
        ephemeral = {c.name for c in self.all_commands() if c.ephemeral}
        try:
            await bot.set_my_commands(private, scope=BotCommandScopeAllPrivateChats())
            if not (
                self.settings.ephemeral_commands
                and await set_commands(bot, group, {"type": "all_group_chats"}, ephemeral)
            ):
                await bot.set_my_commands(group, scope=BotCommandScopeAllGroupChats())
        except TelegramError as e:
            logger.warning("set_my_commands failed: %s", e)
        for admin_id in self.settings.super_admins:
            try:
                await bot.set_my_commands(admin, scope=BotCommandScopeChat(admin_id))
            except TelegramError as e:  # the admin has not started a chat with the bot yet
                logger.info("no admin command menu for %s: %s", admin_id, e)

    async def shutdown(self) -> None:
        for module in self.modules:
            try:
                await module.shutdown()
            except Exception:
                logger.exception("module %s failed to shut down", module.name)
        await self.asks.shutdown()
        if self.admin is not None:
            await self.admin.shutdown()

    def health(self) -> dict:
        updater = self.app.updater if self.app else None
        receiving = (
            self.app is not None
            and self.app.running
            and (not self.settings.polling or (updater is not None and updater.running))
        )
        return {
            "ok": receiving,
            "last_update_s": None if self.last_update is None else round(time.monotonic() - self.last_update),
        }

    async def note_update(self, _update: Update, _context) -> None:
        self.last_update = time.monotonic()

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("error while handling %r", update, exc_info=context.error)

    # --- permissions -----------------------------------------------------------------------------

    def is_allowed(self, chat: Chat, user) -> bool:
        allowed = self.settings.allowed_chats
        if allowed is None or chat.id in allowed:
            return True
        if chat.type != ChatType.PRIVATE:
            return False
        return self.settings.allow_private or (user is not None and user.id in self.settings.super_admins)

    async def gate(self, update: Update, _context) -> None:
        """The chat allowlist, before any handler. /forgetme always works: deleting your data."""
        chat, user = update.effective_chat, update.effective_user
        if chat is None or self.is_allowed(chat, user) or update.my_chat_member is not None:
            return
        message = update.effective_message
        command = COMMAND_RE.match((message.text or "") if message else "")
        if command and command[1] == "forgetme":
            return
        if update.callback_query is not None:
            data = update.callback_query.data or ""
            if data.startswith("u:") and ":fm:" in data:
                return
            with contextlib.suppress(TelegramError):
                await update.callback_query.answer()
        elif command and command[1] == "start" and message is not None:
            await message.reply_text(t("common.not_allowed_chat"), parse_mode=None)
        raise ApplicationHandlerStop

    async def is_chat_admin(self, chat: Chat | int, user_id: int) -> bool:
        if user_id in self.settings.super_admins:
            return True
        chat_id = chat if isinstance(chat, int) else chat.id
        if chat_id > 0:
            return False
        try:
            member = await self.app.bot.get_chat_member(chat_id, user_id)
        except TelegramError:
            return False
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)

    # --- dispatch ------------------------------------------------------------------------------------

    async def on_alias(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Ukrainian command aliases (/промпт, /спринт…). Telegram doesn't treat Cyrillic as commands, so
        these only reach the bot where it sees all messages."""
        message = update.effective_message
        m = ALIAS_RE.match(message.text or "")
        if not m or (m[2] and self.me and m[2].lower() != self.me.username.lower()):
            return
        command = self._aliases.get(m[1].casefold())
        if command is None:
            return
        context.args = (m[3] or "").split()
        await command.callback(update, context)

    async def on_owned(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        _, owner, data = query.data.split(":", 2)
        if str(query.from_user.id) != owner:
            await self.asks.toast(query, "common.not_yours")
            return
        prefix, _, rest = data.partition(":")
        handler = self._asks.get(prefix)
        if handler is None:
            await self.asks.toast(query, "common.stale")
            return
        await handler(query, rest.split(":") if rest else [])

    # --- core commands ---------------------------------------------------------------------------

    def help_text(self) -> str:
        prompts = self.module("prompts")
        override = prompts.config_text("help_message") if prompts is not None else None
        if override:
            return override
        lines = [self.fmt.bold(t("help.title")), ""]
        for c in self.all_commands():
            if c.help:
                lines.append(self.fmt.t(c.help))
        for module in self.modules:
            lines += [self.fmt.t(key) for key in module.help_keys()]
        return "\n".join(lines)

    async def _reply_html_safe(self, message: Message, text: str, **kwargs) -> None:
        try:
            await message.reply_html(text, **kwargs)
        except TelegramError as e:
            if "parse" not in str(e).lower():
                raise
            await message.reply_text(re.sub(r"<[^>]+>", "", text), parse_mode=None, **kwargs)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE and self.people is not None and message.from_user:
            person = await self.people.person_id(PLATFORM, str(message.from_user.id), display_name(message.from_user))
            await self.people.set(person, "can_dm", "1")
        payload = context.args[0] if context.args else ""
        if payload:
            prefix, _, rest = payload.partition("_")
            handler = self._payloads.get(prefix)
            if handler is not None:
                await handler(update, context, rest)
                return
        await self._reply_html_safe(message, self.help_text(), link_preview_options=NO_PREVIEW)

    async def help(self, update: Update, _context) -> None:
        await self._reply_html_safe(update.effective_message, self.help_text(), link_preview_options=NO_PREVIEW)

    async def debuginfo(self, update: Update, _context) -> None:
        message = update.effective_message
        chat, user = message.chat, message.from_user
        state = t("debug.allowed") if self.is_allowed(chat, user) else t("debug.not_allowed")
        lines = [t("debug.chat", id=chat.id, state=state)]
        if message.is_topic_message:
            lines.append(t("debug.topic", id=message.message_thread_id))
        lines.append(t("debug.you", id=user.id, roles=await self._roles(chat, user.id)))
        replied = message.reply_to_message
        if replied and replied.from_user:
            other = replied.from_user
            marks = t("debug.this_bot") if other.id == self.me.id else t("debug.other_bot") if other.is_bot else ""
            lines.append(t("debug.replied", id=other.id, marks=marks, roles=await self._roles(chat, other.id)))
        lines.append(t("debug.version", version=__version__, bot=self.config.name))
        await message.reply_text("\n".join(lines), parse_mode=None)

    async def _roles(self, chat: Chat, user_id: int) -> str:
        roles = ""
        if user_id in self.settings.super_admins:
            roles += t("debug.bot_admin")
        elif chat.type != ChatType.PRIVATE and await self.is_chat_admin(chat, user_id):
            roles += t("debug.chat_admin")
        return roles

    # --- /profile --------------------------------------------------------------------------------

    async def profile(self, update: Update, _context) -> None:
        message = update.effective_message
        text, markup = await self.profile_view(message.from_user, message.chat.type == ChatType.PRIVATE)
        await self.asks.respond(message, text, markup, delete_command=True)

    async def profile_view(self, user, private: bool) -> tuple[str, InlineKeyboardMarkup]:
        person = await self.people.person_id(PLATFORM, str(user.id), display_name(user))
        parts: list[ProfilePart] = []
        for module in self.modules:
            part = await module.profile(user, person, private)
            if part is not None:
                parts.append(part)
        lines = [self.fmt.bold(t("profile.title", name=display_name(user)))]
        rows = []
        for part in parts:
            if part.lines:
                lines += ["", *part.lines]
            if part.buttons:
                rows.append(part.buttons)
        rows.append(
            [
                button(t("profile.button_settings"), user.id, "pf:settings"),
                button(t("common.close"), user.id, "pf:close"),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def profile_asks(self) -> dict:
        return {"pf": self.on_profile_button}

    async def on_profile_button(self, query, args: list[str]) -> None:
        from .base import Sent

        sent = Sent.from_query(query)
        action = args[0] if args else ""
        user = query.from_user
        private = query.message is not None and query.message.chat.type == ChatType.PRIVATE
        if action == "close":
            await query.answer()
            if sent:
                await self.asks.delete(sent)
            return
        if action == "back":
            await query.answer()
            text, markup = await self.profile_view(user, private)
            await self.asks.edit(sent, text, markup)
            return
        if action == "settings":
            await query.answer()
            text, markup = await self.settings_view(user)
            await self.asks.edit(sent, text, markup)
            return
        await self.asks.toast(query, "common.stale")

    async def settings_view(self, user) -> tuple[str, InlineKeyboardMarkup]:
        person = await self.people.person_id(PLATFORM, str(user.id), display_name(user))
        lines = [self.fmt.bold(t("settings.title"))]
        rows = []
        for module in self.modules:
            hook = getattr(module, "settings_rows", None)
            if hook is not None:
                text_lines, module_rows = await hook(user, person)
                lines += text_lines
                rows += module_rows
        if self.data is not None:
            rows.append([button(t("data.button"), user.id, "dt:menu")])
        rows.append([button(t("common.back"), user.id, "pf:back")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def refresh_settings(self, query) -> None:
        from .base import Sent

        text, markup = await self.settings_view(query.from_user)
        await self.asks.edit(Sent.from_query(query), text, markup)

    # --- helpers for modules -----------------------------------------------------------------------

    def deep_link(self, payload: str) -> str:
        return f"https://t.me/{self.me.username}?start={payload}"

    def dm_button(self, text_key: str, payload: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(t(text_key), url=self.deep_link(payload))
