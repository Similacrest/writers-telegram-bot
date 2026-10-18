"""Round 4 of the user workflows (v4.2): pausing and switching works, the split tip, explicit % corrections,
getting started, achievement pictures and the dice stickers."""

import os
from pathlib import Path

import pytest
from fake_telegram import Chat
from harness import ADMIN, ALICE, BOB, CAROL, DAN, GROUP, Harness
from telegram.error import TimedOut

from writers_bot.core.extras import Outcome, Roll
from writers_bot.modules.dice import telegram as dice_telegram
from writers_bot.modules.dice.telegram import clip_name, manifest

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


def clip(sticker_id):
    """The sticker of a message ("dice1_v3_by_writers_test_bot#17") by its place in the pack: its name, "d20_16"."""
    pack, number = sticker_id.rsplit("#", 1)
    return clip_name(manifest()[f"pack{pack[4]}"][int(number) - 1])


def clips(messages):
    return [clip(m.extra["sticker"]) for m in messages if m.kind == "sticker"]


async def ready_packs(h):
    module = h.tbot.module("dice")
    if module._packs is not None:
        await module._packs
    return module


async def test_d01_two_packs_made_once_and_a_bare_roll_is_a_sticker(make):
    h = await make(DICE)
    await ready_packs(h)
    created = h.calls("createNewStickerSet")
    assert len(created) == 2 and {int(c["user_id"]) for c in created} == {ADMIN.id}
    for name in ("dice1_v3_by_writers_test_bot", "dice2_v3_by_writers_test_bot"):
        assert len(h.fake.sticker_sets[name]) == 120  # 50 at first, the rest added
    mark = h.mark()
    await h.say(ALICE, "/d20")
    sent = h.public_since(mark)
    assert [m.kind for m in sent] == ["sticker"]
    assert clips(sent)[0].startswith("d20")


async def test_d02_bonus_or_caption_adds_the_line(make):
    h = await make(DICE)
    await ready_packs(h)
    mark = h.mark()
    await h.say(ALICE, "/d20+5 Атака")
    assert [m.kind for m in h.public_since(mark)] == ["sticker", "text"]
    assert "Атака" in h.last_public().text


async def test_d03_one_or_two_dice_get_stickers_more_only_when_asked(make):
    h = await make(DICE)
    await ready_packs(h)
    kinds = {}
    for text in (
        "/2d6",
        "/d20adv",
        "/3d6",
        "/3d6 -s",
        "/4d6 -s Атака",
        "/5d6 -s",
        "/2d6 -q",
        "/d20 -q",
        "/r 1d20+1d13",
    ):
        mark = h.mark()
        await h.say(ALICE, text)
        kinds[text] = [m.kind for m in h.public_since(mark)]
    assert kinds == {
        "/2d6": ["sticker", "sticker", "text"],
        "/d20adv": ["sticker", "sticker", "text"],  # both throws, then the line with the kept one
        "/3d6": ["text"],
        "/3d6 -s": ["sticker"] * 3 + ["text"],
        "/4d6 -s Атака": ["sticker"] * 4 + ["text"],
        "/5d6 -s": ["text"],  # more than 4 dice: no flood
        "/2d6 -q": ["text"],  # silenced
        "/d20 -q": ["text"],
        "/r 1d20+1d13": ["text"],  # a d13 has no stickers: just the line
    }


async def test_d03b_the_limit_is_configurable(make):
    h = await make({"dice": {"stickers": True, "max_stickers": 1}})
    await ready_packs(h)
    for text in ("/2d6", "/2d6 -s"):
        mark = h.mark()
        await h.say(ALICE, text)
        assert [m.kind for m in h.public_since(mark)] == ["text"]
    mark = h.mark()
    await h.say(ALICE, "/d6")
    assert [m.kind for m in h.public_since(mark)] == ["sticker"]


async def test_d04_percentile_and_odd_dice(make):
    h = await make(DICE)
    await ready_packs(h)
    mark = h.mark()
    await h.say(ALICE, "/d100")
    assert clips(h.public_since(mark))[0].startswith("d100_")
    for sides, even in ((3, "d4_"), (5, "d6_"), (7, "d8_"), (9, "d10_"), (11, "d12")):
        mark = h.mark()
        await h.say(ALICE, f"/d{sides}")
        assert clips(h.public_since(mark))[0].startswith(even), sides
    mark = h.mark()
    await h.say(ALICE, "/d2")
    assert clips(h.public_since(mark))[0].startswith("d2_")  # the coin


async def test_d05_no_owner_no_stickers(make):
    h = await make(DICE, super_admins=frozenset())
    await ready_packs(h)
    assert not h.calls("createNewStickerSet")
    mark = h.mark()
    await h.say(ALICE, "/d20")
    assert [m.kind for m in h.public_since(mark)] == ["text"]


async def test_d06_which_stickers_a_roll_gets(make):
    h = await make(DICE)
    module = await ready_packs(h)
    out = lambda *dice: Outcome("x", tuple(dice))  # noqa: E731
    assert module.pick(Roll("d20+d4", None, "x"), out((20, 7), (4, 1))) == [
        "d20_7",
        "d4n_1",
    ]  # no crit for an addition
    assert module.pick(Roll("d4", None, "x"), out((4, 1))) == ["d4_1"]
    assert module.pick(Roll("d20", "adv", "x"), out((20, 3), (20, 15))) == ["d20_3", "d20a_15"]
    assert module.pick(Roll("d20", "dis", "x"), out((20, 3), (20, 15))) == ["d20d_3", "d20_15"]
    assert module.pick(Roll("2d12", None, "x"), out((12, 9), (12, 4))) == ["d12ba_9", "d12r_4"]  # duality, blue larger
    # Daggerheart's usual roll, a d6 on top: three dice, so only when asked for
    assert module.pick(Roll("2d12+d6", None, "x"), out((12, 4), (12, 9), (6, 6))) is None
    assert module.pick(Roll("2d12+d6", None, "x", True), out((12, 4), (12, 9), (6, 6))) == [
        "d12b_4",
        "d12rd_9",
        "d6n_6",
    ]
    assert module.pick(Roll("d20", None, "x", False), out((20, 7))) is None


async def test_d07_a_pack_put_out_of_order_is_made_again(make):
    h = await make(DICE)
    module = await ready_packs(h)
    name = module.pack_name("pack1")
    for sticker in h.fake.sticker_sets[name]:
        sticker["emoji"] = "🎲"  # not the die markers the manifest has, as if the owner had shuffled the pack
    before = len(h.calls("createNewStickerSet"))
    await module._prepare("pack1", manifest()["pack1"], ADMIN.id)
    assert h.calls("deleteStickerSet")[-1]["name"] == name
    assert len(h.calls("createNewStickerSet")) == before + 1


async def test_d08_existing_packs_are_reused_short_ones_finished(make, tmp_path, clock):
    h = await make(DICE)
    await ready_packs(h)
    name = "dice2_v3_by_writers_test_bot"
    del h.fake.sticker_sets[name][80:]  # an interrupted upload, as live: 80 of 120
    h2 = await Harness.create(tmp_path / "again", clock, DICE)
    h2.fake.sticker_sets = h.fake.sticker_sets
    await ready_packs(h2)
    assert not h2.calls("createNewStickerSet") and not h2.calls("deleteStickerSet")
    assert len(h2.calls("addStickerToSet")) == 40
    assert len(h2.fake.sticker_sets[name]) == 120
    assert len(h2.tbot.module("dice").stickers) == len({clip_name(r) for rows in manifest().values() for r in rows})
    await h2.close()


async def test_d10_uploads_get_long_timeouts(make):
    h = await make(DICE)
    await ready_packs(h)
    uploads = [t for t in h.fake.timeouts if t[0] in ("createNewStickerSet", "addStickerToSet")]
    assert uploads and all(read >= 300 and write >= 300 for _, read, write in uploads)


async def test_d11_an_upload_that_times_out_but_went_through_is_not_repeated(make, monkeypatch):
    monkeypatch.setattr(dice_telegram, "SETTLE_DELAY", 0)
    h = await make(DICE)
    module = await ready_packs(h)
    name = module.pack_name("pack1")
    del h.fake.sticker_sets[name]
    h.fake.time_out_after = {"createNewStickerSet": 1, "addStickerToSet": 2}  # as live: Telegram keeps going
    adds = len(h.calls("addStickerToSet"))
    await module._prepare("pack1", manifest()["pack1"], ADMIN.id)
    assert len(h.fake.sticker_sets[name]) == 120
    assert len(h.calls("addStickerToSet")) - adds == 70  # each sticker once
    assert [s["file_id"] for s in h.fake.sticker_sets[name]] == [f"{name}#{i}" for i in range(1, 121)]


async def test_d12_an_upload_that_was_lost_stops_and_the_next_start_finishes(make, monkeypatch):
    monkeypatch.setattr(dice_telegram, "SETTLE_DELAY", 0)
    h = await make(DICE)
    module = await ready_packs(h)
    name = module.pack_name("pack2")
    del h.fake.sticker_sets[name]
    h.fake.time_out_before = {"addStickerToSet": 1}  # the 51st sticker never arrives
    with pytest.raises(TimedOut):
        await module._prepare("pack2", manifest()["pack2"], ADMIN.id)
    assert len(h.fake.sticker_sets[name]) == 50
    await module._prepare("pack2", manifest()["pack2"], ADMIN.id)
    assert len(h.fake.sticker_sets[name]) == 120
    assert not h.calls("deleteStickerSet")


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
    assert "Для адмінів чату" in text and "/settings" in text and "/award" in text and "/replies" in text
    assert "Для адмінів бота" not in text


async def test_h03_features_off_in_this_chat_leave_help_and_menu(make):
    h = await make(ALL)
    settings = h.tbot.services.shared.extra["settings"]
    await settings.set("telegram", str(GROUP), "sprints.top", False)
    await settings.set("telegram", str(GROUP), "achievements.enabled", False)
    await settings.set("telegram", str(GROUP), "sprints.default_length", 25)
    await h.say(ALICE, "/help")
    text = h.last_private(ALICE).text
    assert "/top" not in text and "/achievements" not in text and "тут за замовчуванням 25 хв" in text
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
    assert "з однієї: " in h.last_private(ALICE).text and "жахи" in h.last_private(ALICE).text


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
