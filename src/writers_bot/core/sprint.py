"""Platform-independent writing sprint engine.

Every sprint has its own asyncio task that drives it through planned → running → reporting → closed.
All state changes for one chat/topic happen under that chat's lock, and a sprint is registered before the
first ``await``, so two simultaneous ``/sprint`` commands can no longer both start one (issue #2), and a
tick cannot race with a cancel or a join.

Adapters implement :class:`SprintPresenter` to show the sprint on their platform.
"""

import asyncio
import contextlib
import logging
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum, auto
from typing import Any, Protocol

from .models import OPEN_STATUSES, ChatKey, Participant, Sprint, SprintStatus, SprintUnit
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


class ReportKind(StrEnum):
    WRITTEN = auto()  # "500" / "+500": words written in this sprint
    TOTAL = auto()  # "=12500": manuscript total now (or at the start, before the sprint ends)
    LINK = auto()
    RECOUNT = auto()  # no argument: recount the link registered at the start


@dataclass(frozen=True, slots=True)
class ReportInput:
    kind: ReportKind
    value: int | None = None
    url: str | None = None


_NUMBER_RE = re.compile(r"^([+=])?\s*(\d[\d\s  '’,.]*?)\s*(%)?$")


def parse_report(text: str, find_url: Callable[[str], str | None]) -> ReportInput | None:
    """Parse "500", "+500", "=12 500", a link, or "" (recount). ``None`` if it is none of these."""
    text = (text or "").strip()
    if not text:
        return ReportInput(ReportKind.RECOUNT)
    url = find_url(text)
    if url:
        return ReportInput(ReportKind.LINK, url=url)
    match = _NUMBER_RE.match(text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match[2])
    if not digits or len(digits) > 9:
        return None
    kind = ReportKind.TOTAL if match[1] == "=" else ReportKind.WRITTEN
    return ReportInput(kind, value=int(digits))


class ReportStatus(StrEnum):
    REPORTED = auto()  # words recorded for a finished sprint
    BASELINE = auto()  # starting count recorded for a planned/running sprint
    NO_SPRINT = auto()
    NEED_BASELINE = auto()  # "=N" or recount without a starting count
    TOO_EARLY = auto()  # "+N" while the sprint is still running
    TOO_MANY = auto()
    PERCENT_ONLY = auto()  # a link, "=N" or a starting count in a percent sprint


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    status: ReportStatus
    sprint: Sprint | None = None
    participant: Participant | None = None
    counted: int | None = None  # words found behind a link


@dataclass(frozen=True, slots=True)
class UserRef:
    user_id: str
    name: str


def validate_params(duration: int, delay: int) -> None:
    if not MIN_SPRINT <= duration <= MAX_SPRINT:
        raise InvalidSprintParamsError(
            f"Довжина спринту має бути цілим числом від {MIN_SPRINT} до {MAX_SPRINT} хвилин!"
        )
    if not MIN_SPRINT_DELAY <= delay <= MAX_SPRINT_DELAY:
        raise InvalidSprintParamsError(
            f"Затримка до початку спринту має бути цілим числом від {MIN_SPRINT_DELAY} до {MAX_SPRINT_DELAY} хвилин!"
        )


class SprintManager:
    def __init__(
        self,
        storage: Storage,
        presenter: SprintPresenter,
        *,
        platform: str,
        report_window: timedelta = timedelta(minutes=30),
        tick_seconds: float = 60.0,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        count_link: Callable[[str], Awaitable[int]] | None = None,
    ):
        self.storage = storage
        self.presenter = presenter
        self.platform = platform
        self.report_window = report_window
        self.tick_seconds = tick_seconds
        self.now = now
        self._sleep = sleep
        self.count_link = count_link
        self._sprints: dict[int, Sprint] = {}
        self._locks: defaultdict[ChatKey, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: dict[int, asyncio.Task] = {}
        self._recounts: set[asyncio.Task] = set()

    # --- lookups ---------------------------------------------------------------------------------

    def lock(self, key: ChatKey) -> asyncio.Lock:
        return self._locks[key]

    def get(self, sprint_id: int) -> Sprint | None:
        return self._sprints.get(sprint_id)

    def active(self, key: ChatKey) -> Sprint | None:
        return next((s for s in self._sprints.values() if s.key == key and s.is_active), None)

    def open_sprints(self, key: ChatKey) -> list[Sprint]:
        return sorted((s for s in self._sprints.values() if s.key == key), key=lambda s: s.id, reverse=True)

    def find(self, predicate: Callable[[Sprint], bool]) -> Sprint | None:
        return next((s for s in self._sprints.values() if predicate(s)), None)

    # --- lifecycle -------------------------------------------------------------------------------

    async def restore(self) -> int:
        """Resume sprints that were open when the bot stopped."""
        for sprint in await self.storage.load_open_sprints(self.platform):
            self._sprints[sprint.id] = sprint
            self._spawn(sprint)
        return len(self._sprints)

    async def shutdown(self) -> None:
        tasks = [*self._tasks.values(), *self._recounts]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

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

    async def _present(self, sprint: Sprint, event: SprintEvent, origin: Any = None) -> None:
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
                            await self._present(sprint, SprintEvent.STARTED)
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
                            await self._present(sprint, SprintEvent.TICK)
                elif sprint.status == SprintStatus.REPORTING:
                    await self._sleep_until(sprint.report_until or self.now())
                    async with self.lock(sprint.key):
                        if sprint.status == SprintStatus.REPORTING and self.now() >= (
                            sprint.report_until or self.now()
                        ):
                            sprint.status = SprintStatus.CLOSED
                            await self._present(sprint, SprintEvent.CLOSED)
                            await self.storage.save_sprint(sprint)
                            self._drop(sprint)
                            return
                else:
                    self._drop(sprint)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sprint %s runner failed; retrying", sprint_id)
                await self._sleep(5)

    async def _finish(self, sprint: Sprint) -> None:
        sprint.status = SprintStatus.REPORTING
        sprint.report_until = self.now() + self.report_window
        await self.storage.save_sprint(sprint)
        await self._present(sprint, SprintEvent.FINISHED)
        await self.storage.save_sprint(sprint)
        if self.count_link and any(p.start_url and p.words is None for p in sprint.participants.values()):
            task = asyncio.create_task(self._recount_links(sprint), name=f"sprint-{sprint.id}-recount")
            self._recounts.add(task)
            task.add_done_callback(self._recounts.discard)

    async def _recount_links(self, sprint: Sprint) -> None:
        """Count the texts linked before the sprint, so those writers don't have to report by hand."""
        counted: dict[str, int] = {}
        for p in list(sprint.participants.values()):
            if not p.start_url or p.words is not None:
                continue
            try:
                counted[p.user_id] = await self.count_link(p.start_url)
            except Exception as e:
                logger.info("sprint %s: could not recount %s: %s", sprint.id, p.start_url, e)
        if not counted:
            return
        async with self.lock(sprint.key):
            if sprint.status != SprintStatus.REPORTING:
                return
            changed = False
            for user_id, words in counted.items():
                p = sprint.participants.get(user_id)
                if p is None or p.words is not None or not p.start_url:
                    continue  # left the sprint or reported by hand meanwhile
                report = ReportInput(ReportKind.LINK, url=p.start_url)
                changed |= self._apply_report(sprint, p, report, words).status == ReportStatus.REPORTED
            if changed:
                await self._present(sprint, SprintEvent.REPORT_UPDATED)
                await self.storage.save_sprint(sprint)

    # --- commands --------------------------------------------------------------------------------

    async def start(
        self,
        key: ChatKey,
        creator: UserRef,
        duration: int,
        delay: int,
        origin: Any = None,
        unit: SprintUnit = SprintUnit.WORDS,
    ) -> Sprint:
        validate_params(duration, delay)
        async with self.lock(key):
            if (existing := self.active(key)) is not None:
                raise SprintAlreadyRunningError(existing)
            now = self.now()
            start_at = now + timedelta(minutes=delay)
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
                participants={creator.user_id: Participant(creator.user_id, creator.name, now)},
                unit=unit,
            )
            await self.storage.create_sprint(sprint)
            self._sprints[sprint.id] = sprint
            await self._present(sprint, SprintEvent.CREATED, origin)
            await self.storage.save_sprint(sprint)
            self._spawn(sprint)
            return sprint

    def _target_active(self, key: ChatKey, sprint_id: int | None) -> Sprint | None:
        sprint = self.get(sprint_id) if sprint_id is not None else self.active(key)
        if sprint is None or sprint.key != key or not sprint.is_active:
            return None
        return sprint

    async def join(
        self, key: ChatKey, user: UserRef, sprint_id: int | None = None
    ) -> tuple[JoinResult, Sprint | None]:
        async with self.lock(key):
            sprint = self._target_active(key, sprint_id)
            if sprint is None:
                return JoinResult.CLOSED, None
            if user.user_id in sprint.participants:
                return JoinResult.ALREADY, sprint
            sprint.participants[user.user_id] = Participant(user.user_id, user.name, self.now())
            await self._present(sprint, SprintEvent.PARTICIPANTS_CHANGED)
            await self.storage.save_sprint(sprint)
            return JoinResult.JOINED, sprint

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
                await self._present(sprint, SprintEvent.PARTICIPANTS_CHANGED)
                await self.storage.save_sprint(sprint)
                return LeaveResult.LEFT, sprint
            if not is_participant and not await is_admin():
                return LeaveResult.NOT_ALLOWED, sprint
            return await self._cancel(sprint), sprint

    async def _cancel(self, sprint: Sprint) -> LeaveResult:
        if sprint.status == SprintStatus.PLANNED:
            sprint.status = SprintStatus.CANCELLED
            await self._present(sprint, SprintEvent.CANCELLED)
            await self.storage.save_sprint(sprint)
            self._drop(sprint)
            return LeaveResult.CANCELLED
        now = self.now()
        sprint.stopped_early = True
        sprint.duration = max(0, int((now - sprint.start_at).total_seconds() // 60))
        sprint.end_at = now
        await self._finish(sprint)
        self._spawn(sprint)  # restart the runner so the report window closes on time
        return LeaveResult.STOPPED

    async def update_refs(self, sprint: Sprint, update: Callable[[dict], None]) -> None:
        """Change the adapter's ``refs`` of a sprint under its lock and persist them."""
        async with self.lock(sprint.key):
            if sprint.id not in self._sprints:
                return
            update(sprint.refs)
            await self.storage.save_sprint(sprint)

    # --- word reports ----------------------------------------------------------------------------

    def report_target(self, key: ChatKey, user_id: str, sprint_id: int | None = None) -> Sprint | None:
        """The sprint a report from ``user_id`` refers to: explicit id, else their latest open sprint here."""
        if sprint_id is not None:
            sprint = self.get(sprint_id)
            return sprint if sprint is not None and sprint.key == key and sprint.status in OPEN_STATUSES else None
        candidates = self.open_sprints(key)
        mine = [s for s in candidates if user_id in s.participants]
        for pool in (mine, [s for s in candidates if s.status == SprintStatus.REPORTING]):
            if pool:
                return pool[0]
        return None

    async def report(
        self,
        key: ChatKey,
        user: UserRef,
        report: ReportInput,
        count_link: Callable[[str], Awaitable[int]],
        sprint_id: int | None = None,
    ) -> ReportOutcome:
        target = self.report_target(key, user.user_id, sprint_id)
        if target is None:
            return ReportOutcome(ReportStatus.NO_SPRINT)

        if target.unit == SprintUnit.PERCENT:
            return await self._report_percent(target, user, report)

        # Network I/O happens before taking the lock.
        counted = None
        if report.kind == ReportKind.LINK:
            counted = await count_link(report.url)
        elif report.kind == ReportKind.RECOUNT:
            participant = target.participants.get(user.user_id)
            if participant is None or not participant.start_url:
                return ReportOutcome(ReportStatus.NEED_BASELINE, target)
            report = ReportInput(ReportKind.LINK, url=participant.start_url)
            counted = await count_link(participant.start_url)

        async with self.lock(key):
            if target.status not in OPEN_STATUSES:
                return ReportOutcome(ReportStatus.NO_SPRINT)
            p = target.participants.get(user.user_id)
            if p is None:
                p = Participant(user.user_id, user.name, self.now())
            else:
                p.name = user.name

            if target.is_active:
                outcome = self._apply_baseline(target, p, report, counted)
                event = SprintEvent.PARTICIPANTS_CHANGED
            else:
                outcome = self._apply_report(target, p, report, counted)
                event = SprintEvent.REPORT_UPDATED
            if outcome.status in (ReportStatus.REPORTED, ReportStatus.BASELINE):
                target.participants[p.user_id] = p
                await self._present(target, event)
                await self.storage.save_sprint(target)
            return outcome

    async def _report_percent(self, target: Sprint, user: UserRef, report: ReportInput) -> ReportOutcome:
        if report.kind != ReportKind.WRITTEN:
            return ReportOutcome(ReportStatus.PERCENT_ONLY, target)
        async with self.lock(target.key):
            if target.status not in OPEN_STATUSES:
                return ReportOutcome(ReportStatus.NO_SPRINT)
            if target.is_active:
                return ReportOutcome(ReportStatus.TOO_EARLY, target)
            if report.value > 100:
                return ReportOutcome(ReportStatus.TOO_MANY, target)
            p = target.participants.get(user.user_id) or Participant(user.user_id, user.name, self.now())
            p.name = user.name
            p.words = report.value
            p.reported_at = self.now()
            target.participants[p.user_id] = p
            await self._present(target, SprintEvent.REPORT_UPDATED)
            await self.storage.save_sprint(target)
            return ReportOutcome(ReportStatus.REPORTED, target, p)

    def _apply_baseline(self, sprint, p, report, counted) -> ReportOutcome:
        if report.kind == ReportKind.LINK:
            p.start_url, p.start_words = report.url, counted
        elif report.kind == ReportKind.TOTAL or (
            report.kind == ReportKind.WRITTEN and sprint.status == SprintStatus.PLANNED
        ):
            if report.value > MAX_TOTAL_WORDS:
                return ReportOutcome(ReportStatus.TOO_MANY, sprint)
            p.start_url, p.start_words = None, report.value
        else:
            return ReportOutcome(ReportStatus.TOO_EARLY, sprint)
        return ReportOutcome(ReportStatus.BASELINE, sprint, p, counted)

    def _apply_report(self, sprint, p, report, counted) -> ReportOutcome:
        if report.kind == ReportKind.WRITTEN:
            words = report.value
        elif report.kind == ReportKind.TOTAL:
            if p.start_words is None:
                return ReportOutcome(ReportStatus.NEED_BASELINE, sprint)
            words = report.value - p.start_words
        else:  # LINK
            same_doc = p.start_url is None or p.start_url == report.url
            words = counted - p.start_words if p.start_words is not None and same_doc else counted
        words = max(0, words)
        if words > MAX_WORDS_PER_SPRINT:
            return ReportOutcome(ReportStatus.TOO_MANY, sprint)
        p.words = words
        p.reported_at = self.now()
        return ReportOutcome(ReportStatus.REPORTED, sprint, p, counted)
