"""Answers to commands and phrases, set up by chat admins (``/replies``, the web app) or in the config file.

A trigger is a command (``/imafan`` answers with a random text from its list) or a phrase ("Слава
Україні" → "Героям слава"). Phrase triggers only work where the bot sees all messages. Answers are HTML;
``{name}`` becomes the sender's first name.

Where the bot has storage, entries live in the database: each chat's own, plus bot-wide defaults (the
``entries`` from the config file and, with ``legacy = true``, the v2 ``/imafan`` texts and the two
slogans, imported once). A chat can turn the defaults off (the ``replies.defaults`` setting). Without
storage, the config file's entries and the legacy ones are used as they are.
"""

import contextlib
import random
import re

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ...core.i18n import raw, t
from ...telegram_bot.app import command_args_text
from ...telegram_bot.base import Command, Sent, TelegramModule, button
from .store import ALL, Entry, ReplyStore, parse_trigger

PLATFORM = "telegram"


def config_entries(options: dict) -> list[Entry]:
    entries = []
    for e in options.get("entries") or []:
        answers = e.get("answers") or ([e["answer"]] if e.get("answer") else [])
        if not answers:
            continue
        chat = str(e["chat"]) if e.get("chat") is not None else None
        if e.get("command"):
            entries.append(Entry(tuple(answers), command=str(e["command"]).lstrip("/").lower(), chat_id=chat))
        elif e.get("phrase"):
            entries.append(Entry(tuple(answers), phrase=str(e["phrase"]).casefold(), chat_id=chat))
    if options.get("legacy"):
        for key in ("replies.legacy_slava_ukraini", "replies.legacy_slava_natsii"):
            phrase, *answers = raw(key)
            entries.append(Entry(tuple(answers), phrase=phrase.casefold()))
    return entries


class Module(TelegramModule):
    name = "replies"

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.static = config_entries(options)
        # In the database (managed by chat admins) only where the bot has /settings; v2 setups keep the
        # config and the sheet, so edits to the sheet still count.
        self.store = ReplyStore(bot.storage) if bot.storage is not None and bot.config.chat_settings else None
        if self.store is not None:
            bot.services.shared.extra["replies"] = self.store

    async def post_init(self) -> None:
        if self.store is None:
            return
        # Bot-wide defaults go into the database once, so chat admins see them and chats can turn them off.
        if not await self.store.imported("config"):
            for e in self.static:
                kind, trigger = ("command", e.command) if e.command else ("phrase", e.phrase)
                for answer in e.answers:
                    with contextlib.suppress(ValueError):
                        await self.store.add(PLATFORM, e.chat_id or ALL, kind, trigger, answer, None)
            await self.store.mark_imported("config")
        sheet = self.bot.services.sheet
        if self.options.get("legacy") and sheet is not None and not await self.store.imported("imafan"):
            texts = list(sheet.config_texts("imafan_message").values())
            if texts:
                for text in texts:
                    with contextlib.suppress(ValueError):
                        await self.store.add(PLATFORM, ALL, "command", "imafan", text, None)
                await self.store.mark_imported("imafan")

    async def entries(self, chat_id: int) -> list[Entry]:
        if self.store is not None:
            settings = self.bot.services.shared.extra.get("settings")
            defaults = await settings.get_bool(PLATFORM, str(chat_id), "replies.defaults") if settings else True
            return await self.store.for_chat(PLATFORM, str(chat_id), defaults=defaults)
        entries = list(self.static)
        if self.options.get("legacy") and self.bot.services.sheet is not None:
            texts = tuple(self.bot.services.sheet.config_texts("imafan_message").values())
            if texts:
                entries.append(Entry(texts, command="imafan"))
        return [e for e in entries if e.chat_id is None or e.chat_id == str(chat_id)]

    def commands(self) -> list[Command]:
        if self.store is None:
            return []
        return [Command("replies", self.manage, "commands.replies", scope="chat_admin")]

    def handlers(self):
        return [
            (MessageHandler(filters.COMMAND, self.on_command), 3),
            # Phrases in their own group, so a reply that is also a sprint report gets both.
            (MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, self.on_text), 2),
        ]

    def asks(self):
        return {"rp": self.on_button}

    def _answer(self, entry: Entry, user) -> str:
        name = self.bot.fmt.bold(user.first_name) if user else ""
        return random.choice(entry.answers).replace("{name}", name)

    async def on_command(self, update: Update, _context) -> None:
        message = update.effective_message
        m = re.match(r"^/(\w+)(?:@(\w+))?", message.text or message.caption or "")
        if not m or (m[2] and self.bot.me and m[2].lower() != self.bot.me.username.lower()):
            return
        command = m[1].lower()
        if command in self.bot._commands:
            return
        for entry in await self.entries(message.chat_id):
            if entry.command == command:
                await self.bot._reply_html_safe(message, self._answer(entry, message.from_user))
                return

    async def on_text(self, update: Update, _context) -> None:
        message = update.effective_message
        text = (message.text or message.caption or "").casefold()
        if not text:
            return
        for entry in await self.entries(message.chat_id):
            if entry.phrase and entry.phrase in text:
                await self.bot._reply_html_safe(message, self._answer(entry, message.from_user))
                return

    # --- the web app ----------------------------------------------------------------------------------

    def web_routes(self, app) -> None:
        if self.store is None:
            return
        from aiohttp import web

        from ...web.server import ApiError, ok

        async def listing(request, user):
            chat_id = int(request.match_info["chat"])
            await app.require_chat_admin(user, chat_id)
            own = await self.store.own(PLATFORM, str(chat_id))
            return web.json_response(
                {"replies": [{"id": e.id, "trigger": e.trigger, "answers": list(e.answers)} for e in own]}
            )

        async def add(request, user):
            chat_id = int(request.match_info["chat"])
            await app.require_chat_admin(user, chat_id)
            body = await app.body(request)
            parsed = parse_trigger(str(body.get("trigger", "")))
            answer = str(body.get("answer", "")).strip()
            if parsed is None or not answer:
                raise ApiError(400, "web.error_bad_request")
            try:
                await self.store.add(PLATFORM, str(chat_id), parsed[0], parsed[1], answer, user.user_id)
            except ValueError:
                raise ApiError(400, "replies.too_many") from None
            return await listing(request, user)

        async def delete(request, user):
            chat_id = int(request.match_info["chat"])
            await app.require_chat_admin(user, chat_id)
            await self.store.delete(PLATFORM, str(chat_id), int(request.match_info["entry"]))
            return ok()

        app.add("GET", r"/api/chat/{chat:-?\d+}/replies", listing)
        app.add("POST", r"/api/chat/{chat:-?\d+}/replies", add)
        app.add("DELETE", r"/api/chat/{chat:-?\d+}/replies/{entry:\d+}", delete)

    # --- /replies (chat admins) ----------------------------------------------------------------------

    async def view(self, chat_id: int, admin_id: int) -> tuple[str, InlineKeyboardMarkup]:
        f = self.bot.fmt
        own = await self.store.own(PLATFORM, str(chat_id))
        lines = [f.bold(t("replies.title"))]
        if not own:
            lines.append(f.t("replies.none"))
        rows = []
        for e in own:
            lines.append(f.t("replies.line", trigger=e.trigger, count=len(e.answers)))
            rows.append([button(t("replies.delete", trigger=e.trigger), admin_id, f"rp:{chat_id}:{e.id}")])
        rows.append([button(t("common.close"), admin_id, f"rp:{chat_id}:close")])
        lines += ["", f.t("replies.usage")]
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def manage(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE or not await self.bot.is_chat_admin(
            message.chat, message.from_user.id
        ):
            await self.bot.asks.respond(message, t("common.admins_only"), html=False, delete_command=True, ttl=60)
            return
        args = command_args_text(message)
        if args.split(maxsplit=1)[:1] == ["add"]:
            rest = args.split(maxsplit=1)[1] if len(args.split(maxsplit=1)) > 1 else ""
            replied = message.reply_to_message
            if replied is not None and "=" not in rest and (replied.text or replied.caption):
                # The answer is the message replied to, formatting and line breaks kept: no syntax to learn.
                trigger_text, sep, answer = rest, "=", replied.text_html or replied.caption_html or ""
            else:
                trigger_text, sep, answer = rest.partition("=")
            parsed = parse_trigger(trigger_text)
            if not sep or parsed is None or not answer.strip():
                await self.bot.asks.respond(message, self.bot.fmt.t("replies.usage"), delete_command=True)
                return
            try:
                await self.store.add(
                    PLATFORM, str(message.chat_id), parsed[0], parsed[1], answer.strip(), str(message.from_user.id)
                )
            except ValueError:
                await self.bot.asks.respond(message, t("replies.too_many"), html=False, delete_command=True)
                return
            await self.bot.asks.respond(
                message, self.bot.fmt.t("replies.added", trigger=trigger_text.strip()), delete_command=True
            )
            return
        text, markup = await self.view(message.chat_id, message.from_user.id)
        await self.bot.asks.respond(message, text, markup, delete_command=True, ttl=10 * 60)

    async def on_button(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        if len(args) < 2:
            await self.bot.asks.toast(query, "common.stale")
            return
        chat_id = int(args[0])
        if args[1] == "close":
            await query.answer()
            if sent:
                await self.bot.asks.delete(sent)
            return
        if not await self.bot.is_chat_admin(chat_id, query.from_user.id):
            await self.bot.asks.toast(query, "common.admins_only", alert=True)
            return
        await self.store.delete(PLATFORM, str(chat_id), int(args[1]))
        await query.answer(t("replies.deleted"))
        text, markup = await self.view(chat_id, query.from_user.id)
        with contextlib.suppress(TelegramError):
            await self.bot.asks.edit(sent, text, markup)
