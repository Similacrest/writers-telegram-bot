"""Round 3 of the user workflows: achievements (phase 5), and a few journeys across all the features."""

import os
from pathlib import Path

import pytest
from fake_telegram import Chat, FakeUser
from harness import ADMIN, ALICE, BOB, CAROL, DAN, GROUP, Harness

MODULES = {"achievements": {}, "sprints": {"reports": True, "works": True, "percent": True}}
OTHER = -1002
OWL = {"file_id": "OWL_FILE", "file_unique_id": "owl", "type": "regular", "width": 512, "height": 512,
       "is_animated": False, "is_video": False, "set_name": "writers_pack"}  # fmt: skip
CAT = {**OWL, "file_id": "CAT_FILE", "file_unique_id": "cat"}
LOOSE = {**OWL, "file_id": "LOOSE", "file_unique_id": "loose", "set_name": None}


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

    async def build(modules=MODULES, **kwargs):
        h = await Harness.create(tmp_path / str(len(made)), clock, modules, **kwargs)
        h.fake.chats[GROUP].roles[DAN.id] = "administrator"
        h.fake.sticker_sets["writers_pack"] = [OWL, CAT]
        made.append(h)
        return h

    yield build
    for h in made:
        await h.close()
    save_transcripts(made, request.node.name)


async def create_owl(h, admin=DAN):
    sticker = await h.say(admin, "", sticker=OWL)
    await h.reply(admin, sticker, "/newachievement Нічна сова — пише після півночі")
    return sticker


async def test_a01_create_from_a_sticker_in_the_group(make):
    h = await make()
    await create_owl(h)
    assert "Досягнення «Нічна сова» створено" in h.last_private(DAN).text
    await h.say(ALICE, "/achievements")
    assert "«Нічна сова» — пише після півночі (0)" in h.last_public().text
    await h.close()


async def test_a02_only_admins_create(make):
    h = await make()
    sticker = await h.say(ALICE, "", sticker=OWL)
    mark = h.mark()
    await h.reply(ALICE, sticker, "/newachievement Моє")
    assert "лише адміни" in h.last_private(ALICE).text and not h.public_since(mark)
    await h.close()


async def test_a03_award_with_buttons(make):
    h = await make()
    await create_owl(h)
    post = await h.say(ALICE, "Дописала главу о третій ночі")
    mark = h.mark()
    cmd = await h.reply(DAN, post, "/award")
    card = h.last_private(DAN)
    assert "Яке досягнення для Alice?" in card.text and cmd.deleted
    await h.press(DAN, card, "Нічна сова")
    public = h.public_since(mark)
    assert [m.kind for m in public] == ["photo"]  # one picture: the card, with one line as its caption
    assert "Alice</a> отримує «Нічна сова»" in public[0].text
    assert h.fake.photos[-1].startswith(b"\x89PNG") and not h.calls("sendSticker")
    await h.close()


async def test_a04_award_by_name_and_twice(make):
    h = await make()
    await create_owl(h)
    post = await h.say(ALICE, "привіт")
    await h.reply(DAN, post, "/award нічна")
    assert "отримує «Нічна сова»" in h.last_public().text
    mark = h.mark()
    await h.reply(DAN, post, "/award Нічна сова")
    assert "вже має" in h.last_private(DAN).text and not [m for m in h.public_since(mark) if m.kind == "sticker"]
    await h.close()


async def test_a05_award_alias(make):
    h = await make()
    await create_owl(h)
    post = await h.say(ALICE, "привіт")
    await h.reply(DAN, post, "/нагорода Нічна сова")
    assert "отримує «Нічна сова»" in h.last_public().text
    await h.close()


async def test_a06_profile_lists_achievements(make):
    h = await make()
    await create_owl(h)
    await h.reply(DAN, await h.say(ALICE, "привіт"), "/award Нічна сова")
    await h.dm(ALICE, "/profile")
    assert "Досягнення: «Нічна сова»" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "/achievements")
    assert h.dms(ALICE)[-1].kind == "photo" and "«Нічна сова»" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "/profile")
    await h.press(ALICE, h.dms(ALICE)[-1], "Мої досягнення")
    assert h.dms(ALICE)[-1].kind == "photo"
    await h.close()


async def test_a07_unaward(make):
    h = await make()
    await create_owl(h)
    post = await h.say(ALICE, "привіт")
    await h.reply(DAN, post, "/award Нічна сова")
    await h.reply(DAN, post, "/unaward")
    card = h.last_private(DAN)
    await h.press(DAN, card, "Нічна сова")
    await h.dm(ALICE, "/achievements")
    assert "ще немає досягнень" in h.dms(ALICE)[-1].text
    await h.close()


async def test_a08_sticker_gone_from_the_pack(make):
    h = await make()
    await create_owl(h)
    h.fake.sticker_sets["writers_pack"] = [CAT]  # the admins removed it in @Stickers
    post = await h.say(ALICE, "привіт")
    mark = h.mark()
    await h.reply(DAN, post, "/award Нічна сова")
    public = h.public_since(mark)
    assert [m.kind for m in public] == ["photo"] and "отримує «Нічна сова»" in public[0].text  # a medal on the card
    question = h.dms(DAN)[-1]
    assert "більше немає у вашому наборі" in question.text  # asked privately, not in the group
    await h.say(DAN, "", DAN.id, sticker=CAT)
    assert "Стікер оновлено" in h.dms(DAN)[-1].text
    await h.reply(DAN, await h.say(BOB, "привіт"), "/award Нічна сова")
    assert h.calls("getFile")[-1]["file_id"] == "CAT_FILE"  # the new sticker is the card's icon
    assert not h.calls("sendSticker")
    await h.close()


async def test_a08b_sticker_gone_admin_without_private_chat(make):
    h = await make()
    await create_owl(h)
    h.fake.sticker_sets["writers_pack"] = [CAT]
    h.fake.blocked_dm.add(DAN.id)
    await h.reply(DAN, await h.say(ALICE, "привіт"), "/award Нічна сова")
    question = h.last_public()
    assert "більше немає в наборі" in question.text
    answer = await h.say(DAN, "", reply_to=question, sticker=CAT)
    assert question.deleted and answer.deleted and "Стікер оновлено" in h.last_private(DAN).text
    await h.close()


async def test_a09_create_in_private(make):
    h = await make()
    await h.say(DAN, "hi")  # the registry knows the group
    await h.say(DAN, "", DAN.id, sticker=OWL)
    question = h.dms(DAN)[-1]
    assert "Як назвати досягнення" in question.text
    await h.dm(DAN, "Марафонець — десять спринтів за тиждень", reply_to=question)
    assert "«Марафонець» створено" in h.dms(DAN)[-1].text  # one chat: no question which
    await h.say(ALICE, "/achievements")
    assert "Марафонець" in h.last_public().text
    await h.close()


async def test_a10_private_with_two_chats(make):
    h = await make()
    h.fake.chats[OTHER] = Chat(OTHER, title="Second", roles={DAN.id: "administrator"})
    await h.say(DAN, "hi")
    await h.say(DAN, "hi", chat=OTHER)
    await h.say(DAN, "", DAN.id, sticker=OWL)
    await h.dm(DAN, "Сова", reply_to=h.dms(DAN)[-1])
    pick = h.dms(DAN)[-1]
    assert "Для якого чату?" in pick.text and set(pick.buttons()) == {"Writers", "Second"}
    await h.press(DAN, pick, "Second")
    await h.say(ALICE, "/achievements", chat=OTHER)
    assert "Сова" in h.last_public(OTHER).text
    await h.say(ALICE, "/achievements")
    assert "Сова" not in h.last_public().text
    await h.close()


async def test_a11_not_from_a_pack(make):
    h = await make()
    sticker = await h.say(DAN, "", sticker=LOOSE)
    await h.reply(DAN, sticker, "/newachievement Щось")
    assert "не з набору" in h.last_private(DAN).text
    await h.close()


async def test_a12_shared_by_a_community(make):
    h = await make()
    h.fake.chats[OTHER] = Chat(OTHER, title="Second", roles={DAN.id: "administrator"})
    communities = h.services.extra["communities"]
    c = await communities.create("Клуб")
    await communities.add(c.id, "telegram", str(GROUP))
    await communities.add(c.id, "telegram", str(OTHER))
    await create_owl(h)
    post = await h.say(BOB, "привіт", chat=OTHER)
    await h.reply(DAN, post, "/award Нічна сова")
    assert "отримує «Нічна сова»" in h.last_public(OTHER).text
    await h.close()


async def test_a13_turned_off(make):
    h = await make()
    await create_owl(h)
    await h.say(DAN, "/settings")
    card = h.last_private(DAN)
    await h.press(DAN, card, "Досягнення: увімкнено")
    await h.reply(DAN, await h.say(ALICE, "привіт"), "/award Нічна сова")
    assert "вимкнено" in h.last_private(DAN).text
    await h.close()


async def test_a14_export_and_forget(make):
    h = await make()
    await create_owl(h)
    await h.reply(DAN, await h.say(ALICE, "привіт"), "/award Нічна сова")
    await h.dm(ALICE, "/export")
    assert h.fake.documents
    await h.dm(ALICE, "/forgetme")
    await h.press(ALICE, h.dms(ALICE)[-1], "Видалити")
    await h.dm(ALICE, "/achievements")
    assert "ще немає досягнень" in h.dms(ALICE)[-1].text
    await h.close()


async def test_a15_award_needs_a_reply(make):
    h = await make()
    await create_owl(h)
    await h.say(DAN, "/award Нічна сова")
    assert "Відповідайте на повідомлення учасника" in h.last_private(DAN).text
    await h.close()


# --- journeys across the features -----------------------------------------------------------------------


async def test_j01_a_week_of_sprints(make):
    """A writer's week: works, sprints in a chat, late reports, a goal, the streak, the share line."""
    h = await make()
    from test_wf_phase3 import add_works

    await add_works(h, ALICE, "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Ціль")
    await h.dm(ALICE, "5000 на тиждень")
    for _day in range(3):
        await h.say(ALICE, "/sprint 30 0")
        await h.press(BOB, h.sprint_message(), "Долучитися")
        await h.advance(30 * 60)
        await h.reply(ALICE, h.board(), "700")
        await h.reply(BOB, h.board(), "✓")
        await h.advance(23.5 * 3600)
    await h.say(ALICE, "/progress")
    panel = h.mine(ALICE)[-1]
    assert "Днів поспіль: 3 дні" in panel.text
    mark = h.mark()
    await h.press(ALICE, panel, "Поділитися")
    shared = h.public_since(mark)
    assert len(shared) == 1 and "2100 слів" in shared[0].text and "3 дні поспіль" in shared[0].text
    # the chat has one message per finished sprint plus the shared line
    boards = [m for m in h.bot_public() if "Підсумки спринту" in m.text]
    assert len(boards) == 3
    await h.close()


async def test_j02_a_quiet_chat(make):
    """With an admin bot, a whole sprint leaves one bot message (and the /sprint command that started it);
    everything personal is private."""
    h = await make()
    mark = h.mark()
    await h.say(ALICE, "/sprint 15 0")
    await h.press(BOB, h.sprint_message(), "Долучитися")
    await h.press(CAROL, h.sprint_message(), "Долучитися")
    await h.advance(15 * 60)
    await h.reply(ALICE, h.board(), "300")
    await h.reply(BOB, h.board(), "40%")
    await h.press(CAROL, h.board(), "✗ Не вийшло")
    await h.say(BOB, "/profile")
    await h.advance(13 * 3600)
    left = [m for m in h.fake.messages[mark:] if m.chat_id == GROUP and not m.deleted and m.receiver is None]
    assert [(m.from_bot, m.text[:7]) for m in left] == [(False, "/sprint"), (True, "<b>Підс")], [
        m.text[:40] for m in left
    ]
    await h.close()


async def test_j03_v2_default_unchanged_end_to_end(make):
    """The default (environment) set: prompts, wordcount and the v2 sprint; nothing new shows up."""
    h = await make({"prompts": {}, "wordcount": {}, "sprints": {}})
    await h.say(ALICE, "/help")
    help_text = h.last_public().text
    assert "/sprint" in help_text and "/progress" not in help_text and "/profile" not in help_text
    await h.say(ALICE, "/sprint 5 0")
    await h.advance(5 * 60)
    assert h.last_public().buttons() == ["Ще раз"]
    await h.say(ALICE, "/profile")  # not a v2 command
    assert "Профіль" not in " ".join(m.text for m in h.fake.messages)
    await h.close()


async def test_j04_two_admins_one_card(make):
    """Someone else's private card can't be used, and admin rights are checked at the press."""
    h = await make()
    h.fake.chats[GROUP].roles[ADMIN.id] = "member"
    await create_owl(h)
    post = await h.say(ALICE, "привіт")
    await h.reply(DAN, post, "/award")
    card = h.last_private(DAN)
    h.fake.chats[GROUP].roles[DAN.id] = "member"  # demoted meanwhile
    answer = await h.press(DAN, card, "Нічна сова")
    assert "лише адміни" in answer["text"]
    await h.close()


__all__ = ["FakeUser"]


async def test_a16_no_card_when_there_is_nothing_to_choose(make):
    h = await make()
    from test_wf_phase3 import add_works

    await add_works(h, ALICE, "Роман")
    await h.say(ALICE, "/sprint 10 0")  # the first sprint: the card with the data notice
    first = len(h.mine(ALICE))
    await h.advance(10 * 60)
    await h.press(ALICE, h.board(), "Ще раз")
    assert len(h.mine(ALICE)) == first  # one kept work: it's simply in the sprint
    assert "Alice</a> — Роман" in h.sprint_message().text
    await h.press(ALICE, h.sprint_message(), "Мої твори")
    assert "✓ Роман" in h.mine(ALICE)[-1].buttons()  # still reachable
    await h.close()
