"""What the October 2026 consistency review found, each as the workflow that showed it."""

import string

import pytest
from aiohttp.test_utils import TestClient, TestServer
from harness import ALICE, DAN, GROUP, Harness
from test_web import WEB, init_data

from writers_bot.botconfig import from_env
from writers_bot.config import ConfigError
from writers_bot.core.i18n import t, texts
from writers_bot.core.settings import SETTINGS

FULL = {"sprints": {"reports": True, "works": True, "percent": True}}


@pytest.fixture
async def make(tmp_path, clock):
    made = []

    async def build(modules=FULL, **kwargs):
        h = await Harness.create(tmp_path / str(len(made)), clock, modules, **kwargs)
        h.fake.chats[GROUP].roles[DAN.id] = "administrator"
        made.append(h)
        return h

    yield build
    for h in made:
        await h.close()


async def login(client, user):
    r = await client.post("/api/session", json={"init_data": init_data(user.to_dict())})
    return {"Authorization": "Bearer " + (await r.json())["token"]}


# --- texts -------------------------------------------------------------------------------------------------


def test_every_setting_text_renders_without_values():
    """The web app's Chat tab renders these with no values: a stray {name} broke it (KeyError) with the greeter."""
    for s in SETTINGS:
        t(s.locale)
        assert "{" not in t(s.locale + "_help") or "{name}" in t(s.locale + "_help")
        for choice in s.choices:
            t(f"{s.locale}_{choice}")


def test_texts_parse_as_format_strings():
    for value in texts().values():
        for text in value if isinstance(value, list) else [value]:
            list(string.Formatter().parse(str(text)))  # raises on a lone brace


# --- the web app ----------------------------------------------------------------------------------------------


async def test_the_chat_tab_works_with_the_greeter(make):
    h = await make({"greeter": {}, **FULL}, web=WEB)
    client = TestClient(TestServer(h.services.web.app))
    await client.start_server()
    try:
        await h.say(ALICE, "hi")
        r = await client.get(f"/api/chat/{GROUP}/settings", headers=await login(client, DAN))
        assert r.status == 200, await r.text()
        greeter = next(s for s in (await r.json())["settings"] if s["key"] == "greeter.text")
        assert "{name}" in greeter["help"]
    finally:
        await client.close()


async def test_no_chat_settings_api_without_chat_settings(make):
    h = await make(web=WEB, admin=False)
    client = TestClient(TestServer(h.services.web.app))
    await client.start_server()
    try:
        r = await client.patch(
            f"/api/chat/{GROUP}/settings", headers=await login(client, DAN), json={"sprints.top": False}
        )
        assert r.status in (404, 405)
    finally:
        await client.close()


# --- /progress takes the chat's default unit ----------------------------------------------------------------


async def test_progress_outside_a_sprint_takes_the_chats_default_unit(make):
    h = await make({"sprints": {"reports": True, "percent": True}})
    await h.tbot.services.shared.extra["settings"].set("telegram", str(GROUP), "sprints.default_unit", "percent")
    await h.say(ALICE, "/progress 40")
    # a % outside a sprint corrects a sprint; with none in the last 3 days, the bot says so (words would be recorded)
    assert "Відсотки записуються за спринт" in h.last_private(ALICE).text


# --- dice ---------------------------------------------------------------------------------------------------------


async def test_a_private_roll_goes_to_the_private_chat_with_its_sticker_when_it_cant_be_ephemeral(make):
    h = await make({"dice": {"stickers": True}}, bot_admin=False)
    mark = h.mark()
    await h.say(ALICE, "/d20 -p")
    assert not [m for m in h.public_since(mark) if m.from_bot]  # nothing public
    dms = h.dms(ALICE)
    assert any("Потайний кидок" in m.text for m in dms)
    assert h.calls("sendSticker") and h.calls("sendSticker")[-1]["chat_id"] == ALICE.id


async def test_a_private_roll_without_a_private_chat_stays_unseen(make):
    h = await make({"dice": {"stickers": True}}, bot_admin=False)
    h.fake.blocked_dm.add(ALICE.id)
    mark = h.mark()
    await h.say(ALICE, "/d20+5 -p Потайний")
    public = [m for m in h.public_since(mark) if m.from_bot]
    assert len(public) == 1 and "особистий чат" in public[0].text  # the hint only, never the roll
    assert "Потайний" not in public[0].text and not h.calls("sendSticker")


async def test_dicevar_answers_go_to_the_private_chat_when_they_cant_be_ephemeral(make):
    h = await make({"dice": {"variables": True}}, bot_admin=False)
    mark = h.mark()
    await h.say(ALICE, "/dicevar $атака d20+5")
    assert not [m for m in h.public_since(mark) if m.from_bot]
    assert "$атака" in h.dms(ALICE)[-1].text


async def test_saving_a_roll_with_variables_off_says_so(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/d20 =$сила")
    assert "вимкнені" in h.last_private(ALICE).text
    assert "d20" in h.last_public().text  # the roll itself still happens


async def test_a_chat_can_turn_the_dice_stickers_off(make):
    h = await make({"dice": {"stickers": True}, **FULL})  # chat settings need the bot with storage
    await h.tbot.services.shared.extra["settings"].set("telegram", str(GROUP), "dice.stickers", False)
    await h.say(ALICE, "/d20")
    assert not h.calls("sendSticker") and "d20" in h.last_public().text
    await h.dm(ALICE, "/d20")  # the private chat keeps them
    assert h.calls("sendSticker")


def test_true_random_dice_in_the_environment_need_their_key_and_the_extras(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_POLLING", "1")
    monkeypatch.setenv("DICE_RANDOMNESS", "random.org")
    with pytest.raises(ConfigError):
        from_env("telegram")  # no EXTRAS
    monkeypatch.setenv("EXTRAS", "1")
    with pytest.raises(ConfigError):
        from_env("telegram")  # no key
    monkeypatch.setenv("RANDOM_ORG_KEY", "k")
    assert from_env("telegram").bots["main"].options("dice")["randomness"] == "random.org"


# --- replies follow the config file ----------------------------------------------------------------------------


async def test_the_config_files_replies_follow_the_file(make):
    h = await make({"replies": {"entries": [{"command": "hi", "answers": ["Привіт"]}]}, **FULL})
    store = h.tbot.module("replies").store
    assert [e.answers for e in await store.for_chat("telegram", str(GROUP))] == [("Привіт",)]
    module = h.tbot.module("replies")
    module.static = __import__("writers_bot.modules.replies.telegram", fromlist=["x"]).config_entries(
        {"entries": [{"command": "bye", "answers": ["Бувай"]}]}
    )
    await module.post_init()  # the next start, with the file changed
    assert [(e.command, e.answers) for e in await store.for_chat("telegram", str(GROUP))] == [("bye", ("Бувай",))]


# --- prompts --------------------------------------------------------------------------------------------------------


async def test_the_shared_option_is_the_default_of_the_chats_switch(make):
    h = await make({"prompts": {"source": "db", "shared": False}, **FULL})
    settings = h.tbot.services.shared.extra["settings"]
    assert await settings.get("telegram", str(GROUP), "prompts.use_shared") is False


# --- achievements ---------------------------------------------------------------------------------------------------


async def test_plain_private_messages_pass_by_while_an_achievement_waits_for_a_picture(make):
    h = await make({"achievements": {}, **FULL})
    await h.say(DAN, "hi")
    await h.dm(DAN, "/newachievement")
    before = len(h.dms(DAN))
    await h.dm(DAN, "просто текст")  # not a reply to the question: not taken as the answer
    assert len(h.dms(DAN)) == before


async def test_achievements_off_means_off(make):
    h = await make({"achievements": {}, **FULL})
    await h.tbot.services.shared.extra["settings"].set("telegram", str(GROUP), "achievements.enabled", False)
    await h.say(ALICE, "/achievements")
    assert "вимкнено" in h.last_private(ALICE).text
