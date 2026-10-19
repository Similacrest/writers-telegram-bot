"""Dice rolls: /d20, /2d6+1, /r 4d6kh3 Ініціатива, /d20adv, !к12 (the d20 library's notation, see ``core.extras``).

With ``stickers = true`` a roll is answered with animated stickers of the dice landing on their results
(``scripts/dice_render`` makes the clips, kept in ``assets/dice_clips``; ``core.dicestickers`` says which sticker each roll
gets). One or two stickers come on their own; three or four only when the roll has ``-s`` (``/3d6 -s``); ``-q`` silences
them (``/d20 -q``); more than ``max_stickers`` (4) stickers, or a die without stickers (d13), and the roll is only the line.
Dice of a kind share stickers (8d6 is three), so the limits count stickers, not dice.

The clips are not in a sticker pack. The bot uploads a clip with ``sendSticker`` the first time a roll needs it and keeps
the ``file_id`` in a cache file (``core.dicecache``), so later rolls send by id. Because the stickers are in no pack,
people cannot send them from the keyboard: a message from a person with one of them is a forward or a copy of a roll, and
the bot says so (the chat setting ``dice.forwards``: off, warn, delete). A bare ``/d20`` is just the sticker; with a bonus
or a caption the usual line follows.
"""

import contextlib
import json
import logging
from importlib.resources import files
from pathlib import Path, PurePosixPath

from telegram import InputFile, ReplyParameters, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import MessageHandler, filters

from ...core import dicestickers, extras, randomness
from ...core.dicecache import StickerCache
from ...telegram_bot.app import display_name
from ...telegram_bot.base import Command, TelegramModule

logger = logging.getLogger(__name__)

DICE = (2, 3, 4, 6, 8, 10, 12, 20, 100)  # the dice with a command of their own
AUTO_STICKERS = 2  # a roll of more stickers than this gets them only when asked for (-s)
UPLOAD_TIMEOUTS = {"read_timeout": 60, "write_timeout": 60}  # Telegram answers an upload slower than the 5 s default
CACHE_FILE = "dice_stickers.json"  # next to the database


def manifest() -> dict[str, dict]:
    """The clips: for each name (``d20_17``), its die, variant, value and effect; its file is ``<name>.webm``."""
    return json.loads(files("writers_bot.assets.dice_clips").joinpath("manifest.json").read_text())


class Module(TelegramModule):
    name = "dice"

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.stickers_on = bool(options.get("stickers"))
        self.max_stickers = int(options.get("max_stickers", 4))  # more dice than this, even with -s: text only
        # "random.org": the dice come from true random bytes (core.randomness) instead of Python's generator
        self.rng: randomness.TrueRandom | None = None
        if options.get("randomness") == "random.org":
            key = str(options.get("random_org_key") or "")
            self.rng = randomness.TrueRandom(lambda n: randomness.random_org(key, n))
        self.files = {name: f"{name}.webm" for name in manifest()}  # clip -> its file
        self.cache = StickerCache(self._cache_path(options))

    def commands(self) -> list[Command]:
        """Each die is a command of its own (/d20 ... in the menu and /help), also as !к20; the rest of the notation
        (/2d6+3, /r 4d6kh3) is read by ``handlers``."""

        if not self.options.get("commands", True):  # a v2 bot's menu has none; the rolls themselves work
            return []

        async def values(_ctx, sides=0):
            return {"sides": sides}

        return [
            Command(
                f"d{sides}", self.roll, "commands.die", uk=f"aliases.d{sides}",
                values=lambda ctx, sides=sides: values(ctx, sides),
            )
            for sides in DICE
        ]  # fmt: skip

    def handlers(self):
        # Not only command entities: whether Telegram marks "/2d6" (a digit first) as a command is unclear
        handlers = [(MessageHandler((filters.COMMAND | filters.TEXT) & filters.Regex(extras.ROLL_RE), self.roll), 0)]
        if self.stickers_on:  # its own group: looking at stickers never stops another handler
            handlers.append((MessageHandler(filters.Sticker.ALL & filters.ChatType.GROUPS, self.on_sticker), 7))
        return handlers

    def _cache_path(self, options: dict) -> Path | None:
        """Where the file ids are kept: ``cache_file``, else next to the database, else nowhere (until the bot stops)."""
        if options.get("cache_file"):
            return Path(options["cache_file"])
        storage = getattr(self.bot, "storage", None)
        return (
            Path(storage.path).parent / CACHE_FILE if storage is not None and str(storage.path) != ":memory:" else None
        )

    async def help_lines(self, ctx) -> list[tuple[str, str, dict]]:
        return [("dice", "help.dice", {})]

    async def post_init(self) -> None:
        if self.rng is not None:
            await (
                self.rng.start()
            )  # the first pool before the first roll; the bot starts without it if random.org is down
        if self.stickers_on:
            self.cache.load(self.bot.me.id)

    async def shutdown(self) -> None:
        if self.rng is not None:
            await self.rng.stop()

    # --- the stickers ------------------------------------------------------------------------------

    async def send_clip(self, message, name: str) -> bool:
        """Send one clip as a sticker in reply to the roll: by its cached file id, else upload it and keep the id."""
        bot = self.bot.app.bot
        where = {
            "message_thread_id": message.message_thread_id if message.is_topic_message else None,
            "reply_parameters": ReplyParameters(message.message_id, allow_sending_without_reply=True),
        }
        cached = self.cache.file_id(name)
        if cached is not None:
            try:
                await bot.send_sticker(message.chat_id, cached, **where)
                return True
            except BadRequest as e:  # the id is no good any more (a lost file, a changed bot): upload again
                logger.info("the cached dice sticker %s was refused (%s): uploading it again", name, e)
                self.cache.forget(name)
            except TelegramError as e:
                logger.info("dice sticker failed: %s", e)
                return False
        try:
            path = self.files[name]
            upload = InputFile(
                files("writers_bot.assets.dice_clips").joinpath(path).read_bytes(), filename=PurePosixPath(path).name
            )
            sent = await bot.send_sticker(message.chat_id, upload, **where, **UPLOAD_TIMEOUTS)
        except TelegramError as e:
            logger.warning("could not upload the dice sticker %s: %s", name, e)
            return False
        if sent.sticker is not None:
            self.cache.put(name, sent.sticker.file_id, sent.sticker.file_unique_id)
        return True

    async def on_sticker(self, update: Update, _context) -> None:
        """A person sent one of the roll stickers. They are in no pack, so it was forwarded or copied from a roll."""
        message = update.effective_message
        user = message.from_user
        if user is None or user.is_bot or message.sticker is None:
            return
        if not self.cache.is_roll_sticker(message.sticker.file_unique_id):
            return
        settings = self.bot.services.shared.extra.get("settings")
        mode = await settings.get("telegram", str(message.chat_id), "dice.forwards") if settings else "warn"
        if mode == "off":
            return
        f = self.bot.fmt
        who = f.mention(str(user.id), display_name(user))
        if mode == "delete" and (await self.bot.asks.rights(message.chat_id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()
                await self.bot.app.bot.send_message(
                    message.chat_id,
                    f.t("dice.forward_deleted", who=who),
                    message_thread_id=message.message_thread_id if message.is_topic_message else None,
                )
                return
        await message.reply_html(f.t("dice.forward_warning", who=who))

    # --- rolling ---------------------------------------------------------------------------------

    def pick(self, roll: extras.Roll, outcome: extras.Outcome) -> list[str] | None:
        """The stickers for a roll, or None for just the line: stickers are off or silenced (-q), there are more
        stickers than allowed (three or more need -s, and no more than ``max_stickers``: dice of a kind share stickers,
        up to three to one) or a die has no sticker."""
        if not self.stickers_on or roll.stickers is False:
            return None
        allowed = self.max_stickers if roll.stickers else min(AUTO_STICKERS, self.max_stickers)
        if len(outcome.dice) > allowed * dicestickers.MOST_PER_STICKER:
            return None
        names = dicestickers.choose(outcome.dice, roll.mode)
        if not names or len(names) > allowed or not all(name in self.files for name in names):
            return None
        return names

    async def roll(self, update: Update, _context) -> None:
        message = update.effective_message
        roll = extras.parse_roll(message.text or message.caption or "")
        if roll is None:
            return
        outcome = extras.throw(self.bot.fmt, roll, self.rng)
        names = self.pick(roll, outcome)
        if names:
            if any(self.cache.file_id(name) is None for name in names):  # an upload takes a moment
                with contextlib.suppress(TelegramError):
                    await self.bot.app.bot.send_chat_action(
                        message.chat_id,
                        ChatAction.CHOOSE_STICKER,
                        message_thread_id=message.message_thread_id if message.is_topic_message else None,
                    )
            sent = 0
            for name in names:  # in the order thrown
                if not await self.send_clip(message, name):
                    break
                sent += 1
            if sent == len(names) and outcome.only_die:
                return
        await message.reply_html(outcome.html)
