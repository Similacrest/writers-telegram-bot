"""Achievements on Telegram (PLAN phase 5).

* **Creating**: a chat admin replies to a sticker, a photo or an image file with ``/newachievement Назва — опис``,
  or sends ``/newachievement`` to the bot in private (the bot asks for the image or sticker, the name, and the
  chat if they administer several). A sticker alone, sent to the bot, starts nothing.
* **Awarding**: reply to a member's message with ``/award`` (alias ``/нагорода``) and pick from buttons,
  or ``/award Назва``; ``/unaward`` takes it back. The bot posts one picture, an "achievement unlocked"
  card with the sticker as its icon (:mod:`.render`), with one line as its caption.
* **Listing**: ``/achievements`` in a group is the community's board (a picture: who has the most) with the
  list as its caption; ``/achievements мої`` (or as a reply to someone) is a person's collection, the
  achievements they don't have yet greyed out. In the private chat it is your own collection.
* Before awarding, the bot checks the pack (``getStickerSet``) of an achievement made from a pack's sticker; if the
  sticker is gone, the achievement keeps its name (the card gets a drawn medal) and the awarding admin is asked for
  a new picture. An achievement made from a photo or an image file keeps the picture by its ``file_id``.

Outside creating one, the raw picture is never posted.

The admins own their sticker packs and manage them through @Stickers; the bot only remembers which picture means
which achievement. Achievements belong to the community where there is one, otherwise to the chat.
"""

import asyncio
import contextlib
import csv
import io
import logging
import re
import secrets
import time
from types import SimpleNamespace

from telegram import ForceReply, InlineKeyboardMarkup, Message, ReplyParameters, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ...core.i18n import plural, t, words
from ...telegram_bot.app import command_args_text, display_name, thread_of
from ...telegram_bot.base import Command, ProfilePart, Sent, TelegramModule, button
from . import render
from .store import AchievementStore, owner_for

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
PACK_TTL = 10 * 60  # how long a sticker pack is trusted before asking Telegram again
ICON_CACHE = 300
COLLECTION_MAX = 30
CAPTION_MAX = 1024
PENDING_TTL = 10 * 60
MAX_IMAGE = 10 * 1024 * 1024  # an image file bigger than this is not taken (the cards are small)
SEPARATOR = re.compile(r"\s+[—–-]\s+|\n")


class _Waiting(filters.MessageFilter):
    """A reply to one of the bot's questions (a new name, a new sticker)."""

    def __init__(self, module: "Module"):
        super().__init__(name="achievement_input")
        self.module = module

    def filter(self, message: Message) -> bool:
        user = message.from_user
        if user is None:
            return False
        if message.chat.type == ChatType.PRIVATE:
            return user.id in self.module._dm and time.monotonic() - self.module._dm[user.id]["since"] < PENDING_TTL
        replied = message.reply_to_message
        return replied is not None and (message.chat.id, replied.message_id) in self.module._prompts


class Module(TelegramModule):
    name = "achievements"
    personal_data = True

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.store = AchievementStore(bot.storage) if bot.storage is not None else None
        self._awards: dict[str, dict] = {}  # token -> pending award (chat, target, origin)
        self._dm: dict[int, dict] = {}  # user -> a sticker sent in private, waiting for its name / chat
        self._prompts: dict[tuple[int, int], dict] = {}  # (chat, question) -> waiting for a new sticker
        self._packs: dict[str, tuple[float, object]] = {}  # pack name -> (fetched, StickerSet or None)
        self._icons: dict[str, bytes | None] = {}  # sticker file_unique_id -> its picture
        self._seen: dict[int, tuple] = {}  # user -> (username, name) last written to seen_users

    # --- wiring ----------------------------------------------------------------------------------

    def commands(self) -> list[Command]:
        if self.store is None:
            return []
        return [
            Command(
                "award", self.award_command, "commands.award", uk="aliases.award", scope="chat_admin",
                usage="help.usage_award", when=self._on_here,
            ),
            Command(
                "unaward", self.unaward_command, "commands.unaward", scope="chat_admin", menu=False,
                usage="help.usage_award", when=self._on_here,
            ),
            Command(
                "newachievement", self.new_command, "commands.newachievement", scope="chat_admin",
                usage="help.usage_newachievement", when=self._on_here,
            ),
            Command(
                "achievements", self.list_command, "commands.achievements", usage="help.usage_achievements",
                when=self._on_here,
            ),
            Command(
                "myachievements", self.mine_command, "commands.myachievements", when=self._on_here,
            ),
        ]  # fmt: skip

    async def _on_here(self, ctx) -> bool:
        return ctx.private or bool(await ctx.setting("achievements.enabled"))

    def handlers(self):
        if self.store is None:
            return []
        return [
            (MessageHandler(_Waiting(self) & ~filters.COMMAND, self.on_input), 0),
            # who is who, for /achievements <name>: its own group, so it never stops other handlers
            (MessageHandler(filters.ChatType.GROUPS & filters.ALL, self.note_user), 6),
        ]

    async def note_user(self, update: Update, _context) -> None:
        user = update.effective_user
        if user is None or user.is_bot:
            return
        known = (user.username, display_name(user))
        if self._seen.get(user.id) == known:
            return
        self._seen[user.id] = known
        await self.store.seen(PLATFORM, str(user.id), user.username, display_name(user))

    def asks(self):
        return {"ac": self.on_button}

    async def enabled(self, chat_id: int) -> bool:
        settings = self.bot.services.shared.extra.get("settings")
        return await settings.get_bool(PLATFORM, str(chat_id), "achievements.enabled") if settings else True

    async def owner(self, chat_id: int) -> str:
        communities = self.bot.services.shared.extra.get("communities")
        community = await communities.of_chat(PLATFORM, str(chat_id)) if communities else None
        return owner_for(PLATFORM, str(chat_id), community.id if community else None)

    def _remember(self, store: dict, value: dict) -> str:
        now = time.monotonic()
        for key in [k for k, v in store.items() if now - v["since"] > PENDING_TTL]:
            del store[key]
        token = secrets.token_hex(4)
        store[token] = value | {"since": now}
        return token

    @staticmethod
    def _split(text: str) -> tuple[str, str | None]:
        parts = SEPARATOR.split(text.strip(), maxsplit=1)
        return parts[0], (parts[1] if len(parts) > 1 else None)

    @staticmethod
    def _media(message: Message | None) -> tuple[str | None, str, str] | None:
        """The picture of a message as (sticker pack or None, file_unique_id, file_id): a sticker, a photo (its
        biggest size) or an image file. A sticker outside a pack that is animated has only its thumbnail to offer."""
        if message is None:
            return None
        if (sticker := message.sticker) is not None:
            if sticker.set_name:
                return sticker.set_name, sticker.file_unique_id, sticker.file_id
            source = sticker.thumbnail if (sticker.is_animated or sticker.is_video) else sticker
            return (None, source.file_unique_id, source.file_id) if source is not None else None
        if message.photo:
            photo = message.photo[-1]
            return None, photo.file_unique_id, photo.file_id
        document = message.document
        if (
            document is not None
            and (document.mime_type or "").startswith("image/")
            and (document.file_size or 0) <= MAX_IMAGE
        ):
            return None, document.file_unique_id, document.file_id
        return None

    # --- creating ----------------------------------------------------------------------------------

    async def new_command(self, update: Update, _context) -> None:
        """/newachievement Назва — опис, as a reply to a sticker, a photo or an image file (in private: the steps)."""
        message = update.effective_message
        f = self.bot.fmt
        name, description = self._split(command_args_text(message))
        if message.chat.type == ChatType.PRIVATE:
            await self._start_private(message, name, description)
            return
        if not await self.bot.is_chat_admin(message.chat, message.from_user.id):
            await self.bot.asks.respond(message, t("common.admins_only"), html=False, delete_command=True, ttl=60)
            return
        media = self._media(message.reply_to_message)
        if media is None or not name:
            await self.bot.asks.respond(message, f.t("achievements.new_usage"), delete_command=True)
            return
        await self.create(message, message.chat_id, name, description, media)

    async def create(self, message: Message, chat_id: int, name: str, description: str | None, media) -> None:
        f = self.bot.fmt
        try:
            achievement = await self.store.create(
                await self.owner(chat_id), name, description, media, str(message.from_user.id)
            )
        except ValueError:
            await self.bot.asks.respond(message, f.t("achievements.duplicate", name=name), delete_command=True)
            return
        await self.bot.asks.respond(message, f.t("achievements.created", name=achievement.name), delete_command=True)

    async def _start_private(self, message: Message, name: str = "", description: str | None = None) -> None:
        """/newachievement in private: ask for the picture (and the name, if the command had none)."""
        f = self.bot.fmt
        chats = await self.bot.admin_chats(message.from_user.id)
        if not chats:
            await message.reply_html(f.t("achievements.no_chats"))
            return
        self._dm[message.from_user.id] = {
            "media": None, "chats": chats, "since": time.monotonic(), "name": name or None, "description": description,
        }  # fmt: skip
        await message.reply_html(
            f.t("achievements.dm_how"),
            reply_markup=ForceReply(input_field_placeholder=t("achievements.image_placeholder")),
        )

    async def _take_image(self, message: Message, media) -> None:
        """A picture sent in private while the bot waits for one: the new picture of an achievement, or the
        picture of the one being made (then its name, or the chat, is next)."""
        f = self.bot.fmt
        pending = self._dm[message.from_user.id]
        if pending.get("replace"):
            self._dm.pop(message.from_user.id)
            await self.store.update(
                pending["replace"], sticker_set=media[0], sticker_unique_id=media[1], sticker_file_id=media[2]
            )
            await message.reply_html(f.t("achievements.sticker_replaced"))
            return
        pending["media"] = media
        if pending.get("name"):
            await self._choose_chat(message, pending)
            return
        await message.reply_html(
            f.t("achievements.ask_name"),
            reply_markup=ForceReply(input_field_placeholder=t("achievements.name_placeholder")),
        )

    async def _choose_chat(self, message: Message, pending: dict) -> None:
        """Everything is known but the chat: the only one is taken, else the admin picks."""
        if len(pending["chats"]) == 1:
            self._dm.pop(message.from_user.id)
            await self.create(
                message, pending["chats"][0]["id"], pending["name"], pending.get("description"), pending["media"]
            )
            return
        rows = [[button(c["title"], message.from_user.id, f"ac:mk:{c['id']}")] for c in pending["chats"][:8]]
        await message.reply_html(self.bot.fmt.t("achievements.which_chat"), reply_markup=InlineKeyboardMarkup(rows))

    async def on_input(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE:
            await self._private_input(message)
            return
        pending = self._prompts.pop((message.chat.id, message.reply_to_message.message_id))
        if pending["admin"] != message.from_user.id:
            self._prompts[(message.chat.id, message.reply_to_message.message_id)] = pending
            return
        media = self._media(message)
        if media is None:
            await self.bot.asks.respond(message, self.bot.fmt.t("achievements.send_an_image"), ttl=60)
            self._prompts[(message.chat.id, message.reply_to_message.message_id)] = pending
            return
        await self.store.update(
            pending["achievement"], sticker_set=media[0], sticker_unique_id=media[1], sticker_file_id=media[2]
        )
        with contextlib.suppress(TelegramError):
            await message.reply_to_message.delete()
        if (await self.bot.asks.rights(message.chat_id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()
        await self.bot.asks.respond(message, self.bot.fmt.t("achievements.sticker_replaced"), ttl=60)

    async def _private_input(self, message: Message) -> None:
        """What the admin sends in private while the bot waits for a picture or a name."""
        f = self.bot.fmt
        pending = self._dm[message.from_user.id]
        media = self._media(message)
        if media is not None:  # a picture: the one asked for, or another one instead
            await self._take_image(message, media)
            return
        if pending.get("replace") or pending["media"] is None:
            await message.reply_html(f.t("achievements.send_an_image"))
            return
        name, description = self._split(message.text or "")
        if not name:
            await message.reply_html(f.t("achievements.ask_name"), reply_markup=ForceReply())
            return
        pending["name"], pending["description"] = name, description
        await self._choose_chat(message, pending)

    # --- awarding ----------------------------------------------------------------------------------

    async def _target(self, message: Message):
        replied = message.reply_to_message
        if replied is None or replied.from_user is None or replied.from_user.is_bot:
            return None
        return replied

    async def award_command(self, update: Update, _context) -> None:
        message = update.effective_message
        f = self.bot.fmt
        if message.chat.type == ChatType.PRIVATE or not await self.bot.is_chat_admin(
            message.chat, message.from_user.id
        ):
            await self.bot.asks.respond(message, t("common.admins_only"), html=False, delete_command=True, ttl=60)
            return
        if not await self.enabled(message.chat_id):
            await self.bot.asks.respond(message, f.t("achievements.off"), delete_command=True)
            return
        target = await self._target(message)
        if target is None:
            await self.bot.asks.respond(message, f.t("achievements.award_usage"), delete_command=True)
            return
        owner = await self.owner(message.chat_id)
        query = command_args_text(message)
        if query:
            achievement = await self.store.find(owner, query)
            if achievement is None:
                await self.bot.asks.respond(message, f.t("achievements.unknown", name=query), delete_command=True)
                return
            await self.give(message.chat_id, thread_of(message), message.from_user, target, achievement)
            await self._clean(message)
            return
        items = await self.store.of_owner(owner)
        if not items:
            await self.bot.asks.respond(message, f.t("achievements.none_yet"), delete_command=True)
            return
        token = self._remember(self._awards, {"chat": message.chat_id, "thread": thread_of(message), "target": target})
        rows = [[button(a.name, message.from_user.id, f"ac:aw:{token}:{a.id}")] for a in items[:20]]
        rows.append([button(t("common.cancel"), message.from_user.id, f"ac:aw:{token}:0")])
        await self.bot.asks.respond(
            message,
            f.t("achievements.pick", name=display_name(target.from_user)),
            InlineKeyboardMarkup(rows),
            delete_command=True,
        )

    async def _clean(self, message: Message) -> None:
        if (await self.bot.asks.rights(message.chat_id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()

    async def _pack(self, name: str, *, fresh: bool = False):
        cached = self._packs.get(name)
        if cached is not None and not fresh and time.monotonic() - cached[0] < PACK_TTL:
            return cached[1]
        try:
            pack = await self.bot.app.bot.get_sticker_set(name)
        except TelegramError:
            pack = None
        self._packs[name] = (time.monotonic(), pack)
        return pack

    async def _in_pack(self, achievement, *, fresh: bool = False):
        """The achievement's sticker if it is still in the admins' pack, else None."""
        if not achievement.sticker_set or not achievement.sticker_file_id:
            return None
        pack = await self._pack(achievement.sticker_set, fresh=fresh)
        if pack is None:
            return None
        return next((s for s in pack.stickers if s.file_unique_id == achievement.sticker_unique_id), None)

    async def _source(self, achievement, *, fresh: bool = False) -> tuple[str, str | None] | None:
        """What draws the achievement's picture, as (file_unique_id, file_id to download or None); None when it is
        gone. A photo or image file is kept by its file id; a sticker of a pack must still be in it (an animated one is
        drawn from its thumbnail)."""
        if not achievement.sticker_file_id:
            return None
        if not achievement.sticker_set:
            return achievement.sticker_unique_id or achievement.sticker_file_id, achievement.sticker_file_id
        sticker = await self._in_pack(achievement, fresh=fresh)
        if sticker is None:
            return None
        source = sticker.thumbnail if (sticker.is_animated or sticker.is_video) else sticker
        return sticker.file_unique_id, source.file_id if source is not None else None

    async def sticker_ok(self, achievement) -> bool:
        """Is the picture still there (a sticker still in the admins' pack)?"""
        return await self._source(achievement, fresh=True) is not None

    async def icon(self, achievement, *, fresh: bool = False) -> bytes | None:
        """The achievement's picture for the cards (a static sticker itself, an animated one's thumbnail, a photo);
        None draws a medal instead."""
        source = await self._source(achievement, fresh=fresh)
        if source is None:
            return None
        unique_id, file_id = source
        if unique_id in self._icons:
            return self._icons[unique_id]
        data = None
        if file_id is not None:
            try:
                file = await self.bot.app.bot.get_file(file_id)
                data = bytes(await file.download_as_bytearray())
            except TelegramError as e:
                logger.info("no picture for achievement picture %s: %s", unique_id, e)
        if len(self._icons) >= ICON_CACHE:
            self._icons.pop(next(iter(self._icons)))
        self._icons[unique_id] = data
        return data

    def _date(self, when) -> str:
        return when.astimezone(self.bot.settings.common.timezone).strftime("%d.%m.%Y")

    async def give(self, chat_id: int, thread: int | None, admin, target: Message, achievement) -> None:
        f = self.bot.fmt
        user = target.from_user
        person = (
            await self.bot.people.person_id(PLATFORM, str(user.id), display_name(user)) if self.bot.people else None
        )
        if not await self.store.award(
            achievement, PLATFORM, str(user.id), display_name(user), person, str(chat_id), str(admin.id)
        ):
            await self.bot.asks.send(
                chat_id,
                admin.id,
                f.t("achievements.already", name=display_name(user), achievement=achievement.name),
                thread_id=thread,
                ttl=60,
            )
            return
        reply = ReplyParameters(target.message_id, allow_sending_without_reply=True)
        sticker = await self.sticker_ok(achievement)
        caption = f.t(
            "achievements.awarded", who=f.mention(str(user.id), display_name(user)), achievement=achievement.name
        )
        holders = len(await self.store.holders(achievement.id))
        card = render.AwardCard(
            t("achievements.card_label"),
            achievement.name,
            achievement.description,
            t(
                "achievements.card_footer",
                name=display_name(user),
                date=self._date(self.bot.now()),
                holders=t("achievements.first")
                if holders <= 1
                else t("achievements.holders", people=plural(holders, "people")),
            ),
            await self.icon(achievement) if sticker else None,
        )
        await self._send_picture(chat_id, thread, render.award_card, card, caption, reply)
        if not sticker:
            await self.ask_for_sticker(chat_id, thread, admin, achievement)

    async def _send_picture(self, chat_id: int, thread: int | None, draw, data, caption, reply=None) -> None:
        """One picture with its caption; the caption alone if drawing or sending the picture fails."""
        bot = self.bot.app.bot
        try:
            png = await asyncio.to_thread(draw, data)
            await bot.send_photo(chat_id, png, caption=caption, message_thread_id=thread, reply_parameters=reply)
        except Exception:  # never lose the announcement over a picture
            logger.exception("could not post an achievement picture in %s", chat_id)
            await bot.send_message(chat_id, caption, message_thread_id=thread, reply_parameters=reply)

    async def ask_for_sticker(self, chat_id: int, thread: int | None, admin, achievement) -> None:
        """The sticker is gone from the pack: ask the awarding admin for a new one, privately if the bot can
        write to them, else with a reply question in the chat."""
        f = self.bot.fmt
        bot = self.bot.app.bot
        try:
            await bot.send_message(
                admin.id,
                f.t("achievements.sticker_gone_dm", achievement=achievement.name),
                reply_markup=ForceReply(input_field_placeholder=t("achievements.sticker_placeholder")),
            )
        except TelegramError:
            question = await bot.send_message(
                chat_id,
                f.t(
                    "achievements.sticker_gone",
                    who=f.mention(str(admin.id), display_name(admin)),
                    achievement=achievement.name,
                ),
                reply_markup=ForceReply(selective=True),
                message_thread_id=thread,
            )
            self._prompts[(chat_id, question.message_id)] = {
                "admin": admin.id,
                "achievement": achievement.id,
                "since": time.monotonic(),
            }
            self.bot.asks.expire(Sent(chat_id, admin.id, message_id=question.message_id), PENDING_TTL)
            return
        self._dm[admin.id] = {"replace": achievement.id, "since": time.monotonic()}

    async def unaward_command(self, update: Update, _context) -> None:
        message = update.effective_message
        f = self.bot.fmt
        if message.chat.type == ChatType.PRIVATE or not await self.bot.is_chat_admin(
            message.chat, message.from_user.id
        ):
            await self.bot.asks.respond(message, t("common.admins_only"), html=False, delete_command=True, ttl=60)
            return
        target = await self._target(message)
        if target is None:
            await self.bot.asks.respond(message, f.t("achievements.unaward_usage"), delete_command=True)
            return
        owner = await self.owner(message.chat_id)
        query = command_args_text(message)
        theirs = [
            a.achievement
            for a in await self.store.of_user(PLATFORM, str(target.from_user.id))
            if a.achievement.owner == owner
        ]
        if query:
            achievement = await self.store.find(owner, query)
            if achievement is None or achievement.id not in {a.id for a in theirs}:
                await self.bot.asks.respond(message, f.t("achievements.unknown", name=query), delete_command=True)
                return
            await self.store.unaward(achievement, PLATFORM, str(target.from_user.id))
            await self.bot.asks.respond(
                message,
                f.t("achievements.unawarded", achievement=achievement.name, name=display_name(target.from_user)),
                delete_command=True,
            )
            return
        if not theirs:
            await self.bot.asks.respond(
                message, f.t("achievements.has_none", name=display_name(target.from_user)), delete_command=True
            )
            return
        token = self._remember(self._awards, {"chat": message.chat_id, "thread": thread_of(message), "target": target})
        rows = [[button(a.name, message.from_user.id, f"ac:un:{token}:{a.id}")] for a in theirs]
        rows.append([button(t("common.cancel"), message.from_user.id, f"ac:un:{token}:0")])
        await self.bot.asks.respond(
            message,
            f.t("achievements.pick_remove", name=display_name(target.from_user)),
            InlineKeyboardMarkup(rows),
            delete_command=True,
        )

    async def on_button(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        f = self.bot.fmt
        if args[:1] == ["me"] and query.message is not None:  # /profile → Досягнення, in the private chat
            await query.answer()
            await self.post_collection(query.message.chat.id, None, query.from_user, None)
            return
        if len(args) < 2:
            await self.bot.asks.toast(query, "common.stale")
            return
        if args[0] == "mk":  # the chat for an achievement made in private
            pending = self._dm.pop(query.from_user.id, None)
            chat_id = int(args[1])
            if pending is None or not any(c["id"] == chat_id for c in pending["chats"]):
                await self.bot.asks.toast(query, "common.stale")
                return
            if not await self.bot.is_chat_admin(chat_id, query.from_user.id):
                await self.bot.asks.toast(query, "common.admins_only", alert=True)
                return
            await query.answer()
            try:
                achievement = await self.store.create(
                    await self.owner(chat_id),
                    pending["name"],
                    pending.get("description"),
                    pending["media"],
                    str(query.from_user.id),
                )
            except ValueError:
                await self.bot.asks.edit(sent, f.t("achievements.duplicate", name=pending["name"]))
                return
            await self.bot.asks.edit(sent, f.t("achievements.created", name=achievement.name))
            return
        pending = self._awards.get(args[1])
        if pending is None or len(args) < 3:
            await self.bot.asks.toast(query, "common.stale")
            return
        if args[2] == "0":
            self._awards.pop(args[1], None)
            await query.answer()
            if sent:
                await self.bot.asks.delete(sent)
            return
        if not await self.bot.is_chat_admin(pending["chat"], query.from_user.id):
            await self.bot.asks.toast(query, "common.admins_only", alert=True)
            return
        achievement = await self.store.get(int(args[2]))
        if achievement is None or achievement.owner != await self.owner(pending["chat"]):
            await self.bot.asks.toast(query, "common.stale")
            return
        self._awards.pop(args[1], None)
        await query.answer()
        if sent:
            await self.bot.asks.delete(sent)
        target = pending["target"]
        if args[0] == "aw":
            await self.give(pending["chat"], pending["thread"], query.from_user, target, achievement)
        else:
            await self.store.unaward(achievement, PLATFORM, str(target.from_user.id))
            await self.bot.asks.send(
                pending["chat"], query.from_user.id,
                f.t("achievements.unawarded", achievement=achievement.name, name=display_name(target.from_user)),
                thread_id=pending["thread"], ttl=60,
            )  # fmt: skip

    # --- listing -----------------------------------------------------------------------------------

    async def list_command(self, update: Update, _context) -> None:
        """In the private chat your collection; in a group the community's board, or someone's collection
        (``/achievements мої``, or as a reply to their message)."""
        message = update.effective_message
        f = self.bot.fmt
        thread = thread_of(message)
        if message.chat.type == ChatType.PRIVATE:
            await self.post_collection(message.chat_id, None, message.from_user, None)
            return
        owner = await self.owner(message.chat_id)
        items = await self.store.of_owner(owner)
        if not items:
            await message.reply_html(f.t("achievements.none_yet"))
            return
        replied = await self._target(message)
        arg = command_args_text(message).strip()
        if replied is not None or arg.casefold() in words("achievements.mine_words"):
            who = replied.from_user if replied is not None else message.from_user
            await self.post_collection(message.chat_id, thread, who, owner, items)
            return
        if arg:  # someone's, by @username or (part of) their name
            found = await self.store.find_person(PLATFORM, owner, arg)
            if found is None:
                await self.bot.asks.respond(message, f.t("achievements.who", name=arg), delete_command=True, ttl=60)
                return
            who = SimpleNamespace(id=int(found[0]), full_name=found[1], username=None)
            await self.post_collection(message.chat_id, thread, who, owner, items)
            return
        await self.post_board(message.chat_id, thread, owner, items)

    async def mine_command(self, update: Update, _context) -> None:
        """/myachievements: your collection, posted in the chat (it's yours to show)."""
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE:
            await self.post_collection(message.chat_id, None, message.from_user, None)
            return
        owner = await self.owner(message.chat_id)
        items = await self.store.of_owner(owner)
        if not items:
            await message.reply_html(self.bot.fmt.t("achievements.none_yet"))
            return
        await self.post_collection(message.chat_id, thread_of(message), message.from_user, owner, items)

    async def post_collection(self, chat_id: int, thread: int | None, user, owner: str | None, items=None) -> None:
        """A person's achievements as a picture: of one owner (the ones they lack greyed out), or all of
        theirs (``owner`` None, in the private chat)."""
        f = self.bot.fmt
        awards = await self.store.of_user(PLATFORM, str(user.id))
        if owner is not None:
            awards = [a for a in awards if a.achievement.owner == owner]
        if not awards and owner is None:
            await self.bot.app.bot.send_message(chat_id, f.t("achievements.yours_none"), message_thread_id=thread)
            return
        earned = {a.achievement.id: a for a in awards}
        cards = [
            render.Item(
                a.achievement.name, a.achievement.description, self._date(a.awarded_at), await self.icon(a.achievement)
            )
            for a in awards
        ]
        total = len(awards)
        if owner is not None:
            locked = [a for a in items or [] if a.id not in earned]
            total = len(earned) + len(locked)
            cards += [
                render.Item(a.name, a.description, t("achievements.locked"), await self.icon(a), locked=True)
                for a in locked
            ]
        shown = cards[:COLLECTION_MAX]
        picture = render.Collection(
            t("achievements.collection_title", name=display_name(user)),
            t("achievements.collection_count", earned=len(earned), total=total)
            if owner is not None
            else plural(len(earned), "achievements"),
            shown,
            len(earned),
            total if owner is not None else 0,
        )
        names = ", ".join(f"«{a.achievement.name}»" for a in awards) or t("achievements.none_in_caption")
        if len(names) > CAPTION_MAX - 100:
            names = names[: CAPTION_MAX - 101].rsplit(", ", 1)[0] + ", …"
        # the name in bold, not a mention: looking at someone's collection shouldn't notify them
        caption = f.t("achievements.collection_caption", who=f.bold(display_name(user)), list=names)
        await self._send_picture(chat_id, thread, render.collection, picture, caption)

    async def post_board(self, chat_id: int, thread: int | None, owner: str, items) -> None:
        """The community's (or chat's) board: who has the most, with their icons; the list in the caption."""
        f = self.bot.fmt
        by_id = {a.id: a for a in items}
        rows = []
        rank, previous = 0, None
        for i, (_platform, _user_id, name, ids) in enumerate(await self.store.leaderboard(owner)):
            if len(ids) != previous:
                rank, previous = i + 1, len(ids)
            icons = [await self.icon(by_id[a]) for a in ids if a in by_id]
            rows.append(render.Row(rank, name or "?", str(len(ids)), icons))
        communities = self.bot.services.shared.extra.get("communities")
        community = await communities.of_chat(PLATFORM, str(chat_id)) if communities else None
        where = t("achievements.board_community", name=community.name) if community else t("achievements.board_chat")
        board = render.Board(
            t("achievements.board_title"),
            t("achievements.board_subtitle", where=where, count=plural(len(items), "achievements")),
            rows,
            t("achievements.board_empty"),
        )
        lines = []
        for a in items:
            holders = len(await self.store.holders(a.id))
            lines.append(
                f.t(
                    "achievements.line",
                    name=a.name,
                    description=f" — {a.description}" if a.description else "",
                    count=holders,
                )
            )
        hint = f.t("achievements.mine_hint")
        caption = f.lines(lines)
        while lines and len(str(caption)) + len(str(hint)) + 2 > CAPTION_MAX:
            lines.pop()
            caption = f.lines([*lines, "…"])
        await self._send_picture(chat_id, thread, render.leaderboard, board, f.lines([caption, "", hint]))

    async def profile(self, user, person_id: int | None, private: bool) -> ProfilePart | None:
        if self.store is None:
            return None
        awards = await self.store.of_user(PLATFORM, str(user.id))
        if not awards:
            return None
        names = ", ".join(f"«{a.achievement.name}»" for a in awards)
        buttons = [button(t("achievements.button"), user.id, "ac:me")] if private else []
        return ProfilePart([self.bot.fmt.t("achievements.profile", list=names)], buttons)

    # --- your data ---------------------------------------------------------------------------------

    async def export_files(self, person_id: int) -> dict[str, bytes]:
        if self.store is None:
            return {}
        rows = []
        for account in await self.bot.people.accounts(person_id):
            for a in await self.store.of_user(account.platform, account.user_id):
                rows.append(
                    [a.achievement.name, a.achievement.description or "", a.chat_id or "", a.awarded_at.isoformat()]
                )
        if not rows:
            return {}
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["achievement", "description", "chat_id", "awarded_at"])
        writer.writerows(rows)
        return {"achievements.csv": buf.getvalue().encode("utf-8-sig")}

    async def forget(self, platform: str, user_id: str) -> None:
        if self.store is not None:
            await self.store.forget(platform, user_id)

    # --- the web app ---------------------------------------------------------------------------------

    def web_tabs(self) -> list[str]:
        return ["achievements"] if self.store is not None else []

    def web_routes(self, app) -> None:
        if self.store is None:
            return
        from aiohttp import web

        from ...web.server import ApiError, ok

        async def owned(request, user):
            chat_id = int(request.match_info["chat"])
            await app.require_chat_admin(user, chat_id)
            return chat_id, await self.owner(chat_id)

        async def listing(request, user):
            _chat_id, owner = await owned(request, user)
            out = []
            for a in await self.store.of_owner(owner):
                holders = await self.store.holders(a.id)
                out.append({"id": a.id, "name": a.name, "description": a.description, "sticker": bool(a.sticker_file_id),
                            "holders": [{"name": h.user_name, "at": h.awarded_at.isoformat()} for h in holders]})  # fmt: skip
            return web.json_response({"achievements": out})

        async def change(request, user):
            _chat_id, owner = await owned(request, user)
            a = await self.store.get(int(request.match_info["id"]))
            if a is None or a.owner != owner:
                raise ApiError(404, "web.error_bad_request")
            if request.method == "DELETE":
                await self.store.delete(a.id)
                return ok()
            body = await app.body(request)
            try:
                await self.store.update(a.id, **{k: str(v) for k, v in body.items() if k in ("name", "description")})
            except ValueError:
                raise ApiError(400, "web.error_bad_request") from None
            return await listing(request, user)

        app.add("GET", r"/api/chat/{chat:-?\d+}/achievements", listing)
        app.add("PATCH", r"/api/chat/{chat:-?\d+}/achievements/{id:\d+}", change)
        app.add("DELETE", r"/api/chat/{chat:-?\d+}/achievements/{id:\d+}", change)
