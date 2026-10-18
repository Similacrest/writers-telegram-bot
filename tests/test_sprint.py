import asyncio

import pytest

from writers_bot.core.models import ChatKey, SprintStatus
from writers_bot.core.sprint import (
    InvalidSprintParamsError,
    JoinResult,
    LeaveResult,
    SprintAlreadyRunningError,
    SprintEvent,
    SprintManager,
    UserRef,
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

    await clock.advance(12 * 3600)
    assert sprint.status == SprintStatus.CLOSED
    assert manager.get(sprint.id) is None
    assert events(manager, sprint.id)[-1] == SprintEvent.CLOSED


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
    await clock.advance(12 * 3600)
    assert sprint.status == SprintStatus.CLOSED


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


async def test_chat_setting_roundtrip(storage):
    from writers_bot.core.models import SprintUnit

    assert await storage.sprint_unit("test", "-1") == SprintUnit.WORDS
    await storage.set_chat_setting("test", "-1", "sprint_unit", "percent")
    assert await storage.sprint_unit("test", "-1") == SprintUnit.PERCENT


async def test_without_reports_a_sprint_closes_when_it_ends(storage, clock):
    m = SprintManager(storage, FakePresenter(), platform="test", now=clock.now, sleep=clock.sleep, reports=False)
    try:
        sprint = await m.start(KEY, ALICE, duration=10, delay=0)
        await clock.advance(10 * 60)
        assert sprint.status == SprintStatus.CLOSED and m.get(sprint.id) is None
        assert events(m, sprint.id)[-1] == SprintEvent.FINISHED

        sprint = await m.start(KEY, ALICE, duration=10, delay=0)
        await clock.advance(60)
        result, _ = await m.leave_or_cancel(KEY, ALICE, no_admin)
        assert result == LeaveResult.STOPPED
        assert sprint.status == SprintStatus.CLOSED and sprint.stopped_early and m.get(sprint.id) is None
        assert not m._tasks
        assert await m.start(KEY, BOB, duration=5, delay=0)  # the chat is free again
    finally:
        await m.shutdown()
