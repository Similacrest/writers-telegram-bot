from functools import wraps

from telegram import MessageEntity


def text_or_caption(message):
    if message:
        return message.caption if message.caption else message.text
    else:
        return ""


def get_command_suffix(message, bot_username, prefix=""):
    commands = [
        s.lower().replace(bot_username, "")
        for s in list(message.parse_entities([MessageEntity.BOT_COMMAND]).values())
        + list(message.parse_caption_entities([MessageEntity.BOT_COMMAND]).values())
        if s.lower().startswith(prefix)
    ]
    if len(commands):
        return commands[0][len(prefix) :]
    else:
        return None


def whitelisted(show_error_message=False):
    def _whitelisted_decorator(method):
        @wraps(method)
        async def _whitelisted(self, update, *args, **kwargs):
            try:
                if not self.check_if_chat_whitelisted(update.effective_message.chat):
                    if show_error_message:
                        await update.effective_message.reply_text(
                            "Цей бот доступний лише для деяких чатів. Зверніться до автора боту."
                        )
                    return
            except AttributeError:
                pass
            return await method(self, update, *args, **kwargs)

        return _whitelisted

    return _whitelisted_decorator


def format_numeral_nouns(num, word):
    if num % 10 == 1 and num // 10 % 10 != 1:
        return f"{num} {word[0]}"
    elif num % 10 >= 2 and num % 10 <= 4 and num // 10 % 10 != 1:
        return f"{num} {word[1]}"
    else:
        return f"{num} {word[2]}"
