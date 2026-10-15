"""Bot API 10.2+ calls that python-telegram-bot 22.8 has no methods for yet, made with ``do_api_request``.

Ephemeral messages are visible only to one member of a group. Telegram accepts them within 15 seconds of
that user's button press (with its ``callback_query_id``) or ephemeral command (replying to its
``ephemeral_message_id``), or at any time when the bot is a chat administrator. Every helper here
returns ``False`` instead of raising, so callers fall back to an ordinary message.
"""

import logging
import warnings

from telegram import Bot, BotCommand, Message
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


def incoming_ephemeral_id(message: Message | None) -> int | None:
    """``ephemeral_message_id`` of an ephemeral command (PTB keeps unknown fields in ``api_kwargs``)."""
    if message is None:
        return None
    value = (message.api_kwargs or {}).get("ephemeral_message_id")
    return int(value) if isinstance(value, int) else None


async def send_ephemeral(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    *,
    callback_query_id: str | None = None,
    reply_to_ephemeral: int | None = None,
    parse_mode: str | None = "HTML",
    message_thread_id: int | None = None,
) -> bool:
    params: dict = {"receiver_user_id": user_id}
    if callback_query_id:
        params["callback_query_id"] = callback_query_id
    extra: dict = {"ephemeral_message_parameters": params}
    if reply_to_ephemeral:
        # PTB's ReplyParameters requires a message_id, which an ephemeral reply doesn't have.
        extra["reply_parameters"] = {"ephemeral_message_id": reply_to_ephemeral}
    try:
        await bot.send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            message_thread_id=message_thread_id,
            api_kwargs=extra,
        )
    except TelegramError as e:
        logger.debug("ephemeral message to %s in %s failed: %s", user_id, chat_id, e)
        return False
    return True


async def set_commands(bot: Bot, commands: list[BotCommand], scope: dict, ephemeral: set[str]) -> bool:
    """setMyCommands with ``is_ephemeral`` on the named commands."""
    payload = [
        {
            "command": c.command,
            "description": c.description,
            **({"is_ephemeral": True} if c.command in ephemeral else {}),
        }
        for c in commands
    ]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # PTB suggests set_my_commands, which can't send is_ephemeral
            await bot.do_api_request("setMyCommands", api_kwargs={"commands": payload, "scope": scope})
    except TelegramError as e:
        logger.warning("setMyCommands with ephemeral commands failed: %s", e)
        return False
    return True
