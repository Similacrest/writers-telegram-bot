"""Welcome messages for new members. The text is per chat: a chat setting where the bot has storage
(phase 4), else the config file (``texts = {"-100…" = "Вітаємо, {name}!"}``), or the v2 config sheet's
``welcome_message`` rows (``from_sheet``). ``{name}`` becomes the new members' mentions."""

from telegram import Update
from telegram.ext import MessageHandler, filters

from ...telegram_bot.base import TelegramModule

SETTING = "greeter.text"


class Module(TelegramModule):
    name = "greeter"

    def handlers(self):
        return [(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome), 0)]

    async def text_for(self, chat_id: int) -> str | None:
        settings = self.bot.services.shared.extra.get("settings")
        if settings is not None and self.bot.storage is not None:
            value = await settings.get_chat("telegram", str(chat_id), SETTING)
            if value:
                return value
        texts = self.options.get("texts") or {}
        if str(chat_id) in texts:
            return texts[str(chat_id)]
        sheet = self.bot.services.sheet
        if self.options.get("from_sheet") and sheet is not None:
            return sheet.config_texts("welcome_message").get(str(chat_id))
        return None

    async def welcome(self, update: Update, _context) -> None:
        message = update.effective_message
        people = [u for u in message.new_chat_members or () if not u.is_bot]
        text = await self.text_for(message.chat_id) if people else None
        if not text:
            return
        names = ", ".join(f"<b>{u.mention_html()}</b>" for u in people)
        await self.bot._reply_html_safe(message, text.replace("{name}", names))
