import asyncio
from datetime import timedelta

import pytest

from writers_bot.core.days import bot_day
from writers_bot.core.models import ChatKey, Goal, GoalPeriod, Outcome, SprintStatus, Unit
from writers_bot.core.people import HIDE_NUMBERS, People
from writers_bot.core.reporting import ChatOptions, Reporting, Status
from writers_bot.core.reportparse import parse
from writers_bot.core.sources import find_url
from writers_bot.core.sprint import SprintManager, UserRef
from writers_bot.core.stats import history, top_minutes
from writers_bot.core.works import WorkError, Works

KEY = ChatKey("test", "-100", None)
ALICE = UserRef("1", "Alice")
BOB = UserRef("2", "Bob")


class FakePresenter:
    def __init__(self):
        self.events = []

    async def show(self, sprint, event, origin=None):
        await asyncio.sleep(0)
        self.events.append((sprint.id, event))


class FakeLinks:
    def __init__(self):
        self.counts: dict[str, int] = {}

    async def count(self, url):
        return self.counts[url]


@pytest.fixture
async def env(storage, clock):
    links = FakeLinks()
    engine = SprintManager(storage, FakePresenter(), platform="test", now=clock.now, sleep=clock.sleep)
    people, works = People(storage), Works(storage)
    rep = Reporting(storage, people, works, engine, links, works_enabled=True, now=clock.now)
    engine.after_finish = rep.after_finish
    yield engine, rep, people, works, links
    await engine.shutdown()


def r(text, clock=None):
    return parse(text, find_url, today=bot_day(clock.now()) if clock else None)


OPTS = {"opts": ChatOptions(works=True, percent=True).to_refs()}


async def start(engine, rep, user=ALICE, duration=10, refs=OPTS):
    return await engine.start(KEY, user, duration, 0, prepare=rep.preparer(user), refs=refs)


async def test_unnamed_work_replace_and_add(env, clock, storage):
    engine, rep, _people, _works, _ = env
    sprint = await start(engine, rep)
    res = await rep.report(sprint, ALICE, r("500"))
    assert res.status == Status.OK and res.changes[0].amount == 500
    res = await rep.report(sprint, ALICE, r("450"))  # replaces
    assert res.changes[0].amount == 450
    res = await rep.report(sprint, ALICE, r("+50"))
    assert res.changes[0].amount == 500
    p = sprint.participants["1"]
    assert p.amounts(Unit.WORDS) == 500
    h = await history(storage, p.person_id)
    assert h.words(bot_day(clock.now()), bot_day(clock.now())) == 500


async def test_not_joined_and_planned(env, clock):
    engine, rep, *_ = env
    sprint = await engine.start(KEY, ALICE, 10, 2, prepare=rep.preparer(ALICE), refs=OPTS)
    assert (await rep.report(sprint, ALICE, r("500"))).status == Status.NOT_STARTED
    assert (await rep.report(sprint, BOB, r("500"))).status == Status.NOT_JOINED


async def test_kept_works_attach_and_ambiguity(env, clock):
    engine, rep, people, works, _ = env
    person = await people.person_id("test", "1", "Alice")
    novel = await works.create(person, "Роман", Unit.WORDS)
    essay = await works.create(person, "Есе", Unit.WORDS)
    plan = await works.create(person, "План", Unit.PERCENT)
    sprint = await start(engine, rep)
    assert set(sprint.participants["1"].works) == {novel.id, essay.id, plan.id}

    res = await rep.report(sprint, ALICE, r("500"))
    assert res.status == Status.AMBIGUOUS and {w.id for w in res.candidates} == {novel.id, essay.id}
    res = await rep.report(sprint, ALICE, r("500"), choice={0: novel.id})
    assert res.status == Status.OK and res.changes[0].work_id == novel.id

    res = await rep.report(sprint, ALICE, r("+300 есе 40%"))
    assert [(c.work_id, c.amount) for c in res.changes] == [(essay.id, 300), (plan.id, 40)]
    assert (await rep.report(sprint, ALICE, r("+10 вірш"))).status == Status.UNKNOWN_WORK
    assert (await rep.report(sprint, ALICE, r("150%"))).status == Status.BAD_PERCENT


async def test_toggle_changes_keep(env, clock):
    engine, rep, people, works, _ = env
    person = await people.person_id("test", "1", "Alice")
    novel = await works.create(person, "Роман", Unit.WORDS)
    sprint = await start(engine, rep)
    assert await rep.toggle_work(sprint, "1", novel.id) is False
    assert not (await works.get(novel.id)).keep
    assert await rep.toggle_work(sprint, "1", novel.id) is True
    assert (await works.get(novel.id)).keep


async def test_linked_work_counts_once(env, clock, storage):
    engine, rep, people, works, links = env
    person = await people.person_id("test", "1", "Alice")
    url = "https://telegra.ph/Novel-10-18"
    links.counts[url] = 1000
    novel = await works.create(person, "Роман", Unit.WORDS, url=url)
    await works.set_total(novel.id, 1000)
    sprint = await start(engine, rep)
    await clock.settle()
    # a typed 300 moves the total, so the recount at the end credits only the rest
    await rep.report(sprint, ALICE, r("300"))
    assert (await works.get(novel.id)).total == 1300
    links.counts[url] = 1520
    await clock.advance(10 * 60)
    await clock.settle()
    assert sprint.status == SprintStatus.REPORTING
    assert sprint.participants["1"].works[novel.id].amount == 520
    assert (await works.get(novel.id)).total == 1520
    # the daily recount finds nothing new
    assert await rep.daily_recount() == 0


async def test_two_sprints_share_growth_once(env, clock):
    engine, rep, people, works, links = env
    person = await people.person_id("test", "1", "Alice")
    url = "https://telegra.ph/Novel-10-18"
    links.counts[url] = 100
    novel = await works.create(person, "Роман", Unit.WORDS, url=url)
    await works.set_total(novel.id, 100)
    s1 = await start(engine, rep)
    s2 = await engine.start(ChatKey("test", "-200"), ALICE, 10, 0, prepare=rep.preparer(ALICE), refs=OPTS)
    await clock.settle()
    links.counts[url] = 400
    await clock.advance(10 * 60)
    await clock.settle()
    got = [s.participants["1"].works[novel.id].amount or 0 for s in (s1, s2)]
    assert sorted(got) == [0, 300]


async def test_total_baseline_then_delta(env, clock):
    engine, rep, _people, works, _ = env
    sprint = await start(engine, rep)
    res = await rep.report(sprint, ALICE, r("=5000"))
    assert res.status == Status.BASELINE
    res = await rep.report(sprint, ALICE, r("=5400"))
    assert res.changes[0].amount == 400 and res.changes[0].credited == 400
    res = await rep.report(sprint, ALICE, r("=5300"))  # shrinking: credits 0, lowers the total
    assert res.changes[0].amount == 400
    work = await works.get(res.changes[0].work_id)
    assert work.total == 5300


async def test_outcomes_and_completed(env, clock):
    engine, rep, *_ = env
    sprint = await start(engine, rep)
    await engine.join(KEY, BOB, prepare=rep.preparer(BOB))
    await rep.set_outcome(sprint, ALICE, Outcome.DONE)
    await rep.report(sprint, BOB, r("0"))
    assert sprint.participants["1"].completed and not sprint.participants["2"].completed
    await rep.report(sprint, BOB, r("✗"))
    assert sprint.participants["2"].outcome == Outcome.FAILED


async def test_undo(env, clock, storage):
    engine, rep, _people, works, _ = env
    sprint = await start(engine, rep)
    await rep.report(sprint, ALICE, r("=1000"))
    res = await rep.report(sprint, ALICE, r("+200"))
    work_id = res.changes[0].work_id
    assert (await works.get(work_id)).total == 1200
    assert not await rep.undo(res.undo, "2")  # someone else's
    assert await rep.undo(res.undo, "1")
    assert sprint.participants["1"].works[work_id].amount is None
    assert (await works.get(work_id)).total == 1000
    assert not await rep.undo(res.undo, "1")  # once


async def test_late_reports_until_next_sprint(env, clock):
    engine, rep, *_ = env
    sprint = await start(engine, rep)
    await clock.advance(10 * 60)
    assert sprint.status == SprintStatus.REPORTING
    await clock.advance(3 * 3600)  # still open hours later
    assert (await rep.report(sprint, ALICE, r("700"))).status == Status.OK
    await engine.start(ChatKey("test", "-200"), ALICE, 10, 0, prepare=rep.preparer(ALICE), refs=OPTS)
    assert (await rep.report(sprint, ALICE, r("800"))).status == Status.CLOSED
    await clock.advance(10 * 3600)
    assert sprint.status == SprintStatus.CLOSED


async def test_progress_outside_and_backfill(env, clock, storage):
    _engine, rep, people, works, _ = env
    person = await people.person_id("test", "1", "Alice")
    novel = await works.create(person, "Роман", Unit.WORDS)
    await works.set_goal(novel, Goal(1000, GoalPeriod.DAY))
    res = await rep.progress(ALICE, r("400", clock))
    assert res.status == Status.OK and res.changes[0].credited == 400 and not res.goals
    res = await rep.progress(ALICE, r("+700", clock))
    assert len(res.goals) == 1 and res.goals[0].progress == 1100
    res = await rep.progress(ALICE, r("+100", clock))
    assert not res.goals  # announced once per period
    res = await rep.progress(ALICE, r("+300 вчора", clock))
    assert res.day == bot_day(clock.now()) - timedelta(days=1)
    h = await history(storage, person)
    assert h.streak(bot_day(clock.now())) == 2
    old = parse("+100 01.10", find_url, today=bot_day(clock.now()))
    assert (await rep.progress(ALICE, old)).status == Status.TOO_OLD


async def test_percent_outside_sprint(env, clock):
    engine, rep, people, works, _ = env
    person = await people.person_id("test", "1", "Alice")
    plan = await works.create(person, "План", Unit.PERCENT)
    assert (await rep.progress(ALICE, r("50% план", clock))).status == Status.NO_PERCENT_SPRINT
    sprint = await start(engine, rep)
    await rep.report(sprint, ALICE, r("30%"))
    await clock.advance(10 * 60)
    res = await rep.progress(ALICE, r("60% план", clock))
    assert res.status == Status.OK and sprint.participants["1"].works[plan.id].amount == 60
    res = await rep.progress(ALICE, r("✓ план", clock))
    assert res.status == Status.OK


async def test_hidden_numbers_off_top(env, clock, storage):
    engine, rep, people, _works, _ = env
    bob_person = await people.person_id("test", "2", "Bob")
    await people.set(bob_person, HIDE_NUMBERS, "1")
    sprint = await start(engine, rep, duration=20)
    await engine.join(KEY, BOB, prepare=rep.preparer(BOB))
    await clock.advance(10 * 60)  # Bob gets credit only from when he joined... both joined at start here
    await clock.advance(10 * 60)
    await rep.report(sprint, ALICE, r("500"))
    await rep.report(sprint, BOB, r("700"))
    assert sprint.participants["2"].hide_numbers
    top = await top_minutes(storage, "test", ["-100"], clock.now() - timedelta(days=1))
    assert [(e.name, e.minutes) for e in top] == [("Alice", 20)]


async def test_minutes_from_join(env, clock, storage):
    engine, rep, *_ = env
    sprint = await start(engine, rep, duration=30)
    await clock.advance(20 * 60)
    await engine.join(KEY, BOB, prepare=rep.preparer(BOB))
    await clock.advance(10 * 60)
    await rep.report(sprint, BOB, r("100"))
    assert sprint.minutes_for(sprint.participants["2"]) == 10


async def test_work_limits(env):
    _, _, people, works, _ = env
    person = await people.person_id("test", "1", "Alice")
    for i in range(5):
        await works.create(person, f"Твір {chr(1040 + i)}", Unit.WORDS)
    with pytest.raises(WorkError):
        await works.create(person, "Шостий", Unit.WORDS)
    with pytest.raises(WorkError):
        await works.create(person, "500 слів", Unit.WORDS)


async def test_forget_person(env, clock, storage):
    engine, rep, people, _works, _ = env
    sprint = await start(engine, rep)
    await rep.report(sprint, ALICE, r("500"))
    await clock.advance(10 * 60)
    await clock.advance(13 * 3600)
    person = sprint.participants["1"].person_id
    removed = await people.forget(person)
    assert removed == 1
    assert await people.find("test", "1") is None
    assert await storage.fetchall("SELECT * FROM works") == []


async def test_done_naming_a_work(env, clock):
    engine, rep, people, works, _ = env
    person = await people.person_id("test", "1", "Alice")
    novel = await works.create(person, "Роман", Unit.WORDS)
    await works.set_flag(novel, "keep", False)
    sprint = await start(engine, rep)
    await clock.advance(10 * 60)
    res = await rep.report(sprint, ALICE, r("✓ роман"))
    assert res.status == Status.OK and res.outcome == Outcome.DONE
    assert novel.id in sprint.participants["1"].works and sprint.participants["1"].completed


def test_confirmation_is_never_empty():
    from zoneinfo import ZoneInfo

    from writers_bot.core.board import confirmation
    from writers_bot.core.fmt import HtmlFormatter

    assert confirmation(HtmlFormatter(ZoneInfo("Europe/Kyiv")), [], None) == "Записано."
