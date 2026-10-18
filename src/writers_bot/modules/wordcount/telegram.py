"""/count (hidden alias /wc): words, characters and letters in a message, the text after the command, or
the text behind a link."""

from telegram import LinkPreviewOptions, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ...core.i18n import t
from ...core.links import LinkError
from ...core.render import text_stats_text
from ...core.sources import find_url, source_locative
from ...core.wordcount import count_words, text_stats
from ...telegram_bot.app import command_args_text, thread_of
from ...telegram_bot.base import Command, TelegramModule

LINK_WITH_SHORT_TEXT = 20  # a message that is mostly a link is counted by fetching the link
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class Module(TelegramModule):
    name = "wordcount"

    def commands(self) -> list[Command]:
        return [Command("count", self.count, "commands.count", uk="aliases.count", aliases=("wc",), help="help.count")]

    async def count(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        f = self.bot.fmt
        own = command_args_text(message)
        replied = message.reply_to_message
        text = own or ((replied.text or replied.caption or "") if replied else "")
        if not text:
            await message.reply_text(t("wordcount.usage"), parse_mode=None)
            return
        url = find_url(text)
        if url and self.bot.links is not None and count_words(text.replace(url, "")) < LINK_WITH_SHORT_TEXT:
            await context.bot.send_chat_action(
                message.chat_id, ChatAction.TYPING, message_thread_id=thread_of(message)
            )
            try:
                linked = await self.bot.links.fetch(url)
            except LinkError as e:
                await message.reply_text(
                    t("common.link_error", error=e), parse_mode=None, link_preview_options=NO_PREVIEW
                )
                return
            await message.reply_html(text_stats_text(f, text_stats(linked.text), source_locative(linked.source)))
            return
        await message.reply_html(text_stats_text(f, text_stats(text)))
