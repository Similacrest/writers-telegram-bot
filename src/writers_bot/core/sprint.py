"""Platform-independent writing sprint engine.

Every sprint has its own asyncio task that drives it through planned → running → reporting → closed.
All state changes for one chat/topic happen under that chat's lock, and a sprint is registered before the
first ``await``, so two simultaneous ``/sprint`` commands can no longer both start one (issue #2), and a
tick cannot race with a cancel or a join.

The engine knows who is in a sprint, not what they write: works and reports are handled by
:mod:`.reporting`, which changes participants under :meth:`SprintManager.lock` and then calls
:meth:`SprintManager.present`. Adapters implement :class:`SprintPresenter` to show the sprint.
"""

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum, auto
from typing import Any, Protocol

from .i18n import t
from .models import ChatKey, Participant, Sprint, SprintStatus, Unit
from .storage import Storage

logger = logging.getLogger(__name__)

MIN_SPRINT = 1
DEFAULT_SPRINT = 30
MAX_SPRINT = 120
MIN_SPRINT_DELAY = 0
DEFAULT_SPRINT_DELAY = 2
MAX_SPRINT_DELAY = 10
MAX_WORDS_PER_SPRINT = 50_000
MAX_TOTAL_WORDS = 10_000_000
LATE_REPORTS = timedelta(hours=12)  # late reports: until your next sprint, or this long after the end


def utc_now() -> datetime:
    return datetime.now(UTC)


class SprintEvent(StrEnum):
    CREATED = auto()
    STARTED = auto()
    TICK = auto()
    PARTICIPANTS_CHANGED = auto()
    FINISHED = auto()
    REPORT_UPDATED = auto()
    CLOSED = auto()
    CANCELLED = auto()


class SprintPresenter(Protocol):
    async def show(self, sprint: Sprint, event: SprintEvent, origin: Any = None) -> None:
        """Render ``sprint`` after ``event``. May store message ids in ``sprint.refs``."""


class SprintError(Exception):
    pass


class SprintAlreadyRunningError(SprintError):
    def __init__(self, sprint: Sprint):
        super().__init__("a sprint is already planned or running here")
        self.sprint = sprint


class InvalidSprintParamsError(SprintError):
    pass


class JoinResult(StrEnum):
    JOINED = auto()
    ALREADY = auto()
    CLOSED = auto()


class LeaveResult(StrEnum):
    LEFT = auto()
    CANCELLED = auto()  # cancelled before the start
    STOPPED = auto()  # stopped early, reports are open
    NOT_ALLOWED = auto()
    NOT_PARTICIPANT = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class UserRef:
    user_id: str
    name: str


def validate_params(duration: int, delay: int) -> None:
    if not MIN_SPRINT <= duration <= MAX_SPRINT:
        raise InvalidSprintParamsError(t("sprint.bad_duration", min=MIN_SPRINT, max=MAX_SPRINT))
    if not MIN_SPRINT_DELAY <= delay <= MAX_SPRINT_DELAY:
        raise InvalidSprintParamsError(t("sprint.bad_delay", min=MIN_SPRINT_DELAY, max=MAX_SPRINT_DELAY))


# Called under the sprint's lock when someone joins (attach their works) and after a sprint finishes
# (recount linked works). Both may be left out: the v2 flow has neither.
PrepareJoin = Callable[[Sprint, Participant], Awaitable[None]]
AfterFinish = Callable[[Sprint], Awaitable[None]]


class SprintManager:
    def __init__(
        self,
        storage: Storage,
        presenter: SprintPresenter,
        *,
        platform: str,
        report_window: timedelta = LATE_REPORTS,
        tick_seconds: float = 60.0,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        reports: bool = True,
        after_finish: AfterFinish | None = None,
    ):
        self.storage = storage
        self.presenter = presenter
        self.platform = platform
        self.report_window = report_window
        self.tick_seconds = tick_seconds
        self.now = now
        self._sleep = sleep
        self.reports = reports  # False: a finished sprint closes at once, as in v2 (no reports)
        self.after_finish = after_finish
        self._sprints: dict[int, Sprint] = {}
        self._locks: defaultdict[ChatKey, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: dict[int, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()

    # --- lookups ---------------------------------------------------------------------------------

    def lock(self, key: ChatKey) -> asyncio.Lock:
        return self._locks[key]

    def get(self, sprint_id: int) -> Sprint | None:
        return self._sprints.get(sprint_id)

    def active(self, key: ChatKey) -> Sprint | None:
        return next((s for s in self._sprints.values() if s.key == key and s.is_active), None)

    def open_sprints(self, key: ChatKey | None = None) -> list[Sprint]:
        """Open sprints here (or everywhere), newest first."""
        return sorted(
            (s for s in self._sprints.values() if key is None or s.key == key), key=lambda s: s.id, reverse=True
        )

    def find(self, predicate: Callable[[Sprint], bool]) -> Sprint | None:
        return next((s for s in self._sprints.values() if predicate(s)), None)

    def accepting(self, sprint: Sprint, user_id: str) -> bool:
        """Does ``sprint`` take reports from ``user_id`` now? Running, or finished and not closed for them."""
        p = sprint.participants.get(user_id)
        if p is None or p.closed:
            return False
        return sprint.status in (SprintStatus.RUNNING, SprintStatus.REPORTING)

    def current_for(self, user_id: str, key: ChatKey | None = None) -> Sprint | None:
        """The sprint someone's report goes to: their newest sprint taking reports, preferring ``key``."""
        mine = [s for s in self.open_sprints() if self.accepting(s, user_id)]
        here = [s for s in mine if key is not None and s.key == key]
        return (here or mine or [None])[0]

    # --- lifecycle -------------------------------------------------------------------------------

    async def restore(self) -> int:
        """Resume sprints that were open when the bot stopped."""
        for sprint in await self.storage.load_open_sprints(self.platform):
            self._sprints[sprint.id] = sprint
            self._spawn(sprint)
        return len(self._sprints)

    async def shutdown(self) -> None:
        tasks = [*self._tasks.values(), *self._background]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    def background(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def _spawn(self, sprint: Sprint) -> None:
        old = self._tasks.pop(sprint.id, None)
        if old is not None and old is not asyncio.current_task():
            old.cancel()
        self._tasks[sprint.id] = asyncio.create_task(self._run(sprint.id), name=f"sprint-{sprint.id}")

    def _drop(self, sprint: Sprint) -> None:
        self._sprints.pop(sprint.id, None)
        task = self._tasks.pop(sprint.id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def present(self, sprint: Sprint, event: SprintEvent, origin: Any = None) -> None:
        try:
            await self.presenter.show(sprint, event, origin)
        except Exception:
            logger.exception("presenter failed for sprint %s on %s", sprint.id, event)

    async def _sleep_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            await self._sleep(delay)

    async def _run(self, sprint_id: int) -> None:
        while True:
            sprint = self._sprints.get(sprint_id)
            if sprint is None:
                return
            try:
                if sprint.status == SprintStatus.PLANNED:
                    await self._sleep_until(sprint.start_at)
                    async with self.lock(sprint.key):
                        if sprint.status == SprintStatus.PLANNED:
                            sprint.status = SprintStatus.RUNNING
                            await self.present(sprint, SprintEvent.STARTED)
                            await self.storage.save_sprint(sprint)
                elif sprint.status == SprintStatus.RUNNING:
                    remaining = (sprint.end_at - self.now()).total_seconds()
                    if remaining > 1:
                        await self._sleep(min(self.tick_seconds, remaining))
                    async with self.lock(sprint.key):
                        if sprint.status != SprintStatus.RUNNING:
                            continue
                        if (sprint.end_at - self.now()).total_seconds() <= 1:
                            await self._finish(sprint)
                        else:
                            await self.present(sprint, SprintEvent.TICK)
                elif sprint.status == SprintStatus.REPORTING:
                    await self._sleep_until(sprint.report_until or self.now())
                    async with self.lock(sprint.key):
                        if sprint.status == SprintStatus.REPORTING and self.now() >= (
                            sprint.report_until or self.now()
                        ):
                            await self._close(sprint)
                            return
                else:
                    self._drop(sprint)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sprint %s runner failed; retrying", sprint_id)
                await self._sleep(5)

    async def _close(self, sprint: Sprint) -> None:
        sprint.status = SprintStatus.CLOSED
        await self.present(sprint, SprintEvent.CLOSED)
        await self.storage.save_sprint(sprint)
        self._drop(sprint)

    async def _finish(self, sprint: Sprint) -> None:
        if not self.reports:
            sprint.status = SprintStatus.CLOSED
            await self.present(sprint, SprintEvent.FINISHED)
            await self.storage.save_sprint(sprint)
            self._drop(sprint)
            return
        sprint.status = SprintStatus.REPORTING
        sprint.report_until = self.now() + self.report_window
        await self.storage.save_sprint(sprint)
        await self.present(sprint, SprintEvent.FINISHED)
        await self.storage.save_sprint(sprint)
        if self.after_finish is not None:
            self.background(self._after_finish(sprint), f"sprint-{sprint.id}-finish")

    async def _after_finish(self, sprint: Sprint) -> None:
        try:
            await self.after_finish(sprint)
        except Exception:
            logger.exception("after-finish hook failed for sprint %s", sprint.id)

    # --- commands --------------------------------------------------------------------------------

    async def start(
        self,
        key: ChatKey,
        creator: UserRef,
        duration: int,
        delay: int,
        origin: Any = None,
        unit: Unit = Unit.WORDS,
        prepare: PrepareJoin | None = None,
        refs: dict | None = None,
    ) -> Sprint:
        validate_params(duration, delay)
        async with self.lock(key):
            if (existing := self.active(key)) is not None:
                raise SprintAlreadyRunningError(existing)
            now = self.now()
            start_at = now + timedelta(minutes=delay)
            creator_p = Participant(creator.user_id, creator.name, now)
            sprint = Sprint(
                id=0,
                key=key,
                status=SprintStatus.RUNNING if delay == 0 else SprintStatus.PLANNED,
                duration=duration,
                planned_duration=duration,
                delay=delay,
                created_by=creator.user_id,
                created_at=now,
                start_at=start_at,
                end_at=start_at + timedelta(minutes=duration),
                participants={creator.user_id: creator_p},
                unit=unit,
                refs=dict(refs or {}),
            )
            await self.storage.create_sprint(sprint)
            self._sprints[sprint.id] = sprint
            if prepare is not None:
                await prepare(sprint, creator_p)
            await self.present(sprint, SprintEvent.CREATED, origin)
            await self.storage.save_sprint(sprint)
            self._spawn(sprint)
        await self._close_earlier(sprint, creator.user_id)
        return sprint

    def _target_active(self, key: ChatKey, sprint_id: int | None) -> Sprint | None:
        sprint = self.get(sprint_id) if sprint_id is not None else self.active(key)
        if sprint is None or sprint.key != key or not sprint.is_active:
            return None
        return sprint

    async def join(
        self, key: ChatKey, user: UserRef, sprint_id: int | None = None, prepare: PrepareJoin | None = None
    ) -> tuple[JoinResult, Sprint | None]:
        async with self.lock(key):
            sprint = self._target_active(key, sprint_id)
            if sprint is None:
                return JoinResult.CLOSED, None
            if user.user_id in sprint.participants:
                return JoinResult.ALREADY, sprint
            p = Participant(user.user_id, user.name, self.now())
            sprint.participants[user.user_id] = p
            if prepare is not None:
                await prepare(sprint, p)
            await self.present(sprint, SprintEvent.PARTICIPANTS_CHANGED)
            await self.storage.save_sprint(sprint)
        await self._close_earlier(sprint, user.user_id)
        return JoinResult.JOINED, sprint

    async def _close_earlier(self, sprint: Sprint, user_id: str) -> None:
        """Joining a sprint ends late reports for your earlier sprints."""
        for other in self.open_sprints():
            if other.id == sprint.id or other.status != SprintStatus.REPORTING:
                continue
            p = other.participants.get(user_id)
            if p is None or p.closed:
                continue
            async with self.lock(other.key):
                if other.id in self._sprints and not p.closed:
                    p.closed = True
                    await self.storage.save_sprint(other)

    async def leave_or_cancel(
        self,
        key: ChatKey,
        user: UserRef,
        is_admin: Callable[[], Awaitable[bool]],
        sprint_id: int | None = None,
    ) -> tuple[LeaveResult, Sprint | None]:
        """Leave the sprint; the last participant (or a chat admin) cancels it instead."""
        async with self.lock(key):
            sprint = self._target_active(key, sprint_id)
            if sprint is None:
                return LeaveResult.CLOSED, None
            is_participant = user.user_id in sprint.participants
            if is_participant and len(sprint.participants) > 1:
                del sprint.participants[user.user_id]
                await self.present(sprint, SprintEvent.PARTICIPANTS_CHANGED)
                await self.storage.save_sprint(sprint)
                return LeaveResult.LEFT, sprint
            if not is_participant and not await is_admin():
                return LeaveResult.NOT_ALLOWED, sprint
            return await self._cancel(sprint), sprint

    async def _cancel(self, sprint: Sprint) -> LeaveResult:
        if sprint.status == SprintStatus.PLANNED:
            sprint.status = SprintStatus.CANCELLED
            await self.present(sprint, SprintEvent.CANCELLED)
            await self.storage.save_sprint(sprint)
            self._drop(sprint)
            return LeaveResult.CANCELLED
        now = self.now()
        sprint.stopped_early = True
        sprint.duration = max(0, int((now - sprint.start_at).total_seconds() // 60))
        sprint.end_at = now
        await self._finish(sprint)
        if sprint.status == SprintStatus.REPORTING:
            self._spawn(sprint)  # restart the runner so late reports close on time
        return LeaveResult.STOPPED

    async def update_refs(self, sprint: Sprint, update: Callable[[dict], None]) -> None:
        """Change the adapter's ``refs`` of a sprint under its lock and persist them."""
        async with self.lock(sprint.key):
            if sprint.id not in self._sprints:
                return
            update(sprint.refs)
            await self.storage.save_sprint(sprint)
