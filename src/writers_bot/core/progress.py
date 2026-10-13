"""Personal progress from a user's sprint reports: goals, streaks and personal bests.

Everything is computed from the stored reports on the fly; a writer has at most a few thousand rows.
Days are counted in the writer's own time zone, and a sprint counts on the day it started.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from .models import ReportRow, SprintStatus, SprintUnit

MIN_WPM_MINUTES = 5  # shorter sprints don't count for the words-per-minute record

TZ_KEY = "tz"
DAILY_KEY = "goal_daily"
MONTHLY_KEY = "goal_monthly"
MAX_GOAL = 1_000_000


@dataclass(frozen=True, slots=True)
class Goals:
    daily: int | None = None
    monthly: int | None = None

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "Goals":
        def num(key):
            value = settings.get(key, "")
            return int(value) if value.isdigit() and int(value) > 0 else None

        return cls(num(DAILY_KEY), num(MONTHLY_KEY))


@dataclass(frozen=True, slots=True)
class Progress:
    today: int
    month: int
    goals: Goals
    streak: int  # consecutive days with words, ending today or yesterday
    best_words: int | None
    best_wpm: float | None


@dataclass(frozen=True, slots=True)
class Records:
    words: int | None = None  # new best words in one sprint
    wpm: float | None = None  # new best words per minute


def user_timezone(settings: dict[str, str], default: tzinfo) -> tzinfo:
    try:
        return ZoneInfo(settings[TZ_KEY])
    except (KeyError, ZoneInfoNotFoundError, ValueError):
        return default


def find_timezone(query: str) -> str | None:
    """'Europe/Kyiv', 'europe/kyiv', 'Kyiv' or 'kiev' -> 'Europe/Kyiv'."""
    q = query.strip().replace(" ", "_").lower()
    if not q:
        return None
    zones = sorted(available_timezones())
    for name in zones:
        if name.lower() == q:
            return name
    aliases = {"kiev": "Europe/Kyiv", "kyiv": "Europe/Kyiv", "київ": "Europe/Kyiv", "utc": "UTC"}
    if q in aliases:
        return aliases[q]
    matches = [n for n in zones if "/" in n and n.rsplit("/", 1)[1].lower() == q and not n.startswith("Etc/")]
    return matches[0] if len(matches) == 1 else None


def _counted(rows: Iterable[ReportRow]) -> list[ReportRow]:
    return [
        r
        for r in rows
        if r.unit == SprintUnit.WORDS
        and r.words is not None
        and r.status in (SprintStatus.REPORTING, SprintStatus.CLOSED)
    ]


def _wpm(row: ReportRow) -> float | None:
    if row.duration < MIN_WPM_MINUTES or not row.words:
        return None
    return row.words / row.duration


def streak_length(days: set[date], today: date) -> int:
    day = today if today in days else today - timedelta(days=1)
    length = 0
    while day in days:
        length += 1
        day -= timedelta(days=1)
    return length


def progress(rows: Iterable[ReportRow], settings: dict[str, str], tz: tzinfo, now: datetime) -> Progress:
    counted = _counted(rows)
    today = now.astimezone(tz).date()
    by_day: dict[date, int] = {}
    for r in counted:
        d = r.start_at.astimezone(tz).date()
        by_day[d] = by_day.get(d, 0) + r.words
    month = sum(w for d, w in by_day.items() if (d.year, d.month) == (today.year, today.month))
    wpms = [w for r in counted if (w := _wpm(r)) is not None]
    return Progress(
        today=by_day.get(today, 0),
        month=month,
        goals=Goals.from_settings(settings),
        streak=streak_length({d for d, w in by_day.items() if w > 0}, today),
        best_words=max((r.words for r in counted), default=None),
        best_wpm=max(wpms, default=None),
    )


def new_records(rows: Iterable[ReportRow], sprint_id: int) -> Records:
    """Records set by ``sprint_id`` compared with the user's earlier sprints (none for a first sprint)."""
    counted = _counted(rows)
    this = next((r for r in counted if r.sprint_id == sprint_id), None)
    others = [r for r in counted if r.sprint_id != sprint_id]
    if this is None or not others or not this.words:
        return Records()
    best_words = max(r.words for r in others)
    other_wpm = [w for r in others if (w := _wpm(r)) is not None]
    this_wpm = _wpm(this)
    return Records(
        words=this.words if this.words > best_words else None,
        wpm=this_wpm if this_wpm is not None and other_wpm and this_wpm > max(other_wpm) else None,
    )
