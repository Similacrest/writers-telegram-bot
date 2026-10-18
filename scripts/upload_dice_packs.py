"""Upload the two dice sticker packs (``src/writers_bot/assets/dice_packs``) to Telegram, once for all bots.

The bots never upload them: each names the same two packs in its ``dice`` options (``packs = [...]``, or
``DICE_PACKS`` in the environment) and reads them at start. Run this once, with the token of the bot that
will make the packs (best a bot just for them) and the user id of the person who will own them (they must have
started that bot):

    TELEGRAM_TOKEN=... uv run python scripts/upload_dice_packs.py --owner 123456789
    uv run python scripts/upload_dice_packs.py --owner 123456789 --token-env DICE_TOKEN --prefix dice

It prints the ``packs`` line for the config. A pack that already exists and is complete is left alone; one that is short
(an interrupted upload) is finished; one put out of order is deleted and made again only with ``--replace``. Uploads get
long timeouts, and after a timeout the script waits for the sticker to show up instead of uploading it twice. Telegram requires a bot-made pack's
name to end in ``_by_<username of the bot that made it>``, so that is the one thing in the name that belongs to a
bot; there is no version in it, and the title has neither. To change the stickers, upload new packs with another
``--prefix`` and change the ``packs`` option of every bot.
"""

import argparse
import asyncio
import contextlib
import os
import sys
from functools import partial
from importlib.resources import files
from pathlib import PurePosixPath

from telegram import Bot, InputFile, InputSticker
from telegram.constants import StickerFormat
from telegram.error import TelegramError, TimedOut

from writers_bot.core.i18n import t
from writers_bot.modules.dice.telegram import manifest

FIRST_UPLOAD = 50  # createNewStickerSet takes at most 50; the rest go in one by one
# Telegram answers a sticker upload only after processing the videos (50 at once in createNewStickerSet), far beyond
# python-telegram-bot's 5 s defaults: live Telegram took the uploads while the bot timed out.
UPLOAD_TIMEOUTS = {"read_timeout": 300, "write_timeout": 300}
SETTLE_TRIES = 12  # after a timeout anyway: look at the pack this many times, SETTLE_DELAY s apart, for the upload
SETTLE_DELAY = 10.0


def pack_name(prefix: str, key: str, username: str) -> str:
    return f"{prefix}{key.removeprefix('pack')}_by_{username}"


def input_sticker(row: dict) -> InputSticker:
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


def in_order(rows: list[dict], pack) -> bool:
    """Each sticker the pack has carries the die marker (the first emoji, the one Telegram hands back) that the manifest
    has at its place. A pack that is only short (an interrupted upload) is in order, and is finished, not made again."""
    if len(pack.stickers) > len(rows):
        return False
    marker = lambda emoji: (emoji or "").replace("\ufe0f", "")  # noqa: E731
    return all(
        not sticker.emoji or marker(sticker.emoji) == marker(row["emoji"][0])
        for row, sticker in zip(rows, pack.stickers, strict=False)
    )


async def upload_one(bot: Bot, call, name: str, expected: int) -> None:
    """One createNewStickerSet or addStickerToSet. A timeout on our side doesn't mean Telegram dropped the upload: it may
    still be processing it, so look at the pack until it has ``expected`` stickers before giving up (running the script
    again then finishes the pack). Uploading again at once could put a sticker in twice, out of order."""
    try:
        await call(**UPLOAD_TIMEOUTS)
        return
    except TimedOut:
        print(f"{name}: an upload timed out, waiting for Telegram to show it")
    for _ in range(SETTLE_TRIES):
        await asyncio.sleep(SETTLE_DELAY)
        with contextlib.suppress(TelegramError):
            if len((await bot.get_sticker_set(name)).stickers) >= expected:
                return
    raise TimedOut(f"the upload to {name} timed out and the pack has fewer than {expected} stickers")


async def upload(bot: Bot, owner: int, prefix: str, *, replace: bool = False) -> list[str]:
    """Make the packs that are missing and finish the ones that are short; the names of all. Raises RuntimeError for a pack
    out of order without ``replace``."""
    username = (await bot.get_me()).username
    names = []
    for key, rows in manifest().items():
        name = pack_name(prefix, key, username)
        names.append(name)
        try:
            pack = await bot.get_sticker_set(name)
        except TelegramError:
            pack = None
        if pack is not None and not in_order(rows, pack):
            if not replace:
                raise RuntimeError(f"{name} is out of order or has too many stickers: use --replace to make it again")
            await bot.delete_sticker_set(name)
            pack = None
        have = len(pack.stickers) if pack is not None else 0
        if have == len(rows):
            print(f"{name}: already there, {len(rows)} stickers")
            continue
        if pack is None:
            first = [input_sticker(row) for row in rows[:FIRST_UPLOAD]]
            title = t(f"dice.{key}_title")
            await upload_one(bot, partial(bot.create_new_sticker_set, owner, name, title, first), name, len(first))
            have = len(first)
        else:
            print(f"{name}: {have} of {len(rows)} stickers, adding the rest")
        for i in range(have, len(rows)):
            sticker = input_sticker(rows[i])
            await upload_one(bot, partial(bot.add_sticker_to_set, owner, name, sticker), name, i + 1)
        print(f"{name}: ready, {len(rows)} stickers")
    return names


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--owner", type=int, required=True, help="Telegram user id of the packs' owner")
    parser.add_argument("--prefix", default="dice", help="start of the pack names (default: dice)")
    parser.add_argument("--token-env", default="TELEGRAM_TOKEN", help="environment variable with the bot token")
    parser.add_argument("--replace", action="store_true", help="make a broken existing pack again")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        sys.exit(f"set {args.token_env} to a bot token")
    async with Bot(token) as bot:
        names = await upload(bot, args.owner, args.prefix, replace=args.replace)
    print("\nFor every bot's config:  packs = [" + ", ".join(f'"{n}"' for n in names) + "]")
    print("or in the environment:   DICE_PACKS=" + ",".join(names))


if __name__ == "__main__":
    asyncio.run(main())
