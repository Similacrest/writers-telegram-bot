"""``/settings`` for chat admins: the chat's settings as toggles in a private card, and a button to the web
app's Chat tab when there is one. Admin rights are checked on every change."""

import contextlib
import time
from typing import TYPE_CHECKING

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ..core.i18n import t
from ..core.settings import SETTINGS, SettingDef, SettingsStore
from .base import Command, Sent, button

if TYPE_CHECKING:
    from .app import TelegramBot

PLATFORM = "telegram"
INPUT_TTL = 10 * 60
LENGTH_STEPS = (-10, -5, 5, 10)


class _AwaitingText(filters.MessageFilter):
    def __init__(self, ui: "ChatSettingsUI"):
        super().__init__(name="settings_text")
        self.ui = ui

    def filter(self, message: Message) -> bool:
        replied = message.reply_to_message
        if replied is None or message.from_user is None:
            return False
        pending = self.ui._pending.get((message.chat.id, replied.message_id))
        return pending is not None and pending[0] == message.from_user.id and time.monotonic() - pending[2] < INPUT_TTL


class ChatSettingsUI:
    def __init__(self, tbot: "TelegramBot", store: SettingsStore):
        self.tbot = tbot
        self.store = store
        self._pending: dict[tuple[int, int], tuple[int, str, float]] = {}  # (chat, prompt) -> (admin, key, since)

    def commands(self) -> list[Command]:
        return [Command("settings", self.command, "commands.settings", scope="chat_admin", help="help.settings")]

    def asks(self) -> dict:
        return {"cs": self.on_button}

    def handlers(self):
        return [(MessageHandler(_AwaitingText(self) & filters.TEXT & ~filters.COMMAND, self.on_text), 0)]

    # --- view ------------------------------------------------------------------------------------

    def _value_text(self, d: SettingDef, value) -> str:
        if d.kind == "bool":
            return t("chatsettings.on") if value else t("chatsettings.off")
        if d.kind == "enum":
            return t(f"{d.locale}_{value}")
        if d.kind == "text":
            return t("chatsettings.text_set") if value else t("chatsettings.text_unset")
        return str(value)

    async def view(self, chat_id: int, admin_id: int) -> tuple[str, InlineKeyboardMarkup]:
        f = self.tbot.fmt
        values = await self.store.resolve(PLATFORM, str(chat_id))
        community = await self.store.communities.of_chat(PLATFORM, str(chat_id))
        lines = [f.bold(t("chatsettings.title"))]
        if community is not None:
            lines.append(f.t("chatsettings.community", name=community.name))
        rows = []
        for d in self.store.defs():
            n = SETTINGS.index(d)  # an index, not the key: callback data holds 64 bytes
            value, source = values[d.key]
            mark = t("chatsettings.inherited") if source == "community" else ""
            label = t(d.locale)
            if d.kind == "bool":
                rows.append(
                    [
                        button(
                            t("chatsettings.toggle", label=label, value=self._value_text(d, value)) + mark,
                            admin_id,
                            f"cs:{chat_id}:{n}:toggle",
                        )
                    ]
                )
            elif d.kind == "enum":
                rows.append(
                    [
                        button(
                            t("chatsettings.toggle", label=label, value=self._value_text(d, value)) + mark,
                            admin_id,
                            f"cs:{chat_id}:{n}:next",
                        )
                    ]
                )
            elif d.kind == "int":
                row = [button(f"{step:+d}", admin_id, f"cs:{chat_id}:{n}:{step}") for step in LENGTH_STEPS[:2]]
                row.append(
                    button(
                        t("chatsettings.number", label=label, value=value) + mark, admin_id, f"cs:{chat_id}:{n}:reset"
                    )
                )
                row += [button(f"{step:+d}", admin_id, f"cs:{chat_id}:{n}:{step}") for step in LENGTH_STEPS[2:]]
                rows.append(row)
            elif d.kind == "text":
                rows.append(
                    [
                        button(
                            t("chatsettings.edit_text", label=label, value=self._value_text(d, value)),
                            admin_id,
                            f"cs:{chat_id}:{n}:edit",
                        )
                    ]
                )
        web = self.tbot.web
        last = []
        if web is not None and self.tbot.me is not None:
            token = web.signer.launch(chat_id, 0)
            last.append(
                InlineKeyboardButton(
                    t("chatsettings.open_web"), url=f"https://t.me/{self.tbot.me.username}?startapp={token}"
                )
            )
        last.append(button(t("common.close"), admin_id, f"cs:{chat_id}:close:x"))
        rows.append(last)
        for module in self.tbot.modules:  # things waiting for the admins (suggested prompts, …)
            hook = getattr(module, "settings_notes", None)
            if hook is not None:
                lines += await hook(chat_id)
        lines.append(f.t("chatsettings.hint"))
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    # --- handlers --------------------------------------------------------------------------------

    async def command(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE:
            text, markup = await self.tbot.settings_view(message.from_user)
            await message.reply_html(text, reply_markup=markup)
            return
        if not await self.tbot.is_chat_admin(message.chat, message.from_user.id):
            await self.tbot.asks.respond(message, t("common.admins_only"), html=False, delete_command=True, ttl=60)
            return
        text, markup = await self.view(message.chat_id, message.from_user.id)
        await self.tbot.asks.respond(message, text, markup, delete_command=True, ttl=10 * 60)

    async def on_button(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        if len(args) < 3:
            await self.tbot.asks.toast(query, "common.stale")
            return
        chat_id, index, action = int(args[0]), args[1], ":".join(args[2:])
        if index == "close":
            await query.answer()
            if sent:
                await self.tbot.asks.delete(sent)
            return
        if not await self.tbot.is_chat_admin(chat_id, query.from_user.id):
            await self.tbot.asks.toast(query, "common.admins_only", alert=True)
            return
        try:
            d = self.store.known(SETTINGS[int(index)].key)
        except (KeyError, IndexError, ValueError):
            await self.tbot.asks.toast(query, "common.stale")
            return
        key = d.key
        current = await self.store.get(PLATFORM, str(chat_id), key)
        if action == "edit":
            await query.answer()
            prompt = await self.tbot.app.bot.send_message(
                chat_id,
                self.tbot.fmt.t(
                    "chatsettings.ask_text",
                    who=self.tbot.fmt.mention(str(query.from_user.id), query.from_user.full_name),
                    label=t(d.locale),
                ),
                reply_markup=ForceReply(selective=True),
                message_thread_id=getattr(query.message, "message_thread_id", None)
                if getattr(query.message, "is_topic_message", False)
                else None,
            )
            self._pending[(chat_id, prompt.message_id)] = (query.from_user.id, key, time.monotonic())
            self.tbot.asks.expire(Sent(chat_id, query.from_user.id, message_id=prompt.message_id), INPUT_TTL)
            return
        if d.kind == "bool" and action == "toggle":
            new = not current
        elif d.kind == "enum" and action == "next":
            new = d.choices[(d.choices.index(current) + 1) % len(d.choices)]
        elif d.kind == "int" and action == "reset":
            new = None
        elif d.kind == "int":
            new = max(d.minimum or 0, min(d.maximum or 10**6, int(current) + int(action)))
        else:
            await self.tbot.asks.toast(query, "common.stale")
            return
        await self.store.set(PLATFORM, str(chat_id), key, new)
        await query.answer(t("chatsettings.saved"))
        text, markup = await self.view(chat_id, query.from_user.id)
        await self.tbot.asks.edit(sent, text, markup)

    async def on_text(self, update: Update, _context) -> None:
        message = update.effective_message
        admin_id, key, _ = self._pending.pop((message.chat_id, message.reply_to_message.message_id))
        if not await self.tbot.is_chat_admin(message.chat, admin_id):
            return
        text = message.text_html or ""
        value = (
            None if text.strip().casefold() in [w.casefold() for w in t("chatsettings.clear_words").split()] else text
        )
        try:
            await self.store.set(PLATFORM, str(message.chat_id), key, value)
        except ValueError:
            await self.tbot.asks.respond(message, t("chatsettings.too_long"), html=False, ttl=60)
            return
        with contextlib.suppress(TelegramError):
            await message.reply_to_message.delete()
        if (await self.tbot.asks.rights(message.chat_id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()
        await self.tbot.asks.respond(message, t("chatsettings.text_saved"), html=False, ttl=60)
