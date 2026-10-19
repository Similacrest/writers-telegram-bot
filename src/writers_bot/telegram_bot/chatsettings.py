"""``/settings`` for chat admins: a short private card with the chat's settings in four sections, each opened
in place, and a button to the web app's Chat tab when there is one. Admin rights are checked on every change.

Text settings (the welcome message) are typed in the private chat with the bot: their button is a link
there that carries the chat (``/start settext_<chat>_<setting>``), so nothing is typed into the group."""

import time
from typing import TYPE_CHECKING

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType
from telegram.ext import MessageHandler, filters

from ..core.i18n import t
from ..core.settings import SETTINGS, SettingDef, SettingsStore
from .base import Command, Sent, button

if TYPE_CHECKING:
    from .app import TelegramBot

PLATFORM = "telegram"
INPUT_TTL = 10 * 60
LENGTH_STEPS = (-10, -5, 5, 10)
# The card's sections (locale: chatsettings.section_<name>) and their settings, in order
SECTIONS = (
    ("sprints", ("sprints.default_length", "sprints.works", "sprints.percent", "sprints.default_unit",
                 "sprints.work_names", "sprints.top")),
    ("privacy", ("sprints.hidden_ok", "sprints.delete_reports")),
    ("prompts", ("prompts.use_shared", "replies.defaults")),
    ("welcome", ("greeter.text", "achievements.enabled")),
    ("dice", ("dice.forwards",)),
)  # fmt: skip


class _AwaitingText(filters.MessageFilter):
    """A private message from an admin the bot asked for a text setting."""

    def __init__(self, ui: "ChatSettingsUI"):
        super().__init__(name="settings_text")
        self.ui = ui

    def filter(self, message: Message) -> bool:
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return False
        pending = self.ui._pending.get(message.from_user.id)
        return pending is not None and time.monotonic() - pending[2] < INPUT_TTL


class ChatSettingsUI:
    def __init__(self, tbot: "TelegramBot", store: SettingsStore):
        self.tbot = tbot
        self.store = store
        self._pending: dict[int, tuple[int, str, float]] = {}  # admin -> (chat, setting key, since)

    def commands(self) -> list[Command]:
        return [Command("settings", self.command, "commands.settings", scope="chat_admin")]

    def asks(self) -> dict:
        return {"cs": self.on_button}

    def handlers(self):
        return [(MessageHandler(_AwaitingText(self) & filters.TEXT & ~filters.COMMAND, self.on_text), 0)]

    def start_payloads(self) -> dict:
        return {"settext": self.on_start}

    # --- view ------------------------------------------------------------------------------------

    def _value_text(self, d: SettingDef, value) -> str:
        if d.kind == "bool":
            return t("chatsettings.on") if value else t("chatsettings.off")
        if d.kind == "enum":
            return t(f"{d.locale}_{value}")
        if d.kind == "text":
            return t("chatsettings.text_set") if value else t("chatsettings.text_unset")
        return str(value)

    def _sections(self) -> list[tuple[str, list[SettingDef]]]:
        """The sections with the settings this bot has (an empty one isn't shown)."""
        defs = {d.key: d for d in self.store.defs()}
        out = [(name, [defs[k] for k in keys if k in defs]) for name, keys in SECTIONS]
        placed = {k for _, keys in SECTIONS for k in keys}
        rest = [d for d in defs.values() if d.key not in placed]
        if rest:  # a setting not placed above still shows, in the last section
            out[-1] = (out[-1][0], out[-1][1] + rest)
        return [(name, ds) for name, ds in out if ds]

    def _footer(self, chat_id: int, admin_id: int, back: bool) -> list:
        last = []
        if back:
            last.append(button(t("common.back"), admin_id, f"cs:{chat_id}:s:main"))
        web = self.tbot.web
        if web is not None and self.tbot.me is not None:
            token = web.signer.launch(chat_id, 0)
            last.append(
                InlineKeyboardButton(
                    t("chatsettings.open_web"), url=f"https://t.me/{self.tbot.me.username}?startapp={token}"
                )
            )
        last.append(button(t("common.close"), admin_id, f"cs:{chat_id}:close:x"))
        return last

    async def view(self, chat_id: int, admin_id: int, section: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
        """The main card (a button per section) or one section's settings."""
        f = self.tbot.fmt
        sections = self._sections()
        if len(sections) == 1:
            section = sections[0][0]
        values = await self.store.resolve(PLATFORM, str(chat_id))
        community = await self.store.communities.of_chat(PLATFORM, str(chat_id))
        lines = [f.bold(t("chatsettings.title"))]
        if community is not None:
            lines.append(f.t("chatsettings.community", name=community.name))
        rows = []
        if section is None:
            for i, (name, defs) in enumerate(sections):
                changed = sum(1 for d in defs if values[d.key][1] != "default")
                label = t(f"chatsettings.section_{name}")
                if changed:
                    label = t("chatsettings.section_changed", label=label, n=changed)
                rows.append([button(label, admin_id, f"cs:{chat_id}:s:{i}")])
            for module in self.tbot.modules:  # things waiting for the admins (suggested prompts, …)
                hook = getattr(module, "settings_notes", None)
                if hook is not None:
                    lines += await hook(chat_id)
            lines.append(f.t("chatsettings.pick_section"))
        else:
            defs = next((ds for name, ds in sections if name == section), [])
            lines.append(f.bold(t(f"chatsettings.section_{section}")))
            for d in defs:
                rows.append(self._row(chat_id, admin_id, d, *values[d.key]))
            lines.append(f.t("chatsettings.hint"))
        rows.append(self._footer(chat_id, admin_id, back=section is not None and len(sections) > 1))
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def _row(self, chat_id: int, admin_id: int, d: SettingDef, value, source: str) -> list:
        n = SETTINGS.index(d)  # an index, not the key: callback data holds 64 bytes
        mark = t("chatsettings.inherited") if source == "community" else ""
        label = t(d.locale)
        if d.kind == "bool":
            text = t("chatsettings.toggle", label=label, value=self._value_text(d, value)) + mark
            return [button(text, admin_id, f"cs:{chat_id}:{n}:toggle")]
        if d.kind == "enum":
            text = t("chatsettings.toggle", label=label, value=self._value_text(d, value)) + mark
            return [button(text, admin_id, f"cs:{chat_id}:{n}:next")]
        if d.kind == "int":
            row = [button(f"{step:+d}", admin_id, f"cs:{chat_id}:{n}:{step}") for step in LENGTH_STEPS[:2]]
            row.append(
                button(t("chatsettings.number", label=label, value=value) + mark, admin_id, f"cs:{chat_id}:{n}:reset")
            )
            return row + [button(f"{step:+d}", admin_id, f"cs:{chat_id}:{n}:{step}") for step in LENGTH_STEPS[2:]]
        # a text: typed in the private chat, which the link opens with the chat in it
        text = t("chatsettings.edit_text", label=label, value=self._value_text(d, value))
        return [self.tbot.dm_button_text(text, f"settext_{chat_id}_{n}")]

    def _section_of(self, d: SettingDef) -> str | None:
        return next((name for name, defs in self._sections() if d in defs), None)

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
        if index == "s":  # open a section, or back to the main card
            await query.answer()
            sections = self._sections()
            section = sections[int(action)][0] if action.isdigit() and int(action) < len(sections) else None
            text, markup = await self.view(chat_id, query.from_user.id, section)
            await self.tbot.asks.edit(sent, text, markup)
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
        text, markup = await self.view(chat_id, query.from_user.id, self._section_of(d))
        await self.tbot.asks.edit(sent, text, markup)

    async def on_start(self, update: Update, _context, rest: str) -> None:
        """``/start settext_<chat>_<setting>``: the admin types a text setting here, in private."""
        message = update.effective_message
        chat_part, _, index = rest.rpartition("_")
        try:
            chat_id, d = int(chat_part), self.store.known(SETTINGS[int(index)].key)
        except (ValueError, IndexError, KeyError):
            await message.reply_text(t("common.stale"))
            return
        if d.kind != "text" or not await self.tbot.is_chat_admin(chat_id, message.from_user.id):
            await message.reply_text(t("common.admins_only"))
            return
        title = await self._title(chat_id)
        current = await self.store.get(PLATFORM, str(chat_id), d.key)
        f = self.tbot.fmt
        lines = [f.t("chatsettings.ask_text_dm", label=t(d.locale), chat=title)]
        if current:
            lines += ["", f.t("chatsettings.current"), current]
        self._pending[message.from_user.id] = (chat_id, d.key, time.monotonic())
        await message.reply_html(
            "\n".join(lines), reply_markup=ForceReply(input_field_placeholder=t("chatsettings.text_placeholder"))
        )

    async def _title(self, chat_id: int) -> str:
        row = await self.tbot.storage.get_chat(PLATFORM, str(chat_id)) if self.tbot.storage is not None else None
        return (row.title if row is not None and row.title else None) or str(chat_id)

    async def on_text(self, update: Update, _context) -> None:
        message = update.effective_message
        chat_id, key, _ = self._pending.pop(message.from_user.id)
        if not await self.tbot.is_chat_admin(chat_id, message.from_user.id):
            await message.reply_text(t("common.admins_only"))
            return
        text = message.text_html or ""
        value = (
            None if text.strip().casefold() in [w.casefold() for w in t("chatsettings.clear_words").split()] else text
        )
        try:
            await self.store.set(PLATFORM, str(chat_id), key, value)
        except ValueError:
            self._pending[message.from_user.id] = (chat_id, key, time.monotonic())
            await message.reply_text(t("chatsettings.too_long"))
            return
        await message.reply_html(self.tbot.fmt.t("chatsettings.text_saved_dm", chat=await self._title(chat_id)))
