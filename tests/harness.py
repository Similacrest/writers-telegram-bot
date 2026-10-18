"""Drive a whole Telegram bot through user actions, against :mod:`fake_telegram` and a fake clock.

h = await Harness.create(tmp_path, clock, modules={"sprints": {"reports": True, "works": True}})
await h.say(ALICE, "/sprint 20 0")
await h.press(BOB, h.last_public(), "Долучитися")
await h.advance(20 * 60)
await h.reply(ALICE, h.board(), "500")
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fake_telegram import BOT_ID, Chat, FakeTelegram, FakeUser, Msg, process

from writers_bot.botconfig import BotConfig, ProcessConfig
from writers_bot.config import CommonSettings, TelegramSettings
from writers_bot.modules import MODULES
from writers_bot.services import Services
from writers_bot.telegram_bot.app import TelegramBot

GROUP = -1001
ALICE = FakeUser(1, "Alice", "alice")
BOB = FakeUser(2, "Bob", "bob")
CAROL = FakeUser(3, "Carol")
DAN = FakeUser(4, "Dan")
ADMIN = FakeUser(42, "Admin")


def settings(db: Path, **overrides) -> TelegramSettings:
    common = CommonSettings(
        timezone=ZoneInfo("Europe/Kyiv"),
        db_path=db,
        report_window=timedelta(minutes=30),
        google=None,
        preview_features=True,
    )
    base = TelegramSettings(
        token="123:TEST",
        super_admins=frozenset({ADMIN.id}),
        allowed_chats=None,
        polling=True,
        webhook_host=None,
        webhook_listen="0.0.0.0",
        webhook_port=80,
        webhook_path="",
        webhook_secret=None,
        extras=False,
        allow_private=True,
        slogan_replies=False,
        ephemeral_replies=True,
        ephemeral_commands=False,
        common=common,
    )
    return replace(base, **overrides)


class FakeLinks:
    def __init__(self, counts: dict[str, int]):
        self.counts = counts

    async def count(self, url: str) -> int:
        from writers_bot.core.links import LinkError

        if url not in self.counts:
            raise LinkError("unsupported")
        return self.counts[url]

    async def fetch(self, url: str):
        from writers_bot.core.sources import LinkedText

        return LinkedText("telegraph", "", "слово " * await self.count(url), url)


class Harness:
    def __init__(self, tbot: TelegramBot, fake: FakeTelegram, clock, services: Services):
        self.tbot = tbot
        self.fake = fake
        self.clock = clock
        self.services = services
        self.app = tbot.app
        self.log: list[str] = []

    @classmethod
    async def create(
        cls,
        tmp_path: Path,
        clock,
        modules: dict[str, dict] | None = None,
        *,
        bot_admin: bool = True,
        web=None,
        **settings_overrides,
    ) -> "Harness":
        modules = modules if modules is not None else {"sprints": {"reports": True, "works": True, "percent": True}}
        modules = {k: MODULES[k].defaults | v for k, v in modules.items()}
        tg = settings(tmp_path / "bot.sqlite3", **settings_overrides)
        bot = BotConfig("main", modules, telegram=tg, admin=True)
        config = ProcessConfig({"main": bot}, tg.common, web)
        services = Services.build(config)
        await services.start()
        fake = FakeTelegram(clock)
        fake.chats[GROUP] = Chat(GROUP, bot_status="administrator" if bot_admin else "member")
        tbot = TelegramBot(bot, services.for_bot(bot), now=clock.now, sleep=clock.sleep)
        if web is not None:
            from writers_bot.web.server import WebApp

            services.web = WebApp(web, services)
            tbot.services.web = services.web
        app = tbot.build(request=fake)
        fake.app = app
        await app.initialize()
        await tbot.post_init(app)
        h = cls(tbot, fake, clock, services)
        if web is not None:
            services.web.attach([tbot])
        return h

    async def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        await self.tbot.shutdown()
        await self.app.shutdown()
        await self.services.stop()

    # --- user actions ----------------------------------------------------------------------------

    async def say(
        self,
        user: FakeUser,
        text: str,
        chat: int = GROUP,
        *,
        reply_to: Msg | None = None,
        ephemeral: bool = False,
        thread_id=None,
        **extra,
    ) -> Msg:
        c = self.fake.chat(chat if chat != "dm" else user.id)
        m = self.fake._user_message(
            c, user, text, reply_to.message_id if reply_to else None, thread_id, ephemeral=ephemeral, **extra
        )
        where = "DM" if c.id > 0 else str(c.id)
        line = f"{user.first_name} [{where}]: {text}" + (f"  (↩ {reply_to.text[:30]!r})" if reply_to else "")
        self.log.append(line)
        self.fake.note(line)
        await process(self.app, self.fake.message_update(m))
        await self.settle()
        return m

    async def dm(self, user: FakeUser, text: str, *, reply_to: Msg | None = None) -> Msg:
        return await self.say(user, text, user.id, reply_to=reply_to)

    async def reply(self, user: FakeUser, to: Msg, text: str) -> Msg:
        return await self.say(user, text, to.chat_id, reply_to=to)

    async def press(self, user: FakeUser, msg: Msg, button_text: str) -> dict:
        b = msg.button(button_text)
        self.log.append(f"{user.first_name} presses [{b['text']}]")
        self.fake.note(f"{user.first_name} presses [{b['text']}]")
        if "callback_data" not in b:
            return b
        data = self.fake.callback_update(msg.chat_id, user, msg, b["callback_data"])
        await process(self.app, data)
        await self.settle()
        return self.fake.answers.get(data["callback_query"]["id"], {})

    async def settle(self) -> None:
        await self.clock.settle()
        for _ in range(3):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        self.fake.note(f"… {seconds / 60:g} min later")
        await self.clock.advance(seconds)
        await self.settle()

    def transcript(self) -> str:
        return "\n".join(self.fake.events)

    def mark(self) -> int:
        """Position in the message log, to count what a step sent."""
        return len(self.fake.messages)

    def public_since(self, mark: int, chat: int = GROUP) -> list[Msg]:
        """Public messages the bot sent since ``mark`` that are still in the chat."""
        return [
            m
            for m in self.fake.messages[mark:]
            if m.chat_id == chat and m.receiver is None and not m.deleted and m.from_bot
        ]

    def use_links(self, counts: dict[str, int]) -> "FakeLinks":
        links = FakeLinks(counts)
        self.tbot.links = links
        sprints = self.tbot.module("sprints")
        if sprints is not None:
            sprints.rep.links = links
        return links

    # --- reading ---------------------------------------------------------------------------------

    def public(self, chat: int = GROUP) -> list[Msg]:
        return self.fake.public(chat)

    def bot_public(self, chat: int = GROUP) -> list[Msg]:
        return [m for m in self.public(chat) if m.from_bot]

    def last_public(self, chat: int = GROUP) -> Msg:
        return self.bot_public(chat)[-1]

    def mine(self, user: FakeUser, chat: int = GROUP) -> list[Msg]:
        """Ephemeral messages ``user`` sees in ``chat``."""
        return [m for m in self.fake.visible(chat, user.id) if m.receiver == user.id]

    def last_private(self, user: FakeUser, chat: int = GROUP) -> Msg:
        """The newest message only ``user`` should see: ephemeral, or an owner-only fallback."""
        own = self.mine(user, chat)
        if own:
            return own[-1]
        fallback = [m for m in self.bot_public(chat) if f"u:{user.id}:" in str(m.markup)]
        return fallback[-1]

    def dms(self, user: FakeUser) -> list[Msg]:
        return [m for m in self.fake.visible(user.id) if m.from_bot]

    def sprint(self):
        sprints = self.tbot.module("sprints").engine.open_sprints()
        return sprints[0] if sprints else None

    def sprint_message(self, chat: int = GROUP) -> Msg:
        s = self.sprint()
        return self.fake.find(chat, s.refs["msg"])

    def board(self, chat: int = GROUP) -> Msg:
        s = self.sprint()
        return self.fake.find(chat, s.refs["finish"])

    def calls(self, name: str) -> list[dict]:
        return [p for n, p in self.fake.calls if n == name]

    def snapshot(self, chat: int = GROUP, user: FakeUser | None = None) -> str:
        lines = []
        for m in self.fake.visible(chat, user.id if user else None):
            who = "bot" if m.from_bot else m.sender.get("first_name")
            tag = " (only you)" if m.receiver else ""
            lines.append(f"[{who}{tag}] {m.text}" + (f"  {m.buttons()}" if m.buttons() else ""))
        return "\n".join(lines)


__all__ = ["ADMIN", "ALICE", "BOB", "BOT_ID", "CAROL", "DAN", "GROUP", "Harness"]
