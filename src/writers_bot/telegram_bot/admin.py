"""Super-admin tools: the list of chats the bot is in, and broadcasting a message to them.

Telegram has no method that lists a bot's chats, so every update is recorded in the ``chats`` table
(throttled), and ``my_chat_member`` updates track joins, removals and users blocking the bot. Chats the
bot has not heard from since this was deployed can be imported once from the old ``db.pickle``
(``scripts/import_legacy_chats.py``).

Broadcasts copy a message (any type, formatting kept) to every reachable chat after the admin confirms.
Chats that turn out to be gone are marked so they are skipped next time.
"""

import asyncio
import contextlib
import csv
import io
import logging
import secrets
import time
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram import (
    Chat,
    ChatMemberUpdated,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, KeyboardButtonStyle
from telegram.error import BadRequest, ChatMigrated, Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from ..core.fmt import HtmlFormatter
from ..core.i18n import t
from ..core.models import ChatRecord, ChatStatus
from ..core.storage import Storage

if TYPE_CHECKING:
    from .app import TelegramBot

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # admin output: chat titles and broadcast texts
TRACK_EVERY = timedelta(minutes=10)  # refresh last_seen at most this often per chat
COMMUNITY_CHECK_EVERY = timedelta(days=1)
PENDING_TTL = 3600
PROGRESS_EVERY = 5.0  # seconds between progress edits
LIST_AS_FILE_OVER = 60
TARGETS = {"groups": False, "private": True, "all": None}
PICK_PAGE = 8  # chats per page when adding one to a community
NAME_TTL = 10 * 60


class _NameWanted:
    """A super-admin's private answer to "what's the community called?" (a ``filters.MessageFilter``)."""

    def __new__(cls, tools: "AdminTools"):
        from telegram.ext import filters

        class Filter(filters.MessageFilter):
            def filter(self, message: Message) -> bool:
                if message.chat.type != ChatType.PRIVATE or message.from_user is None:
                    return False
                waiting = tools._naming.get(message.from_user.id)
                return waiting is not None and time.monotonic() - waiting[1] < NAME_TTL

        return Filter(name="community_name")


_FMT = HtmlFormatter(UTC)
TYPE_KEYS = {
    "private": "admin.type_private",
    "group": "admin.type_group",
    "supergroup": "admin.type_group",
    "channel": "admin.type_channel",
}
STATUS_KEYS = {
    ChatStatus.MEMBER: None,
    ChatStatus.UNKNOWN: "admin.status_unknown",
    ChatStatus.LEFT: "admin.status_left",
    ChatStatus.KICKED: "admin.status_kicked",
    ChatStatus.MIGRATED: "admin.status_migrated",
}


def record_from_chat(chat: Chat, status: ChatStatus = ChatStatus.MEMBER) -> ChatRecord:
    title = chat.title or " ".join(filter(None, [chat.first_name, chat.last_name])) or None
    return ChatRecord(PLATFORM, str(chat.id), chat.type, title, chat.username, status, last_seen=datetime.now(UTC))


def member_status(update: ChatMemberUpdated) -> ChatStatus:
    new = update.new_chat_member
    if new.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return ChatStatus.MEMBER
    if new.status == ChatMemberStatus.RESTRICTED:
        return ChatStatus.MEMBER if getattr(new, "is_member", True) else ChatStatus.LEFT
    if new.status == ChatMemberStatus.BANNED:
        return ChatStatus.KICKED
    return ChatStatus.LEFT


def _retry_seconds(error: RetryAfter) -> float:
    # PTB 22 is moving retry_after from int to timedelta and warns on access; accept both.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value = error.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


@dataclass(slots=True)
class PendingBroadcast:
    admin_id: int
    target: str
    source_chat: int
    source_message: int
    created: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class BroadcastResult:
    sent: int = 0
    gone: int = 0  # bot removed / blocked / chat deleted: marked and skipped from now on
    failed: int = 0
    errors: dict[str, int] = field(default_factory=dict)


class AdminTools:
    def __init__(self, storage: Storage, super_admins: frozenset[int], tbot: "TelegramBot | None" = None):
        # admin -> (community being renamed, or None for a new one; since; the card; the question)
        self._naming: dict[int, tuple[int | None, float, int | None, int]] = {}
        self.storage = storage
        self.super_admins = super_admins
        self.tbot = tbot
        self._seen: dict[int, float] = {}
        self._pending: dict[str, PendingBroadcast] = {}
        self._broadcast_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._fmt = _FMT

    def commands(self):
        from .base import Command

        return [
            Command("chats", self.chats, "commands.chats", scope="admin", help="help.chats"),
            Command("broadcast", self.broadcast, "commands.broadcast", scope="admin", help="help.broadcast"),
            Command("bcexclude", self.exclude, "commands.bcexclude", scope="admin", help="help.bcexclude"),
            Command("bcinclude", self.include, "commands.bcinclude", scope="admin"),
            *(
                [Command("community", self.community, "commands.community", scope="admin", help="help.community")]
                if self.communities
                else []
            ),
        ]

    def handlers(self):
        from telegram.ext import CallbackQueryHandler, MessageHandler, filters

        return [
            (CallbackQueryHandler(self.on_broadcast_button, pattern=r"^bc:(go|no):[0-9a-f]+$"), 0),
            (MessageHandler(_NameWanted(self) & filters.TEXT & ~filters.COMMAND, self.on_community_name), 0),
        ]

    def asks(self) -> dict:
        return {"cm": self.on_community_button} if self.communities is not None else {}

    # --- tracking --------------------------------------------------------------------------------

    async def track(self, update: Update, _context) -> None:
        """Runs before every other handler (group -1)."""
        chat = update.effective_chat
        if chat is None:
            return
        message = update.effective_message
        try:
            if message is not None and message.migrate_to_chat_id:
                await self.storage.set_chat_status(PLATFORM, str(chat.id), ChatStatus.MIGRATED)
                await self.storage.upsert_chat(
                    ChatRecord(PLATFORM, str(message.migrate_to_chat_id), ChatType.SUPERGROUP, chat.title)
                )
                return
            if update.my_chat_member is not None:
                return  # handled by on_my_chat_member
            if (
                message is not None
                and self.communities is not None
                and await self._community_service_message(chat, message)
            ):
                return
            now = time.monotonic()
            if now - self._seen.get(chat.id, -1e9) < TRACK_EVERY.total_seconds():
                return
            self._seen[chat.id] = now
            await self.storage.upsert_chat(record_from_chat(chat))
            if self.communities is not None and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                self._spawn(self._check_community(chat.id))
        except Exception:
            logger.exception("could not record chat %s", chat.id)

    # --- communities -----------------------------------------------------------------------------

    @property
    def communities(self):
        if self.tbot is None or not (self.tbot.config.admin or self.tbot.config.chat_settings):
            return None  # the v2 set has no communities
        return self.tbot.services.shared.extra.get("communities")

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _community_service_message(self, chat: Chat, message: Message) -> bool:
        """Bot API 10.2 service messages (PTB keeps them in ``api_kwargs``). They are the news, so the daily
        check is pushed back rather than letting it undo them."""
        extra = message.api_kwargs or {}
        added = extra.get("community_chat_added")
        if isinstance(added, dict) and isinstance(added.get("community"), dict):
            c = added["community"]
            await self.storage.upsert_chat(record_from_chat(chat))
            await self.communities.link_telegram(str(chat.id), int(c["id"]), str(c.get("name") or ""))
            logger.info("chat %s joined community %s", chat.id, c.get("id"))
        elif "community_chat_removed" in extra:
            await self.storage.upsert_chat(record_from_chat(chat))
            await self.communities.unlink_telegram(str(chat.id))
            logger.info("chat %s left its community", chat.id)
        else:
            return False
        await self.communities.mark_checked(str(chat.id))
        self._seen[chat.id] = time.monotonic()
        return True

    async def _check_community(self, chat_id: int) -> None:
        """At most daily: which Telegram Community the chat belongs to (``ChatFullInfo.community``)."""
        try:
            checked = await self.communities.last_checked(str(chat_id))
            if checked is not None and datetime.now(UTC) - checked < COMMUNITY_CHECK_EVERY:
                return
            full = await self.tbot.app.bot.get_chat(chat_id)
            community = (full.api_kwargs or {}).get("community")
            if isinstance(community, dict) and "id" in community:
                await self.communities.link_telegram(
                    str(chat_id), int(community["id"]), str(community.get("name") or "")
                )
            else:
                await self.communities.unlink_telegram(str(chat_id))
            with contextlib.suppress(TelegramError):
                await self.storage.set_member_count(
                    PLATFORM, str(chat_id), await self.tbot.app.bot.get_chat_member_count(chat_id)
                )
            await self.communities.mark_checked(str(chat_id))
        except Exception as e:
            logger.info("community check for %s failed: %s", chat_id, e)

    async def on_my_chat_member(self, update: Update, _context) -> None:
        change = update.my_chat_member
        status = member_status(change)
        await self.storage.upsert_chat(record_from_chat(change.chat, status))
        self._seen.pop(change.chat.id, None)
        logger.info("bot is now %s in chat %s (%s)", status, change.chat.id, change.chat.type)

    # --- permissions -----------------------------------------------------------------------------

    async def _check_admin(self, update: Update) -> bool:
        message = update.effective_message
        user = update.effective_user
        if user is None or user.id not in self.super_admins:
            return False  # stay silent: don't reveal admin commands
        if update.effective_chat.type != ChatType.PRIVATE:
            await message.reply_text(t("admin.private_only"))
            return False
        return True

    # --- /chats ----------------------------------------------------------------------------------

    async def chats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_admin(update):
            return
        message = update.effective_message
        arg = (context.args[0].lower() if context.args else "all").strip()
        if arg not in TARGETS:
            await message.reply_text(t("admin.chats_usage"))
            return
        chats = await self.storage.list_chats(PLATFORM, private=TARGETS[arg])
        if not chats:
            await message.reply_text(t("admin.no_chats"))
            return

        reachable = [c for c in chats if c.status in (ChatStatus.MEMBER, ChatStatus.UNKNOWN)]
        groups = [c for c in reachable if not c.is_private]
        private = [c for c in reachable if c.is_private]
        summary = self._fmt.t(
            "admin.chats_summary",
            title=self._fmt.bold(t("admin.chats_title")),
            reachable=len(reachable),
            groups=len(groups),
            private=len(private),
            unreachable=len(chats) - len(reachable),
        )
        if len(chats) > LIST_AS_FILE_OVER:
            await message.reply_document(InputFile(self._csv(chats), filename="chats.csv"), caption=summary)
            return
        lines = [self._line(c) for c in chats]
        await self._send_chunks(message, summary, lines)

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _line(self, c: ChatRecord) -> str:
        name = self._escape(c.title or "—")
        if c.username:
            name += f" (@{self._escape(c.username)})"
        seen = c.last_seen.strftime("%Y-%m-%d") if c.last_seen else "?"
        excluded = t("admin.excluded_mark") if c.broadcast_excluded else ""
        status = t(STATUS_KEYS[c.status]) if STATUS_KEYS[c.status] else ""
        kind = t(TYPE_KEYS[c.type]) if c.type in TYPE_KEYS else c.type
        return f"{name} ({self._escape(kind)}) <code>{c.chat_id}</code>, {seen}{self._escape(status + excluded)}"

    @staticmethod
    def _csv(chats: list[ChatRecord]) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["chat_id", "type", "title", "username", "status", "first_seen", "last_seen", "broadcast_excluded"]
        )
        for c in chats:
            writer.writerow(
                [
                    c.chat_id,
                    c.type,
                    c.title or "",
                    c.username or "",
                    c.status.value,
                    c.first_seen.isoformat() if c.first_seen else "",
                    c.last_seen.isoformat() if c.last_seen else "",
                    int(c.broadcast_excluded),
                ]
            )
        return buf.getvalue().encode("utf-8-sig")  # BOM so spreadsheet apps detect UTF-8

    @staticmethod
    async def _send_chunks(message: Message, head: str, lines: list[str], limit: int = 3800) -> None:
        chunk = head
        for line in lines:
            if len(chunk) + len(line) + 1 > limit:
                await message.reply_html(chunk, link_preview_options=NO_PREVIEW)
                chunk = ""
            chunk += ("\n" if chunk else "") + line
        if chunk:
            await message.reply_html(chunk, link_preview_options=NO_PREVIEW)

    # --- /broadcast ------------------------------------------------------------------------------

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_admin(update):
            return
        message = update.effective_message
        args = context.args or []
        target = args[0].lower() if args else ""
        if target not in TARGETS:
            await message.reply_html(self._fmt.t("admin.broadcast_usage"))
            return

        chat_id = message.chat_id
        if message.reply_to_message is not None:
            preview = await context.bot.copy_message(chat_id, chat_id, message.reply_to_message.message_id)
        else:
            text = (message.text or "").split(maxsplit=2)
            if len(text) < 3:
                await message.reply_text(t("admin.broadcast_no_text"))
                return
            preview = await context.bot.send_message(
                chat_id, text[2], parse_mode=None, link_preview_options=NO_PREVIEW
            )

        await self.refresh_member_counts(target)
        recipients = await self._recipients(target, exclude=chat_id)
        skipped = await self._excluded_count(target)
        token = secrets.token_hex(6)
        self._expire_pending()
        self._pending[token] = PendingBroadcast(message.from_user.id, target, chat_id, preview.message_id)
        note = t("admin.broadcast_skipped", n=skipped) if skipped else ""
        await context.bot.send_message(
            chat_id,
            self._fmt.t(
                "admin.broadcast_confirm",
                target=t(f"admin.target_{target}"),
                count=self._fmt.bold(len(recipients)),
                note=note,
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t("admin.broadcast_send", count=len(recipients)),
                            callback_data=f"bc:go:{token}",
                            style=KeyboardButtonStyle.SUCCESS,
                        ),
                        InlineKeyboardButton(t("common.cancel"), callback_data=f"bc:no:{token}"),
                    ]
                ]
            ),
        )

    async def _recipients(self, target: str, exclude: int | None = None) -> list[ChatRecord]:
        """A community gets one broadcast, in its largest chat."""
        chats = await self.storage.list_chats(PLATFORM, private=TARGETS[target], reachable_only=True)
        chats = [c for c in chats if c.chat_id != str(exclude) and not c.broadcast_excluded]
        if self.communities is None:
            return chats
        best: dict[int, ChatRecord] = {}
        out = []
        for c in chats:
            community = None if c.is_private else await self.communities.of_chat(PLATFORM, c.chat_id)
            if community is None:
                out.append(c)
                continue
            current = best.get(community.id)
            if current is None or (c.member_count or 0) > (current.member_count or 0):
                best[community.id] = c
        return out + list(best.values())

    async def refresh_member_counts(self, target: str) -> None:
        """Before choosing a community's largest chat, ask Telegram how big its chats are."""
        if self.communities is None or self.tbot is None:
            return
        for c in await self.storage.list_chats(PLATFORM, private=False, reachable_only=True):
            if await self.communities.of_chat(PLATFORM, c.chat_id) is None:
                continue
            with contextlib.suppress(TelegramError):
                count = await self.tbot.app.bot.get_chat_member_count(int(c.chat_id))
                await self.storage.set_member_count(PLATFORM, c.chat_id, count)

    async def _excluded_count(self, target: str) -> int:
        chats = await self.storage.list_chats(PLATFORM, private=TARGETS[target], reachable_only=True)
        return sum(c.broadcast_excluded for c in chats)

    # --- /community -----------------------------------------------------------------------------

    async def community(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/community [new <name> | add <id> <chat>… | remove <chat>… | rename <id> <name> | delete <id> |
        set <id> <setting> <value|reset>]"""
        if not await self._check_admin(update):
            return
        message = update.effective_message
        args = context.args or []
        f = self._fmt
        c = self.communities
        action = args[0].lower() if args else ""
        try:
            if action == "new" and len(args) > 1:
                created = await c.create(" ".join(args[1:]))
                await message.reply_html(f.t("admin.community_created", id=created.id, name=created.name))
            elif action == "add" and len(args) > 2 and args[1].isdigit():
                if await c.get(int(args[1])) is None:
                    raise ValueError
                for chat_id in args[2:]:
                    await c.add(int(args[1]), PLATFORM, str(int(chat_id)))
                await message.reply_html(f.t("admin.community_added", n=len(args) - 2))
            elif action == "remove" and len(args) > 1:
                removed = [chat_id for chat_id in args[1:] if await c.remove(PLATFORM, str(int(chat_id)))]
                await message.reply_html(f.t("admin.community_removed", n=len(removed)))
            elif action == "rename" and len(args) > 2 and args[1].isdigit():
                await c.rename(int(args[1]), " ".join(args[2:]))
                await message.reply_html(f.t("admin.community_renamed"))
            elif action == "delete" and len(args) == 2 and args[1].isdigit():
                await c.delete(int(args[1]))
                await message.reply_html(f.t("admin.community_deleted"))
            elif action == "set" and len(args) > 3 and args[1].isdigit():
                settings = self.tbot.services.shared.extra.get("settings")
                value = None if args[3].lower() == "reset" else " ".join(args[3:])
                await settings.set_community(int(args[1]), args[2], value)
                await message.reply_html(f.t("admin.community_set", key=args[2], value=value or "—"))
            elif not action:
                text, markup = await self.communities_card(message.from_user.id)
                await message.reply_html(text, reply_markup=markup)
            elif action == "list":
                await self._list_communities(message)
            else:
                await message.reply_html(f.t("admin.community_usage"))
        except (ValueError, KeyError):
            await message.reply_html(f.t("admin.community_usage"))

    async def _list_communities(self, message: Message) -> None:
        f = self._fmt
        communities = await self.communities.all()
        if not communities:
            await message.reply_html(f"{f.t('admin.community_none')}\n\n{f.t('admin.community_usage')}")
            return
        titles = {r.chat_id: r.title for r in await self.storage.list_chats(PLATFORM)}
        lines = []
        for com in communities:
            kind = f.t("admin.community_telegram") if com.telegram_id else f.t("admin.community_manual")
            lines.append(f.bold(f"{com.id}. {com.name}") + f" ({kind})")
            for platform, chat_id in com.chats:
                lines.append(
                    f"  {f.escape(titles.get(chat_id) or '—')} <code>{chat_id}</code>"
                    + (f" ({platform})" if platform != PLATFORM else "")
                )
        await self._send_chunks(message, f.bold(t("admin.community_title")), lines)

    # --- /community with buttons ---------------------------------------------------------------------

    async def _titles(self) -> dict[str, str]:
        return {r.chat_id: r.title or r.chat_id for r in await self.storage.list_chats(PLATFORM, private=False)}

    async def communities_card(self, admin_id: int):
        from .base import button

        f = self._fmt
        communities = await self.communities.all()
        lines = [f.bold(t("admin.community_title"))]
        lines.append(f.t("admin.community_pick") if communities else f.t("admin.community_none"))
        rows = [
            [button(t("admin.community_button", name=c.name, n=len(c.chats)), admin_id, f"cm:c:{c.id}")]
            for c in communities
        ]
        rows.append([button(t("admin.community_new"), admin_id, "cm:new")])
        rows.append([button(t("common.close"), admin_id, "cm:close")])
        lines += ["", f.t("admin.community_commands")]
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def community_card(self, admin_id: int, community_id: int):
        from .base import button

        f = self._fmt
        c = await self.communities.get(community_id)
        if c is None:
            return await self.communities_card(admin_id)
        titles = await self._titles()
        kind = f.t("admin.community_telegram") if c.telegram_id else f.t("admin.community_manual")
        lines = [f.bold(c.name) + f" ({kind})"]
        rows = []
        for platform, chat_id in c.chats:
            lines.append(
                f"• {f.escape(titles.get(chat_id, chat_id))}" + (f" ({platform})" if platform != PLATFORM else "")
            )
            if platform == PLATFORM:
                rows.append([button(t("admin.community_remove", title=titles.get(chat_id, chat_id)), admin_id,
                                    f"cm:rm:{c.id}:{chat_id}")])  # fmt: skip
        if not c.chats:
            lines.append(f.t("admin.community_empty"))
        rows.append([button(t("admin.community_add"), admin_id, f"cm:pick:{c.id}:0")])
        rows.append(
            [
                button(t("admin.community_rename"), admin_id, f"cm:ren:{c.id}"),
                button(t("admin.community_delete"), admin_id, f"cm:del:{c.id}"),
            ]
        )
        rows.append([button(t("common.back"), admin_id, "cm:list")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def _free_chats(self) -> list:
        linked = {chat for c in await self.communities.all() for _, chat in c.chats}
        chats = await self.storage.list_chats(PLATFORM, private=False, reachable_only=True)
        free = [c for c in chats if c.chat_id not in linked and c.type != "channel"]
        return sorted(free, key=lambda c: -(c.member_count or 0))

    async def on_community_button(self, query, args: list[str]) -> None:
        from .base import Sent, button

        sent = Sent.from_query(query)
        admin_id = query.from_user.id
        if admin_id not in self.super_admins:
            await self.tbot.asks.toast(query, "common.admins_only")
            return
        action = args[0] if args else ""
        c = self.communities
        f = self._fmt
        ident = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        await query.answer()
        if action == "close":
            await self.tbot.asks.delete(sent)
            return
        if action == "new" or action == "ren":
            question = await self.tbot.app.bot.send_message(
                admin_id,
                f.t("admin.community_ask_name"),
                reply_markup=ForceReply(input_field_placeholder=t("admin.community_name_placeholder")),
            )
            self._naming[admin_id] = (ident, time.monotonic(), sent.message_id if sent else None, question.message_id)
            return
        if action == "rm" and ident is not None and len(args) > 2:
            await c.remove(PLATFORM, args[2])
        elif action == "add" and ident is not None and len(args) > 2:
            await c.add(ident, PLATFORM, args[2])
        elif action == "delok" and ident is not None:
            await c.delete(ident)
            ident = None
        if action == "del" and ident is not None:
            markup = InlineKeyboardMarkup(
                [[button(t("admin.community_delete_yes"), admin_id, f"cm:delok:{ident}"),
                  button(t("common.cancel"), admin_id, f"cm:c:{ident}")]]
            )  # fmt: skip
            await self.tbot.asks.edit(sent, f.t("admin.community_delete_confirm"), markup)
            return
        if action == "pick" and ident is not None:
            page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 0
            free = await self._free_chats()
            shown = free[page * PICK_PAGE : (page + 1) * PICK_PAGE]
            rows = [[button(ch.title or ch.chat_id, admin_id, f"cm:add:{ident}:{ch.chat_id}")] for ch in shown]
            nav = []
            if (page + 1) * PICK_PAGE < len(free):
                nav.append(button(t("admin.community_more"), admin_id, f"cm:pick:{ident}:{page + 1}"))
            nav.append(button(t("common.back"), admin_id, f"cm:c:{ident}"))
            rows.append(nav)
            text = f.t("admin.community_pick_chat") if free else f.t("admin.community_no_free")
            await self.tbot.asks.edit(sent, text, InlineKeyboardMarkup(rows))
            return
        if ident is not None and action in ("c", "rm", "add"):
            text, markup = await self.community_card(admin_id, ident)
        else:
            text, markup = await self.communities_card(admin_id)
        await self.tbot.asks.edit(sent, text, markup)

    async def on_community_name(self, update: Update, _context) -> None:
        from .base import Sent

        message = update.effective_message
        admin_id = message.from_user.id
        ident, _, card_id, question_id = self._naming.pop(admin_id)
        name = " ".join((message.text or "").split())[:100]
        if not name:
            return
        if ident is None:
            ident = (await self.communities.create(name)).id
        else:
            await self.communities.rename(ident, name)
        bot = self.tbot.app.bot
        for message_id in (question_id, message.message_id):
            with contextlib.suppress(TelegramError):
                await bot.delete_message(admin_id, message_id)
        text, markup = await self.community_card(admin_id, ident)
        if card_id:
            await self.tbot.asks.edit(Sent(admin_id, admin_id, message_id=card_id), text, markup)
        else:
            await bot.send_message(admin_id, text, reply_markup=markup)

    # --- /bcexclude, /bcinclude ------------------------------------------------------------------

    async def exclude(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_excluded(update, context, excluded=True)

    async def include(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_excluded(update, context, excluded=False)

    async def _set_excluded(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, excluded: bool) -> None:
        if not await self._check_admin(update):
            return
        message = update.effective_message
        ids = [a for a in context.args or [] if a.lstrip("-").isdigit()]
        if not ids:
            listed = [c for c in await self.storage.list_chats(PLATFORM) if c.broadcast_excluded]
            usage = self._fmt.t("admin.exclude_usage")
            if not listed:
                await message.reply_html(f"{usage}\n\n{self._fmt.t('admin.exclude_none')}")
                return
            head = f"{usage}\n\n{self._fmt.bold(t('admin.exclude_list'))}"
            await self._send_chunks(message, head, [self._line(c) for c in listed])
            return
        known = await self.storage.set_broadcast_excluded(PLATFORM, ids, excluded)
        text = t("admin.excluded" if excluded else "admin.included", n=known)
        if known < len(ids):
            text += " " + t("admin.unknown_ids", n=len(ids) - known)
        await message.reply_text(text)

    def _expire_pending(self) -> None:
        now = time.monotonic()
        for token, pending in list(self._pending.items()):
            if now - pending.created > PENDING_TTL:
                del self._pending[token]

    async def on_broadcast_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        _, action, token = query.data.split(":", 2)
        pending = self._pending.get(token)
        if query.from_user.id not in self.super_admins or pending is None or pending.admin_id != query.from_user.id:
            await query.answer(t("admin.broadcast_stale"))
            with contextlib.suppress(TelegramError):
                await query.edit_message_reply_markup(None)
            return
        del self._pending[token]
        if action == "no":
            await query.answer(t("admin.cancelled_toast"))
            await query.edit_message_text(t("admin.broadcast_cancelled"))
            return
        if self._broadcast_lock.locked():
            self._pending[token] = pending
            await query.answer(t("admin.broadcast_busy"), show_alert=True)
            return
        await query.answer(t("admin.sending"))
        status = await query.edit_message_text(t("admin.sending"))
        task = asyncio.create_task(self._run_broadcast(context.bot, pending, status))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_broadcast(self, bot, pending: PendingBroadcast, status_message: Message) -> None:
        async with self._broadcast_lock:
            recipients = await self._recipients(pending.target, exclude=pending.source_chat)
            result = BroadcastResult()
            last_progress = time.monotonic()
            logger.info("broadcast by %s to %s: %d chats", pending.admin_id, pending.target, len(recipients))
            for i, chat in enumerate(recipients, 1):
                await self._send_one(bot, pending, chat, result)
                if time.monotonic() - last_progress > PROGRESS_EVERY:
                    last_progress = time.monotonic()
                    with contextlib.suppress(TelegramError):
                        await status_message.edit_text(t("admin.sending_progress", i=i, n=len(recipients)))
            summary = [
                self._fmt.bold(t("admin.broadcast_done")),
                t("admin.broadcast_sent", sent=result.sent, n=len(recipients)),
            ]
            if result.gone:
                summary.append(t("admin.broadcast_gone", n=result.gone))
            if result.failed:
                details = ", ".join(f"{k}: {v}" for k, v in result.errors.items())
                summary.append(self._escape(t("admin.broadcast_failed", n=result.failed, details=details)))
            logger.info("broadcast finished: %s", result)
            try:
                await status_message.edit_text("\n".join(summary))
            except TelegramError:
                await bot.send_message(pending.source_chat, "\n".join(summary))

    async def _send_one(self, bot, pending: PendingBroadcast, chat: ChatRecord, result: BroadcastResult) -> None:
        chat_id = int(chat.chat_id)
        for _attempt in range(3):
            try:
                await bot.copy_message(chat_id, pending.source_chat, pending.source_message)
            except RetryAfter as e:
                await asyncio.sleep(_retry_seconds(e))
                continue
            except ChatMigrated as e:
                await self.storage.set_chat_status(PLATFORM, chat.chat_id, ChatStatus.MIGRATED)
                await self.storage.upsert_chat(
                    ChatRecord(PLATFORM, str(e.new_chat_id), ChatType.SUPERGROUP, chat.title)
                )
                chat_id = e.new_chat_id
                continue
            except Forbidden:
                await self.storage.set_chat_status(PLATFORM, chat.chat_id, ChatStatus.KICKED)
                result.gone += 1
                return
            except BadRequest as e:
                if "chat not found" in str(e).lower():
                    await self.storage.set_chat_status(PLATFORM, chat.chat_id, ChatStatus.LEFT)
                    result.gone += 1
                else:
                    self._fail(result, e)
                return
            except TelegramError as e:
                self._fail(result, e)
                return
            else:
                result.sent += 1
                if chat.status == ChatStatus.UNKNOWN:
                    await self.storage.set_chat_status(PLATFORM, str(chat_id), ChatStatus.MEMBER)
                return
        self._fail(result, TelegramError("retries exhausted"))

    @staticmethod
    def _fail(result: BroadcastResult, error: Exception) -> None:
        result.failed += 1
        key = str(error)[:60] or type(error).__name__
        result.errors[key] = result.errors.get(key, 0) + 1

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
