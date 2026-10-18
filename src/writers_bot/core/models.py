"""Data model shared by storage, the sprint engine and the platform adapters."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class SprintStatus(StrEnum):
    PLANNED = "planned"  # waiting for the start
    RUNNING = "running"
    REPORTING = "reporting"  # finished (or stopped early), late reports still accepted
    CLOSED = "closed"  # reports closed, final results posted
    CANCELLED = "cancelled"  # cancelled before it started


class Unit(StrEnum):
    """What a work is measured in: words written, or how much of the plan for a sprint got done."""

    WORDS = "words"
    PERCENT = "percent"


SprintUnit = Unit  # v3.2 name, still used by the v2-style sprint flow and old rows


class Outcome(StrEnum):
    DONE = "done"  # Done ✓, no number
    FAILED = "failed"  # Didn't make it ✗


ACTIVE_STATUSES = (SprintStatus.PLANNED, SprintStatus.RUNNING)
OPEN_STATUSES = (SprintStatus.PLANNED, SprintStatus.RUNNING, SprintStatus.REPORTING)


@dataclass(frozen=True, slots=True)
class ChatKey:
    """Where a sprint lives: a chat plus, for forum chats, a topic (issue #3)."""

    platform: str
    chat_id: str
    thread_id: str | None = None


@dataclass(slots=True)
class SprintWork:
    """One work in one person's sprint. ``amount`` is words, or % of the plan for a % work."""

    work_id: int
    unit: Unit
    label: str | None = None  # the work's name when it joined; None for the unnamed work
    hidden: bool = False  # the work hides its title
    linked: bool = False  # has a link that is recounted at the end
    amount: int | None = None
    reported_at: datetime | None = None


@dataclass(slots=True)
class Participant:
    user_id: str
    name: str
    joined_at: datetime
    # v3.3 reports (still read from old rows; the v2 flow never sets them)
    start_words: int | None = None
    start_url: str | None = None
    words: int | None = None
    reported_at: datetime | None = None
    # v3.4
    person_id: int | None = None
    works: dict[int, SprintWork] = field(default_factory=dict)
    outcome: Outcome | None = None
    hide_numbers: bool = False  # snapshot of the person's switch, refreshed when it changes
    closed: bool = False  # joined a later sprint: no more late reports here

    def amounts(self, unit: Unit) -> int | None:
        """Sum over this person's works in ``unit``; None if none of them has a number."""
        values = [w.amount for w in self.works.values() if w.unit == unit and w.amount is not None]
        return sum(values) if values else None

    @property
    def completed(self) -> bool:
        """Any positive progress, or Done. A 0, ✗ or no report doesn't count."""
        if self.outcome == Outcome.DONE:
            return True
        if any((w.amount or 0) > 0 for w in self.works.values()):
            return True
        return (self.words or 0) > 0

    @property
    def has_report(self) -> bool:
        return self.outcome is not None or any(w.amount is not None for w in self.works.values())


@dataclass(slots=True)
class Sprint:
    id: int
    key: ChatKey
    status: SprintStatus
    duration: int  # minutes actually written (shrinks if stopped early)
    planned_duration: int
    delay: int
    created_by: str
    created_at: datetime
    start_at: datetime
    end_at: datetime
    report_until: datetime | None = None
    stopped_early: bool = False
    participants: dict[str, Participant] = field(default_factory=dict)
    refs: dict = field(default_factory=dict)  # platform message ids etc.
    unit: Unit = Unit.WORDS  # v3.2 per-chat unit; the v3.4 flow keeps units per work

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def leaderboard(self) -> list[Participant]:
        """v3.3 board: people with a ``words`` report, highest first."""
        reported = [p for p in self.participants.values() if p.words is not None]
        return sorted(reported, key=lambda p: (-(p.words or 0), p.reported_at or p.joined_at))

    def wpm(self, participant: Participant) -> float | None:
        if self.unit != Unit.WORDS or participant.words is None or self.duration <= 0:
            return None
        return participant.words / self.duration

    def minutes_for(self, participant: Participant) -> int:
        """Minutes of writing credited to someone: from when they joined (or the start) to the end."""
        start = max(self.start_at, participant.joined_at)
        return max(0, min(self.duration, int((self.end_at - start).total_seconds() // 60)))


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One user's entry in one sprint, as stored (v3.3 export format)."""

    sprint_id: int
    chat_id: str
    status: SprintStatus
    unit: Unit
    start_at: datetime
    duration: int
    name: str
    joined_at: datetime
    start_words: int | None
    start_url: str | None
    words: int | None
    reported_at: datetime | None


@dataclass(frozen=True, slots=True)
class UserStats:
    sprints: int
    reported_sprints: int
    words: int
    best_words: int
    minutes: int


class ChatStatus(StrEnum):
    MEMBER = "member"  # the bot is in the chat / the user has not blocked it
    UNKNOWN = "unknown"  # imported from old data, not confirmed yet
    LEFT = "left"  # removed from the group, or the chat no longer exists
    KICKED = "kicked"  # banned from the group, or the user blocked the bot
    MIGRATED = "migrated"  # group upgraded to a supergroup with a new id


REACHABLE_CHAT_STATUSES = (ChatStatus.MEMBER, ChatStatus.UNKNOWN)


@dataclass(slots=True)
class ChatRecord:
    """A chat the bot has seen. Telegram has no API to list a bot's chats, so the bot keeps this itself."""

    platform: str
    chat_id: str
    type: str  # private, group, supergroup, channel
    title: str | None = None
    username: str | None = None
    status: ChatStatus = ChatStatus.MEMBER
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    broadcast_excluded: bool = False
    member_count: int | None = None

    @property
    def is_private(self) -> bool:
        return self.type == "private"


@dataclass(frozen=True, slots=True)
class TopEntry:
    user_id: str
    name: str
    words: int
    sprints: int


@dataclass(frozen=True, slots=True)
class MinutesEntry:
    person_id: int
    name: str
    minutes: int
    sprints: int


# --- works and progress ------------------------------------------------------------------------------

MAX_WORKS = 5  # also the number of fields a Discord modal holds


class GoalPeriod(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class Goal:
    amount: int
    period: GoalPeriod


@dataclass(slots=True)
class Work:
    id: int
    person_id: int
    name: str | None  # None: the person's unnamed work for that unit
    unit: Unit
    url: str | None = None
    hide_title: bool = False
    keep: bool = True  # attach to the next sprint
    total: int | None = None  # last counted total (words); compare-and-set on every credit
    total_at: datetime | None = None
    created_at: datetime | None = None
    goal: Goal | None = None

    @property
    def unnamed(self) -> bool:
        return self.name is None

    def public_label(self) -> str | None:
        """What others may see: the name, unless the title is hidden or there is none."""
        return None if self.hide_title or self.name is None else self.name


class EntryKind(StrEnum):
    PROGRESS = "progress"  # /progress
    RECOUNT = "recount"  # growth of a linked work found outside a sprint
    MARK = "mark"  # ✓ writing day without a number (a % work outside a sprint)


@dataclass(frozen=True, slots=True)
class Credit:
    """Words or % credited to one work on one day, from a sprint or an entry."""

    person_id: int
    work_id: int | None
    unit: Unit
    day: date
    amount: int
    kind: str  # "sprint" or an EntryKind
    sprint_id: int | None = None
    active: bool = True  # counts as a writing day for the streak
    minutes: int = 0
    chat_id: str | None = None
    created_at: datetime | None = None
