"""Prompts on Telegram: /prompt, /image (and /image_<category>), /promptstats, /reload."""

import logging

from telegram import LinkPreviewOptions, Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ...core.i18n import t
from ...core.prompts import PromptsUnavailableError
from ...telegram_bot.base import Command, TelegramModule

logger = logging.getLogger(__name__)

LANG = "ua"
IMAGE_CMD = filters.Regex(r"^/image(?:_[\w]+)?(?:@\w+)?(?:\s|$)")


class Module(TelegramModule):
    name = "prompts"

    @property
    def store(self):
        return self.bot.services.sheet

    def config_text(self, key: str) -> str | None:
        return self.store.config_text(key) if self.store is not None else None

    @property
    def categories(self) -> bool:
        return bool(self.options.get("categories"))

    def commands(self) -> list[Command]:
        return [
            Command(
                "prompt",
                self.prompt,
                "commands.prompt",
                uk="aliases.prompt",
                aliases=("prompt_ua",),
                help="help.prompt_categories" if self.categories else "help.prompt",
            ),
            Command(
                "image",
                self.image,
                "commands.image",
                uk="aliases.image",
                help="help.image_categories" if self.categories else "help.image",
            ),
            Command("promptstats", self.stats, None, aliases=("stats",)),
            Command("reload", self.reload, None),
        ]

    def handlers(self):
        # /image_<category> isn't a fixed command name, so it takes a regex.
        return [(MessageHandler(filters.COMMAND & IMAGE_CMD, self.image), 0)]

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        f = self.bot.fmt
        query = " ".join(context.args or []) if self.categories else ""  # v2: one prompt from every category
        if self.store is None:
            await message.reply_text(t("prompts.unavailable"))
            return
        try:
            category = None
            if query:
                category = self.store.find_category(LANG, query)
                if category is None:
                    cats = ", ".join(self.store.text_categories(LANG)) or "—"
                    await message.reply_html(f.t("prompts.no_category", categories=cats))
                    return
            prompt = self.store.random_text(LANG, category)
        except PromptsUnavailableError:
            await message.reply_text(t("prompts.unavailable"))
            return
        await message.reply_html("\n".join(f"{f.bold(k)}: {f.escape(v)}" for k, v in prompt.items()))

    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        f = self.bot.fmt
        command = (message.text or message.caption or "").split()[0].split("@")[0].lower()
        category = command.removeprefix("/image").removeprefix("_") or None
        if category is None and context.args and self.categories:
            category = context.args[0]  # v2 only had /image_<category>
        if self.store is None:
            await message.reply_text(t("prompts.images_unavailable"))
            return
        try:
            cat, num, image = self.store.random_image(category)
        except PromptsUnavailableError:
            await message.reply_text(t("prompts.images_unavailable"))
            return
        link = image.get("webContentLink") or image.get("webViewLink") or ""
        await message.reply_html(
            f"{f.escape(cat)} #{f.link(link, num) if link else num}",
            link_preview_options=LinkPreviewOptions(is_disabled=False, url=link, prefer_large_media=True)
            if link
            else None,
        )

    async def stats(self, update: Update, _context) -> None:
        f = self.bot.fmt
        stats = self.store.stats() if self.store is not None else {}
        text = "\n".join(f"{f.bold(k)}: {v}" for k, v in stats.items()) or f.escape(t("prompts.not_loaded"))
        await update.effective_message.reply_html(text)

    async def reload(self, update: Update, _context) -> None:
        message = update.effective_message
        if not await self.bot.is_chat_admin(message.chat, message.from_user.id):
            await message.reply_text(t("prompts.reload_admins"))
            return
        if self.store is None:
            await message.reply_text(t("prompts.unavailable"))
            return
        try:
            await self.store.load()
        except Exception as e:
            logger.exception("reload failed")
            await message.reply_text(t("prompts.reload_failed", error=type(e).__name__))
            return
        await message.reply_text(t("prompts.reloaded"))
