"""Dice rolls: /d20, /2d6+1, /r 4к6 Ініціатива, /d20adv."""

from telegram import Update
from telegram.ext import MessageHandler, filters

from ...core import extras
from ...telegram_bot.base import TelegramModule


class Module(TelegramModule):
    name = "dice"

    def handlers(self):
        return [(MessageHandler(filters.COMMAND & filters.Regex(extras.ROLL_RE), self.roll), 0)]

    def help_keys(self) -> list[str]:
        return ["help.dice"]

    async def roll(self, update: Update, _context) -> None:
        message = update.effective_message
        roll = extras.parse_roll(message.text or message.caption or "")
        if roll is not None:
            await message.reply_html(extras.roll_html(self.bot.fmt, roll))
