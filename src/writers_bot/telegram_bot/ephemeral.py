"""Bot API 10.2+ calls that python-telegram-bot 22.8 has no methods for yet, made with ``do_api_request``.

Ephemeral messages are visible only to one member of a group. Telegram accepts them within 15 seconds of
that user's button press (with its ``callback_query_id``) or ephemeral command (replying to its
``ephemeral_message_id``), or at any time when the bot is a chat administrator. They can carry an inline
keyboard and be edited or deleted with their ``ephemeral_message_id``. Every helper here returns a falsy
value instead of raising, so callers fall back to an ordinary message.
"""

import logging
import warnings

from telegram import Bot, BotCommand, InlineKeyboardMarkup, LinkPreviewOptions, Message
from telegram.error import TelegramError

logger = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # private answers never need a link preview


def incoming_ephemeral_id(message: Message | None) -> int | None:
    """``ephemeral_message_id`` of an ephemeral message (PTB keeps unknown fields in ``api_kwargs``)."""
    if message is None:
        return None
    value = (message.api_kwargs or {}).get("ephemeral_message_id")
    return int(value) if isinstance(value, int) else None


async def _call(bot: Bot, method: str, params: dict):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # PTB warns when do_api_request is used for a method it knows
        return await bot.do_api_request(method, api_kwargs=params)


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
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int | None:
    """Send an ephemeral message; returns its ``ephemeral_message_id`` (or -1 if Telegram didn't say), or
    None if it couldn't be sent."""
    params: dict = {"receiver_user_id": user_id}
    if callback_query_id:
        params["callback_query_id"] = callback_query_id
    extra: dict = {"ephemeral_message_parameters": params}
    if reply_to_ephemeral:
        # PTB's ReplyParameters requires a message_id, which an ephemeral reply doesn't have.
        extra["reply_parameters"] = {"ephemeral_message_id": reply_to_ephemeral}
    try:
        sent = await bot.send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            message_thread_id=message_thread_id,
            reply_markup=reply_markup,
            link_preview_options=NO_PREVIEW,
            api_kwargs=extra,
        )
    except TelegramError as e:
        logger.debug("ephemeral message to %s in %s failed: %s", user_id, chat_id, e)
        return None
    return incoming_ephemeral_id(sent) or -1


async def edit_ephemeral(
    bot: Bot,
    chat_id: int,
    user_id: int,
    ephemeral_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    params = {
        "chat_id": chat_id,
        "receiver_user_id": user_id,
        "ephemeral_message_id": ephemeral_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_markup is not None:
        params["reply_markup"] = reply_markup.to_dict()
    try:
        await _call(bot, "editEphemeralMessageText", params)
    except TelegramError as e:
        if "not modified" in str(e).lower():
            return True
        logger.debug("editing ephemeral %s for %s failed: %s", ephemeral_id, user_id, e)
        return False
    return True


async def delete_ephemeral(bot: Bot, chat_id: int, user_id: int, ephemeral_id: int) -> bool:
    try:
        await _call(
            bot,
            "deleteEphemeralMessage",
            {"chat_id": chat_id, "receiver_user_id": user_id, "ephemeral_message_id": ephemeral_id},
        )
    except TelegramError as e:
        logger.debug("deleting ephemeral %s for %s failed: %s", ephemeral_id, user_id, e)
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
        await _call(bot, "setMyCommands", {"commands": payload, "scope": scope})
    except TelegramError as e:
        logger.warning("setMyCommands with ephemeral commands failed: %s", e)
        return False
    return True
