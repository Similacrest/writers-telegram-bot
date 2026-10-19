"""Round 4 of the user workflows (v4.2): pausing and switching works, the split tip, explicit % corrections,
getting started, achievement pictures and the dice stickers."""

import os
from pathlib import Path

import pytest
from fake_telegram import Chat
from harness import ADMIN, ALICE, BOB, CAROL, DAN, GROUP, Harness

from writers_bot.core import extras
from writers_bot.core import stitch as stitch_core
from writers_bot.core.extras import Outcome, Roll

FULL = {"sprints": {"reports": True, "works": True, "percent": True}}
ACH = {"achievements": {}, **FULL}
DICE = {"dice": {"stickers": True}}
OWL = {"file_id": "OWL_FILE", "file_unique_id": "owl", "type": "regular", "width": 512, "height": 512,
       "is_animated": False, "is_video": False, "set_name": "writers_pack"}  # fmt: skip
CAT = {**OWL, "file_id": "CAT_FILE", "file_unique_id": "cat"}
FOX = {**OWL, "file_id": "FOX_FILE", "file_unique_id": "fox", "is_animated": True,
       "thumbnail": {"file_id": "FOX_THUMB", "file_unique_id": "foxt", "width": 128, "height": 128}}  # fmt: skip


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
        h.fake.sticker_sets["writers_pack"] = [OWL, CAT, FOX]
        made.append(h)
        return h

    yield build
    for h in made:
        await h.close()
    save_transcripts(made, request.node.name)


async def add_works(h, user, *names, percent=()):
    await h.dm(user, "/start works")
    for name in names:
        await h.press(user, h.dms(user)[-1], "+ Новий твір")
        await h.dm(user, name, reply_to=h.dms(user)[-1])
        card = h.dms(user)[-1]
        if name in percent:
            await h.press(user, card, "Слова / %")
        await h.press(user, card, "← Назад")


async def finish_onboarding(h, user):
    """Through the getting-started steps, so their prompts don't get in the way of other workflows."""
    await h.dm(user, "/start onboard")
    while "Пропустити" in h.dms(user)[-1].buttons():
        await h.press(user, h.dms(user)[-1], "Пропустити")


# --- pausing and switching works ---------------------------------------------------------------------


async def test_p01_split_tip_with_their_own_works(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await h.say(ALICE, "/progress +500")
    ask = h.last_private(ALICE)
    assert "Куди записати 500?" in ask.text
    assert "«300 Роман 200 Есе»" in ask.text and "«500 Роман»" in ask.text  # made from their works
    assert ask.buttons() == ["Роман", "Есе", "⏸ Пауза…"]
    await h.press(ALICE, ask, "Есе")
    assert "«Есе»: 500 слів" in ask.text


async def test_p02_tip_stops_once_they_name_works(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await h.say(ALICE, "/progress 300 роман 200 есе")
    assert "«Роман»: 300 слів" in h.last_private(ALICE).text
    await h.say(ALICE, "/progress +100")
    ask = h.last_private(ALICE)
    assert "Куди записати 100?" in ask.text and "Можна одразу" not in ask.text


async def test_p03_tip_at_most_three_times(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    seen = 0
    for _ in range(5):
        await h.say(ALICE, "/progress +10")
        ask = h.last_private(ALICE)
        seen += "Можна одразу" in ask.text
        await h.press(ALICE, ask, "Роман")
    assert seen == 3


async def test_p04_pause_from_the_question_then_it_records(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await h.say(ALICE, "/progress +400")
    ask = h.last_private(ALICE)
    await h.press(ALICE, ask, "⏸ Пауза…")
    assert "Над чим пишете зараз" in ask.text and ask.buttons()[:2] == ["✓ Роман", "✓ Есе"]
    await h.press(ALICE, ask, "✓ Есе")
    assert ask.buttons()[:2] == ["✓ Роман", "⏸ Есе"]
    await h.press(ALICE, ask, "Готово")
    assert "«Роман»: 400 слів" in ask.text  # the waiting report went where it now can
    await h.say(ALICE, "/progress +50")
    assert "«Роман»: 50 слів" in h.last_private(ALICE).text  # no more question


async def test_p05_works_command_in_a_group_switches(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    mark = h.mark()
    await h.say(ALICE, "/works")
    card = h.last_private(ALICE)
    assert "Над чим пишете зараз" in card.text and not h.public_since(mark)
    await h.press(ALICE, card, "✓ Есе")
    await h.say(ALICE, "/sprint 10 0")
    assert "Alice</a> — Роман" in h.sprint_message().text and "Есе" not in h.sprint_message().text


async def test_p06_works_command_during_a_sprint_is_the_sprint_card(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await finish_onboarding(h, ALICE)
    await h.say(ALICE, "/sprint 10 0")
    await h.say(ALICE, "/works")
    card = h.last_private(ALICE)
    assert "Ви у спринті" in card.text
    await h.press(ALICE, card, "✓ Есе")
    assert "Есе" not in h.sprint_message().text


async def test_p07_paused_work_takes_no_bare_numbers(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await h.say(ALICE, "/works")
    await h.press(ALICE, h.last_private(ALICE), "✓ Роман")
    await h.say(ALICE, "/progress +300")
    assert "«Есе»: 300 слів" in h.last_private(ALICE).text
    await h.say(ALICE, "/progress +20 роман")  # named, a paused work still takes it
    assert "«Роман»: 20 слів" in h.last_private(ALICE).text


async def test_p08_profile_works_button_in_a_group(make):
    h = await make()
    await add_works(h, ALICE, "Роман")
    await h.say(ALICE, "/profile")
    card = h.last_private(ALICE)
    await h.press(ALICE, card, "Твори")
    assert "Над чим пишете зараз" in card.text and "Керувати творами" in card.buttons()


async def test_p09_works_in_private_is_the_menu_with_pause(make):
    h = await make()
    await add_works(h, ALICE, "Роман")
    await h.dm(ALICE, "/works")
    menu = h.dms(ALICE)[-1]
    assert "Ваші твори" in menu.text
    await h.press(ALICE, menu, "Роман (слова)")
    await h.press(ALICE, menu, "⏸ Пауза")
    assert "На паузі" in menu.text and "▶ Продовжити" in menu.buttons()
    await h.press(ALICE, menu, "← Назад")
    assert "Роман (слова) · на паузі" in menu.buttons()


async def test_p10_works_without_works(make):
    h = await make()
    await h.say(BOB, "/works")
    card = h.last_private(BOB)
    assert "Творів ще немає" in card.text and "Керувати творами" in card.buttons()


# --- % outside a sprint ------------------------------------------------------------------------------------


async def test_c01_percent_correction_names_the_sprint(make):
    h = await make()
    await add_works(h, ALICE, "План", percent=("План",))
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "40%")
    await h.say(ALICE, "/progress 60% план")
    confirm = h.last_private(ALICE)
    assert "Виправив ваш спринт сьогодні о" in confirm.text and "60%" in confirm.text
    assert "Скасувати звіт" in confirm.buttons()


async def test_c02_correction_confirmed_even_without_ephemeral_rights(make):
    h = await make(bot_admin=False)
    await add_works(h, ALICE, "План", percent=("План",))
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "40%")
    mark = h.mark()
    await h.say(ALICE, "/progress 70% план")
    shown = [m for m in h.public_since(mark) if "Виправив" in m.text]
    assert shown and "70%" in shown[0].text  # a short-lived reply, since nothing else says where it went
    await h.advance(61)
    assert all(m.deleted for m in shown)


async def test_c03_ephemeral_command_gets_an_ephemeral_confirmation(make):
    h = await make(bot_admin=False)
    await h.say(ALICE, "/progress +300", ephemeral=True)
    assert "Записано: 300 слів" in h.mine(ALICE)[-1].text


# --- getting started ------------------------------------------------------------------------------------


async def test_o01_onboarding_all_steps(make):
    h = await make()
    await h.dm(ALICE, "/start")
    step = h.dms(ALICE)[-1]
    assert "3 кроки" in step.text and "1/3. Над чим пишете?" in step.text
    await h.dm(ALICE, "Роман", reply_to=step)
    step = h.dms(ALICE)[-1]
    assert "2/3. Ціль для «Роман»" in step.text
    await h.dm(ALICE, "500 на день", reply_to=step)
    step = h.dms(ALICE)[-1]
    assert "3/3." in step.text
    await h.press(ALICE, step, "Лише ✓")
    assert "Готово!" in step.text
    await h.dm(ALICE, "/profile")
    profile = h.dms(ALICE)[-1].text
    assert "«Роман»" in profile and "500" in profile and "Налаштування:" not in profile
    await h.dm(ALICE, "/start")
    assert "Над чим пишете" not in h.dms(ALICE)[-1].text  # not offered again


async def test_o02_skipping_everything(make):
    h = await make()
    await h.dm(ALICE, "/start")
    await h.press(ALICE, h.dms(ALICE)[-1], "Пропустити")
    assert "3/3." in h.dms(ALICE)[-1].text  # no work, so no goal to ask about
    await h.press(ALICE, h.dms(ALICE)[-1], "Пропустити")
    assert "Готово!" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "/profile")
    assert "Налаштування:" not in h.dms(ALICE)[-1].text


async def test_o03_a_goal_it_cant_read(make):
    h = await make()
    await h.dm(ALICE, "/start")
    await h.dm(ALICE, "Роман", reply_to=h.dms(ALICE)[-1])
    step = h.dms(ALICE)[-1]
    await h.dm(ALICE, "багато", reply_to=step)
    assert "Не зрозумів ціль" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "3000 на тиждень", reply_to=step)
    assert "3/3." in h.dms(ALICE)[-1].text


async def test_o04_later_and_continue_from_profile(make):
    h = await make()
    await h.dm(ALICE, "/start")
    await h.dm(ALICE, "Роман", reply_to=h.dms(ALICE)[-1])
    await h.press(ALICE, h.dms(ALICE)[-1], "Не зараз")
    assert "Продовжити можна в /profile" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "Просто повідомлення")  # not taken as an answer any more
    await h.dm(ALICE, "/profile")
    profile = h.dms(ALICE)[-1]
    assert "Налаштування: 1 з 3 кроків" in profile.text
    await h.press(ALICE, profile, "Продовжити налаштування")
    assert "2/3. Ціль для «Роман»" in profile.text


async def test_o05_first_sprint_card_offers_it(make):
    h = await make()
    await h.say(ALICE, "/sprint 10 0")
    card = h.last_private(ALICE)
    assert "Налаштувати (1 хв)" in card.buttons()
    link = card.button("Налаштувати (1 хв)")["url"]
    assert link.endswith("?start=onboard")


async def test_o06_a_work_from_the_menu_counts_as_the_step(make):
    h = await make()
    await add_works(h, ALICE, "Роман")
    await h.dm(ALICE, "/start onboard")
    assert "2/3. Ціль для «Роман»" in h.dms(ALICE)[-1].text


async def test_o07_group_profile_links_to_it(make):
    h = await make()
    await h.say(ALICE, "/profile")
    card = h.last_private(ALICE)
    assert "Налаштування: 0 з 3 кроків" in card.text
    assert card.button("Продовжити налаштування")["url"].endswith("?start=onboard")


# --- achievement pictures ---------------------------------------------------------------------------------


async def create(h, sticker, name):
    s = await h.say(DAN, "", sticker=sticker)
    await h.reply(DAN, s, f"/newachievement {name}")


async def test_a01_award_is_one_picture(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова — пише після півночі")
    mark = h.mark()
    await h.reply(DAN, await h.say(ALICE, "дописала"), "/award Нічна сова")
    public = h.public_since(mark)
    assert [m.kind for m in public] == ["photo"] and "отримує «Нічна сова»" in public[0].text
    assert h.calls("getFile")[-1]["file_id"] == "OWL_FILE"


async def test_a02_board_in_the_group(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    await create(h, CAT, "Кіт")
    await h.reply(DAN, await h.say(ALICE, "a"), "/award Нічна сова")
    await h.reply(DAN, await h.say(ALICE, "b"), "/award Кіт")
    await h.reply(DAN, await h.say(BOB, "c"), "/award Кіт")
    mark = h.mark()
    await h.say(BOB, "/achievements")
    board = h.public_since(mark)[-1]
    assert board.kind == "photo" and "«Кіт» (2)" in board.text and "/myachievements" in board.text


async def test_a03_my_collection_in_the_group(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    await create(h, CAT, "Кіт")
    await h.reply(DAN, await h.say(ALICE, "a"), "/award Кіт")
    cmd = await h.say(ALICE, "/myachievements")
    shown = h.last_public()  # public: it's theirs to show
    assert not cmd.deleted
    assert shown.kind == "photo" and "Alice</b>: «Кіт»" in shown.text


async def test_a04_someone_elses_collection(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    post = await h.say(ALICE, "a")
    await h.reply(DAN, post, "/award Нічна сова")
    await h.reply(BOB, post, "/achievements")
    assert "Alice</b>: «Нічна сова»" in h.last_public().text and "tg://user" not in h.last_public().text


async def test_a04b_someone_by_username_or_name(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    await h.reply(DAN, await h.say(ALICE, "a"), "/award Нічна сова")
    await h.say(BOB, "привіт")
    for query in ("@alice", "alice", "Alice", "alic", "Alise"):  # username, name, part, a typo
        await h.say(CAROL, f"/achievements {query}")
        assert "Alice</b>: «Нічна сова»" in h.last_public().text, query
    await h.say(CAROL, "/achievements bob")
    assert "Bob</b>: ще немає" in h.last_public().text  # known, no achievements yet
    mark = h.mark()
    await h.say(CAROL, "/achievements Зигмунд")
    assert "Не знайшов" in h.last_private(CAROL).text and not h.public_since(mark)


async def test_a05_animated_sticker_uses_its_thumbnail(make):
    h = await make(ACH)
    await create(h, FOX, "Лис")
    await h.reply(DAN, await h.say(ALICE, "a"), "/award Лис")
    assert h.calls("getFile")[-1]["file_id"] == "FOX_THUMB"


async def test_a06_picture_failure_still_announces(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    module = h.tbot.module("achievements")

    def broken(_card):
        raise OSError("no fonts")

    import writers_bot.modules.achievements.render as render

    original = render.award_card
    render.award_card = broken
    try:
        mark = h.mark()
        await h.reply(DAN, await h.say(ALICE, "a"), "/award Нічна сова")
    finally:
        render.award_card = original
    public = h.public_since(mark)
    assert [m.kind for m in public] == ["text"] and "отримує «Нічна сова»" in public[0].text
    assert module is not None


async def test_a07_private_list_when_there_are_none(make):
    h = await make(ACH)
    await h.dm(ALICE, "/achievements")
    assert "У вас ще немає досягнень" in h.dms(ALICE)[-1].text


# --- dice stickers --------------------------------------------------------------------------------------


def clips(messages):
    """The clips of the stickers the bot sent: their names ("d20_16")."""
    return [m.extra["clip"] for m in messages if m.kind == "sticker"]


def fixed(monkeypatch, *dice, bare=True):
    """Every roll comes out as these dice (sides, value)."""
    monkeypatch.setattr(extras, "throw", lambda f, roll, rng=None: Outcome("x", tuple(dice), bare))


async def test_d01_a_clip_is_uploaded_the_first_time_and_then_sent_by_its_id(make, monkeypatch):
    fixed(monkeypatch, (20, 17))
    h = await make(DICE)
    mark = h.mark()
    await h.say(ALICE, "/d20")
    assert [m.kind for m in h.public_since(mark)] == ["sticker"]  # a bare roll is just the sticker
    assert clips(h.public_since(mark)) == ["d20_17"] and h.fake.uploads == ["d20_17.webm"]
    first = h.fake.messages[-1].extra["sticker"]
    assert not h.calls("createNewStickerSet")  # no pack, ever
    await h.say(ALICE, "/d20")
    await h.say(BOB, "/d20")
    assert h.fake.uploads == ["d20_17.webm"]  # not again: by the file id Telegram gave
    assert h.fake.messages[-1].extra["sticker"] == first
    assert all(read >= 60 for name, read, _ in h.fake.timeouts if name == "sendSticker" and read)  # uploads are slow
    cache = h.tbot.module("dice").cache
    assert cache.file_id("d20_17") == first and cache.is_roll_sticker("u-d20_17")


async def test_d01b_a_refused_file_id_is_uploaded_again(make, monkeypatch):
    fixed(monkeypatch, (20, 17))
    h = await make(DICE)
    await h.say(ALICE, "/d20")
    h.fake.bad_file_ids.add(h.fake.messages[-1].extra["sticker"])  # Telegram no longer takes that id
    mark = h.mark()
    await h.say(ALICE, "/d20")
    assert clips(h.public_since(mark)) == ["d20_17"] and len(h.fake.uploads) == 2
    await h.say(ALICE, "/d20")
    assert len(h.fake.uploads) == 2  # the new id is kept


async def test_d02_bonus_or_caption_adds_the_line(make):
    h = await make(DICE)
    mark = h.mark()
    await h.say(ALICE, "/d20+5 Атака")
    assert [m.kind for m in h.public_since(mark)] == ["sticker", "text"]
    assert "Атака" in h.last_public().text


async def test_d03_one_or_two_stickers_come_on_their_own_more_only_when_asked(make):
    h = await make(DICE)
    kinds = {}
    for text in (
        "/2d6",
        "/d20adv",
        "/3d6",
        "/3d6 -s",
        "/4d6 -s Атака",
        "/8d6",
        "/8d6 -s",
        "/13d6 -s",
        "/3d20",
        "/3d20 -s",
        "/2d6 -q",
        "/d20 -q",
        "/r 1d20+1d13",
    ):
        mark = h.mark()
        await h.say(ALICE, text)
        kinds[text] = [m.kind for m in h.public_since(mark)]
    assert kinds == {
        "/2d6": ["sticker", "text"],  # a pair is one sticker, then the line with the sum
        "/d20adv": ["sticker", "text"],  # both d20s in one sticker, then the line with the kept one
        "/3d6": ["sticker", "text"],
        "/3d6 -s": ["sticker", "text"],
        "/4d6 -s Атака": ["sticker"] * 2 + ["text"],  # 2 + 2
        "/8d6": ["text"],  # three stickers: only when asked for
        "/8d6 -s": ["sticker"] * 3 + ["text"],  # 3 + 3 + 2
        "/13d6 -s": ["text"],  # five stickers: no flood
        "/3d20": ["text"],  # three single stickers
        "/3d20 -s": ["sticker"] * 3 + ["text"],
        "/2d6 -q": ["text"],  # silenced
        "/d20 -q": ["text"],
        "/r 1d20+1d13": ["text"],  # a d13 has no stickers: just the line
    }


async def test_d03b_the_limit_counts_stickers_and_is_configurable(make):
    h = await make({"dice": {**DICE["dice"], "max_stickers": 1}})
    for text, kinds in (("/2d6", ["sticker", "text"]), ("/4d6 -s", ["text"]), ("/d6", ["sticker"])):
        mark = h.mark()
        await h.say(ALICE, text)
        assert [m.kind for m in h.public_since(mark)] == kinds, text


async def test_d04_percentile_and_odd_dice(make):
    h = await make(DICE)
    mark = h.mark()
    await h.say(ALICE, "/d100")
    assert clips(h.public_since(mark))[0].startswith("d100_")
    for sides, even in ((3, "d3_"), (5, "d6_"), (7, "d8_"), (9, "d10_"), (11, "d12_")):
        mark = h.mark()
        await h.say(ALICE, f"/d{sides}")
        assert clips(h.public_since(mark))[0].startswith(even), sides
    mark = h.mark()
    await h.say(ALICE, "/d2")
    assert clips(h.public_since(mark))[0].startswith("d2_")  # the coin
    mark = h.mark()
    await h.say(ALICE, "/2d10")
    assert clips(h.public_since(mark))[0].startswith("d100")  # two d10 are a tens die and a ones die


async def test_d05_stickers_are_off_by_default(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/d20")
    assert h.last_public().kind == "text" and not h.fake.uploads


async def test_d06_which_stickers_a_roll_gets(make):
    h = await make(DICE)
    module = h.tbot.module("dice")
    out = lambda *dice: Outcome("x", tuple(dice))  # noqa: E731
    assert module.pick(Roll("d20+d4", None, "x"), out((20, 7), (4, 1))) == [
        "d20_7",
        "d4n_1",
    ]  # no crit for an addition
    assert module.pick(Roll("d4", None, "x"), out((4, 1))) == ["d4_1"]
    assert module.pick(Roll("d20", "adv", "x"), out((20, 15), (20, 3))) == ["2d20a_3_15"]
    assert module.pick(Roll("d20", "dis", "x"), out((20, 3), (20, 15))) == ["2d20d_3_15"]
    assert module.pick(Roll("3d6", None, "x"), out((6, 5), (6, 1), (6, 5))) == ["3d6_1_5_5"]
    assert module.pick(Roll("2d12", None, "x"), out((12, 9), (12, 4))) == ["2d12_9_4"]  # duality: blue, red
    # Daggerheart's usual roll, a d6 on top: two stickers, so on their own
    assert module.pick(Roll("2d12+d6", None, "x"), out((12, 4), (12, 9), (6, 6))) == ["2d12_4_9", "d6n_6"]
    assert module.pick(Roll("d20", None, "x", False), out((20, 7))) is None


def stranger_sticker(unique_id):
    return {"file_id": "FWD", "file_unique_id": unique_id, "type": "regular", "width": 512, "height": 512,
            "is_animated": False, "is_video": True}  # fmt: skip


async def test_d07_a_roll_sticker_sent_by_a_person_is_a_forward_and_gets_a_warning(make, monkeypatch):
    fixed(monkeypatch, (20, 17))
    h = await make(DICE)
    await h.say(ALICE, "/d20")  # the bot learns what its stickers look like
    mark = h.mark()
    await h.say(BOB, "", sticker=stranger_sticker("u-d20_17"))  # BOB forwarded ALICE's roll
    text = h.public_since(mark)[-1]
    assert "стікер із кидка" in text.text and f"tg://user?id={BOB.id}" in text.text
    mark = h.mark()
    await h.say(BOB, "", sticker=stranger_sticker("somebody-elses"))  # any other sticker is none of its business
    await h.say(BOB, "", BOB.id, sticker=stranger_sticker("u-d20_17"))  # nor is the private chat
    assert not h.public_since(mark)


async def test_d07b_the_forward_setting_off_and_delete(make, monkeypatch):
    fixed(monkeypatch, (20, 17))
    h = await make({**DICE, **FULL})  # a bot with storage has chat settings
    await h.say(ALICE, "/d20")
    settings = h.tbot.services.shared.extra["settings"]
    await settings.set("telegram", str(GROUP), "dice.forwards", "off")
    mark = h.mark()
    await h.say(BOB, "", sticker=stranger_sticker("u-d20_17"))
    assert not h.public_since(mark)
    await settings.set("telegram", str(GROUP), "dice.forwards", "delete")
    copy = await h.say(BOB, "", sticker=stranger_sticker("u-d20_17"))
    assert copy.deleted and "видалено" in h.last_public().text
    h.fake.chats[GROUP].bot_can_delete = False  # no right to delete: the warning instead
    copy = await h.say(BOB, "", sticker=stranger_sticker("u-d20_17"))
    assert not copy.deleted and "стікер із кидка" in h.last_public().text


def stitching(tmp_path, **options):
    """A dice module that stitches, into a folder of this test (never one a test before left files in)."""
    return {"dice": {"stickers": True, "stitch": True, "stitch_folder": str(tmp_path / "stitched"), **options}}


needs_ffmpeg = pytest.mark.skipif(stitch_core.ffmpeg_path() is None, reason="no ffmpeg")


@needs_ffmpeg
async def test_d20_a_roll_of_several_clips_is_one_stitched_sticker_made_once(make, monkeypatch, tmp_path):
    fixed(monkeypatch, (20, 17), (4, 1), bare=False)
    h = await make(stitching(tmp_path))
    mark = h.mark()
    await h.say(ALICE, "/d20+d4")
    assert [m.kind for m in h.public_since(mark)] == ["sticker", "text"]  # one sticker, not two
    assert h.fake.uploads == ["d20_17+d4n_1.webm"]
    await h.say(BOB, "/d20+d4")
    assert h.fake.uploads == ["d20_17+d4n_1.webm"]  # by the file id now
    stitched = next((h.tbot.module("dice").stitcher.folder).glob("*.webm"))
    assert 0 < stitched.stat().st_size <= 256 * 1024


@needs_ffmpeg
async def test_d21_eight_d6_are_two_stickers_of_four_dice(make, monkeypatch, tmp_path):
    fixed(monkeypatch, *[(6, v) for v in (3, 1, 6, 2, 2, 5, 4, 4)], bare=False)
    h = await make(stitching(tmp_path))
    mark = h.mark()
    await h.say(ALICE, "/8d6")
    assert [m.kind for m in h.public_since(mark)] == [
        "sticker",
        "sticker",
        "text",
    ]  # no -s: two stickers come on their own
    assert h.fake.uploads == ["2d6_1_3+2d6_2_6.webm", "2d6_2_5+2d6_4_4.webm"]


async def test_d22_one_clip_alone_is_not_stitched_and_stitching_is_off_by_default(make, monkeypatch, tmp_path):
    fixed(monkeypatch, (6, 5), (6, 2))
    h = await make(stitching(tmp_path))
    await h.say(ALICE, "/2d6")
    assert h.fake.uploads == ["2d6_2_5.webm"]  # a pair is one clip: sent as it is
    fixed(monkeypatch, (20, 17), (4, 1), bare=False)
    off = await make(DICE)
    await off.say(ALICE, "/d20+d4")
    assert off.fake.uploads == ["d20_17.webm", "d4n_1.webm"]  # two stickers, as before


async def test_d23_when_ffmpeg_fails_the_clips_go_one_by_one(make, monkeypatch, tmp_path):
    fixed(monkeypatch, (20, 17), (4, 1), bare=False)
    h = await make(stitching(tmp_path, ffmpeg="/nonexistent/ffmpeg"))
    mark = h.mark()
    await h.say(ALICE, "/d20+d4")
    assert [m.kind for m in h.public_since(mark)] == ["sticker", "sticker", "text"]
    assert h.fake.uploads == ["d20_17.webm", "d4n_1.webm"]


async def test_d12_true_random_dice_draw_from_random_org(make, monkeypatch):
    from writers_bot.core import randomness

    asked = []

    async def fake_random_org(key, nbytes, **_):
        asked.append((key, nbytes))
        return b"\x80" * nbytes  # a d20 shows 17 on these bits

    monkeypatch.setattr(randomness, "random_org", fake_random_org)
    h = await make({"dice": {"randomness": "random.org", "random_org_key": "KEY"}})
    assert asked and asked[0][0] == "KEY"  # the first pool is fetched at start
    await h.say(ALICE, "/d20")
    assert "17" in h.last_public().text
    await h.say(ALICE, "/2d20kh1")
    assert h.last_public().text.count("17") >= 2


async def test_d09_not_a_roll(make):
    h = await make(DICE)
    await h.say(ALICE, "/r дракон")
    assert "Не зрозумів кидок" in h.last_public().text


async def test_d10_stickers_off_by_default(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/d20")
    assert h.last_public().kind == "text" and not h.calls("createNewStickerSet")


async def test_d11_new_notation(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/r 4d6kh3 Сила")
    text = h.last_public().text
    assert "Сила" in text and "4d6kh3" in text and text.count("<s>") == 1


async def test_o09_only_replies_answer_a_step(make):
    h = await make()
    await h.dm(ALICE, "/start")
    step = h.dms(ALICE)[-1]
    await h.dm(ALICE, "привіт, а що вміє бот?")  # not a reply: not a work's name
    assert not await h.tbot.services.works.for_person(await h.tbot.people.person_id("telegram", str(ALICE.id)))
    await h.dm(ALICE, "Роман", reply_to=step)
    assert "2/3." in h.dms(ALICE)[-1].text


async def test_o08_works_menu_question_wins_over_a_waiting_step(make):
    h = await make()
    await h.dm(ALICE, "/start")  # step 1 waits for a name
    await h.dm(ALICE, "/works")
    await h.press(ALICE, h.dms(ALICE)[-1], "+ Новий твір")
    await h.dm(ALICE, "Есе", reply_to=h.dms(ALICE)[-1])
    assert "Есе" in h.dms(ALICE)[-1].text and "Рахується" in h.dms(ALICE)[-1].text  # the menu's work card


# --- /help and the command menus, per chat and person --------------------------------------------------


ALL = {"prompts": {"source": "db"}, "achievements": {}, "replies": {}, "dice": {}, **FULL}


async def test_h01_member_help_is_private_and_without_admin_lines(make):
    h = await make(ALL)
    mark = h.mark()
    await h.say(ALICE, "/help")
    text = h.last_private(ALICE).text
    assert not h.public_since(mark)
    for part in ("Промпти", "Спринти", "/sprint", "/progress", "/works", "/achievements", "/d20"):
        assert part in text, part
    assert "Для адмінів чату" not in text and "/settings" not in text and "/award" not in text


async def test_h02_chat_admin_sees_their_section(make):
    h = await make(ALL)
    await h.say(DAN, "/help")
    text = h.last_private(DAN).text
    assert "Налаштування чату" in text and "/settings" in text and "/award" in text and "/replies" in text
    assert "Для адмінів бота" not in text
    await h.press(DAN, h.last_private(DAN), "Досягнення")
    assert "/award" in h.last_private(DAN).text and "(адміни чату)" in h.last_private(DAN).text


async def test_h03_features_off_in_this_chat_leave_help_and_menu(make):
    h = await make(ALL)
    settings = h.tbot.services.shared.extra["settings"]
    await settings.set("telegram", str(GROUP), "sprints.top", False)
    await settings.set("telegram", str(GROUP), "achievements.enabled", False)
    await settings.set("telegram", str(GROUP), "sprints.default_length", 25)
    await h.say(ALICE, "/help")
    card = h.last_private(ALICE)
    assert "/top" not in card.text and "/achievements" not in card.text
    assert "Досягнення" not in card.buttons()
    await h.press(ALICE, card, "Спринти і прогрес")
    assert "тут за замовчуванням 25 хв" in card.text and "/top" not in card.text
    chat_menus = [c for c in h.calls("setMyCommands") if (c.get("scope") or {}).get("chat_id") == GROUP]
    members = next(c for c in reversed(chat_menus) if c["scope"]["type"] == "chat")
    names = {c["command"] for c in members["commands"]}
    assert "top" not in names and "achievements" not in names and "sprint" in names
    await settings.set("telegram", str(GROUP), "sprints.top", None)
    await settings.set("telegram", str(GROUP), "achievements.enabled", None)
    await settings.set("telegram", str(GROUP), "sprints.default_length", None)
    assert any((c.get("scope") or {}).get("chat_id") == GROUP for c in h.calls("deleteMyCommands"))


async def test_h04_bot_wide_menus(make):
    h = await make(ALL)
    calls = {c["scope"]["type"]: {x["command"] for x in c["commands"]} for c in h.calls("setMyCommands")}
    assert "settings" in calls["all_chat_administrators"] and "settings" not in calls["all_group_chats"]
    assert "award" in calls["all_chat_administrators"] and "export" in calls["all_private_chats"]
    assert "chats" in calls["chat"]  # the super-admin's private menu


async def test_h05_super_admin_help_in_private(make):
    h = await make(ALL)
    await h.dm(ADMIN, "/help")
    text = h.dms(ADMIN)[-1].text
    assert "Для адмінів бота" in text and "/broadcast" in text and "/review" in text
    assert "Ваші дані" in text and "/export" in text


async def test_h06_categories_in_help(make):
    h = await make(ALL)
    await h.say(DAN, "/addprompt жахи Хтось стукає у двері")
    await h.say(ALICE, "/help")
    card = h.last_private(ALICE)
    await h.press(ALICE, card, "Промпти")
    assert "[категорія: " in card.text and "жахи" in card.text


async def test_h07_help_is_generated_with_a_submenu_per_module(make):
    h = await make(ALL)
    await h.dm(ALICE, "/help")
    card = h.dms(ALICE)[-1]
    assert sorted(card.buttons()) == sorted(["Промпти", "Спринти і прогрес", "Кубики", "Досягнення", "Ваші дані"])
    assert "!промпт" in card.text  # the Ukrainian way is what the first page asks for
    await h.press(ALICE, card, "Кубики")
    for line in ("<b>/d4</b> (!к4)", "<b>/d12</b> (!к12)", "<b>/d100</b> (!к100)", "<b>/d2</b> (!к2)", "/r 4d6kh3"):
        assert line in card.text, line
    assert card.buttons() == ["← Назад"]
    await h.press(ALICE, card, "← Назад")
    assert "Бот для письменників" in card.text and "Кубики" in card.buttons()
    await h.press(ALICE, card, "Спринти і прогрес")
    assert "<b>/sprint [хвилин] [затримка]</b> (!спринт) — Почати спринт" in card.text
    other = await h.press(BOB, card, "← Назад")  # somebody else's buttons are not for them
    assert card.buttons() == ["← Назад"] and other


async def test_h08_every_die_is_a_command_in_the_menu_and_works_as_a_ukrainian_word(make):
    h = await make(ALL)
    private = next(c for c in h.calls("setMyCommands") if c["scope"]["type"] == "all_private_chats")
    menu = {c["command"]: c["description"] for c in private["commands"]}
    assert {f"d{n}" for n in (2, 3, 4, 6, 8, 10, 12, 20, 100)} <= set(menu) and menu["d12"] == "Кинути d12"
    for text in ("!к12", "/к12", "!к20+5 Атака", "!2к6"):
        await h.say(ALICE, text)
        assert "Результат" in h.last_public().text or "Атака" in h.last_public().text, text
    assert "d12" in h.last_public().text or "2d6" in h.last_public().text


async def test_h09_ukrainian_commands_start_with_an_exclamation_mark_or_a_slash(make):
    h = await make(ALL)
    await h.say(ALICE, "!допомога")
    assert "Бот для письменників" in h.last_private(ALICE).text
    await h.say(ALICE, "/допомога")
    assert "Бот для письменників" in h.last_private(ALICE).text


async def test_h10_the_dice_commands_option_leaves_the_die_commands_out_of_the_menu(make):
    h = await make({"prompts": {}, "sprints": {}, "dice": {"commands": False}}, admin=False)
    await h.say(ALICE, "/help")
    card = h.last_public()  # a bot without the admin tools answers /help in public, as v2 did
    assert "Промпти" in card.buttons() and "/sprint" in card.text and "Для адмінів" not in card.text
    assert all("d20" not in {x["command"] for x in c["commands"]} for c in h.calls("setMyCommands"))
    await h.say(ALICE, "/d20")  # the roll itself works
    assert "Результат" in h.last_public().text


async def test_h11_a_bang_that_is_not_a_command_reaches_the_other_handlers(make):
    h = await make(ALL)
    await h.say(ALICE, "!привіт усім")  # no such command: nobody answers, and nothing breaks
    await h.say(ALICE, "!r 2d6 Атака")  # a roll the alias handler must not take
    assert "Атака" in h.last_public().text


# --- the admin tools (v4.2 suggestions) -------------------------------------------------------------------


async def test_s01_settings_card_in_sections(make):
    h = await make(ALL)
    await h.say(DAN, "/settings")
    card = h.last_private(DAN)
    assert card.buttons()[:4] == ["Спринти", "Звіти й приватність", "Промпти й відповіді", "Привітання й досягнення"]
    await h.press(DAN, card, "Звіти й приватність")
    assert any(b.startswith("Прибирати звіти") for b in card.buttons()) and "← Назад" in card.buttons()
    await h.press(DAN, card, "Прибирати звіти: увімкнено")
    assert "Прибирати звіти: вимкнено" in card.buttons()  # stays in the section
    await h.press(DAN, card, "← Назад")
    assert "Звіти й приватність · змінено: 1" in card.buttons()


async def test_s02_reply_becomes_the_answer(make):
    h = await make(ALL)
    answer = await h.say(DAN, "Ти — <b>фан</b>!\nДругий рядок")
    await h.reply(DAN, answer, "/replies add /imafan")
    assert "Додано відповідь на /imafan" in h.last_private(DAN).text
    await h.say(ALICE, "/imafan")
    assert "Другий рядок" in h.last_public().text


async def test_s03_accept_and_share(make):
    h = await make(ALL)
    await h.say(DAN, "/addprompt жахи Хтось стукає")
    await h.say(ALICE, "/suggest жахи Тінь без господаря")
    await h.say(DAN, "/suggestions")
    card = h.last_private(DAN)
    answer = await h.press(DAN, card, "Прийняти й запропонувати всім")
    assert "запропоновано для всіх" in answer["text"]
    await h.dm(ADMIN, "/review")
    assert "Тінь без господаря" in h.dms(ADMIN)[-1].text


async def test_s04_misspelt_category_is_asked_about(make):
    h = await make(ALL)
    await h.say(DAN, "/addprompt Комедія Кіт пише роман")
    await h.say(ALICE, "/suggest коммедія Пес редагує")
    await h.say(DAN, "/suggestions")
    card = h.last_private(DAN)
    await h.press(DAN, card, "Прийняти")
    assert "«коммедія» схожа на «Комедія»" in card.text
    await h.press(DAN, card, "До «Комедія»")
    prompts = h.tbot.module("prompts")
    own = (await prompts.sets(GROUP))[0]
    moved = next(p for p in await prompts.db.by_status(own) if p.text == "Пес редагує")
    assert moved.category.label == "Комедія"  # into the existing category, not a new misspelt one
    for _ in range(10):
        await h.say(BOB, "/prompt Комедія")
        if "Пес редагує" in h.last_public().text:
            break
    assert "Пес редагує" in h.last_public().text


async def test_s05_case_only_difference_needs_no_question(make):
    h = await make(ALL)
    await h.say(DAN, "/addprompt Комедія Кіт пише роман")
    await h.say(ALICE, "/suggest комедія Пес редагує")
    await h.say(DAN, "/suggestions")
    card = h.last_private(DAN)
    answer = await h.press(DAN, card, "Прийняти")
    assert answer["text"] == "Прийнято" and "схожа" not in card.text


async def test_s06_communities_with_buttons(make):
    h = await make(ALL)
    h.fake.chats[-1002] = Chat(-1002, title="Second")
    await h.say(DAN, "привіт")  # the chats get known to the bot
    await h.say(DAN, "привіт", chat=-1002)
    await h.dm(ADMIN, "/community")
    card = h.dms(ADMIN)[-1]
    await h.press(ADMIN, card, "+ Нова спільнота")
    await h.dm(ADMIN, "Клуб", reply_to=h.dms(ADMIN)[-1])
    assert "Клуб" in card.text and "Чатів ще немає" in card.text
    await h.press(ADMIN, card, "+ Додати чат")
    await h.press(ADMIN, card, "Writers")
    assert "Writers" in card.text
    communities = h.tbot.services.shared.extra["communities"]
    assert (await communities.of_chat("telegram", str(GROUP))).name == "Клуб"
    await h.press(ADMIN, card, "✕ Writers")
    assert await communities.of_chat("telegram", str(GROUP)) is None
    await h.press(ADMIN, card, "Видалити")
    await h.press(ADMIN, card, "Так, видалити")
    assert not await communities.all()
