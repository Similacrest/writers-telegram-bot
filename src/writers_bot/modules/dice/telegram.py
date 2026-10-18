"""Dice rolls: /d20, /2d6+1, /r 4d6kh3 Ініціатива, /d20adv, !к12 (the d20 library's notation, see ``core.extras``).

With ``stickers = true`` a roll is answered with animated stickers of the dice landing on their results (the
packs are made by ``scripts/dice_render`` and kept in ``assets/dice_packs``; ``core.dicestickers`` says which sticker
each die gets). One or two dice get stickers on their own; three or four only when the roll has ``-s`` (``/3d6 -s``);
``-q`` silences them (``/d20 -q``); more than ``max_stickers`` (4) dice, or a die without stickers (d13), and the
roll is only the line. The bot never uploads the packs: ``scripts/upload_dice_packs.py`` does it once, and every bot
names the same two packs in its ``packs`` option (names or t.me/addstickers links). The bot reads them at start and
from then on sends the stickers by ``file_id``. A bare ``/d20`` is just the sticker; with a bonus or a caption the
usual line follows.
"""

import asyncio
import contextlib
import json
import logging
from importlib.resources import files
from pathlib import PurePosixPath

from telegram import ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ...core import dicestickers, extras, randomness
from ...telegram_bot.base import Command, TelegramModule

logger = logging.getLogger(__name__)

DICE = (2, 4, 6, 8, 10, 12, 20, 100)  # the dice with a command of their own
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
        # "random.org": the dice come from true random bytes (core.randomness) instead of Python's generator
        self.rng: randomness.TrueRandom | None = None
        if options.get("randomness") == "random.org":
            key = str(options.get("random_org_key") or "")
            self.rng = randomness.TrueRandom(lambda n: randomness.random_org(key, n))
        self.stickers: dict[str, str] = {}  # sticker name ("d20_17") -> file_id in the bot's packs
        self._packs: asyncio.Task | None = None

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
        return [(MessageHandler((filters.COMMAND | filters.TEXT) & filters.Regex(extras.ROLL_RE), self.roll), 0)]

    async def help_lines(self, ctx) -> list[tuple[str, str, dict]]:
        return [("dice", "help.dice", {})]

    async def post_init(self) -> None:
        if self.rng is not None:
            await (
                self.rng.start()
            )  # the first pool before the first roll; the bot starts without it if random.org is down
        if self.stickers_on:
            self._packs = asyncio.create_task(self.load_packs(), name="dice-packs")

    async def shutdown(self) -> None:
        if self.rng is not None:
            await self.rng.stop()
        if self._packs is not None and not self._packs.done():
            self._packs.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._packs

    # --- the sticker packs -------------------------------------------------------------------------

    @staticmethod
    def pack_name(reference: str) -> str:
        """A pack's name from what the config says: the name itself or a t.me/addstickers link."""
        return reference.strip().rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _intact(rows: list[dict], pack) -> bool:
        """The pack has all its stickers, each with the die marker (the first emoji, the one Telegram hands back) that the
        manifest has at its place: the bot finds a sticker by its place, so a pack put out of order is no use."""
        if len(pack.stickers) != len(rows):
            return False
        marker = lambda emoji: (emoji or "").replace("\ufe0f", "")  # noqa: E731
        return all(
            not sticker.emoji or marker(sticker.emoji) == marker(row["emoji"][0])
            for row, sticker in zip(rows, pack.stickers, strict=True)
        )

    async def load_packs(self) -> None:
        """Read the configured packs and remember their stickers' file ids; a pack that is missing, incomplete or put out
        of order is left out (its dice get no stickers) with a warning: ``scripts/upload_dice_packs.py`` makes them."""
        references = list(self.options.get("packs") or [])
        if len(references) != 2:
            logger.warning("dice stickers are on but `packs` does not name the two packs: rolls go without stickers")
            return
        found: dict[str, str] = {}
        for (key, rows), reference in zip(manifest().items(), references, strict=True):
            name = self.pack_name(reference)
            try:
                pack = await self.bot.app.bot.get_sticker_set(name)
            except TelegramError as e:
                logger.warning("the dice sticker pack %s (%s) cannot be read: %s", name, key, e)
                continue
            if not self._intact(rows, pack):
                logger.warning("the dice sticker pack %s (%s) is incomplete or out of order: not used", name, key)
                continue
            for row, sticker in zip(rows, pack.stickers, strict=True):
                found[clip_name(row)] = sticker.file_id
        self.stickers = found

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
        outcome = extras.throw(self.bot.fmt, roll, self.rng)
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
