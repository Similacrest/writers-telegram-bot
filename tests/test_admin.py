import io
import pickle
import sys
import warnings
from datetime import timedelta
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, ChatMigrated, Forbidden, RetryAfter, TimedOut

from writers_bot.core.models import ChatRecord, ChatStatus
from writers_bot.telegram_bot.admin import AdminTools, BroadcastResult, PendingBroadcast, member_status
from writers_bot.telegram_bot.legacy import guess_chat_type, read_legacy_ids

ADMIN = 42


def rec(chat_id, type_="supergroup", status=ChatStatus.MEMBER, title=None):
    return ChatRecord("telegram", str(chat_id), type_, title, None, status)


# --- legacy import -----------------------------------------------------------------------------------


class Evil:
    def __reduce__(self):
        return (sys.exit, (99,))  # would run on a plain pickle.load


class _BotRefPickler(pickle.Pickler):
    def persistent_id(self, obj):
        return "bot" if obj == "BOT" else None


def test_read_legacy_ids_without_running_anything(tmp_path):
    data = {
        "chat_data": {-1001234567890: {}, -4567: {}, 555: {"x": "BOT"}},
        "user_data": {555: {}, 777: {}},
        "bot_data": {"evil": Evil()},
        "conversations": {},
        "callback_data": None,
    }
    buf = io.BytesIO()
    _BotRefPickler(buf).dump(data)
    path = tmp_path / "db.pickle"
    path.write_bytes(buf.getvalue())
    chats, users = read_legacy_ids(path)  # must not call sys.exit
    assert chats == {-1001234567890, -4567, 555}
    assert users == {555, 777}


def test_guess_chat_type():
    assert guess_chat_type(555) == "private"
    assert guess_chat_type(-4567) == "group"
    assert guess_chat_type(-1001234567890) == "supergroup"


# --- storage -----------------------------------------------------------------------------------------


async def test_chat_registry(storage):
    await storage.upsert_chat(rec(-1001, title="Writers"))
    await storage.upsert_chat(rec(5, "private", title="Ann"))
    await storage.upsert_chat(rec(-1002, status=ChatStatus.KICKED))
    # importing old ids never overwrites what the bot has seen
    await storage.upsert_chat(rec(-1001, "group", ChatStatus.UNKNOWN), only_new=True)
    await storage.upsert_chat(rec(-1003, "group", ChatStatus.UNKNOWN), only_new=True)

    all_chats = {c.chat_id: c for c in await storage.list_chats("telegram")}
    assert all_chats["-1001"].type == "supergroup" and all_chats["-1001"].status == ChatStatus.MEMBER
    assert all_chats["-1003"].status == ChatStatus.UNKNOWN

    groups = await storage.list_chats("telegram", private=False, reachable_only=True)
    assert {c.chat_id for c in groups} == {"-1001", "-1003"}
    private = await storage.list_chats("telegram", private=True, reachable_only=True)
    assert [c.chat_id for c in private] == ["5"]

    await storage.set_chat_status("telegram", "5", ChatStatus.KICKED)
    assert await storage.list_chats("telegram", private=True, reachable_only=True) == []


# --- tracking ----------------------------------------------------------------------------------------


def fake_update(chat_id, type_="group", title="G", migrate_to=None, my_chat_member=None):
    chat = SimpleNamespace(id=chat_id, type=type_, title=title, first_name=None, last_name=None, username=None)
    message = SimpleNamespace(migrate_to_chat_id=migrate_to)
    return SimpleNamespace(effective_chat=chat, effective_message=message, my_chat_member=my_chat_member)


async def test_track_records_and_throttles(storage):
    tools = AdminTools(storage, frozenset({ADMIN}))
    await tools.track(fake_update(-1, title="First"), None)
    await tools.track(fake_update(-1, title="Renamed"), None)  # throttled
    [chat] = await storage.list_chats("telegram")
    assert chat.title == "First"


async def test_track_group_migration(storage):
    tools = AdminTools(storage, frozenset({ADMIN}))
    await tools.track(fake_update(-1), None)
    await tools.track(fake_update(-1, migrate_to=-1009), None)
    chats = {c.chat_id: c.status for c in await storage.list_chats("telegram")}
    assert chats == {"-1": ChatStatus.MIGRATED, "-1009": ChatStatus.MEMBER}


@pytest.mark.parametrize(
    ("status", "is_member", "expected"),
    [
        ("member", None, ChatStatus.MEMBER),
        ("administrator", None, ChatStatus.MEMBER),
        ("left", None, ChatStatus.LEFT),
        ("kicked", None, ChatStatus.KICKED),
        ("restricted", False, ChatStatus.LEFT),
        ("restricted", True, ChatStatus.MEMBER),
    ],
)
def test_member_status(status, is_member, expected):
    change = SimpleNamespace(new_chat_member=SimpleNamespace(status=status, is_member=is_member))
    assert member_status(change) == expected


# --- broadcasting ------------------------------------------------------------------------------------


class FakeBot:
    def __init__(self, behaviour):
        self.behaviour = behaviour  # chat_id -> list of exceptions to raise before succeeding
        self.sent = []

    async def copy_message(self, chat_id, from_chat_id, message_id):
        queue = self.behaviour.get(chat_id, [])
        if queue:
            raise queue.pop(0)
        self.sent.append(chat_id)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(("text", chat_id))


class FakeStatusMessage:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)


async def test_broadcast_handles_gone_chats_and_retries(storage, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("writers_bot.telegram_bot.admin.asyncio.sleep", no_sleep)
    for chat in [
        rec(-1),
        rec(-2),
        rec(-3),
        rec(-4),
        rec(-5),
        rec(7, "private"),
        rec(ADMIN, "private"),
        rec(-6, status=ChatStatus.LEFT),
    ]:
        await storage.upsert_chat(chat)
    await storage.upsert_chat(rec(-8, "group", ChatStatus.UNKNOWN), only_new=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # PTB warns about the int -> timedelta change of retry_after
        retry = RetryAfter(timedelta(seconds=1))
    bot = FakeBot(
        {
            -2: [Forbidden("bot was kicked")],
            -3: [BadRequest("Chat not found")],
            -4: [retry],
            -5: [ChatMigrated(-1005)],
            7: [TimedOut()],
        }
    )
    tools = AdminTools(storage, frozenset({ADMIN}))
    status = FakeStatusMessage()
    await tools._run_broadcast(bot, PendingBroadcast(ADMIN, "all", ADMIN, 10), status)

    assert sorted(bot.sent) == sorted([-1, -4, -1005, -8])  # never the source chat, never the left chat
    chats = {c.chat_id: c.status for c in await storage.list_chats("telegram")}
    assert chats["-2"] == ChatStatus.KICKED
    assert chats["-3"] == ChatStatus.LEFT
    assert chats["-5"] == ChatStatus.MIGRATED and chats["-1005"] == ChatStatus.MEMBER
    assert chats["-8"] == ChatStatus.MEMBER  # confirmed by a successful delivery
    assert "Надіслано: 4 з 7" in status.texts[-1]
    assert "Помилки: 1" in status.texts[-1]


async def test_broadcast_target_filters(storage):
    for chat in [rec(-1), rec(7, "private"), rec(8, "private")]:
        await storage.upsert_chat(chat)
    tools = AdminTools(storage, frozenset({ADMIN}))
    assert {c.chat_id for c in await tools._recipients("groups")} == {"-1"}
    assert {c.chat_id for c in await tools._recipients("private", exclude=8)} == {"7"}
    assert len(await tools._recipients("all")) == 3


async def test_broadcast_buttons_only_for_the_admin_who_asked(storage):
    tools = AdminTools(storage, frozenset({ADMIN, 43}))
    tools._pending["abc"] = PendingBroadcast(ADMIN, "all", ADMIN, 1)
    answers = []

    async def answer(text=None, **kwargs):
        answers.append(text)

    async def edit_reply_markup(*args, **kwargs):
        return None

    query = SimpleNamespace(
        data="bc:go:abc", from_user=SimpleNamespace(id=43), answer=answer, edit_message_reply_markup=edit_reply_markup
    )
    await tools.on_broadcast_button(SimpleNamespace(callback_query=query), None)
    assert answers == ["Ця розсилка вже неактуальна."]
    assert "abc" in tools._pending


def test_result_defaults():
    assert BroadcastResult().sent == 0


async def test_excluded_chats_get_no_broadcast(storage):
    for chat in [rec(-1), rec(-2), rec(7, "private")]:
        await storage.upsert_chat(chat)
    assert await storage.set_broadcast_excluded("telegram", ["-2", "-999"], True) == 1
    tools = AdminTools(storage, frozenset({ADMIN}))
    assert {c.chat_id for c in await tools._recipients("all")} == {"-1", "7"}
    assert await tools._excluded_count("groups") == 1
    # a later sighting of the chat keeps the exclusion
    await storage.upsert_chat(rec(-2, title="Renamed"))
    assert await tools._excluded_count("all") == 1
    await storage.set_broadcast_excluded("telegram", ["-2"], False)
    assert len(await tools._recipients("all")) == 3


async def test_migrates_a_v3_0_database(tmp_path):
    import sqlite3

    from writers_bot.core.storage import SCHEMA, SCHEMA_VERSION, Storage

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA + "PRAGMA user_version = 2;")
    old.execute(
        "INSERT INTO chats VALUES ('telegram', '-1', 'group', 'G', NULL, 'member', '2026-01-01', '2026-01-01')"
    )
    old.commit()
    old.close()
    s = Storage(path)
    await s.open()
    try:
        [chat] = await s.list_chats("telegram")
        assert chat.title == "G" and not chat.broadcast_excluded
        async with s.db.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == SCHEMA_VERSION
    finally:
        await s.close()
    await s.open()  # opening again is a no-op
    await s.close()


def test_private_chats_and_allowlist(monkeypatch):
    from writers_bot.config import TelegramSettings
    from writers_bot.telegram_bot.app import WritersTelegramBot

    monkeypatch.setenv("TELEGRAM_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    monkeypatch.setenv("ALLOWED_CHATS", "-100")
    monkeypatch.setenv("EXTRAS", "1")
    monkeypatch.setenv("BOT_SUPERADMINS", str(ADMIN))
    group = SimpleNamespace(id=-200, type="supergroup")
    private = SimpleNamespace(id=5, type="private")
    user = SimpleNamespace(id=5)

    bot = WritersTelegramBot(TelegramSettings.from_env())
    assert bot.is_allowed(SimpleNamespace(id=-100, type="supergroup"), user)
    assert not bot.is_allowed(group, user)
    assert bot.is_allowed(private, user)  # like the v2 "extended" bot

    monkeypatch.delenv("EXTRAS")  # plain behaviour: only the allowlist and super-admins
    bot = WritersTelegramBot(TelegramSettings.from_env())
    assert not bot.is_allowed(private, user)
    monkeypatch.setenv("EXTRAS", "1")

    monkeypatch.setenv("ALLOW_PRIVATE_CHATS", "0")
    bot = WritersTelegramBot(TelegramSettings.from_env())
    assert not bot.is_allowed(private, user)
    assert bot.is_allowed(SimpleNamespace(id=ADMIN, type="private"), SimpleNamespace(id=ADMIN))


def test_catch_all_command_handler_is_registered_last(monkeypatch):
    from telegram.ext import CommandHandler, MessageHandler

    from writers_bot.config import TelegramSettings
    from writers_bot.telegram_bot.app import build_application

    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    monkeypatch.setenv("EXTRAS", "1")
    app = build_application(TelegramSettings.from_env())
    group = app.handlers[0]
    last_command = max(i for i, h in enumerate(group) if isinstance(h, CommandHandler))
    assert isinstance(group[-1], MessageHandler) and group[-1].callback.__name__ == "other_command"
    assert last_command < len(group) - 1


def test_backup_script(tmp_path, monkeypatch, capsys):
    import runpy
    import sqlite3

    source = tmp_path / "bot.sqlite3"
    db = sqlite3.connect(source)
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("CREATE TABLE chats (title TEXT)")
    db.execute("INSERT INTO chats VALUES ('Backed up')")
    db.commit()  # left open, like the running bot
    target = tmp_path / "copy.sqlite3"
    monkeypatch.setattr(sys, "argv", ["backup_db.py", str(target), "--db", str(source)])
    runpy.run_path("scripts/backup_db.py", run_name="__main__")
    db.close()
    assert "integrity ok" in capsys.readouterr().out
    assert sqlite3.connect(target).execute("SELECT title FROM chats").fetchone() == ("Backed up",)


def test_extras_off_registers_no_extra_handlers(monkeypatch):
    from telegram.ext import CommandHandler, MessageHandler

    from writers_bot.config import TelegramSettings
    from writers_bot.telegram_bot.app import build_application

    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")

    def callbacks(app):
        return {h.callback.__name__ for group in app.handlers.values() for h in group if hasattr(h, "callback")}

    monkeypatch.delenv("EXTRAS", raising=False)
    plain = callbacks(build_application(TelegramSettings.from_env()))
    assert not {"smash", "imafan", "welcome", "on_text", "other_command"} & plain
    assert "sprint" in plain

    monkeypatch.setenv("EXTRAS", "1")
    full = callbacks(build_application(TelegramSettings.from_env()))
    assert {"smash", "imafan", "welcome", "on_text", "other_command"} <= full
    assert CommandHandler and MessageHandler


def test_preview_features_off_by_default(monkeypatch):
    from writers_bot.config import TelegramSettings
    from writers_bot.telegram_bot.app import V2_COMMANDS, build_application, command_menus

    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    v2 = {"start", "help", "prompt", "image", "wordcount", "stats", "reload", "debuginfo", "sprint"}
    v2 |= {"on_join", "on_leave", "on_repeat"}
    preview = {"sprint_help", "words", "join", "sprint_mode", "mystats", "top", "on_report_button", "on_reply"}
    preview |= {"tz", "goal", "export", "forgetme", "on_forget_button"}
    preview |= {"chats", "broadcast", "exclude", "include", "on_broadcast_button"}

    def callbacks(app):
        return {h.callback.__name__ for group in app.handlers.values() for h in group if hasattr(h, "callback")}

    def names(commands):
        return {c.command for c in commands}

    monkeypatch.delenv("PREVIEW_FEATURES", raising=False)
    plain = build_application(TelegramSettings.from_env())
    assert v2 <= callbacks(plain) and not callbacks(plain) & preview
    assert all(names(menu) <= V2_COMMANDS for menu in command_menus(False))

    monkeypatch.setenv("PREVIEW_FEATURES", "1")
    assert v2 | preview <= callbacks(build_application(TelegramSettings.from_env()))
    group, private, admin = command_menus(True)
    assert "forgetme" in names(private) and "forgetme" not in names(group)
    assert {"goal", "join", "chats", "reload"} <= names(admin)


async def test_image_prompt_shows_a_link_preview(monkeypatch):
    from telegram import LinkPreviewOptions
    from telegram.ext import Defaults, ExtBot

    from writers_bot.config import TelegramSettings
    from writers_bot.telegram_bot.app import WritersTelegramBot

    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    bot = WritersTelegramBot(TelegramSettings.from_env())
    link = "https://drive.google.com/uc?id=abc&export=download"
    bot.prompts.random_image = lambda category: ("Персонаж", 7, {"webContentLink": link})
    sent = {}

    async def reply_html(text, **kwargs):
        sent.update(kwargs, text=text)

    message = SimpleNamespace(text="/image", caption=None, reply_html=reply_html)
    update = SimpleNamespace(effective_chat=None, effective_user=None, effective_message=message)
    await bot.image(update, SimpleNamespace(args=[]))

    # The application's Defaults disable previews; what the image prompt passes must override that.
    ext = ExtBot("123:abc", defaults=Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True)))
    merged = ext._merge_lpo_defaults(sent["link_preview_options"])
    assert merged.is_disabled is False and merged.url == link
    assert 'href="https://drive.google.com/uc?id=abc&amp;export=download"' in sent["text"]


async def test_link_previews_follow_v2(monkeypatch):
    from writers_bot.config import TelegramSettings
    from writers_bot.telegram_bot.app import WritersTelegramBot, build_application

    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    assert build_application(TelegramSettings.from_env()).bot.defaults.link_preview_options is None

    bot = WritersTelegramBot(TelegramSettings.from_env())
    bot.prompts.random_text = lambda lang, category=None: {"Сюжет": "див. https://example.com"}
    sent = []

    async def reply_html(text, **kwargs):
        sent.append(kwargs)

    message = SimpleNamespace(text="/help", caption=None, reply_html=reply_html)
    update = SimpleNamespace(effective_chat=None, effective_user=None, effective_message=message)
    await bot.help(update, SimpleNamespace(args=[]))
    assert sent[-1]["link_preview_options"].is_disabled  # v2 turned previews off for /help and /start
    await bot.prompt(update, SimpleNamespace(args=[]))
    assert "link_preview_options" not in sent[-1]  # prompts from the sheet get previews, as in v2
