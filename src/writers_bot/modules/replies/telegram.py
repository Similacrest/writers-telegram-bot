"""Answers to commands and phrases, set up by chat admins (phase 4) or in the config file.

A trigger is a command (``/imafan`` answers with a random text from its list) or a phrase ("Слава
Україні" → "Героям слава"). Phrase triggers only work where the bot sees all messages. The answers are
HTML; ``{name}`` becomes the sender's first name. ``legacy = true`` brings the v2 extras: ``/imafan`` with
the config sheet's ``imafan_message`` texts, and the two slogan replies from the locale file.
"""

import random
import re
from dataclasses import dataclass

from telegram import Update
from telegram.ext import MessageHandler, filters

from ...core.i18n import raw
from ...telegram_bot.base import TelegramModule


@dataclass(frozen=True, slots=True)
class Entry:
    answers: tuple[str, ...]
    command: str | None = None  # without the slash, lower case
    phrase: str | None = None  # lower case
    chat_id: str | None = None  # None: every chat of this bot


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

    async def entries(self, chat_id: int) -> list[Entry]:
        entries = list(self.static)
        store = self.bot.services.shared.extra.get("replies")
        if store is not None:
            entries += await store.for_chat("telegram", str(chat_id))
        if self.options.get("legacy") and self.bot.services.sheet is not None:
            texts = tuple(self.bot.services.sheet.config_texts("imafan_message").values())
            if texts:
                entries.append(Entry(texts, command="imafan"))
        return [e for e in entries if e.chat_id is None or e.chat_id == str(chat_id)]

    def handlers(self):
        return [
            (MessageHandler(filters.COMMAND, self.on_command), 3),
            # Phrases in their own group, so a reply that is also a sprint report gets both.
            (MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, self.on_text), 2),
        ]

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
