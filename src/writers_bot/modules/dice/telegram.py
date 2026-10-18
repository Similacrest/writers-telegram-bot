"""Dice rolls: /d20, /2d6+1, /r 4d6kh3 Ініціатива, /d20adv (the d20 library's notation, see ``core.extras``).

With ``stickers = true`` a roll of one d4, d6, d8, d10, d12, d20 or d100 (the two percentile d10s, tens and
ones) is answered with an animated sticker of the dice landing on the result (``scripts/dice_stickers.py``
makes them, ``assets/dice`` keeps them). The bot uploads them once into its own sticker packs,
``d20_v2_by_<bot>`` and so on, owned by the super admin with the lowest id (Telegram wants a person as a pack's
owner), and from then on sends them by ``file_id``. A bare ``/d20`` is just the sticker; with a bonus or a
caption the usual line follows. A roll of several dice gets a sticker per die, up to ``max_stickers`` (4);
more dice than that, or a die without stickers (d3, d7), and the roll is only the line.
"""

import asyncio
import contextlib
import logging
from importlib.resources import files

from telegram import InputFile, InputSticker, ReplyParameters, Update
from telegram.constants import StickerFormat
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ...core import extras
from ...core.i18n import t
from ...telegram_bot.base import TelegramModule

logger = logging.getLogger(__name__)

SIDES = (4, 6, 8, 10, 12, 20, 100)
EMOJI = "🎲"
# Part of the pack names: bump it when the animations change, so the bot uploads them as new packs (the old
# ones are simply no longer used). 2: the v4.2.1 animations (a 0.6 s roll, then still).
ANIMATIONS = 2
FIRST_UPLOAD = 50  # createNewStickerSet takes at most 50; the rest go in one by one


def sticker_file(sides: int, value: int) -> bytes:
    return files("writers_bot.assets.dice").joinpath(f"d{sides}_{value}.webm").read_bytes()


class Module(TelegramModule):
    name = "dice"

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.stickers_on = bool(options.get("stickers"))
        self.max_stickers = int(options.get("max_stickers", 4))  # more dice than this: text only, no flood
        self.stickers: dict[tuple[int, int], str] = {}  # (sides, value) -> file_id in the bot's packs
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

    def pack_name(self, sides: int) -> str:
        return f"d{sides}_v{ANIMATIONS}_by_{self.bot.me.username}"

    async def prepare_packs(self) -> None:
        """Find the packs (or make the missing ones) and remember their stickers' file ids."""
        owner = min(self.bot.settings.super_admins) if self.bot.settings.super_admins else None
        for sides in SIDES:
            try:
                await self._prepare(sides, owner)
            except Exception:  # one die failing shouldn't stop the others; rolls then go without it
                logger.exception("could not prepare the d%d stickers", sides)

    async def _prepare(self, sides: int, owner: int | None) -> None:
        bot = self.bot.app.bot
        name = self.pack_name(sides)
        try:
            pack = await bot.get_sticker_set(name)
        except TelegramError:
            pack = None
        if pack is not None and len(pack.stickers) != sides and owner is not None:
            await bot.delete_sticker_set(name)  # an incomplete pack from an interrupted upload
            pack = None
        if pack is None:
            if owner is None:
                logger.info("no super admin to own the dice sticker packs: rolls go without stickers")
                return
            stickers = [
                InputSticker(
                    InputFile(sticker_file(sides, value), filename=f"d{sides}_{value}.webm"),
                    [EMOJI],
                    StickerFormat.VIDEO,
                )
                for value in range(1, sides + 1)
            ]
            await bot.create_new_sticker_set(owner, name, t("dice.pack_title", sides=sides), stickers[:FIRST_UPLOAD])
            for sticker in stickers[FIRST_UPLOAD:]:
                await bot.add_sticker_to_set(owner, name, sticker)
            pack = await bot.get_sticker_set(name)
            logger.info("created the sticker pack %s", name)
        for value, sticker in enumerate(pack.stickers[:sides], 1):
            self.stickers[(sides, value)] = sticker.file_id

    # --- rolling ---------------------------------------------------------------------------------

    async def roll(self, update: Update, _context) -> None:
        message = update.effective_message
        roll = extras.parse_roll(message.text or message.caption or "")
        if roll is None:
            return
        outcome = extras.throw(self.bot.fmt, roll)
        stickers = [self.stickers.get(die) for die in outcome.dice]
        if self.stickers_on and stickers and len(stickers) <= self.max_stickers and all(stickers):
            sent = 0
            for sticker in stickers:  # one per die, in the order thrown
                try:
                    await self.bot.app.bot.send_sticker(
                        message.chat_id,
                        sticker,
                        message_thread_id=message.message_thread_id if message.is_topic_message else None,
                        reply_parameters=ReplyParameters(message.message_id, allow_sending_without_reply=True),
                    )
                    sent += 1
                except TelegramError as e:
                    logger.info("dice sticker failed: %s", e)
                    break
            if sent == len(stickers) and outcome.only_die:
                return
        await message.reply_html(outcome.html)
