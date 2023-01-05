#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Bot to generate writing prompts
"""

import logging, random, os, re
import asyncio

from functools import wraps

from telegram.ext import Application, AIORateLimiter, CommandHandler, MessageHandler, filters, PicklePersistence
from telegram import Bot, MessageEntity
from telegraph import Telegraph
from parsel import Selector
import string

import prompts_store, smash
from roller import Roller

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)

logger = logging.getLogger(__name__)

class PromptsBot:
    def __init__(self):
        self.prompts = prompts_store.PromptsStore()
        self.super_admins = [int(userid) for userid in os.environ.get('BOT_SUPERADMINS').split(',')]
        self.help_text = "\n".join(self.prompts.config['help_message'])
        self.imafan_texts = list(self.prompts.config['imafan_message'].values())
        self.welcome_texts = {int(k): '\n'.join(v) if type(v) is list else v for k, v in self.prompts.config['welcome_message'].items()}
        self.telegraph = Telegraph()
        self.app = None
        self.me = None
        self.bot_username = None

    async def set_app(self, app):
        self.app = app
        self.me = await self.app.bot.get_me()
        self.bot_username = '@' + self.me.username


    @staticmethod
    def text_or_caption(message):
        if message:
            return message.caption if message.caption else message.text
        else:
            return ''

    async def get_command_suffix(self, message, prefix=''):
        commands = [s.lower().replace(self.bot_username, '') for s in list(message.parse_entities([MessageEntity.BOT_COMMAND]).values()) + list(message.parse_caption_entities([MessageEntity.BOT_COMMAND]).values()) if s.lower().startswith(prefix)]
        if len(commands):
            return commands[0][len(prefix):]
        else:
            return None

    def check_if_chat_whitelisted(self, chat):
        return chat.type == 'private' or (chat.type in ['group', 'supergroup'] and chat.id in self.welcome_texts.keys())

    @staticmethod
    def whitelisted(show_error_message=False):
        def _whitelisted_decorator(method):
            @wraps(method)
            async def _whitelisted(self, update, *args, **kwargs):
                try:
                    if not self.check_if_chat_whitelisted(update.message.chat):
                        if show_error_message:
                            await update.message.reply_text("Цей бот доступний лише для деяких чатів. Зверніться до автора боту.")
                        return
                except AttributeError:
                    pass 
                return await method(self, update, *args, **kwargs)

            return _whitelisted
        return _whitelisted_decorator

    @staticmethod
    def _format_numeral_nouns(num, word):
        if num % 10 == 1 and num // 10 % 10 != 1:
            return f"{num} {word[0]}"
        elif num % 10 >= 2 and num % 10 <= 4 and num // 10 %10 != 1:
            return f"{num} {word[1]}"
        else:
            return f"{num} {word[2]}"

    @whitelisted(show_error_message=True)
    async def start(self, update, context):
        """Send a message when the command /start is issued."""
        await update.message.reply_html("Я переродився, Райтенчіле!\n\n" + self.help_text, disable_web_page_preview=True)


    async def help_command(self, update, context):
        """Send a message when the command /help is issued."""
        await update.message.reply_html(self.help_text, disable_web_page_preview=True)

    @whitelisted()
    async def imafan_command(self, update, context):
        """Send a message when the command /imafan is issued."""
        if not self.imafan_texts: 
            return
        response = random.choice(self.imafan_texts)
        if "{name}" in response:
            response = response.format(name=f"<b>{update.message.from_user.first_name}</b>")
        await update.message.reply_html(response)

    @whitelisted()
    async def prompt_command(self, update, context):
        """Send a message when the command /help is issued."""
        prompt_string = '\n'.join([f"<b>{k}:</b> {v}" for k, v in self.prompts.random_text("ua").items()])

        await update.message.reply_html(prompt_string)

    @whitelisted()
    async def image_command(self, update, context):
        """Send a message when the command /help is issued."""
        cat = await self.get_command_suffix(update.message, '/image')
        cat = cat[1:] if cat else 'all'

        image_prompt = self.prompts.random_image(cat)
        await update.message.reply_html(f"{image_prompt['cat']} #<a href=\'{image_prompt['webContentLink']}\'>{image_prompt['num']}</a>")

    @whitelisted()
    async def stats_command(self, update, context):
        await update.message.reply_html("\n".join([f"<b>{stat}:</b> {value}"
                                             for stat, value in self.prompts.get_stats().items()]))

    @whitelisted()
    async def wordcount_command(self, update, context):
        if update.message.reply_to_message:
            txt = PromptsBot.text_or_caption(update.message.reply_to_message)
        else:
            txt = PromptsBot.text_or_caption(update.message)
        txt = txt.replace('/wc', '').replace(self.bot_username, '').strip()
        result = ""
        linked_text = re.search(r':\/\/telegra\.ph\/([\w-]+)', txt)
        if linked_text and linked_text[1]:
            page = self.telegraph.get_page(linked_text[1], return_content=True, return_html=True)
            text_elements = Selector(text=page['content']).css('::text').getall()
            txt = page['title'] + '<SEPARATOR>' + "<SEPARATOR>".join(text_elements)
            txt = re.sub(r"(\s|(<SEPARATOR>))+", ' ', txt)
            txt = re.sub(r"<SEPARATOR>", '', txt)

            result = "У Телеграфі:\n"

        words = PromptsBot._format_numeral_nouns(sum(len(word.strip(string.punctuation)) > 0 for word in txt.split()), ['слово', 'слова', 'слів'])
        characters = PromptsBot._format_numeral_nouns(len(txt), ['символ', 'символа', 'символів'])
        letters = PromptsBot._format_numeral_nouns(len(re.sub('[{}]'.format(re.escape(string.whitespace + string.punctuation + string.digits)), '', txt)), ['літера', 'літери', 'літер'])
        result += f"{words}\n{characters}\n{letters}" 
        await update.message.reply_html(result)

    async def debuginfo_command(self, update, context):
        res = f"Чат id: {update.message.chat.id}"
        if self.check_if_chat_whitelisted(update.message.chat):
            res += " (авторизований)"
        else:
            res += " (не авторизований)"
        res += f"\nВаш юзер id: {update.message.from_user.id}"
        if update.message.from_user.id in self.super_admins:
            res += " (адмін бота)"
        if update.message.chat.type in ['group', 'supergroup'] and update.message.from_user.id in [admin.user.id for admin in await update.message.chat.get_administrators()]:
            res += " (адмін чату)"

        if update.message.reply_to_message:
            res += f"\nВідповідаєте юзеру з id: {update.message.reply_to_message.from_user.id}"
            if update.message.reply_to_message.from_user.id == self.me.id:
                res += " (цей бот)"            
            elif update.message.reply_to_message.from_user.is_bot:
                res += " (інший бот)"
            if update.message.reply_to_message.from_user.id in self.super_admins:
                res += " (адмін бота)"
            if update.message.chat.type in ['group', 'supergroup'] and update.message.reply_to_message.from_user.id in [admin.user.id for admin in await update.message.chat.get_administrators()]:
                res += " (адмін чату)"
        await update.message.reply_text(res)

    @whitelisted()
    async def other_command(self, update, context):
        """Send a message when the command /help is issued."""
        txt = PromptsBot.text_or_caption(update.message)
        txt.replace(self.bot_username, "")
        roller = Roller(txt)
        if roller.is_valid_command:
            await update.message.reply_html(roller.execute_roll())

    @whitelisted()
    async def smash_command(self, update, context):
        await update.message.reply_html(f"<code>{smash.smash()}</code>")

    @whitelisted()
    async def process_message(self, update, context):
        if 'слава україні' in PromptsBot.text_or_caption(update.message).lower():
            await update.message.reply_html("<i>Героям Слава!</i>")
        if 'слава нації' in PromptsBot.text_or_caption(update.message).lower():
            await update.message.reply_html(random.choices(["<i>Смерть ворогам!</i>", "Пизда ᵖосійській ᶲедерації!"], weights=[0.8, 0.2])[0])

    @whitelisted()
    async def welcome(self, update, context):
        if update.message.new_chat_members:
            if update.message.chat.id in self.welcome_texts.keys():
                welcome_text = self.welcome_texts[update.message.chat.id]
            else:
                welcome_text = self.welcome_texts[1]
            names = ""
            for user in update.message.new_chat_members:
                if not user.is_bot:
                    if names:
                        names += ', '
                    names += f"<b><a href='tg://user?id={user.id}'>{user.first_name}</a></b>"
            if names and "{name}" in welcome_text:
                welcome_text = welcome_text.format(name=names)
            await update.message.reply_html(welcome_text)

    @whitelisted(show_error_message=True)
    async def reload_command(self, update, context):
        if update.message.from_user.id in [self.super_admins] or (update.message.chat.type in ['group', 'supergroup'] 
        and update.message.from_user.id in [admin.user.id for admin in await update.message.chat.get_administrators()]):
           app = self.app
           self.__init__()
           await self.get_me(app)
           await update.message.reply_text('Перезавантажено!')

def main():
    """Start the bot."""
    persistence = PicklePersistence(filepath='db.pickle')

    bot_logic = PromptsBot()
    app = Application.builder().token(os.environ['TELEGRAM_TOKEN'])\
.persistence(persistence).rate_limiter(AIORateLimiter())\
.post_init(bot_logic.set_app).build()    


    # on different commands - answer in Telegram
    app.add_handler(CommandHandler('start', bot_logic.start))
    app.add_handler(CommandHandler('help', bot_logic.help_command))
    app.add_handler(CommandHandler('imafan', bot_logic.imafan_command))
    app.add_handler(CommandHandler(['prompt', 'prompt_ua'], bot_logic.prompt_command))
    app.add_handler(CommandHandler('wc', bot_logic.wordcount_command))
    app.add_handler(CommandHandler('smash', bot_logic.smash_command))
    app.add_handler(CommandHandler('stats', bot_logic.stats_command))
    app.add_handler(CommandHandler('reload', bot_logic.reload_command))
    app.add_handler(CommandHandler('debuginfo', bot_logic.debuginfo_command))
    app.add_handler(
        CommandHandler(
            ['image', 'image_character', 'image_location', 'image_other'],
            bot_logic.image_command))
    app.add_handler(MessageHandler(filters.COMMAND, bot_logic.other_command))

    app.add_handler(
        MessageHandler(filters.TEXT, bot_logic.process_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bot_logic.welcome))
    # Start the Bot
    app.run_webhook(listen="0.0.0.0", port=80, webhook_url=f"https://{os.environ.get('HOSTNAME')}/", secret_token=os.environ.get('TELEGRAM_WEBHOOK_TOKEN'))


if __name__ == '__main__':
    main()
