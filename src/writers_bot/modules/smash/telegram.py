"""/smash: a random keyboard smash."""

from telegram import Update

from ...core import extras
from ...telegram_bot.base import Command, TelegramModule


class Module(TelegramModule):
    name = "smash"

    def commands(self) -> list[Command]:
        return [Command("smash", self.smash, "commands.smash", menu=False)]

    async def smash(self, update: Update, _context) -> None:
        await update.effective_message.reply_html(self.bot.fmt.code(extras.smash()))
