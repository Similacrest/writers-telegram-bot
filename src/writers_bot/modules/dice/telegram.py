"""Dice rolls: /d20, /2d6+1, /r 4d6kh3 Ініціатива, /d20adv (the d20 library's notation, see ``core.extras``).

With ``stickers = true`` a roll is answered with animated stickers of the dice landing on their results (the
packs are made by ``scripts/dice_render`` and kept in ``assets/dice_packs``; ``core.dicestickers`` says which sticker
each die gets). One or two dice get stickers on their own; three or four only when the roll has ``-s`` (``/3d6 -s``);
``-q`` silences them (``/d20 -q``); more than ``max_stickers`` (4) dice, or a die without stickers (d13), and the roll
is only the line. The bot uploads the two packs once, ``dice1_v3_by_<bot>`` and ``dice2_v3_by_<bot>``, owned by the
super admin with the lowest id (Telegram wants a person as a pack's owner), and from then on sends the stickers by
``file_id``. A bare ``/d20`` is just the sticker; with a bonus or a caption the usual line follows.
"""

import asyncio
import contextlib
import json
import logging
from importlib.resources import files
from pathlib import PurePosixPath

from telegram import InputFile, InputSticker, ReplyParameters, Update
from telegram.constants import StickerFormat
from telegram.error import TelegramError, TimedOut
from telegram.ext import MessageHandler, filters

from ...core import dicestickers, extras
from ...core.i18n import t
from ...telegram_bot.base import TelegramModule

logger = logging.getLogger(__name__)

# Part of the pack names: bump it when the animations change, so the bot uploads them as new packs (the old
# ones are simply no longer used). 3: the packs of v4.2.2 (a d20 and d12 with advantage looks, a coin, white numerals).
ANIMATIONS = 3
FIRST_UPLOAD = 50  # createNewStickerSet takes at most 50; the rest go in one by one
# Telegram answers a sticker upload only after processing the videos (50 at once in createNewStickerSet), far beyond
# python-telegram-bot's 5 s defaults; on live Telegram v4.2.5 timed out while the uploads went through.
UPLOAD_TIMEOUTS = {"read_timeout": 300, "write_timeout": 300}
SETTLE_TRIES = 12  # after a timeout anyway: look at the pack this many times, SETTLE_DELAY s apart, for the upload
SETTLE_DELAY = 10.0
AUTO_STICKERS = 2  # a roll of more dice than this gets stickers only when asked for (-s)


def manifest() -> dict[str, list[dict]]:
    """The two packs: for each, its stickers in the order they sit in the pack."""
    return json.loads(files("writers_bot.assets.dice_packs").joinpath("manifest.json").read_text())


def clip_name(row: dict) -> str:
    return PurePosixPath(row["file"]).stem  # "pack1/d20_17.webm" -> "d20_17"


class Module(TelegramModule):
    name = "dice"

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.stickers_on = bool(options.get("stickers"))
        self.max_stickers = int(options.get("max_stickers", 4))  # more dice than this, even with -s: text only
        self.stickers: dict[str, str] = {}  # sticker name ("d20_17") -> file_id in the bot's packs
        self._packs: asyncio.Task | None = None

    def handlers(self):
        # Not only command entities: whether Telegram marks "/2d6" (a digit first) as a command is unclear
        return [(MessageHandler((filters.COMMAND | filters.TEXT) & filters.Regex(extras.ROLL_RE), self.roll), 0)]

    async def help_lines(self, ctx) -> list[tuple[str, str, dict]]:
        return [("tools", "help.dice", {})]

    async def post_init(self) -> None:
        if self.stickers_on:
            self._packs = asyncio.create_task(self.prepare_packs(), name="dice-packs")

    async def shutdown(self) -> None:
        if self._packs is not None and not self._packs.done():
            self._packs.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._packs

    # --- the sticker packs -------------------------------------------------------------------------

    def pack_name(self, key: str) -> str:
        return f"dice{key.removeprefix('pack')}_v{ANIMATIONS}_by_{self.bot.me.username}"

    @staticmethod
    def _in_order(rows: list[dict], pack) -> bool:
        """Each sticker the pack has carries the die marker (the first emoji, the one Telegram hands back) that the manifest
        has at its place: the bot finds a sticker by its place, so a pack put out of order is no use. A pack that is only
        short (an interrupted upload) is in order, and is finished rather than made again."""
        if len(pack.stickers) > len(rows):
            return False
        marker = lambda emoji: (emoji or "").replace("\ufe0f", "")  # noqa: E731
        return all(
            not sticker.emoji or marker(sticker.emoji) == marker(row["emoji"][0])
            for row, sticker in zip(rows, pack.stickers, strict=False)
        )

    async def prepare_packs(self) -> None:
        """Find the packs (or make the missing ones) and remember their stickers' file ids."""
        owner = min(self.bot.settings.super_admins) if self.bot.settings.super_admins else None
        for key, rows in manifest().items():
            try:
                await self._prepare(key, rows, owner)
            except Exception:  # one pack failing shouldn't stop the other; its rolls then go without stickers
                logger.exception("could not prepare the dice sticker pack %s", key)

    async def _prepare(self, key: str, rows: list[dict], owner: int | None) -> None:
        bot = self.bot.app.bot
        name = self.pack_name(key)
        try:
            pack = await bot.get_sticker_set(name)
        except TelegramError:
            pack = None
        if pack is not None and not self._in_order(rows, pack) and owner is not None:
            await bot.delete_sticker_set(name)  # put out of order: no use, it is made again
            pack = None
        have = len(pack.stickers) if pack is not None else 0
        if have < len(rows):
            if owner is None:
                if pack is None:
                    logger.info("no super admin to own the dice sticker packs: rolls go without stickers")
                    return
            else:
                if pack is None:
                    first = [self._input_sticker(row) for row in rows[:FIRST_UPLOAD]]
                    title = t(f"dice.{key}_title")
                    await self._upload(
                        lambda **kw: bot.create_new_sticker_set(owner, name, title, first, **kw), name, len(first)
                    )
                    have = len(first)
                else:
                    logger.info("the sticker pack %s has %d of %d stickers: adding the rest", name, have, len(rows))
                for i in range(have, len(rows)):
                    sticker = self._input_sticker(rows[i])
                    await self._upload(
                        lambda sticker=sticker, **kw: bot.add_sticker_to_set(owner, name, sticker, **kw), name, i + 1
                    )
                pack = await bot.get_sticker_set(name)
                logger.info("the sticker pack %s is ready", name)
        for row, sticker in zip(rows, pack.stickers, strict=False):
            self.stickers[clip_name(row)] = sticker.file_id

    @staticmethod
    def _input_sticker(row: dict) -> InputSticker:
        return InputSticker(
            InputFile(
                files("writers_bot.assets.dice_packs").joinpath(row["file"]).read_bytes(),
                filename=PurePosixPath(row["file"]).name,
                attach=True,  # InputSticker only attaches what it parses itself, not a ready InputFile
            ),
            row["emoji"],
            StickerFormat.VIDEO,
            keywords=row["keywords"],
        )

    async def _upload(self, call, name: str, expected: int) -> None:
        """One createNewStickerSet or addStickerToSet. A timeout on our side doesn't mean Telegram dropped the upload: it
        may still be processing it, so look at the pack until it has ``expected`` stickers before giving up (the next
        start then finishes the pack). Uploading again at once could put a sticker in twice, out of order."""
        try:
            await call(**UPLOAD_TIMEOUTS)
            return
        except TimedOut:
            logger.info("a sticker upload to %s timed out: waiting for Telegram to show it", name)
        for _ in range(SETTLE_TRIES):
            await asyncio.sleep(SETTLE_DELAY)
            with contextlib.suppress(TelegramError):
                if len((await self.bot.app.bot.get_sticker_set(name)).stickers) >= expected:
                    return
        raise TimedOut(f"the upload to {name} timed out and the pack has fewer than {expected} stickers")

    # --- rolling ---------------------------------------------------------------------------------

    def pick(self, roll: extras.Roll, outcome: extras.Outcome) -> list[str] | None:
        """The stickers for a roll, or None for just the line: stickers are off or silenced (-q), there are more
        dice than allowed (three or more need -s, and no more than ``max_stickers``) or a die has no sticker."""
        if not self.stickers_on or roll.stickers is False:
            return None
        allowed = self.max_stickers if roll.stickers else min(AUTO_STICKERS, self.max_stickers)
        if len(outcome.dice) > allowed:
            return None
        names = dicestickers.choose(outcome.dice, roll.mode)
        if not names or not all(name in self.stickers for name in names):
            return None
        return names

    async def roll(self, update: Update, _context) -> None:
        message = update.effective_message
        roll = extras.parse_roll(message.text or message.caption or "")
        if roll is None:
            return
        outcome = extras.throw(self.bot.fmt, roll)
        names = self.pick(roll, outcome)
        if names:
            sent = 0
            for name in names:  # one per die, in the order thrown
                try:
                    await self.bot.app.bot.send_sticker(
                        message.chat_id,
                        self.stickers[name],
                        message_thread_id=message.message_thread_id if message.is_topic_message else None,
                        reply_parameters=ReplyParameters(message.message_id, allow_sending_without_reply=True),
                    )
                    sent += 1
                except TelegramError as e:
                    logger.info("dice sticker failed: %s", e)
                    break
            if sent == len(names) and outcome.only_die:
                return
        await message.reply_html(outcome.html)
