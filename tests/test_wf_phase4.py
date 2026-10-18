"""Round 2 of the user workflows: settings, communities, replies, prompt databases, the dashboard (phase 4)."""

import os
from pathlib import Path

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer
from fake_telegram import Chat, FakeUser
from harness import ADMIN, ALICE, BOB, CAROL, DAN, GROUP, Harness
from test_web import WEB, init_data

from writers_bot.botconfig import WebConfig
from writers_bot.core.prompts import PromptsStore

FULL = {"sprints": {"reports": True, "works": True, "percent": True}}
OTHER = -1002
EVE = FakeUser(5, "Eve")


def save_transcripts(made, name: str) -> None:
    out = os.environ.get("WF_TRANSCRIPTS")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    for i, h in enumerate(made):
        Path(out, f"{name}{'-' + str(i) if i else ''}.txt").write_text(h.transcript(), encoding="utf-8")


@pytest.fixture
async def make(tmp_path, clock, request):
    made = []

    async def build(modules=FULL, **kwargs):
        h = await Harness.create(tmp_path / str(len(made)), clock, modules, **kwargs)
        h.fake.chats[GROUP].roles[DAN.id] = "administrator"
        made.append(h)
        return h

    yield build
    for h in made:
        await h.close()
    save_transcripts(made, request.node.name)


def sheet(texts=None, images=None, config=None) -> PromptsStore:
    store = PromptsStore(None)
    store.data.texts = {"ua": texts or {}}
    store.data.images = images or {}
    store.data.config = config or {}
    return store


async def open_settings(h, admin=DAN):
    await h.say(admin, "/settings")
    return h.last_private(admin)


# --- chat settings ------------------------------------------------------------------------------------


async def test_x01_admin_turns_top_off(make):
    h = await make()
    card = await open_settings(h)
    assert "Налаштування чату" in card.text and not [m for m in h.bot_public() if "Налаштування чату" in m.text]
    await h.press_setting(DAN, card, "/top: увімкнено")
    assert "/top: вимкнено" in card.buttons()
    await h.say(ALICE, "/top")
    assert "вимкнено" in h.last_public().text
    await h.close()


async def test_x02_members_cant_change_settings(make):
    h = await make()
    mark = h.mark()
    cmd = await h.say(ALICE, "/settings")
    assert "лише адміни" in h.last_private(ALICE).text
    assert cmd.deleted and not h.public_since(mark)
    card = await open_settings(h)
    answer = await h.press(ALICE, card, "Спринти")  # someone else's card
    assert "не для вас" in answer["text"]
    await h.close()


async def test_x03_default_length(make):
    h = await make()
    card = await open_settings(h)
    await h.press_setting(DAN, card, "-5")
    assert any("Тривалість спринту, хв: 25" in b for b in card.buttons())
    await h.say(ALICE, "/sprint")
    assert h.sprint().planned_duration == 25
    await h.close()


async def test_x04_works_off_in_this_chat(make):
    h = await make()
    from test_wf_phase3 import add_works

    await add_works(h, ALICE, "Роман", "Есе")
    card = await open_settings(h)
    await h.press_setting(DAN, card, "Твори: увімкнено")
    await h.say(ALICE, "/sprint 10 0")
    assert "Роман" not in h.sprint_message().text
    assert "Мої твори" not in h.sprint_message().buttons()
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "500")  # no works in this chat: no question
    assert "Записано: 500 слів" in h.mine(ALICE)[-1].text
    await h.close()


async def test_x05_hidden_numbers_off_in_this_chat(make):
    h = await make()
    await h.dm(BOB, "/profile")
    await h.press(BOB, h.dms(BOB)[-1], "Налаштування")
    await h.press(BOB, h.dms(BOB)[-1], "Мої числа: видно")
    card = await open_settings(h)
    await h.press_setting(DAN, card, "Приховані числа (✓ на дошці): увімкнено")
    await h.say(ALICE, "/sprint 10 0")
    await h.press(BOB, h.sprint_message(), "Долучитися")
    assert "видно всім" in h.mine(BOB)[-1].text  # told before writing
    await h.advance(10 * 60)
    await h.reply(BOB, h.board(), "900")
    assert "900 слів" in h.board().text
    await h.close()


async def test_x06_keep_report_messages(make):
    h = await make()
    card = await open_settings(h)
    await h.press_setting(DAN, card, "Прибирати звіти: увімкнено")
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    m = await h.reply(ALICE, h.board(), "500")
    assert not m.deleted and m.reactions == ["👍"]
    await h.close()


async def test_x07_greeter_text_from_settings(make):
    h = await make({"greeter": {}, **FULL})
    card = await open_settings(h)
    await h.press(DAN, card, "Привітання й досягнення")
    link = card.button("Привітання: немає — змінити")["url"]  # typed in private, not in the group
    mark = h.mark()
    await h.dm(DAN, "/start " + link.split("start=")[1])
    assert "«Привітання» у чаті «Writers»" in h.dms(DAN)[-1].text
    await h.dm(DAN, "Вітаємо, {name}! Пишіть у спринтах.")
    assert "Збережено" in h.dms(DAN)[-1].text and not h.public_since(mark)
    await h.dm(ALICE, "/start " + link.split("start=")[1])  # not an admin there
    assert "лише адміни" in h.dms(ALICE)[-1].text
    await h.say(DAN, "", new_chat_members=[EVE.to_dict()])
    assert "Вітаємо" in h.last_public().text and "Eve" in h.last_public().text
    await h.close()


# --- communities ----------------------------------------------------------------------------------------


async def test_x08_telegram_community_service_message(make):
    h = await make()
    h.fake.chats[OTHER] = Chat(OTHER, title="Second")
    community = {"id": 777, "name": "Письменники"}
    await h.say(DAN, "", community_chat_added={"community": community})
    await h.say(DAN, "", chat=OTHER, community_chat_added={"community": community})
    communities = h.services.extra["communities"]
    c = await communities.of_chat("telegram", str(GROUP))
    assert c is not None and c.name == "Письменники" and len(c.chats) == 2
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "300")
    await h.say(BOB, "/sprint 20 0", chat=OTHER)
    await h.advance(20 * 60)
    await h.reply(BOB, h.board(OTHER), "300")
    await h.advance(13 * 3600)
    await h.say(CAROL, "/top")
    top = h.last_public().text
    assert "Alice" in top and "Bob" in top  # shared across the community
    await h.say(DAN, "", community_chat_removed={})
    assert await communities.of_chat("telegram", str(GROUP)) is None
    await h.close()


async def test_x09_community_from_get_chat(make):
    h = await make()
    h.fake.chats[GROUP].community = {"id": 99, "name": "Клуб"}
    await h.say(ALICE, "hello")
    await h.settle()
    c = await h.services.extra["communities"].of_chat("telegram", str(GROUP))
    assert c is not None and c.name == "Клуб"
    await h.close()


async def test_x10_community_settings_inherit(make):
    h = await make()
    communities = h.services.extra["communities"]
    c = await communities.create("Клуб")
    await communities.add(c.id, "telegram", str(GROUP))
    await h.dm(ADMIN, f"/community set {c.id} sprints.default_length 45")
    assert "45" in h.dms(ADMIN)[-1].text
    card = await open_settings(h)
    assert "Спринти · змінено: 1" in card.buttons()  # the main card says which section differs
    await h.press(DAN, card, "Спринти · змінено: 1")
    assert any("45 *" in b for b in card.buttons())  # inherited, marked
    await h.press_setting(DAN, card, "+5")
    await h.say(ALICE, "/sprint")
    assert h.sprint().planned_duration == 50
    await h.close()


async def test_x11_broadcast_once_per_community(make):
    h = await make()
    h.fake.chats[OTHER] = Chat(OTHER, title="Small", member_count=3)
    h.fake.chats[GROUP].member_count = 50
    communities = h.services.extra["communities"]
    c = await communities.create("Клуб")
    await communities.add(c.id, "telegram", str(GROUP))
    await communities.add(c.id, "telegram", str(OTHER))
    await h.say(ALICE, "hi")
    await h.say(ALICE, "hi", chat=OTHER)
    await h.dm(ADMIN, "/broadcast groups Оголошення")
    confirm = h.dms(ADMIN)[-1]
    assert "Отримувачів: <b>1</b>" in confirm.text
    await h.press(ADMIN, confirm, "Надіслати")
    await h.settle()
    copies = [m.chat_id for m in h.fake.messages if m.kind == "copy"]
    assert copies == [GROUP]
    await h.close()


async def test_x12_manual_community(make):
    h = await make()
    await h.dm(ADMIN, "/community new Клуб")
    assert "Спільноту створено: 1" in h.dms(ADMIN)[-1].text
    await h.dm(ADMIN, f"/community add 1 {GROUP} {OTHER}")
    await h.dm(ADMIN, "/community list")
    listing = h.dms(ADMIN)[-1].text
    assert "Клуб" in listing and str(GROUP) in listing and "вручну" in listing
    await h.dm(ADMIN, f"/community remove {OTHER}")
    assert "Прибрано чатів: 1" in h.dms(ADMIN)[-1].text
    await h.say(ALICE, "/community")  # not for members: silent
    assert not [m for m in h.bot_public() if "Спільнот" in m.text]
    await h.close()


# --- replies ---------------------------------------------------------------------------------------------


async def test_x13_replies_by_chat_admins(make):
    h = await make({"replies": {}, **FULL})
    await h.say(DAN, "/replies add /imafan = Я фанат, {name}!")
    assert "Додано" in h.last_private(DAN).text
    await h.say(ALICE, "/imafan")
    assert "Я фанат, <b>Alice</b>!" in h.last_public().text
    await h.say(ALICE, "/replies add /spam = нічого")
    assert "лише адміни" in h.last_private(ALICE).text
    await h.say(DAN, "/replies")
    card = h.last_private(DAN)
    await h.press(DAN, card, "Видалити /imafan")
    mark = h.mark()
    await h.say(ALICE, "/imafan")
    assert not h.public_since(mark)
    await h.close()


async def test_x14_legacy_replies_become_defaults(make):
    h = await make({"replies": {"legacy": True}, **FULL})
    await h.say(ALICE, "Слава Україні!")
    assert "Героям Слава" in h.last_public().text
    card = await open_settings(h)
    await h.press_setting(DAN, card, "Відповіді бота за замовчуванням: увімкнено")
    mark = h.mark()
    await h.say(ALICE, "Слава Україні!")
    assert not h.public_since(mark)
    await h.close()


# --- prompt databases ---------------------------------------------------------------------------------


PROMPTS_DB = {"prompts": {"source": "db"}, **FULL}


async def with_sheet(make, **kw):
    h = await make(PROMPTS_DB)
    h.tbot.services.sheet = sheet(**kw)
    h.services.sheet = h.tbot.services.sheet
    await h.tbot.module("prompts").post_init()
    return h


async def test_x15_prompts_imported_once(make):
    h = await with_sheet(make, texts={"Персонаж": ["лицар", "відьма"], "Жахи": ["тінь у кутку"]})
    await h.say(ALICE, "/prompt")
    text = h.last_public().text
    assert "<b>Персонаж</b>" in text and "<b>Жахи</b>" in text
    await h.say(ALICE, "/prompt жахи")
    assert h.last_public().text == "<b>Жахи</b>: тінь у кутку"
    await h.say(ALICE, "/prompt zhakhy")  # the English (Latin) name works too
    assert "тінь" in h.last_public().text
    await h.say(ALICE, "/prompt космос")
    assert "Немає такої категорії" in h.last_public().text
    assert await h.tbot.module("prompts").db.import_sheet(h.tbot.services.sheet.data) == 0  # once
    await h.close()


async def test_x16_chat_prompts_and_shared_switch(make):
    h = await with_sheet(make, texts={"Жахи": ["тінь у кутку"]})
    await h.say(DAN, "/addprompt жахи скрип сходів уночі")
    assert "Додано до категорії «жахи»" in h.last_private(DAN).text
    card = await open_settings(h)
    await h.press_setting(DAN, card, "Спільні промпти: увімкнено")
    for _ in range(5):
        await h.say(ALICE, "/prompt жахи")
        assert "скрип сходів" in h.last_public().text
    await h.close()


async def test_x17_member_suggestion(make):
    h = await with_sheet(make, texts={"Жахи": ["тінь"]})
    await h.say(ALICE, "/suggest комедія кіт на клавіатурі")
    assert "розглянуть" in h.last_private(ALICE).text
    await h.say(DAN, "/suggestions")
    card = h.last_private(DAN)
    assert "кіт на клавіатурі" in card.text
    await h.press(DAN, card, "Прийняти")
    assert "Нових пропозицій немає" in card.text
    await h.say(ALICE, "/prompt комедія")
    assert "кіт на клавіатурі" in h.last_public().text
    await h.close()


async def test_x18_image_prompt_from_a_photo(make):
    h = await with_sheet(make)
    photo = [{"file_id": "PHOTO1", "file_unique_id": "u1", "width": 100, "height": 100}]
    await h.say(DAN, "", photo=photo, caption="/addimage пейзаж")
    assert "Картинку додано" in h.last_private(DAN).text
    await h.say(ALICE, "/image пейзаж")
    sent = h.calls("sendPhoto")
    assert sent and sent[-1]["photo"] == "PHOTO1"
    await h.close()


# --- the dashboard ----------------------------------------------------------------------------------------


@pytest.fixture
async def web_h(make):
    h = await make({"replies": {}, "prompts": {"source": "db"}, **FULL}, web=WEB)
    client = TestClient(TestServer(h.services.web.app))
    await client.start_server()
    yield h, client
    await client.close()


async def login(client, user):
    r = await client.post("/api/session", json={"init_data": init_data(user.to_dict())})
    return {"Authorization": "Bearer " + (await r.json())["token"]}


async def test_x19_chat_settings_in_the_web_app(web_h):
    h, client = web_h
    await h.say(ALICE, "hi")
    dan, alice = await login(client, DAN), await login(client, ALICE)
    chats = await (await client.get("/api/chats", headers=dan)).json()
    assert [c["id"] for c in chats["chats"]] == [GROUP]
    assert (await (await client.get("/api/chats", headers=alice)).json())["chats"] == []
    r = await client.get(f"/api/chat/{GROUP}/settings", headers=alice)
    assert r.status == 403
    r = await client.patch(f"/api/chat/{GROUP}/settings", headers=dan, json={"sprints.top": False})
    data = await r.json()
    assert next(s for s in data["settings"] if s["key"] == "sprints.top")["value"] is False
    await h.say(ALICE, "/top")
    assert "вимкнено" in h.last_public().text
    h.fake.chats[GROUP].roles[DAN.id] = "member"  # no longer an admin: checked on every change
    r = await client.patch(f"/api/chat/{GROUP}/settings", headers=dan, json={"sprints.top": True})
    assert r.status == 403


async def test_x20_settings_link_from_the_chat(web_h):
    h, client = web_h
    await h.say(DAN, "/settings")
    card = h.last_private(DAN)
    url = card.button("Відкрити в застосунку")["url"]
    token = url.split("startapp=")[1]
    dan = await login(client, DAN)
    launch = await (await client.get(f"/api/launch?token={token}", headers=dan)).json()
    assert launch == {"kind": "chat", "chat_id": GROUP, "sprint_id": None}


async def test_x21_prompts_and_submissions_in_the_web_app(web_h):
    h, client = web_h
    dan = await login(client, DAN)
    r = await client.post(
        f"/api/chat/{GROUP}/prompts", headers=dan, json={"category": "fantasy", "text": "дракон-бухгалтер"}
    )
    prompt = (await r.json())["prompts"][0]
    assert (await client.post(f"/api/chat/{GROUP}/prompts/{prompt['id']}/submit", headers=dan, json={})).status == 200
    await h.dm(ADMIN, "/review")
    card = h.dms(ADMIN)[-1]
    assert "дракон-бухгалтер" in card.text
    await h.press(ADMIN, card, "Прийняти")
    h.fake.chats[OTHER] = Chat(OTHER, title="Other")
    await h.say(BOB, "/prompt fantasy", chat=OTHER)
    assert "дракон-бухгалтер" in h.last_public(OTHER).text


async def test_x22_your_data_in_the_web_app(web_h):
    h, client = web_h
    await h.dm(ALICE, "/start")
    await h.say(ALICE, "/progress +500")
    alice = await login(client, ALICE)
    r = await client.post("/api/export/send", headers=alice, json={})
    assert r.status == 200 and h.fake.documents
    r = await client.get("/api/export", headers=alice)
    assert r.status == 200 and r.headers["Content-Type"] == "application/zip"
    assert (await client.delete("/api/me", headers=alice)).status == 200
    assert (await client.get("/api/export", headers=alice)).status == 404


async def test_x23_discord_sign_in(make, monkeypatch):
    from writers_bot.web import discord_oauth

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "abc"})
        return httpx.Response(200, json={"id": "555", "global_name": "Discordian"})

    web = WebConfig(
        url="https://writers.example.org", secret="s" * 32, discord_client_id="1", discord_client_secret="2"
    )
    real = discord_oauth.routes
    monkeypatch.setattr(discord_oauth, "routes", lambda app: real(app, transport=httpx.MockTransport(handler)))
    h = await make(FULL, web=web)
    client = TestClient(TestServer(h.services.web.app))
    await client.start_server()
    try:
        r = await client.get("/auth/discord", allow_redirects=False)
        assert r.status == 302 and "discord.com/oauth2/authorize" in r.headers["Location"]
        state = httpx.URL(r.headers["Location"]).params["state"]
        r = await client.get(f"/auth/discord/callback?code=x&state={state}", allow_redirects=False)
        session = r.headers["Location"].split("#session=")[1]
        headers = {"Authorization": "Bearer " + session}
        assert (await (await client.get("/api/me", headers=headers)).json())["name"] == "Discordian"
        r = await client.post("/api/works", headers=headers, json={"name": "Роман"})
        assert r.status == 200
        r = await client.get("/auth/discord/callback?code=x&state=forged.123.abc", allow_redirects=False)
        assert "login=failed" in r.headers["Location"]
    finally:
        await client.close()


async def test_x24_settings_snapshot_at_the_start(make):
    h = await make()
    from test_wf_phase3 import add_works

    await add_works(h, ALICE, "Роман")
    await h.say(ALICE, "/sprint 10 0")
    card = await open_settings(h)
    await h.press_setting(DAN, card, "Твори: увімкнено")  # applies from the next sprint
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "300")
    assert "«Роман»" in h.mine(ALICE)[-1].text
    await h.close()


async def test_x25_greeter_from_the_sheet_imported_once(make):
    h = await make({"greeter": {"from_sheet": True}, **FULL})
    h.tbot.services.sheet = sheet(config={"welcome_message": {str(GROUP): ["Привіт, {name}!"]}})
    await h.tbot.module("greeter").post_init()
    await h.say(DAN, "", new_chat_members=[EVE.to_dict()])
    assert "Привіт" in h.last_public().text
    card = await open_settings(h)
    await h.press(DAN, card, "Привітання й досягнення · змінено: 1")
    assert any("Привітання: задано" in b for b in card.buttons())
    await h.close()


async def test_x26_admins_see_waiting_suggestions(make):
    h = await with_sheet(make, texts={"Жахи": ["тінь"]})
    await h.say(ALICE, "/suggest жахи двері, що відчиняються самі")
    card = await open_settings(h)
    assert "Пропозицій промптів від учасників: 1" in card.text
    await h.close()
