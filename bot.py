#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Bot to generate writing prompts
"""

import asyncio
import logging
import os
import random
import re
import string

import pytz
from parsel import Selector
from telegram import Bot, MessageEntity
from telegram.constants import ParseMode
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    PicklePersistence,
    filters,
)
from telegraph import Telegraph

import prompts_store
import utils
from sprint import *
from utils import whitelisted

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

logger = logging.getLogger(__name__)


class PromptsBot:
    def __init__(self):
        self.prompts = prompts_store.PromptsStore()
        self.super_admins = [int(userid) for userid in os.environ.get("BOT_SUPERADMINS").split(",")]
        self.help_text = "\n".join(self.prompts.config["help_message"])
        self.telegraph = Telegraph()
        self.app = None
        self.me = None
        self.bot_username = None

    async def set_app(self, app):
        self.app = app
        self.me = await self.app.bot.get_me()
        self.bot_username = "@" + self.me.username

    def check_if_chat_whitelisted(self, chat):
        return True

    @whitelisted(show_error_message=True)
    async def start(self, update, context):
        """Send a message when the command /start is issued."""
        await update.effective_message.reply_html(self.help_text, disable_web_page_preview=True)

    async def help_command(self, update, context):
        """Send a message when the command /help is issued."""
        await update.effective_message.reply_html(self.help_text, disable_web_page_preview=True)

    @whitelisted()
    async def prompt_command(self, update, context):
        """Send a message when the command /help is issued."""
        prompt_string = "\n".join([f"<b>{k}:</b> {v}" for k, v in self.prompts.random_text("ua").items()])

        await update.effective_message.reply_html(prompt_string)

    @whitelisted()
    async def image_command(self, update, context):
        """Send a message when the command /help is issued."""
        cat = utils.get_command_suffix(update.effective_message, "/image", self.bot_username)
        cat = cat[1:] if cat else "all"

        image_prompt = self.prompts.random_image(cat)
        await update.effective_message.reply_html(
            f"{image_prompt['cat']} #<a href='{image_prompt['webContentLink']}'>{image_prompt['num']}</a>",
            disable_web_page_preview=False,
        )

    @whitelisted()
    async def stats_command(self, update, context):
        await update.effective_message.reply_html(
            "\n".join([f"<b>{stat}:</b> {value}" for stat, value in self.prompts.get_stats().items()])
        )

    @whitelisted()
    async def wordcount_command(self, update, context):
        if update.effective_message.reply_to_message:
            txt = utils.text_or_caption(update.effective_message.reply_to_message)
        else:
            txt = utils.text_or_caption(update.effective_message)
        txt = txt.replace("/wc", "").replace(self.bot_username, "").strip()
        result = ""
        linked_text = re.search(r":\/\/telegra\.ph\/([\w-]+)", txt)
        if linked_text and linked_text[1]:
            page = self.telegraph.get_page(linked_text[1], return_content=True, return_html=True)
            text_elements = Selector(text=page["content"]).css("::text").getall()
            txt = page["title"] + "<SEPARATOR>" + "<SEPARATOR>".join(text_elements)
            txt = re.sub(r"(\s|(<SEPARATOR>))+", " ", txt)
            txt = re.sub(r"<SEPARATOR>", "", txt)

            result = "У Телеграфі:\n"

        words = utils.format_numeral_nouns(
            sum(len(word.strip(string.punctuation)) > 0 for word in txt.split()), ["слово", "слова", "слів"]
        )
        characters = utils.format_numeral_nouns(len(txt), ["символ", "символа", "символів"])
        letters = utils.format_numeral_nouns(
            len(re.sub("[{}]".format(re.escape(string.whitespace + string.punctuation + string.digits)), "", txt)),
            ["літера", "літери", "літер"],
        )
        result += f"{words}\n{characters}\n{letters}"
        await update.effective_message.reply_html(result)

    async def debuginfo_command(self, update, context):
        res = f"Чат id: {update.effective_message.chat.id}"
        if self.check_if_chat_whitelisted(update.effective_message.chat):
            res += " (авторизований)"
        else:
            res += " (не авторизований)"
        res += f"\nВаш юзер id: {update.effective_message.from_user.id}"
        if update.effective_message.from_user.id in self.super_admins:
            res += " (адмін боту)"
        if update.effective_message.chat.type in ["group", "supergroup"] and update.effective_message.from_user.id in [
            admin.user.id for admin in await update.effective_message.chat.get_administrators()
        ]:
            res += " (адмін чату)"

        if update.effective_message.reply_to_message:
            res += f"\nВідповідаєте юзеру з id: {update.effective_message.reply_to_message.from_user.id}"
            if update.effective_message.reply_to_message.from_user.id == self.me.id:
                res += " (цей бот)"
            elif update.effective_message.reply_to_message.from_user.is_bot:
                res += " (інший бот)"
            if update.effective_message.reply_to_message.from_user.id in self.super_admins:
                res += " (адмін боту)"
            if update.effective_message.chat.type in [
                "group",
                "supergroup",
            ] and update.effective_message.reply_to_message.from_user.id in [
                admin.user.id for admin in await update.effective_message.chat.get_administrators()
            ]:
                res += " (адмін чату)"
        await update.effective_message.reply_text(res)

    async def start_sprint(self, message, user, duration, delay, context):
        data = Sprint(duration, delay)
        await data.plan_sprint(message, user)
        job = context.job_queue.run_repeating(
            data.tick,
            interval=60,
            first=delay * 60 + 1,
            chat_id=message.chat_id,
            name=f"sprint_{message.chat_id}",
            data=data,
        )
        data.job = job

    @whitelisted()
    async def sprint_command(self, update, context):
        jobs = context.job_queue.get_jobs_by_name(f"sprint_{update.effective_message.chat_id}")
        try:
            arg = context.args[0]
            duration = int(arg)
        except (IndexError, ValueError):
            duration = DEFAULT_SPRINT
        try:
            arg = context.args[1]
            delay = int(arg)
        except (IndexError, ValueError):
            delay = DEFAULT_SPRINT_DELAY
        if len(jobs):
            await update.effective_message.reply_text("Спринт вже запущено!")
            return
        elif MIN_SPRINT <= duration <= MAX_SPRINT:
            if MIN_SPRINT_DELAY <= delay <= MAX_SPRINT_DELAY:
                await self.start_sprint(
                    update.effective_message, update.effective_message.from_user, duration, delay, context
                )
            else:
                await update.effective_message.reply_text(
                    f"Затримка до початку спринту має бути цілим числом від {MIN_SPRINT_DELAY} до {MAX_SPRINT_DELAY} хвилин!"
                )
        else:
            await update.effective_message.reply_text(
                f"Довжина спринту має бути цілим числом від {MIN_SPRINT} до {MAX_SPRINT} хвилин!"
            )
            return

    async def repeat_last_sprint(self, update, context):
        jobs = context.job_queue.get_jobs_by_name(f"sprint_{update.callback_query.message.chat_id}")
        data_match = re.match(r"^repeat_last_sprint_(\d+)(_\d+)?$", update.callback_query.data)
        try:
            duration = int(data_match[1])
        except (IndexError, ValueError, TypeError):
            duration = DEFAULT_SPRINT
        try:
            delay = int(data_match[2][1:])
        except (IndexError, ValueError, TypeError):
            delay = DEFAULT_SPRINT_DELAY

        if len(jobs):
            await update.callback_query.answer("Спринт вже запущено!")
            return
        elif MIN_SPRINT <= duration <= MAX_SPRINT:
            if MIN_SPRINT_DELAY <= delay <= MAX_SPRINT_DELAY:
                await self.start_sprint(
                    update.callback_query.message, update.callback_query.from_user, duration, delay, context
                )
                await update.callback_query.answer()
            else:
                await update.callback_query.answer(
                    f"Затримка до початку спринту має бути цілим числом від {MIN_SPRINT_DELAY} до {MAX_SPRINT_DELAY} хвилин!"
                )

        else:
            await update.callback_query.answer(
                f"Довжина спринту має бути цілим числом від {MIN_SPRINT} до {MAX_SPRINT} хвилин!"
            )
            return

    async def add_user_to_sprint(self, update, context):
        chat_id = update.callback_query.message.chat_id
        jobs = context.job_queue.get_jobs_by_name(f"sprint_{chat_id}")
        if len(jobs):
            await jobs[0].data.add_user(update.callback_query)
        else:
            await update.callback_query.answer("Не можна додатися до цього спринту!")

    async def leave_or_cancel_sprint(self, update, context):
        chat_id = update.callback_query.message.chat_id
        jobs = context.job_queue.get_jobs_by_name(f"sprint_{chat_id}")
        if len(jobs):
            await jobs[0].data.leave_or_cancel_sprint(update.callback_query)
        else:
            await update.callback_query.answer("Не можна вийти з цього спринту!")

    @whitelisted(show_error_message=True)
    async def reload_command(self, update, context):
        if update.effective_message.from_user.id in [self.super_admins] or (
            update.effective_message.chat.type in ["group", "supergroup"]
            and update.effective_message.from_user.id
            in [admin.user.id for admin in await update.effective_message.chat.get_administrators()]
        ):
            app = self.app
            self.__init__()
            await self.set_app(app)
            await update.effective_message.reply_text("Перезавантажено!")


def main():
    """Start the bot."""
    persistence = PicklePersistence(filepath="db.pickle")

    bot_logic = PromptsBot()
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_TOKEN"])
        .persistence(persistence)
        .rate_limiter(AIORateLimiter())
        .defaults(
            Defaults(parse_mode=ParseMode.HTML, allow_sending_without_reply=True, tzinfo=pytz.timezone("Europe/Kiev"))
        )
        .post_init(bot_logic.set_app)
        .build()
    )

    # on different commands - answer in Telegram
    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("help", bot_logic.help_command))
    app.add_handler(CommandHandler(["prompt", "prompt_ua"], bot_logic.prompt_command))
    app.add_handler(CommandHandler("wc", bot_logic.wordcount_command))
    app.add_handler(CommandHandler("stats", bot_logic.stats_command))
    app.add_handler(CommandHandler("reload", bot_logic.reload_command))
    app.add_handler(CommandHandler("debuginfo", bot_logic.debuginfo_command))
    app.add_handler(CommandHandler("sprint", bot_logic.sprint_command))
    app.add_handler(CallbackQueryHandler(bot_logic.add_user_to_sprint, pattern=r"^join_sprint$"))
    app.add_handler(CallbackQueryHandler(bot_logic.leave_or_cancel_sprint, pattern=r"^leave_or_cancel_sprint$"))
    app.add_handler(CallbackQueryHandler(bot_logic.repeat_last_sprint, pattern=r"^repeat_last_sprint_(\d+)(_\d+)?$"))
    app.add_handler(
        CommandHandler(["image", "image_character", "image_location", "image_other"], bot_logic.image_command)
    )
    # Start the Bot

    if os.environ.get("TEST_ENV"):
        app.run_polling()
    else:
        app.run_webhook(
            listen="0.0.0.0",
            port=80,
            webhook_url=f"https://{os.environ.get('HOSTNAME')}/",
            secret_token=os.environ.get("TELEGRAM_WEBHOOK_TOKEN"),
        )


if __name__ == "__main__":
    main()
