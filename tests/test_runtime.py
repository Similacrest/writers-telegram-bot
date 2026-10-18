"""The runtime: config from the environment or a file, modules, menus, the allowlist, link previews."""

from types import SimpleNamespace

import pytest
from harness import ADMIN, ALICE, BOB, GROUP, Harness

from writers_bot.botconfig import from_env, from_file
from writers_bot.config import ConfigError
from writers_bot.services import Services
from writers_bot.telegram_bot.app import TelegramBot


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.sqlite3"))
    for name in (
        "EXTRAS",
        "PREVIEW_FEATURES",
        "ALLOWED_CHATS",
        "BOT_SUPERADMINS",
        "ALLOW_PRIVATE_CHATS",
        "BOT_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def tbot_from_env() -> TelegramBot:
    config = from_env("telegram")
    services = Services.build(config)
    return TelegramBot(config.bots["main"], services.for_bot(config.bots["main"]))


def menu_names(tbot):
    return [{c.command for c in menu} for menu in tbot.menus()]


def test_v2_by_default(env):
    tbot = tbot_from_env()
    assert [m.name for m in tbot.modules] == ["prompts", "wordcount", "sprints"]
    group, private, admin = menu_names(tbot)
    assert group == private == {"help", "prompt", "image", "count", "sprint"}
    assert admin == group  # no admin tools without the preview
    assert not tbot.profile_enabled and tbot.data is None


def test_preview_features(env):
    env.setenv("PREVIEW_FEATURES", "1")
    env.setenv("BOT_SUPERADMINS", "42")
    tbot = tbot_from_env()
    group, private, admin = menu_names(tbot)
    assert {"progress", "top", "profile"} <= group
    assert "export" in private and "export" not in group and "forgetme" in private
    assert {"chats", "broadcast"} <= admin and "chats" not in private


def test_extras_modules(env):
    env.setenv("EXTRAS", "1")
    tbot = tbot_from_env()
    assert {m.name for m in tbot.modules} == {"prompts", "wordcount", "sprints", "dice", "smash", "greeter", "replies"}


def test_allowlist(env):
    env.setenv("ALLOWED_CHATS", "-100")
    env.setenv("EXTRAS", "1")
    env.setenv("BOT_SUPERADMINS", "42")
    group = SimpleNamespace(id=-200, type="supergroup")
    private = SimpleNamespace(id=5, type="private")
    user = SimpleNamespace(id=5)
    tbot = tbot_from_env()
    assert tbot.is_allowed(SimpleNamespace(id=-100, type="supergroup"), user)
    assert not tbot.is_allowed(group, user)
    assert tbot.is_allowed(private, user)  # like the v2 "extended" bot
    env.delenv("EXTRAS")
    tbot = tbot_from_env()
    assert not tbot.is_allowed(private, user)
    assert tbot.is_allowed(SimpleNamespace(id=42, type="private"), SimpleNamespace(id=42))


def test_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MAIN", "1:main")
    monkeypatch.setenv("TG_GREETER", "2:greeter")
    path = tmp_path / "bots.toml"
    path.write_text(
        """
[common]
db = "%s"

[bots.main]
modules = ["prompts", "wordcount", "sprints", "dice"]
sprints = { reports = true, works = true }
telegram = { token = "env:TG_MAIN", super_admins = [42] }

[bots.greeter]
modules = ["greeter"]
greeter = { texts = { "-100" = "Вітаємо, {name}!" } }
telegram = { token = "env:TG_GREETER" }
"""
        % (tmp_path / "x.sqlite3")
    )
    config = from_file(path)
    assert config.storage_bot.name == "main"
    assert not config.bots["greeter"].storage
    assert config.bots["main"].options("sprints")["works"] is True
    assert config.bots["main"].telegram.token == "1:main"
    assert config.only("greeter").bots.keys() == {"greeter"}

    path.write_text(path.read_text().replace('modules = ["greeter"]', 'modules = ["greeter", "sprints"]'))
    with pytest.raises(ConfigError, match="only one bot may have storage"):
        from_file(path)
    path.write_text(path.read_text().replace('"greeter", "sprints"', '"greeter", "nope"'))
    with pytest.raises(ConfigError, match="unknown modules"):
        from_file(path)
    monkeypatch.delenv("TG_GREETER")
    path.write_text(path.read_text().replace('"greeter", "nope"', '"greeter"'))
    with pytest.raises(ConfigError, match="TG_GREETER"):
        from_file(path)


class FakeSheet:
    def __init__(self, **data):
        self.data = data

    def config_text(self, key, subkey=""):
        return self.data.get(key)

    def config_texts(self, key):
        return self.data.get(key + "s", {})

    def random_image(self, category):
        return "Персонаж", 7, {"webContentLink": "https://drive.google.com/uc?id=abc&export=download"}

    def random_text(self, lang, category=None):
        return {"Сюжет": "див. https://example.com"}

    def text_categories(self, lang):
        return ["Сюжет"]

    def find_category(self, lang, q):
        return None


async def test_image_and_prompt_previews(tmp_path, clock):
    h = await Harness.create(tmp_path, clock, {"prompts": {}})
    h.tbot.services.sheet = FakeSheet()
    try:
        await h.say(ALICE, "/image")
        params = h.calls("sendMessage")[-1]
        assert params["link_preview_options"]["is_disabled"] is False
        assert 'href="https://drive.google.com/uc?id=abc&amp;export=download"' in params["text"]
        await h.say(ALICE, "/prompt")
        assert "link_preview_options" not in h.calls("sendMessage")[-1]  # sheet prompts get previews, as in v2
        await h.say(ALICE, "/help")
        assert h.calls("sendMessage")[-1]["link_preview_options"]["is_disabled"] is True
        await h.say(ALICE, "/промпт")  # the Ukrainian alias, as text
        assert "Сюжет" in h.calls("sendMessage")[-1]["text"]
    finally:
        await h.close()


async def test_help_lists_only_the_bots_modules(tmp_path, clock):
    h = await Harness.create(tmp_path, clock, {"wordcount": {}, "dice": {}})
    try:
        await h.say(ALICE, "/help")
        text = h.last_public().text
        assert "/count" in text and "/d20" in text and "/sprint" not in text and "/prompt" not in text
        await h.say(ALICE, "/wc раз два три")
        assert "3 слова" in h.last_public().text
        await h.say(ALICE, "/підрахуй раз два")
        assert "2 слова" in h.last_public().text
    finally:
        await h.close()


async def test_private_answers_fall_back_when_the_bot_is_not_admin(tmp_path, clock):
    h = await Harness.create(tmp_path, clock, bot_admin=False)
    try:
        await h.say(ALICE, "/profile")
        fallback = h.last_public()
        assert "Профіль" in fallback.text and f"u:{ALICE.id}:" in str(fallback.markup)
        answer = await h.press(BOB, fallback, "Налаштування")
        assert "не для вас" in answer.get("text", "")
        await h.advance(6 * 60)
        assert fallback.deleted  # removed after a few minutes
    finally:
        await h.close()


async def test_private_answers_are_ephemeral_for_admin_bots(tmp_path, clock):
    h = await Harness.create(tmp_path, clock)
    try:
        cmd = await h.say(ALICE, "/profile")
        assert cmd.deleted  # the command goes too
        card = h.mine(ALICE)[-1]
        assert "Профіль" in card.text
        assert not [m for m in h.bot_public() if "Профіль" in m.text]
        await h.press(ALICE, card, "Налаштування")
        assert "Налаштування" in card.text
    finally:
        await h.close()


async def test_super_admin_tools(tmp_path, clock):
    h = await Harness.create(tmp_path, clock)
    try:
        await h.say(ALICE, "hello")  # the registry records the group
        await h.dm(ADMIN, "/chats")
        assert "Writers" in h.dms(ADMIN)[-1].text or "chat" in h.dms(ADMIN)[-1].text
        await h.say(BOB, "/chats")  # silent for everyone else
        assert not [m for m in h.bot_public() if "Чати з ботом" in m.text]
    finally:
        await h.close()


async def test_v2_extras_keep_reading_the_sheet(env, clock, tmp_path):
    """EXTRAS without the preview: the welcome text comes from the sheet every time."""
    env.setenv("EXTRAS", "1")
    config = from_env("telegram")
    bot = config.bots["main"]
    assert not bot.chat_settings and not bot.admin
    h = await Harness.create(tmp_path, clock, {k: v for k, v in bot.modules.items()})
    h.tbot.services.sheet = FakeSheet(welcome_messages={str(GROUP): "Привіт, {name}!"}, imafan_messages={"": "Фан!"})
    try:
        greeter = h.tbot.module("greeter")
        greeter.bot.config = bot  # the environment's bot: no /settings
        assert not greeter.managed
        await h.say(ALICE, "", new_chat_members=[BOB.to_dict()])
        assert "Привіт" in h.last_public().text
        h.tbot.services.sheet.data["welcome_messages"] = {str(GROUP): "Вітаю, {name}!"}  # edited in the sheet
        await h.say(ALICE, "", new_chat_members=[BOB.to_dict()])
        assert "Вітаю" in h.last_public().text
    finally:
        await h.close()
