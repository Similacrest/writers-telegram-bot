"""The bot-wide day: it starts at 03:00 Kyiv time, for goals, streaks and /top."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import GoalPeriod

DAY_ZONE = ZoneInfo("Europe/Kyiv")
DAY_STARTS = timedelta(hours=3)
BACKFILL_DAYS = 3  # late updates fill gaps up to this many days back


def bot_day(when: datetime) -> date:
    return (when.astimezone(DAY_ZONE) - DAY_STARTS).date()


def day_start(day: date) -> datetime:
    """The moment ``day`` begins, in UTC."""
    local = datetime.combine(day, time(), tzinfo=DAY_ZONE) + DAY_STARTS
    return local.astimezone(UTC)


def next_reset(now: datetime) -> datetime:
    return day_start(bot_day(now) + timedelta(days=1))


def period_start(day: date, period: GoalPeriod) -> date:
    if period == GoalPeriod.DAY:
        return day
    if period == GoalPeriod.WEEK:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def period_id(day: date, period: GoalPeriod) -> str:
    """Stable name of the period containing ``day``, to announce a reached goal once."""
    return f"{period.value}:{period_start(day, period).isoformat()}"


def fillable(day: date, today: date) -> bool:
    """Can a late update still go to ``day``?"""
    return today - timedelta(days=BACKFILL_DAYS) <= day <= today
