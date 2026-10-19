"""Dice rolls: /d20, /2d6+1, /r 4d6kh3 Ініціатива, /d20adv, !к12 (the d20 library's notation, see ``core.extras``).

With ``stickers = true`` a roll is answered with animated stickers of the dice landing on their results
(``scripts/dice_render`` makes the clips, kept in ``assets/dice_clips``; ``core.dicestickers`` says which sticker each roll
gets). One or two stickers come on their own; three or four only when the roll has ``-s`` (``/3d6 -s``); ``-q`` silences
them (``/d20 -q``); more than ``max_stickers`` (4) stickers, or a die without stickers (d13), and the roll is only the line.
Dice of a kind share stickers (8d6 is three), so the limits count stickers, not dice.

The clips are not in a sticker pack. The bot uploads a clip with ``sendSticker`` the first time a roll needs it and keeps
the ``file_id`` in a cache file (``core.dicecache``), so later rolls send by id. With ``stitch = true`` a roll that needs several
stickers gets one instead, made on the spot from its clips with ffmpeg (``core.stitch``) and kept the same way. Because the stickers are in no pack,
people cannot send them from the keyboard: a message from a person with one of them is a forward or a copy of a roll, and
the bot says so (the chat setting ``dice.forwards``: off, warn, delete). A bare ``/d20`` is just the sticker; with a bonus
or a caption the usual line follows.
"""

import contextlib
import csv
import io
import json
import logging
import tempfile
from importlib.resources import files
from pathlib import Path, PurePosixPath

from telegram import InputFile, ReplyParameters, Update
from telegram.constants import ChatAction, ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import MessageHandler, filters

from ...core import dicestickers, dicevars, extras, randomness, stitch
from ...core.dicecache import StickerCache
from ...telegram_bot.app import display_name
from ...telegram_bot.base import Command, TelegramModule
from ...telegram_bot.ephemeral import incoming_ephemeral_id
from .variables import Variables

logger = logging.getLogger(__name__)

DICE = (2, 3, 4, 6, 8, 10, 12, 20, 100)  # the dice with a command of their own
AUTO_STICKERS = 2  # a roll of more stickers than this gets them only when asked for (-s)
UPLOAD_TIMEOUTS = {"read_timeout": 60, "write_timeout": 60}  # Telegram answers an upload slower than the 5 s default
CACHE_FILE = "dice_stickers.json"  # next to the database


class _InPrivate:
    """Where a private roll goes when it can't be ephemeral: the roller's private chat with the bot (no message to reply to)."""

    is_topic_message = False
    message_thread_id = None
    message_id = None

    def __init__(self, bot, user_id: int):
        self.bot, self.chat_id = bot, user_id

    async def reply_html(self, text: str):
        return await self.bot.send_message(self.chat_id, text, parse_mode="HTML")


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
        # roll variables (/dicevar, /$name) need the database; a bot without one has none
        self.vars = Variables(self) if options.get("variables") and bot.storage is not None else None
        self.stitcher = self._make_stitcher(options) if options.get("stitch") else None

    def commands(self) -> list[Command]:
        """Each die is a command of its own (/d20 ... in the menu and /help), also as !к20; the rest of the notation
        (/2d6+3, /r 4d6kh3) is read by ``handlers``."""

        extra = self.vars.commands() if self.vars is not None else []
        if not self.options.get(
            "commands", True
        ):  # commands = false: no die commands in the menu; the rolls still work
            return extra

        async def values(_ctx, sides=0):
            return {"sides": sides}

        return [
            Command(
                f"d{sides}", self.roll, "commands.die", uk=f"aliases.d{sides}",
                values=lambda ctx, sides=sides: values(ctx, sides),
            )
            for sides in DICE
        ] + extra  # fmt: skip

    def handlers(self):
        # Not only command entities: whether Telegram marks "/2d6" (a digit first) as a command is unclear
        handlers = [(MessageHandler((filters.COMMAND | filters.TEXT) & filters.Regex(extras.ROLL_RE), self.roll), 0)]
        if self.stickers_on:  # its own group: looking at stickers never stops another handler
            handlers.append((MessageHandler(filters.Sticker.ALL & filters.ChatType.GROUPS, self.on_sticker), 7))
        return handlers

    def _make_stitcher(self, options: dict) -> stitch.Stitcher | None:
        ffmpeg = stitch.ffmpeg_path(str(options.get("ffmpeg") or ""))
        if ffmpeg is None:
            logger.warning("dice stitching is on but there is no ffmpeg: a roll of many dice gets stickers as before")
            return None
        if options.get("stitch_folder"):
            return stitch.Stitcher(ffmpeg, Path(options["stitch_folder"]))
        if self.cache.path is not None:
            return stitch.Stitcher(ffmpeg, self.cache.path.parent / "dice_stitched")
        return stitch.Stitcher(ffmpeg, Path(tempfile.gettempdir()) / "writers_dice_stitched", keep=100)

    def _cache_path(self, options: dict) -> Path | None:
        """Where the file ids are kept: ``cache_file``, else next to the database, else nowhere (until the bot stops)."""
        if options.get("cache_file"):
            return Path(options["cache_file"])
        storage = getattr(self.bot, "storage", None)
        return (
            Path(storage.path).parent / CACHE_FILE if storage is not None and str(storage.path) != ":memory:" else None
        )

    async def help_lines(self, ctx) -> list[tuple[str, str, dict]]:
        lines = [("dice", "help.dice", {})]
        if self.stickers_on and (ctx.private or await ctx.setting("dice.stickers")):
            lines.append(("dice", "help.dice_stickers", {"max": self.max_stickers}))
        return lines

    @property
    def personal_data(self) -> bool:
        return self.vars is not None  # the variables are kept about people: /export and /forgetme take them

    def asks(self) -> dict:
        return self.vars.asks() if self.vars is not None else {}

    async def export_files(self, person_id: int) -> dict[str, bytes]:
        if self.vars is None:
            return {}
        rows = await self.vars.export_rows(await self.bot.people.accounts(person_id))
        if not rows:
            return {}
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["chat_id", "variable", "rolls"])
        writer.writerows(rows)
        return {"dice-variables.csv": buf.getvalue().encode("utf-8-sig")}

    async def forget(self, platform: str, user_id: str) -> None:
        if self.vars is not None:
            await self.vars.forget(platform, user_id)

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
        """Send one clip as a sticker in reply to the roll."""

        async def load() -> tuple[str, bytes]:
            path = self.files[name]
            return PurePosixPath(path).name, files("writers_bot.assets.dice_clips").joinpath(path).read_bytes()

        return await self._send(message, name, load)

    async def send_stitched(self, message, names: list[str]) -> bool:
        """Send several clips as one sticker, stitched together (made the first time, then sent by its id)."""
        key = stitch.key_of(names)

        async def load() -> tuple[str, bytes]:
            folder = files("writers_bot.assets.dice_clips")
            inputs = [Path(str(folder.joinpath(self.files[name]))) for name in names]
            path = await self.stitcher.make(key, inputs)
            return path.name, path.read_bytes()

        return await self._send(message, key, load)

    async def _send(self, message, key: str, load) -> bool:
        """Send a sticker by its cached file id, else upload it (``load`` gives its file name and bytes) and keep the id."""
        bot = self.bot.app.bot
        where = {
            "message_thread_id": message.message_thread_id if message.is_topic_message else None,
            "reply_parameters": ReplyParameters(message.message_id, allow_sending_without_reply=True)
            if message.message_id
            else None,
        }
        cached = self.cache.file_id(key)
        if cached is not None:
            try:
                await bot.send_sticker(message.chat_id, cached, **where)
                return True
            except BadRequest as e:  # the id is no good any more (a lost file, a changed bot): upload again
                logger.info("the cached dice sticker %s was refused (%s): uploading it again", key, e)
                self.cache.forget(key)
            except TelegramError as e:
                logger.info("dice sticker failed: %s", e)
                return False
        try:
            filename, data = await load()
            sent = await bot.send_sticker(
                message.chat_id, InputFile(data, filename=filename), **where, **UPLOAD_TIMEOUTS
            )
        except stitch.StitchError as e:
            logger.warning("could not stitch the dice sticker %s: %s", key, e)
            return False
        except TelegramError as e:
            logger.warning("could not upload the dice sticker %s: %s", key, e)
            return False
        if sent.sticker is not None:
            self.cache.put(key, sent.sticker.file_id, sent.sticker.file_unique_id)
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

    def plan(self, roll: extras.Roll, outcome: extras.Outcome) -> list[list[str]] | None:
        """The stickers for a roll, each a list of clips (one clip: that clip; more: stitched into one), or None for just the
        line: stickers are off or silenced (-q), there are more stickers than allowed (three or more need -s, and no more than
        ``max_stickers``) or a die has no sticker. Dice of a kind share clips (8d6 is three), and with stitching up to six
        dice share a sticker, shared out evenly (8d6 is two stickers of four)."""
        if not self.stickers_on or roll.stickers is False:
            return None
        allowed = self.max_stickers if roll.stickers else min(AUTO_STICKERS, self.max_stickers)
        per = dicestickers.STITCHED_DICE if self.stitcher is not None else dicestickers.MOST_PER_STICKER
        if len(outcome.dice) > allowed * per:
            return None
        if self.stitcher is not None:
            stickers = dicestickers.grouped(outcome.dice, roll.mode)
        else:
            names = dicestickers.choose(outcome.dice, roll.mode)
            stickers = [[name] for name in names] if names else None
        if not stickers or len(stickers) > allowed or not all(n in self.files for part in stickers for n in part):
            return None
        return stickers

    def pick(self, roll: extras.Roll, outcome: extras.Outcome) -> list[str] | None:
        """The clips of the stickers of ``plan``, in order."""
        stickers = self.plan(roll, outcome)
        return [name for part in stickers for name in part] if stickers else None

    async def roll(self, update: Update, _context) -> None:
        message = update.effective_message
        text = message.text or message.caption or ""
        if ref := dicevars.reference(text):  # /$hit: a variable of the sender's
            if self.vars is None:
                await message.reply_html(self.bot.fmt.t("dice.var_off"))
            else:
                await self.vars.roll_reference(message, ref)
            return
        roll = extras.parse_roll(text)
        if roll is None:
            return
        if roll.save_as and self.vars is not None:  # /d20+4 =$strcheck keeps the roll, then rolls it
            await self.vars.save_from_roll(message, roll)
        elif roll.save_as:  # say so rather than drop the =$name quietly; the roll itself still happens
            await self.bot.asks.respond(message, self.bot.fmt.t("dice.var_off"), ttl=60)
        await self.deliver(message, [roll])

    async def deliver(self, message, rolls: list[extras.Roll]) -> None:
        """Roll and answer: the stickers (one roll only, or more with -s, up to ``max_stickers`` in all), then the lines in one
        message. A private roll (-p) is answered to the roller alone, as text, and its request is deleted."""
        outcomes = [extras.throw(self.bot.fmt, roll, self.rng) for roll in rolls]
        html = "\n\n".join(str(outcome.html) for outcome in outcomes)
        if any(roll.private for roll in rolls) and message.chat.type != ChatType.PRIVATE:
            await self._private(message, rolls, outcomes, html)
            return
        if not await self.stickers_here(message.chat_id):
            await message.reply_html(html)
            return
        await self._send_roll(message, rolls, outcomes, html)

    async def _private(self, message, rolls, outcomes, html: str) -> None:
        """A private roll in a group: an ephemeral message where Telegram allows (text only), else the private chat
        with the bot (stickers and all), else a hint to start that chat. The request goes where the bot may delete it."""
        asks, user = self.bot.asks, message.from_user
        thread = message.message_thread_id if message.is_topic_message else None
        sent = await asks.send(
            message.chat_id, user.id, html, thread_id=thread,
            reply_to_ephemeral=incoming_ephemeral_id(message), public=False,
        )  # fmt: skip
        if sent is None:
            head = self.bot.fmt.t("dice.private_head", chat=message.chat.title or str(message.chat_id))
            try:
                await self.bot.app.bot.send_message(user.id, head, parse_mode="HTML")
            except TelegramError:
                await asks.dm_hint(message)  # the roll stays unseen
                return
            if self.stickers_on:
                await self._send_roll(_InPrivate(self.bot.app.bot, user.id), rolls, outcomes, html)
            else:
                await self.bot.app.bot.send_message(user.id, html, parse_mode="HTML")
        if not incoming_ephemeral_id(message) and (await asks.rights(message.chat_id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()

    async def stickers_here(self, chat_id: int) -> bool:
        """The chat setting ``dice.stickers``: a chat can turn the roll stickers off."""
        if not self.stickers_on or chat_id > 0:
            return self.stickers_on
        settings = self.bot.services.shared.extra.get("settings")
        return await settings.get_bool("telegram", str(chat_id), "dice.stickers") if settings else True

    async def _send_roll(self, message, rolls, outcomes, html) -> None:
        parts: list[list[str]] = []  # the stickers, each the clips it is made of
        for roll, outcome in zip(rolls, outcomes, strict=True):
            if len(rolls) > 1 and not roll.stickers:  # a series has stickers only when asked for (-s)
                continue
            planned = self.plan(roll, outcome)
            if planned and len(parts) + len(planned) <= self.max_stickers:
                parts += planned
        if parts:
            names = [name for part in parts for name in part]
            if any(self.cache.file_id(stitch.key_of(part) if len(part) > 1 else part[0]) is None for part in parts):
                with contextlib.suppress(TelegramError):  # an upload (and stitching) takes a moment
                    await self.bot.app.bot.send_chat_action(
                        message.chat_id,
                        ChatAction.CHOOSE_STICKER,
                        message_thread_id=message.message_thread_id if message.is_topic_message else None,
                    )
            sent = 0
            for part in parts:  # in the order thrown
                if not await (
                    self.send_stitched(message, part) if len(part) > 1 else self.send_clip(message, part[0])
                ):
                    break
                sent += 1
            if sent == 0 and any(len(part) > 1 for part in parts) and len(names) <= self.max_stickers:
                for name in names:  # ffmpeg failed: the clips one by one
                    if not await self.send_clip(message, name):
                        break
                    sent += 1
                parts = [[name] for name in names]
            if sent == len(parts) and len(rolls) == 1 and outcomes[0].only_die:
                return
        await message.reply_html(html)
