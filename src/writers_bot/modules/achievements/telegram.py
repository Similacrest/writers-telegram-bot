"""Achievements on Telegram (PLAN phase 5).

* **Creating**: a chat admin replies to a sticker from their pack with ``/newachievement Назва — опис``,
  or sends the sticker to the bot in private (the bot asks for the name, and for the chat if they
  administer several).
* **Awarding**: reply to a member's message with ``/award`` (alias ``/нагорода``) and pick from buttons,
  or ``/award Назва``; ``/unaward`` takes it back. The bot posts the sticker and one line.
* Before posting, the bot checks the pack (``getStickerSet``); if the sticker is gone, the achievement keeps
  its name and the awarding admin is asked for a new sticker.

The admins own the sticker pack and manage it through @Stickers; the bot only remembers which sticker means
which achievement. Achievements belong to the community where there is one, otherwise to the chat.
"""

import contextlib
import csv
import io
import re
import secrets
import time

from telegram import ForceReply, InlineKeyboardMarkup, Message, ReplyParameters, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

from ...core.i18n import t
from ...telegram_bot.app import command_args_text, display_name, thread_of
from ...telegram_bot.base import Command, ProfilePart, Sent, TelegramModule, button
from .store import AchievementStore, owner_for

PLATFORM = "telegram"
PENDING_TTL = 10 * 60
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

    # --- wiring ----------------------------------------------------------------------------------

    def commands(self) -> list[Command]:
        if self.store is None:
            return []
        return [
            Command("award", self.award_command, None, uk="aliases.award"),
            Command("unaward", self.unaward_command, None),
            Command("newachievement", self.new_command, None),
            Command("achievements", self.list_command, "commands.achievements", help="help.achievements"),
        ]

    def handlers(self):
        if self.store is None:
            return []
        return [
            (MessageHandler(_Waiting(self) & ~filters.COMMAND, self.on_input), 0),
            (MessageHandler(filters.ChatType.PRIVATE & filters.Sticker.ALL, self.on_private_sticker), 0),
        ]

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
    def _sticker(message: Message | None):
        return message.sticker if message is not None and message.sticker is not None else None

    # --- creating ----------------------------------------------------------------------------------

    async def new_command(self, update: Update, _context) -> None:
        """/newachievement Назва — опис, as a reply to a sticker from the admins' pack."""
        message = update.effective_message
        f = self.bot.fmt
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_html(f.t("achievements.dm_how"))
            return
        if not await self.bot.is_chat_admin(message.chat, message.from_user.id):
            await self.bot.asks.respond(message, t("common.admins_only"), html=False, delete_command=True, ttl=60)
            return
        sticker = self._sticker(message.reply_to_message)
        name, description = self._split(command_args_text(message))
        if sticker is None or not name:
            await self.bot.asks.respond(message, f.t("achievements.new_usage"), delete_command=True)
            return
        await self.create(message, message.chat_id, name, description, sticker)

    async def create(self, message: Message, chat_id: int, name: str, description: str | None, sticker) -> None:
        f = self.bot.fmt
        if not sticker.set_name:
            await self.bot.asks.respond(message, f.t("achievements.not_from_a_pack"), delete_command=True)
            return
        try:
            achievement = await self.store.create(
                await self.owner(chat_id),
                name,
                description,
                (sticker.set_name, sticker.file_unique_id, sticker.file_id),
                str(message.from_user.id),
            )
        except ValueError:
            await self.bot.asks.respond(message, f.t("achievements.duplicate", name=name), delete_command=True)
            return
        await self.bot.asks.respond(message, f.t("achievements.created", name=achievement.name), delete_command=True)

    async def on_private_sticker(self, update: Update, _context) -> None:
        await self._start_private(update.effective_message)

    async def _start_private(self, message: Message) -> None:
        f = self.bot.fmt
        sticker = message.sticker
        pending = self._dm.get(message.from_user.id)
        if pending and pending.get("replace") and time.monotonic() - pending["since"] < PENDING_TTL:
            if not sticker.set_name:
                await message.reply_html(f.t("achievements.not_from_a_pack"))
                return
            self._dm.pop(message.from_user.id)
            await self.store.update(
                pending["replace"],
                sticker_set=sticker.set_name,
                sticker_unique_id=sticker.file_unique_id,
                sticker_file_id=sticker.file_id,
            )
            await message.reply_html(f.t("achievements.sticker_replaced"))
            return
        if not sticker.set_name:
            await message.reply_html(f.t("achievements.not_from_a_pack"))
            return
        chats = await self.bot.admin_chats(message.from_user.id)
        if not chats:
            await message.reply_html(f.t("achievements.no_chats"))
            return
        self._dm[message.from_user.id] = {"sticker": sticker, "chats": chats, "since": time.monotonic(), "name": None}
        await message.reply_html(
            f.t("achievements.ask_name"),
            reply_markup=ForceReply(input_field_placeholder=t("achievements.name_placeholder")),
        )

    async def on_input(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE:
            await self._private_name(message)
            return
        pending = self._prompts.pop((message.chat.id, message.reply_to_message.message_id))
        if pending["admin"] != message.from_user.id:
            self._prompts[(message.chat.id, message.reply_to_message.message_id)] = pending
            return
        sticker = message.sticker
        if sticker is None or not sticker.set_name:
            await self.bot.asks.respond(message, self.bot.fmt.t("achievements.not_from_a_pack"), ttl=60)
            self._prompts[(message.chat.id, message.reply_to_message.message_id)] = pending
            return
        await self.store.update(
            pending["achievement"],
            sticker_set=sticker.set_name,
            sticker_unique_id=sticker.file_unique_id,
            sticker_file_id=sticker.file_id,
        )
        with contextlib.suppress(TelegramError):
            await message.reply_to_message.delete()
        if (await self.bot.asks.rights(message.chat_id))[1]:
            with contextlib.suppress(TelegramError):
                await message.delete()
        await self.bot.asks.respond(message, self.bot.fmt.t("achievements.sticker_replaced"), ttl=60)

    async def _private_name(self, message: Message) -> None:
        f = self.bot.fmt
        pending = self._dm[message.from_user.id]
        if message.sticker is not None:  # a sticker: the replacement, or start over with it
            await self._start_private(message)
            return
        if pending.get("replace"):
            await message.reply_html(f.t("achievements.send_a_sticker"))
            return
        name, description = self._split(message.text or "")
        if not name:
            await message.reply_html(f.t("achievements.ask_name"), reply_markup=ForceReply())
            return
        pending["name"], pending["description"] = name, description
        if len(pending["chats"]) == 1:
            self._dm.pop(message.from_user.id)
            await self.create(message, pending["chats"][0]["id"], name, description, pending["sticker"])
            return
        rows = [[button(c["title"], message.from_user.id, f"ac:mk:{c['id']}")] for c in pending["chats"][:8]]
        await message.reply_html(f.t("achievements.which_chat"), reply_markup=InlineKeyboardMarkup(rows))

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

    async def sticker_ok(self, achievement) -> bool:
        """Is the sticker still in the admins' pack?"""
        if not achievement.sticker_set or not achievement.sticker_file_id:
            return False
        try:
            pack = await self.bot.app.bot.get_sticker_set(achievement.sticker_set)
        except TelegramError:
            return False
        return any(s.file_unique_id == achievement.sticker_unique_id for s in pack.stickers)

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
        bot = self.bot.app.bot
        reply = ReplyParameters(target.message_id, allow_sending_without_reply=True)
        sticker = await self.sticker_ok(achievement)
        if sticker:
            with contextlib.suppress(TelegramError):
                await bot.send_sticker(
                    chat_id, achievement.sticker_file_id, message_thread_id=thread, reply_parameters=reply
                )
                reply = None
        await bot.send_message(
            chat_id,
            f.t("achievements.awarded", who=f.mention(str(user.id), display_name(user)), achievement=achievement.name),
            message_thread_id=thread,
            reply_parameters=reply,
        )
        if not sticker:
            await self.ask_for_sticker(chat_id, thread, admin, achievement)

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
                    await self.owner(chat_id), pending["name"], pending.get("description"),
                    (pending["sticker"].set_name, pending["sticker"].file_unique_id, pending["sticker"].file_id), str(query.from_user.id),
                )  # fmt: skip
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
        message = update.effective_message
        f = self.bot.fmt
        if message.chat.type == ChatType.PRIVATE:
            awards = await self.store.of_user(PLATFORM, str(message.from_user.id))
            names = ", ".join(f"«{a.achievement.name}»" for a in awards)
            await message.reply_html(
                f.t("achievements.yours", list=names) if awards else f.t("achievements.yours_none")
            )
            return
        items = await self.store.of_owner(await self.owner(message.chat_id))
        if not items:
            await message.reply_html(f.t("achievements.none_yet"))
            return
        lines = [f.bold(t("achievements.title"))]
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
        await message.reply_html("\n".join(lines))

    async def profile(self, user, person_id: int | None, private: bool) -> ProfilePart | None:
        if self.store is None:
            return None
        awards = await self.store.of_user(PLATFORM, str(user.id))
        if not awards:
            return None
        names = ", ".join(f"«{a.achievement.name}»" for a in awards)
        return ProfilePart([self.bot.fmt.t("achievements.profile", list=names)])

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
