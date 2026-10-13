"""Sprint data model shared by storage, the sprint engine and the platform adapters."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SprintStatus(StrEnum):
    PLANNED = "planned"  # waiting for the start
    RUNNING = "running"
    REPORTING = "reporting"  # finished (or stopped early), collecting word reports
    CLOSED = "closed"  # reports closed, final results posted
    CANCELLED = "cancelled"  # cancelled before it started


ACTIVE_STATUSES = (SprintStatus.PLANNED, SprintStatus.RUNNING)
OPEN_STATUSES = (SprintStatus.PLANNED, SprintStatus.RUNNING, SprintStatus.REPORTING)


@dataclass(frozen=True, slots=True)
class ChatKey:
    """Where a sprint lives: a chat plus, for forum chats, a topic (issue #3)."""

    platform: str
    chat_id: str
    thread_id: str | None = None


@dataclass(slots=True)
class Participant:
    user_id: str
    name: str
    joined_at: datetime
    start_words: int | None = None  # manuscript total at the start, if reported
    start_url: str | None = None  # link counted at the start, recounted on report
    words: int | None = None  # words written during the sprint (the report)
    reported_at: datetime | None = None


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

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def leaderboard(self) -> list[Participant]:
        reported = [p for p in self.participants.values() if p.words is not None]
        return sorted(reported, key=lambda p: (-(p.words or 0), p.reported_at or p.joined_at))

    def wpm(self, participant: Participant) -> float | None:
        if participant.words is None or self.duration <= 0:
            return None
        return participant.words / self.duration


@dataclass(frozen=True, slots=True)
class UserStats:
    sprints: int
    reported_sprints: int
    words: int
    best_words: int
    minutes: int


@dataclass(frozen=True, slots=True)
class TopEntry:
    user_id: str
    name: str
    words: int
    sprints: int
