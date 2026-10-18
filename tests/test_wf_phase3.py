"""Round 1 of the user workflows: the sprint redesign (PLAN phase 3) as people would use it.

Each test is one workflow. Set ``WF_TRANSCRIPTS=<dir>`` to write what everyone saw to files, for review.
"""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from fake_telegram import Chat
from harness import ADMIN, ALICE, BOB, CAROL, DAN, GROUP, Harness

from writers_bot.core.models import Unit

FULL = {"sprints": {"reports": True, "works": True, "percent": True}}
NO_WORKS = {"sprints": {"reports": True}}
V2 = {"prompts": {}, "wordcount": {}, "sprints": {}}


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
        made.append(h)
        return h

    yield build
    for h in made:
        await h.close()
    save_transcripts(made, request.node.name)


# --- the v2 sprint is unchanged by default -------------------------------------------------------------


async def test_w01_v2_sprint_unchanged(make):
    h = await make(V2)
    await h.say(ALICE, "/sprint 20 2")
    planned = h.last_public()
    assert "почнеться за 2 хвилини" in planned.text and planned.buttons() == ["Долучитися", "Вийти/скасувати"]
    await h.press(BOB, planned, "Долучитися")
    await h.advance(2 * 60)
    started = h.last_public()
    assert planned.deleted and "розпочався" in started.text and "Bob" in started.text
    await h.advance(20 * 60)
    finished = h.last_public()
    assert "Спринт завершено" in finished.text and finished.buttons() == ["Ще раз"]
    assert "Звіт" not in finished.text
    mark = h.mark()
    await h.reply(BOB, finished, "500")  # v2 has no reports: the reply is just a reply
    assert not h.public_since(mark) and not h.mine(BOB)
    await h.close()


async def test_w02_v2_stop_early(make):
    h = await make(V2)
    await h.say(ALICE, "/sprint 20 0")
    msg = h.last_public()
    await h.advance(5 * 60)
    answer = await h.press(ALICE, msg, "Вийти/скасувати")
    assert answer["text"] == "Спринт зупинено"
    assert "зупинено" in msg.text and msg.buttons() == ["Ще раз"]
    await h.close()


# --- joining and reporting ----------------------------------------------------------------------------


async def test_w03_two_writers_report_by_reply(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 25 0")
    await h.press(BOB, h.sprint_message(), "Долучитися")
    await h.advance(25 * 60)
    board = h.board()
    assert "Ще пишуть" in board.text and "Alice" in board.text
    a = await h.reply(ALICE, board, "450")
    b = await h.reply(BOB, board, "+700")
    assert a.deleted and b.deleted  # report replies go once recorded
    text = h.board().text
    assert text.index("Bob") < text.index("Alice")  # high to low
    assert "1." not in text and "🥇" not in text  # no medals, no ranks
    assert "Разом: <b>1150 слів</b>" in text
    assert "Записано: 450 слів" in h.mine(ALICE)[-1].text
    await h.close()


async def test_w04_replies_from_outsiders_are_left_alone(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    mark = h.mark()
    m = await h.reply(CAROL, h.board(), "500")
    assert not m.deleted and not m.reactions and not h.mine(CAROL) and not h.public_since(mark)
    assert "Carol" not in h.board().text
    await h.close()


async def test_w05_ordinary_replies_are_not_reports(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    for text in ("дякую всім!", "500 нарешті, ура", "2 сцени дописала"):
        m = await h.reply(ALICE, h.board(), text)
        assert not m.deleted, text
    assert "Ще пишуть" in h.board().text
    await h.close()


async def test_w06_report_button_asks_and_cleans_up(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.press(ALICE, h.board(), "Звітувати")
    prompt = h.last_public()
    assert "скільки написали" in prompt.text
    answer = await h.reply(ALICE, prompt, "=12000")
    assert prompt.deleted and answer.deleted
    assert "Запам'ятав загальну кількість" in h.mine(ALICE)[-1].text
    await h.press(ALICE, h.board(), "Звітувати")
    await h.reply(ALICE, h.last_public(), "=12400")
    assert "400 слів" in h.board().text
    await h.close()


async def test_w07_done_and_failed_buttons(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.press(BOB, h.sprint_message(), "Долучитися")
    await h.advance(10 * 60)
    assert (await h.press(ALICE, h.board(), "✓ Готово"))["text"] == "Записано: готово ✓"
    assert (await h.press(BOB, h.board(), "✗ Не вийшло"))["text"] == "Записано: не вийшло ✗"
    assert (await h.press(CAROL, h.board(), "✓ Готово"))["text"] == "Ви не у спринті"
    text = h.board().text
    assert "✓ <a" in text and "Alice" in text.split("✓")[1] and "✗" in text
    await h.close()


async def test_w08_hidden_numbers(make):
    h = await make(NO_WORKS)
    await h.dm(BOB, "/profile")
    card = h.dms(BOB)[-1]
    await h.press(BOB, card, "Налаштування")
    await h.press(BOB, card, "Мої числа: видно")
    assert "Мої числа: приховано" in card.buttons()
    await h.say(ALICE, "/sprint 10 0")
    await h.press(BOB, h.sprint_message(), "Долучитися")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "300")
    await h.reply(BOB, h.board(), "900")
    text = h.board().text
    assert "900" not in text and "✓" in text.split("Слова")[1] and "Bob" in text
    assert "Разом" not in text  # one visible number: no total that would give the hidden one away
    await h.advance(13 * 3600)
    await h.say(CAROL, "/top")
    top = h.last_public().text
    assert "Alice" in top and "Bob" not in top
    await h.close()


# --- works --------------------------------------------------------------------------------------------


async def add_works(h, user, *names, unit_percent=()):
    await h.dm(user, "/start works")
    for name in names:
        menu = h.dms(user)[-1]
        await h.press(user, menu, "+ Новий твір")
        question = h.dms(user)[-1]
        await h.dm(user, name, reply_to=question)
        card = h.dms(user)[-1]
        if name in unit_percent:
            await h.press(user, card, "Слова / %")
        await h.press(user, card, "← Назад")


async def test_w09_works_card_and_ambiguity(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await h.say(ALICE, "/sprint 15 0")
    card = h.last_private(ALICE)
    assert card.buttons()[:2] == ["✓ Роман", "✓ Есе"]
    assert "Alice</a> — Роман, Есе" in h.sprint_message().text
    await h.advance(15 * 60)
    m = await h.reply(ALICE, h.board(), "500")
    ask = h.last_private(ALICE)
    assert "Куди записати 500" in ask.text and m.deleted
    await h.press(ALICE, ask, "Роман")
    assert "«Роман»: 500 слів" in ask.text
    await h.reply(ALICE, h.board(), "+200 есе")
    assert "700 слів" in h.board().text  # summed per person
    await h.close()


async def test_w10_toggle_a_work_off_for_this_sprint(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Есе")
    await h.say(ALICE, "/sprint 15 0")
    card = h.last_private(ALICE)
    await h.press(ALICE, card, "✓ Есе")
    assert card.buttons()[:2] == ["✓ Роман", "Есе"]
    assert "Alice</a> — Роман" in h.sprint_message().text and "Есе" not in h.sprint_message().text
    await h.advance(15 * 60)
    await h.reply(ALICE, h.board(), "500")  # only one work now: no question
    assert "Записано: «Роман»: 500 слів" in h.mine(ALICE)[-1].text
    await h.close()


async def test_w11_linked_work_counts_itself(make):
    h = await make()
    url = "https://telegra.ph/Roman-10-18"
    links = h.use_links({url: 10_000})
    await add_works(h, ALICE, "Роман")
    menu = h.dms(ALICE)[-1]
    await h.press(ALICE, menu, "Роман")
    card = h.dms(ALICE)[-1]
    await h.press(ALICE, card, "Посилання")
    await h.dm(ALICE, url, reply_to=h.dms(ALICE)[-1])
    assert "10000" in h.dms(ALICE)[-1].text
    await h.say(ALICE, "/sprint 30 0")
    links.counts[url] = 10_850
    await h.advance(30 * 60)
    await h.settle()
    assert "850 слів" in h.board().text  # no report needed
    await h.close()


async def test_w12_late_report_until_next_sprint(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    old_board = h.board()
    await h.advance(3 * 3600)
    await h.reply(ALICE, old_board, "1200")
    assert "1200" in old_board.text
    await h.say(ALICE, "/sprint 10 0")
    m = await h.reply(ALICE, old_board, "1300")
    assert "1300" not in old_board.text
    assert "вже закрито" in h.last_private(ALICE).text and not m.deleted
    await h.close()


async def test_w13_undo(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "5000")  # a typo
    confirm = h.mine(ALICE)[-1]
    await h.press(ALICE, confirm, "Скасувати звіт")
    assert "Звіт скасовано" in confirm.text
    assert "5000 слів" not in h.board().text
    await h.reply(ALICE, h.board(), "500")
    assert "500 слів" in h.board().text
    await h.close()


# --- progress outside sprints ---------------------------------------------------------------------------


async def test_w14_progress_and_share(make):
    h = await make()
    await h.say(ALICE, "/progress +400")
    assert "Записано: 400 слів" in h.mine(ALICE)[-1].text
    mark = h.mark()
    await h.say(ALICE, "/progress")
    panel = h.mine(ALICE)[-1]
    assert "Сьогодні: 400 слів" in panel.text and "Поділитися" in panel.buttons()
    await h.press(ALICE, panel, "Поділитися")
    shared = h.public_since(mark)
    assert len(shared) == 1 and "Alice: <b>400 слів</b> сьогодні" in shared[0].text
    await h.close()


async def test_w15_progress_yesterday_fills_streak(make):
    h = await make()
    await h.say(ALICE, "/progress +300 вчора")
    await h.say(ALICE, "/progress +200")
    await h.dm(ALICE, "/profile")
    assert "Днів поспіль: 2 дні" in h.dms(ALICE)[-1].text
    await h.close()


async def test_w16_progress_during_a_sprint_is_the_report(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 20 0")
    await h.advance(10 * 60)
    await h.say(ALICE, "/progress 350")
    await h.advance(10 * 60)
    assert "350 слів" in h.board().text
    await h.close()


async def test_w17_profile_is_private(make):
    h = await make()
    cmd = await h.say(ALICE, "/profile")
    assert cmd.deleted and not [m for m in h.bot_public() if "Профіль" in m.text]
    card = h.mine(ALICE)[-1]
    assert "Профіль: Alice" in card.text
    assert any(b.startswith("Твори") for b in card.buttons())
    await h.close()


async def test_w18_goal_in_sprint_goes_on_the_board(make):
    h = await make()
    await add_works(h, ALICE, "Роман")
    menu = h.dms(ALICE)[-1]
    await h.press(ALICE, menu, "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Ціль")
    await h.dm(ALICE, "1000 на день", reply_to=h.dms(ALICE)[-1])
    assert "Ціль: 1000 слів на день" in h.dms(ALICE)[-1].text
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "1100")
    assert "Цілі досягнуто: Alice — «Роман»" in h.board().text
    dms_before = len(h.dms(ALICE))
    await h.say(ALICE, "/progress +100")
    assert len(h.dms(ALICE)) == dms_before  # once a day
    await h.close()


async def test_w19_goal_outside_sprint_is_a_dm(make):
    h = await make()
    await add_works(h, ALICE, "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Ціль")
    await h.dm(ALICE, "500", reply_to=h.dms(ALICE)[-1])  # "на день" is the default
    await h.say(ALICE, "/progress +600")
    assert "Ціль виконано: «Роман», 600 з 500 слів на день" in h.dms(ALICE)[-1].text
    assert "Ціль" not in " ".join(m.text for m in h.bot_public())
    await h.close()


async def test_w20_percent_works(make):
    h = await make()
    await add_works(h, ALICE, "План", unit_percent=("План",))
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    await h.reply(ALICE, h.board(), "40%")
    assert "% від плану" in h.board().text and "40%" in h.board().text
    await h.say(ALICE, "/progress 60% план")
    assert "60%" in h.board().text
    await h.say(ALICE, "/progress 150%")
    assert "від 0 до 100" in h.mine(ALICE)[-1].text
    await h.close()


# --- your data ----------------------------------------------------------------------------------------


async def test_w21_export_from_profile(make):
    h = await make()
    await h.say(ALICE, "/progress +400")
    await h.dm(ALICE, "/profile")
    card = h.dms(ALICE)[-1]
    await h.press(ALICE, card, "Налаштування")
    await h.press(ALICE, card, "Мої дані")
    answer = await h.press(ALICE, card, "Отримати файлом")
    assert answer["text"] == "Надіслав файл в особисті повідомлення."
    assert h.fake.documents  # the ZIP went to the private chat
    await h.close()


async def test_w22_export_without_private_chat(make):
    h = await make()
    await h.say(BOB, "/progress +100")
    h.fake.blocked_dm.add(BOB.id)
    await h.say(BOB, "/export")
    assert "особистий чат" in h.mine(BOB)[-1].text
    await h.close()


async def test_w23_forget_me(make):
    h = await make()
    await h.say(ALICE, "/progress +400")
    await h.dm(ALICE, "/forgetme")
    await h.press(ALICE, h.dms(ALICE)[-1], "Видалити")
    assert "Готово" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "/export")
    assert "нічого не збережено" in h.dms(ALICE)[-1].text
    await h.close()


# --- chat situations ----------------------------------------------------------------------------------


async def test_w24_ukrainian_aliases(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/спринт 10 0")
    assert h.sprint() is not None
    await h.say(ALICE, "/прогрес 120")
    await h.advance(10 * 60)
    assert "120 слів" in h.board().text
    await h.say(ALICE, "/профіль")
    assert "Профіль" in h.mine(ALICE)[-1].text
    await h.close()


async def test_w25_forum_topics(make):
    h = await make(NO_WORKS)
    h.fake.chats[GROUP].is_forum = True
    await h.say(ALICE, "/sprint 10 0", thread_id=7)
    await h.say(BOB, "/sprint 10 0", thread_id=9)
    sprints = h.tbot.module("sprints").engine.open_sprints()
    assert {s.key.thread_id for s in sprints} == {"7", "9"}
    await h.close()


async def test_w26_bot_without_admin_rights(make):
    h = await make(NO_WORKS, bot_admin=False)
    await h.say(ALICE, "/sprint 10 0")
    await h.advance(10 * 60)
    mark = h.mark()
    m = await h.reply(ALICE, h.board(), "500")
    assert not m.deleted and m.reactions == ["👍"]
    assert not h.public_since(mark)  # no public confirmation
    assert "500 слів" in h.board().text
    await h.close()


async def test_w27_first_sprint_notice_once(make):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    assert "зберігаються" in h.last_private(ALICE).text
    await h.advance(10 * 60)
    cards = len(h.mine(ALICE))
    await h.press(ALICE, h.board(), "Ще раз")
    assert len(h.mine(ALICE)) == cards  # no card the second time: nothing to pick
    await h.close()


async def test_w28_two_chats_one_linked_work(make):
    h = await make()
    url = "https://telegra.ph/Roman-10-18"
    links = h.use_links({url: 100})
    await add_works(h, ALICE, "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Посилання")
    await h.dm(ALICE, url, reply_to=h.dms(ALICE)[-1])
    other = -1002
    h.fake.chats[other] = Chat(other, title="Second")
    await h.say(ALICE, "/sprint 10 0")
    await h.say(ALICE, "/sprint 10 0", chat=other)
    links.counts[url] = 400
    await h.advance(10 * 60)
    await h.settle()
    boards = [m.text for m in h.bot_public(GROUP) + h.bot_public(other) if "Спринт завершено (" in m.text]
    assert sum("300 слів" in b for b in boards) == 1
    await h.close()


async def test_w29_admin_cancels_someone_elses_sprint(make):
    h = await make(NO_WORKS)
    h.fake.chats[GROUP].roles[DAN.id] = "administrator"
    await h.say(ALICE, "/sprint 10 5")
    assert (await h.press(BOB, h.sprint_message(), "Вийти/скасувати"))["text"] == "Ви не маєте права скасувати спринт"
    assert (await h.press(DAN, h.sprint_message(), "Вийти/скасувати"))["text"] == "Спринт скасовано"
    await h.close()


async def test_w30_hidden_titles_on_the_sprint_message(make):
    h = await make()
    await add_works(h, ALICE, "Роман", "Щоденник")
    await h.press(ALICE, h.dms(ALICE)[-1], "Щоденник")
    await h.press(ALICE, h.dms(ALICE)[-1], "Приховати назву / показати")
    await h.say(ALICE, "/sprint 10 0")
    text = h.sprint_message().text
    assert "Роман, ще 1" in text and "Щоденник" not in text
    await h.close()


async def test_w31_restart_mid_sprint(make, tmp_path, clock):
    h = await make(NO_WORKS)
    await h.say(ALICE, "/sprint 10 0")
    await h.press(BOB, h.sprint_message(), "Долучитися")
    db = h.tbot.storage.path
    await h.close()
    h2 = await Harness.create(db.parent.parent / db.parent.name, clock, NO_WORKS)
    h2.fake.messages = h.fake.messages
    h2.fake._ids = h.fake._ids
    await h2.advance(10 * 60)
    board = h2.board()
    await h2.reply(BOB, board, "700")
    assert "700" in h2.board().text
    await h2.close()


async def test_w32_works_menu_errors(make):
    h = await make()
    await add_works(h, ALICE, "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Роман")
    await h.press(ALICE, h.dms(ALICE)[-1], "Посилання")
    await h.dm(ALICE, "не посилання", reply_to=h.dms(ALICE)[-1])
    assert "не схоже на посилання" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "ні")  # retry: remove the link
    assert "Без посилання" in h.dms(ALICE)[-1].text
    await h.press(ALICE, h.dms(ALICE)[-1], "Ціль")
    await h.dm(ALICE, "багато")
    assert "Не зрозумів ціль" in h.dms(ALICE)[-1].text
    await h.dm(ALICE, "5000 на тиждень")
    assert "5000 слів на тиждень" in h.dms(ALICE)[-1].text
    await h.press(ALICE, h.dms(ALICE)[-1], "Видалити")
    await h.press(ALICE, h.dms(ALICE)[-1], "Так, видалити")
    assert "Творів ще немає" in h.dms(ALICE)[-1].text
    await h.close()


async def test_w33_admin_super_tools_unchanged(make):
    h = await make()
    await h.say(ALICE, "hi")
    await h.dm(ADMIN, "/broadcast all Оголошення")
    confirm = h.dms(ADMIN)[-1]
    assert "Отримувачів" in confirm.text
    await h.press(ADMIN, confirm, "Надіслати")
    await h.settle()
    assert any(m.kind == "copy" for m in h.fake.messages if m.chat_id == GROUP)
    await h.close()


async def test_w34_six_works_is_too_many(make):
    h = await make()
    await add_works(h, ALICE, "А1", "Б2", "В3", "Г4", "Д5")
    menu = h.dms(ALICE)[-1]
    assert "+ Новий твір" not in menu.buttons()
    await h.close()


__all__ = ["Unit", "timedelta"]
