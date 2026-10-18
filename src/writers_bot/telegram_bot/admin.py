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

from telegram import (
    Chat,
    ChatMemberUpdated,
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

from ..core.models import ChatRecord, ChatStatus
from ..core.storage import Storage

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # admin output: chat titles and broadcast texts
TRACK_EVERY = timedelta(minutes=10)  # refresh last_seen at most this often per chat
PENDING_TTL = 3600
PROGRESS_EVERY = 5.0  # seconds between progress edits
LIST_AS_FILE_OVER = 60
TARGETS = {"groups": False, "private": True, "all": None}
TARGET_NAMES = {"groups": "у всі групи й канали", "private": "в усі особисті чати", "all": "в усі чати"}

TYPE_LABELS = {"private": "особистий", "group": "група", "supergroup": "група", "channel": "канал"}
STATUS_LABELS = {
    ChatStatus.MEMBER: "",
    ChatStatus.UNKNOWN: ", не перевірено",
    ChatStatus.LEFT: ", бота немає",
    ChatStatus.KICKED: ", заблоковано",
    ChatStatus.MIGRATED: ", перенесено",
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
    def __init__(self, storage: Storage, super_admins: frozenset[int]):
        self.storage = storage
        self.super_admins = super_admins
        self._seen: dict[int, float] = {}
        self._pending: dict[str, PendingBroadcast] = {}
        self._broadcast_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

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
            now = time.monotonic()
            if now - self._seen.get(chat.id, -1e9) < TRACK_EVERY.total_seconds():
                return
            self._seen[chat.id] = now
            await self.storage.upsert_chat(record_from_chat(chat))
        except Exception:
            logger.exception("could not record chat %s", chat.id)

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
            await message.reply_text("Ця команда працює лише в особистому чаті з ботом.")
            return False
        return True

    # --- /chats ----------------------------------------------------------------------------------

    async def chats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_admin(update):
            return
        message = update.effective_message
        arg = (context.args[0].lower() if context.args else "all").strip()
        if arg not in TARGETS:
            await message.reply_text("Використання: /chats [groups|private|all]")
            return
        chats = await self.storage.list_chats(PLATFORM, private=TARGETS[arg])
        if not chats:
            await message.reply_text("Поки що немає записаних чатів.")
            return

        reachable = [c for c in chats if c.status in (ChatStatus.MEMBER, ChatStatus.UNKNOWN)]
        groups = [c for c in reachable if not c.is_private]
        private = [c for c in reachable if c.is_private]
        summary = (
            f"<b>Чати з ботом</b>: {len(reachable)} доступних (груп і каналів: {len(groups)}, особистих: {len(private)})"
            f", ще {len(chats) - len(reachable)} недоступних.\n"
            "Бот бачить лише чати, з яких отримував повідомлення."
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
        excluded = ", без розсилок" if c.broadcast_excluded else ""
        return (
            f"{name} ({TYPE_LABELS.get(c.type, c.type)}) <code>{c.chat_id}</code>, {seen}"
            f"{STATUS_LABELS[c.status]}{excluded}"
        )

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
            await message.reply_html(
                "Використання: у відповідь на повідомлення, яке треба розіслати:\n"
                "<code>/broadcast groups</code> — у групи й канали\n"
                "<code>/broadcast private</code> — в особисті чати\n"
                "<code>/broadcast all</code> — усюди\n\n"
                "Або текстом: <code>/broadcast all Текст оголошення</code>. "
                "Перед надсиланням бот покаже попередній перегляд і попросить підтвердження.\n\n"
                "Чати, позначені через <code>/bcexclude</code>, розсилку не отримують."
            )
            return

        chat_id = message.chat_id
        if message.reply_to_message is not None:
            preview = await context.bot.copy_message(chat_id, chat_id, message.reply_to_message.message_id)
        else:
            text = (message.text or "").split(maxsplit=2)
            if len(text) < 3:
                await message.reply_text("Надішліть команду у відповідь на повідомлення або додайте текст після цілі.")
                return
            preview = await context.bot.send_message(
                chat_id, text[2], parse_mode=None, link_preview_options=NO_PREVIEW
            )

        recipients = await self._recipients(target, exclude=chat_id)
        skipped = await self._excluded_count(target)
        token = secrets.token_hex(6)
        self._expire_pending()
        self._pending[token] = PendingBroadcast(message.from_user.id, target, chat_id, preview.message_id)
        note = f" Без розсилок: {skipped}." if skipped else ""
        await context.bot.send_message(
            chat_id,
            f"Надіслати це повідомлення {TARGET_NAMES[target]}? Отримувачів: <b>{len(recipients)}</b>.{note}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"Надіслати ({len(recipients)})",
                            callback_data=f"bc:go:{token}",
                            style=KeyboardButtonStyle.SUCCESS,
                        ),
                        InlineKeyboardButton("Скасувати", callback_data=f"bc:no:{token}"),
                    ]
                ]
            ),
        )

    async def _recipients(self, target: str, exclude: int | None = None) -> list[ChatRecord]:
        chats = await self.storage.list_chats(PLATFORM, private=TARGETS[target], reachable_only=True)
        return [c for c in chats if c.chat_id != str(exclude) and not c.broadcast_excluded]

    async def _excluded_count(self, target: str) -> int:
        chats = await self.storage.list_chats(PLATFORM, private=TARGETS[target], reachable_only=True)
        return sum(c.broadcast_excluded for c in chats)

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
            usage = (
                "Використання: <code>/bcexclude &lt;id чату&gt; ...</code> прибирає чати з розсилок, "
                "<code>/bcinclude &lt;id чату&gt; ...</code> повертає. Id видно в /chats."
            )
            if not listed:
                await message.reply_html(usage + "\n\nЗараз виключених чатів немає.")
                return
            await self._send_chunks(message, usage + "\n\n<b>Без розсилок:</b>", [self._line(c) for c in listed])
            return
        known = await self.storage.set_broadcast_excluded(PLATFORM, ids, excluded)
        verb = "Виключено з розсилок" if excluded else "Повернуто до розсилок"
        text = f"{verb}: {known}."
        if known < len(ids):
            text += f" Невідомих id: {len(ids) - known} (бот їх ще не бачив)."
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
            await query.answer("Ця розсилка вже неактуальна.")
            with contextlib.suppress(TelegramError):
                await query.edit_message_reply_markup(None)
            return
        del self._pending[token]
        if action == "no":
            await query.answer("Скасовано")
            await query.edit_message_text("Розсилку скасовано.")
            return
        if self._broadcast_lock.locked():
            self._pending[token] = pending
            await query.answer("Інша розсилка ще триває, зачекайте.", show_alert=True)
            return
        await query.answer("Надсилаю…")
        status = await query.edit_message_text("Надсилаю…")
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
                        await status_message.edit_text(f"Надсилаю… {i}/{len(recipients)}")
            summary = [
                "<b>Розсилку завершено.</b>",
                f"Надіслано: {result.sent} з {len(recipients)}",
            ]
            if result.gone:
                summary.append(f"Недоступні (бота видалили чи заблокували): {result.gone}. Їх більше не враховано.")
            if result.failed:
                details = ", ".join(f"{k}: {v}" for k, v in result.errors.items())
                summary.append(f"Помилки: {result.failed} ({self._escape(details)})")
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
