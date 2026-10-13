import asyncio
from datetime import timedelta

import pytest

from writers_bot.core.models import ChatKey, SprintStatus
from writers_bot.core.sources import find_url
from writers_bot.core.sprint import (
    InvalidSprintParamsError,
    JoinResult,
    LeaveResult,
    ReportInput,
    ReportKind,
    ReportStatus,
    SprintAlreadyRunningError,
    SprintEvent,
    SprintManager,
    UserRef,
    parse_report,
)

KEY = ChatKey("test", "-100", None)
TOPIC = ChatKey("test", "-100", "7")
ALICE = UserRef("1", "Alice")
BOB = UserRef("2", "Bob")


class FakePresenter:
    def __init__(self):
        self.events: list[tuple[int, SprintEvent]] = []

    async def show(self, sprint, event, origin=None):
        await asyncio.sleep(0)  # yield like a real network call would
        self.events.append((sprint.id, event))
        sprint.refs["last"] = event.value


@pytest.fixture
async def manager(storage, clock):
    m = SprintManager(
        storage,
        FakePresenter(),
        platform="test",
        report_window=timedelta(minutes=30),
        now=clock.now,
        sleep=clock.sleep,
    )
    yield m
    await m.shutdown()


async def no_admin():
    return False


async def yes_admin():
    return True


def events(manager, sprint_id):
    return [e for sid, e in manager.presenter.events if sid == sprint_id]


async def test_full_lifecycle(manager, clock, storage):
    sprint = await manager.start(KEY, ALICE, duration=10, delay=2)
    assert sprint.status == SprintStatus.PLANNED
    await clock.advance(120)
    assert sprint.status == SprintStatus.RUNNING
    await clock.advance(5 * 60)
    assert SprintEvent.TICK in events(manager, sprint.id)
    await clock.advance(5 * 60)
    assert sprint.status == SprintStatus.REPORTING

    outcome = await manager.report(KEY, ALICE, ReportInput(ReportKind.WRITTEN, 450), count_link=None)
    assert outcome.status == ReportStatus.REPORTED
    assert sprint.participants["1"].words == 450

    await clock.advance(30 * 60)
    assert sprint.status == SprintStatus.CLOSED
    assert manager.get(sprint.id) is None
    assert events(manager, sprint.id)[-1] == SprintEvent.CLOSED
    stats = await storage.user_stats("test", "1")
    assert (stats.sprints, stats.words, stats.minutes) == (1, 450, 10)


async def test_concurrent_starts_create_one_sprint(manager):
    """Issue #2: two /sprint commands at the same moment must not both start a sprint."""
    results = await asyncio.gather(
        manager.start(KEY, ALICE, 30, 2), manager.start(KEY, BOB, 30, 2), return_exceptions=True
    )
    started = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, SprintAlreadyRunningError)]
    assert len(started) == 1 and len(errors) == 1


async def test_forum_topics_have_separate_sprints(manager):
    """Issue #3: one sprint per topic, not per chat."""
    a = await manager.start(KEY, ALICE, 30, 2)
    b = await manager.start(TOPIC, BOB, 30, 2)
    assert a.id != b.id
    assert manager.active(KEY) is a and manager.active(TOPIC) is b


async def test_invalid_params(manager):
    with pytest.raises(InvalidSprintParamsError):
        await manager.start(KEY, ALICE, 0, 2)
    with pytest.raises(InvalidSprintParamsError):
        await manager.start(KEY, ALICE, 30, 11)


async def test_join_and_leave(manager):
    sprint = await manager.start(KEY, ALICE, 30, 2)
    assert (await manager.join(KEY, BOB))[0] == JoinResult.JOINED
    assert (await manager.join(KEY, BOB))[0] == JoinResult.ALREADY
    assert (await manager.leave_or_cancel(KEY, BOB, no_admin))[0] == LeaveResult.LEFT
    assert list(sprint.participants) == ["1"]


async def test_join_old_sprint_message_is_rejected(manager, clock):
    old = await manager.start(KEY, ALICE, 1, 0)
    await clock.advance(61)
    assert old.status == SprintStatus.REPORTING
    await manager.start(KEY, ALICE, 30, 2)
    assert (await manager.join(KEY, BOB, sprint_id=old.id))[0] == JoinResult.CLOSED


async def test_last_participant_cancels_planned(manager, clock):
    sprint = await manager.start(KEY, ALICE, 30, 2)
    assert (await manager.leave_or_cancel(KEY, ALICE, no_admin))[0] == LeaveResult.CANCELLED
    assert sprint.status == SprintStatus.CANCELLED
    assert manager.active(KEY) is None
    await clock.advance(10 * 60)
    assert SprintEvent.STARTED not in events(manager, sprint.id)


async def test_outsider_cannot_cancel_but_admin_can(manager):
    await manager.start(KEY, ALICE, 30, 2)
    assert (await manager.leave_or_cancel(KEY, BOB, no_admin))[0] == LeaveResult.NOT_ALLOWED
    assert (await manager.leave_or_cancel(KEY, BOB, yes_admin))[0] == LeaveResult.CANCELLED


async def test_stop_early_opens_reports(manager, clock):
    sprint = await manager.start(KEY, ALICE, 30, 0)
    await clock.advance(12 * 60 + 30)
    assert (await manager.leave_or_cancel(KEY, ALICE, no_admin))[0] == LeaveResult.STOPPED
    assert sprint.status == SprintStatus.REPORTING and sprint.stopped_early
    assert sprint.duration == 12
    await clock.advance(30 * 60)
    assert sprint.status == SprintStatus.CLOSED


async def test_link_baseline_and_recount(manager, clock):
    counts = iter([1000, 1600])

    async def count_link(url):
        return next(counts)

    sprint = await manager.start(KEY, ALICE, 10, 1)
    baseline = await manager.report(KEY, ALICE, ReportInput(ReportKind.LINK, url="https://telegra.ph/x"), count_link)
    assert baseline.status == ReportStatus.BASELINE and baseline.counted == 1000
    await clock.advance(11 * 60)
    assert sprint.status == SprintStatus.REPORTING
    outcome = await manager.report(KEY, ALICE, ReportInput(ReportKind.RECOUNT), count_link)
    assert outcome.status == ReportStatus.REPORTED
    assert sprint.participants["1"].words == 600


async def test_total_report_needs_baseline(manager, clock):
    sprint = await manager.start(KEY, ALICE, 5, 0)
    early = await manager.report(KEY, ALICE, ReportInput(ReportKind.WRITTEN, 100), None)
    assert early.status == ReportStatus.TOO_EARLY
    await manager.report(KEY, ALICE, ReportInput(ReportKind.TOTAL, 12000), None)
    await clock.advance(5 * 60)
    assert (
        await manager.report(KEY, BOB, ReportInput(ReportKind.TOTAL, 500), None)
    ).status == ReportStatus.NEED_BASELINE
    assert (
        await manager.report(KEY, ALICE, ReportInput(ReportKind.TOTAL, 12345), None)
    ).status == ReportStatus.REPORTED
    assert sprint.participants["1"].words == 345


async def test_late_reporter_joins_board(manager, clock):
    sprint = await manager.start(KEY, ALICE, 5, 0)
    await clock.advance(5 * 60)
    outcome = await manager.report(KEY, BOB, ReportInput(ReportKind.WRITTEN, 300), None)
    assert outcome.status == ReportStatus.REPORTED
    assert [p.name for p in sprint.leaderboard()] == ["Bob"]


async def test_report_without_sprint(manager):
    assert (
        await manager.report(KEY, ALICE, ReportInput(ReportKind.WRITTEN, 1), None)
    ).status == ReportStatus.NO_SPRINT


async def test_restore_after_restart(storage, clock):
    first = SprintManager(storage, FakePresenter(), platform="test", now=clock.now, sleep=clock.sleep)
    sprint = await first.start(KEY, ALICE, 10, 2)
    await first.join(KEY, BOB)
    await first.shutdown()

    second = SprintManager(storage, FakePresenter(), platform="test", now=clock.now, sleep=clock.sleep)
    assert await second.restore() == 1
    restored = second.get(sprint.id)
    assert set(restored.participants) == {"1", "2"}
    assert restored.refs["last"] == SprintEvent.PARTICIPANTS_CHANGED.value
    await clock.advance(12 * 60)
    assert restored.status == SprintStatus.REPORTING
    await second.shutdown()


async def test_presenter_failure_does_not_stop_sprint(storage, clock):
    class Broken(FakePresenter):
        async def show(self, sprint, event, origin=None):
            raise RuntimeError("telegram is down")

    m = SprintManager(storage, Broken(), platform="test", now=clock.now, sleep=clock.sleep)
    sprint = await m.start(KEY, ALICE, 2, 0)
    await clock.advance(3 * 60)
    assert sprint.status == SprintStatus.REPORTING
    await m.shutdown()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500", ReportInput(ReportKind.WRITTEN, 500)),
        ("+1 200", ReportInput(ReportKind.WRITTEN, 1200)),
        ("=12 500", ReportInput(ReportKind.TOTAL, 12500)),
        ("", ReportInput(ReportKind.RECOUNT)),
        ("ось https://telegra.ph/a-1", ReportInput(ReportKind.LINK, url="https://telegra.ph/a-1")),
        ("дякую!", None),
        ("-5", None),
        ("1234567890", None),
    ],
)
def test_parse_report(text, expected):
    assert parse_report(text, find_url) == expected


async def test_start_link_is_counted_when_the_sprint_ends(storage, clock):
    counts = {"https://telegra.ph/a": iter([1000, 1450]), "https://telegra.ph/b": iter([200, 900])}

    async def count_link(url):
        return next(counts[url])

    m = SprintManager(
        storage, FakePresenter(), platform="test", now=clock.now, sleep=clock.sleep, count_link=count_link
    )
    try:
        sprint = await m.start(KEY, ALICE, 10, 0)
        await m.join(KEY, BOB)
        await m.report(KEY, ALICE, ReportInput(ReportKind.LINK, url="https://telegra.ph/a"), count_link)
        await m.report(KEY, BOB, ReportInput(ReportKind.LINK, url="https://telegra.ph/b"), count_link)
        await clock.advance(10 * 60)
        assert sprint.status == SprintStatus.REPORTING
        assert sprint.participants["1"].words == 450
        assert sprint.participants["2"].words == 700
        assert events(m, sprint.id)[-1] == SprintEvent.REPORT_UPDATED
    finally:
        await m.shutdown()


async def test_recount_failure_leaves_manual_report_open(storage, clock):
    calls = []

    async def count_link(url):
        calls.append(url)
        if len(calls) > 1:
            raise RuntimeError("site is down")
        return 1000

    m = SprintManager(
        storage, FakePresenter(), platform="test", now=clock.now, sleep=clock.sleep, count_link=count_link
    )
    try:
        sprint = await m.start(KEY, ALICE, 5, 0)
        await m.report(KEY, ALICE, ReportInput(ReportKind.LINK, url="https://telegra.ph/a"), count_link)
        await clock.advance(5 * 60)
        assert sprint.participants["1"].words is None
        outcome = await m.report(KEY, ALICE, ReportInput(ReportKind.WRITTEN, 300), count_link)
        assert outcome.status == ReportStatus.REPORTED
    finally:
        await m.shutdown()


async def test_percent_sprint(manager, clock, storage):
    from writers_bot.core.models import SprintUnit

    sprint = await manager.start(KEY, ALICE, 10, 0, unit=SprintUnit.PERCENT)
    await manager.join(KEY, BOB)
    early = await manager.report(KEY, ALICE, parse_report("40%", find_url), None)
    assert early.status == ReportStatus.TOO_EARLY
    await clock.advance(10 * 60)
    assert (await manager.report(KEY, ALICE, parse_report("40%", find_url), None)).status == ReportStatus.REPORTED
    assert (await manager.report(KEY, BOB, parse_report("120", find_url), None)).status == ReportStatus.TOO_MANY
    link = parse_report("https://telegra.ph/x", find_url)
    assert (await manager.report(KEY, BOB, link, None)).status == ReportStatus.PERCENT_ONLY
    assert sprint.participants["1"].words == 40 and sprint.wpm(sprint.participants["1"]) is None
    await clock.advance(30 * 60)
    # percentages never count as words in stats or the top
    assert (await storage.user_stats("test", "1")).words == 0


async def test_chat_setting_roundtrip(storage):
    from writers_bot.core.models import SprintUnit

    assert await storage.sprint_unit("test", "-1") == SprintUnit.WORDS
    await storage.set_chat_setting("test", "-1", "sprint_unit", "percent")
    assert await storage.sprint_unit("test", "-1") == SprintUnit.PERCENT
