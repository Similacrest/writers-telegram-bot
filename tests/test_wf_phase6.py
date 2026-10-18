"""Round 4 of the user workflows (v4.2): pausing and switching works, the split tip, explicit % corrections,
getting started, achievement pictures and the dice stickers."""

import os
from pathlib import Path

import pytest
from harness import ADMIN, ALICE, BOB, DAN, GROUP, Harness

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
    await h.dm(ALICE, "Роман")
    step = h.dms(ALICE)[-1]
    assert "2/3. Ціль для «Роман»" in step.text
    await h.dm(ALICE, "500 на день")
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
    await h.dm(ALICE, "Роман")
    await h.dm(ALICE, "багато")
    assert "Не зрозумів ціль" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "3000 на тиждень")
    assert "3/3." in h.dms(ALICE)[-1].text


async def test_o04_later_and_continue_from_profile(make):
    h = await make()
    await h.dm(ALICE, "/start")
    await h.dm(ALICE, "Роман")
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
    assert board.kind == "photo" and "«Кіт» (2)" in board.text and "/achievements мої" in board.text


async def test_a03_my_collection_in_the_group(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    await create(h, CAT, "Кіт")
    await h.reply(DAN, await h.say(ALICE, "a"), "/award Кіт")
    await h.say(ALICE, "/achievements мої")
    shown = h.last_public()
    assert shown.kind == "photo" and "Alice</b>: «Кіт»" in shown.text


async def test_a04_someone_elses_collection(make):
    h = await make(ACH)
    await create(h, OWL, "Нічна сова")
    post = await h.say(ALICE, "a")
    await h.reply(DAN, post, "/award Нічна сова")
    await h.reply(BOB, post, "/achievements")
    assert "Alice</b>: «Нічна сова»" in h.last_public().text and "tg://user" not in h.last_public().text


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


async def ready_packs(h):
    module = h.tbot.module("dice")
    if module._packs is not None:
        await module._packs
    return module


async def test_d01_packs_made_once_and_a_bare_roll_is_a_sticker(make):
    h = await make(DICE)
    await ready_packs(h)
    created = h.calls("createNewStickerSet")
    assert len(created) == 7 and {int(c["user_id"]) for c in created} == {ADMIN.id}
    assert len(h.fake.sticker_sets["d100_by_writers_test_bot"]) == 100  # 50 at first, the rest added
    mark = h.mark()
    await h.say(ALICE, "/d20")
    sent = h.public_since(mark)
    assert [m.kind for m in sent] == ["sticker"]
    assert sent[0].extra["sticker"].startswith("d20_by_writers_test_bot#")


async def test_d02_bonus_or_caption_adds_the_line(make):
    h = await make(DICE)
    await ready_packs(h)
    mark = h.mark()
    await h.say(ALICE, "/d20+5 Атака")
    assert [m.kind for m in h.public_since(mark)] == ["sticker", "text"]
    assert "Атака" in h.last_public().text


async def test_d03_several_dice_are_text(make):
    h = await make(DICE)
    await ready_packs(h)
    mark = h.mark()
    await h.say(ALICE, "/2d6")
    assert [m.kind for m in h.public_since(mark)] == ["text"]


async def test_d04_percentile(make):
    h = await make(DICE)
    await ready_packs(h)
    await h.say(ALICE, "/d100")
    assert h.last_public().extra["sticker"].startswith("d100_by_writers_test_bot#")


async def test_d05_no_owner_no_stickers(make):
    h = await make(DICE, super_admins=frozenset())
    await ready_packs(h)
    assert not h.calls("createNewStickerSet")
    mark = h.mark()
    await h.say(ALICE, "/d20")
    assert [m.kind for m in h.public_since(mark)] == ["text"]


async def test_d06_existing_packs_are_reused_incomplete_ones_remade(make, tmp_path, clock):
    h = await make(DICE)
    await ready_packs(h)
    h.fake.sticker_sets["d6_by_writers_test_bot"].pop()  # a broken upload
    h2 = await Harness.create(tmp_path / "again", clock, DICE)
    h2.fake.sticker_sets = h.fake.sticker_sets
    await ready_packs(h2)
    assert [c["name"] for c in h2.calls("createNewStickerSet")] == ["d6_by_writers_test_bot"]
    assert h2.calls("deleteStickerSet")
    await h2.close()


async def test_d07_not_a_roll(make):
    h = await make(DICE)
    await h.say(ALICE, "/r дракон")
    assert "Не зрозумів кидок" in h.last_public().text


async def test_d08_stickers_off_by_default(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/d20")
    assert h.last_public().kind == "text" and not h.calls("createNewStickerSet")


async def test_d09_new_notation(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/r 4d6kh3 Сила")
    text = h.last_public().text
    assert "Сила" in text and "4d6kh3" in text and text.count("<s>") == 1


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
