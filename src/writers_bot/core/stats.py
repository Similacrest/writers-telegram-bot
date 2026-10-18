"""Personal progress computed from what was credited: sprint entries (per work) and ``/progress`` entries.

Nothing is cached: a writer has at most a few thousand rows. Days are bot days (03:00 Kyiv, see
:mod:`.days`). A sprint counts on the day it started.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .days import bot_day, fillable, period_start
from .models import Credit, EntryKind, GoalPeriod, MinutesEntry, Outcome, Unit, Work
from .storage import Storage, iso, parse_dt

COUNTED_STATUSES = ("running", "reporting", "closed")


@dataclass(frozen=True, slots=True)
class Participation:
    sprint_id: int
    chat_id: str
    platform: str
    day: date
    minutes: int
    completed: bool
    start_at: datetime


@dataclass(slots=True)
class History:
    credits: list[Credit] = field(default_factory=list)
    sprints: list[Participation] = field(default_factory=list)

    # --- days and streaks ------------------------------------------------------------------------

    def active_days(self) -> set[date]:
        days = {c.day for c in self.credits if c.active}
        days |= {p.day for p in self.sprints if p.completed}
        return days

    def streak(self, today: date) -> int:
        """Days in a row with writing, ending today or yesterday."""
        days = self.active_days()
        day = today if today in days else today - timedelta(days=1)
        length = 0
        while day in days:
            length += 1
            day -= timedelta(days=1)
        return length

    def last_days(self, today: date, n: int = 7) -> list[tuple[date, bool]]:
        days = self.active_days()
        return [(today - timedelta(days=i), today - timedelta(days=i) in days) for i in range(n - 1, -1, -1)]

    def fillable_gaps(self, today: date) -> list[date]:
        return [d for d, on in self.last_days(today, 4) if not on and fillable(d, today)]

    # --- sums ------------------------------------------------------------------------------------

    def words(self, start: date, end: date, work_id: int | None = None) -> int:
        return sum(
            c.amount
            for c in self.credits
            if c.unit == Unit.WORDS and start <= c.day <= end and (work_id is None or c.work_id == work_id)
        )

    def work_period(self, work: Work, today: date, period: GoalPeriod) -> int:
        return self.words(period_start(today, period), today, work.id)

    def completed(self, start: date | None = None, end: date | None = None) -> list[Participation]:
        return [
            p
            for p in self.sprints
            if p.completed and (start is None or p.day >= start) and (end is None or p.day <= end)
        ]

    def minutes(self, start: date | None = None, end: date | None = None) -> int:
        return sum(p.minutes for p in self.completed(start, end))

    def share_period(self, today: date) -> tuple[str, int, int, int] | None:
        """The shortest calendar period with any progress: (name, words, completed sprints, minutes)."""
        periods = [
            ("today", today),
            ("week", today - timedelta(days=today.weekday())),
            ("month", today.replace(day=1)),
            ("year", today.replace(month=1, day=1)),
        ]
        for name, start in periods:
            words = self.words(start, today)
            done = self.completed(start, today)
            marks = any(c.active for c in self.credits if start <= c.day <= today)
            if words > 0 or done or marks:
                return name, words, len(done), sum(p.minutes for p in done)
        return None


def _minutes(start_at: datetime, end_at: datetime, joined_at: datetime, duration: int) -> int:
    start = max(start_at, joined_at)
    return max(0, min(duration, int((end_at - start).total_seconds() // 60)))


async def history(storage: Storage, person_id: int) -> History:
    rows = await storage.fetchall(
        """SELECT s.id AS sid, s.chat_id, s.platform, s.start_at, s.end_at, s.duration, s.unit AS sunit,
                  p.user_id, p.joined_at, p.words, p.outcome,
                  sw.work_id, sw.unit AS wunit, sw.amount
           FROM participants p
           JOIN sprints s ON s.id = p.sprint_id
           JOIN accounts a ON a.platform = s.platform AND a.user_id = p.user_id
           LEFT JOIN sprint_works sw ON sw.sprint_id = p.sprint_id AND sw.user_id = p.user_id
           WHERE a.person_id = ? AND s.status IN ('running', 'reporting', 'closed')
           ORDER BY s.start_at, s.id""",
        (person_id,),
    )
    h = History()
    groups: dict[tuple[int, str], list] = defaultdict(list)
    for r in rows:
        groups[(r["sid"], r["user_id"])].append(r)
    for rs in groups.values():
        r0 = rs[0]
        start_at, end_at = parse_dt(r0["start_at"]), parse_dt(r0["end_at"])
        day = bot_day(start_at)
        with_works = [r for r in rs if r["work_id"] is not None]
        completed = r0["outcome"] == Outcome.DONE.value or any((r["amount"] or 0) > 0 for r in with_works)
        if not with_works:
            completed = completed or (r0["words"] or 0) > 0
            if r0["words"] is not None:
                h.credits.append(
                    Credit(person_id, None, Unit(r0["sunit"]), day, r0["words"], "sprint", r0["sid"], completed)
                )
        for r in with_works:
            if r["amount"] is not None:
                h.credits.append(
                    Credit(person_id, r["work_id"], Unit(r["wunit"]), day, r["amount"], "sprint", r["sid"], completed)
                )
        h.sprints.append(
            Participation(
                sprint_id=r0["sid"],
                chat_id=r0["chat_id"],
                platform=r0["platform"],
                day=day,
                minutes=_minutes(start_at, end_at, parse_dt(r0["joined_at"]), r0["duration"]),
                completed=completed,
                start_at=start_at,
            )
        )
    for e in await storage.fetchall("SELECT * FROM entries WHERE person_id = ? ORDER BY id", (person_id,)):
        h.credits.append(
            Credit(
                person_id=person_id,
                work_id=e["work_id"],
                unit=Unit(e["unit"]),
                day=date.fromisoformat(e["day"]),
                amount=e["amount"],
                kind=e["kind"],
                active=e["amount"] > 0 or e["kind"] == EntryKind.MARK.value,
                chat_id=e["chat_id"],
                created_at=parse_dt(e["created_at"]),
            )
        )
    return h


async def top_minutes(
    storage: Storage, platform: str, chat_ids: Iterable[str], since: datetime, limit: int = 10
) -> list[MinutesEntry]:
    """Minutes of completed sprints in these chats since ``since``. Writers who hide their numbers aren't
    on it; credit starts when you joined."""
    chat_ids = list(chat_ids)
    if not chat_ids:
        return []
    rows = await storage.fetchall(
        f"""SELECT s.id AS sid, s.start_at, s.end_at, s.duration, p.user_id, p.name, p.joined_at, p.words,
                   p.outcome, a.person_id,
                   (SELECT MAX(COALESCE(sw.amount, 0)) FROM sprint_works sw
                     WHERE sw.sprint_id = p.sprint_id AND sw.user_id = p.user_id) AS best
            FROM participants p
            JOIN sprints s ON s.id = p.sprint_id
            LEFT JOIN accounts a ON a.platform = s.platform AND a.user_id = p.user_id
            WHERE s.platform = ? AND s.chat_id IN ({",".join("?" * len(chat_ids))}) AND s.start_at >= ?
              AND s.status IN ('reporting', 'closed')""",  # noqa: S608
        (platform, *chat_ids, iso(since)),
    )
    hidden = {
        r["person_id"]
        for r in await storage.fetchall(
            "SELECT person_id FROM person_settings WHERE key = 'hide_numbers' AND value = '1'"
        )
    }
    totals: dict[str, list] = {}
    for r in rows:
        completed = r["outcome"] == Outcome.DONE.value or (r["best"] or 0) > 0 or (r["words"] or 0) > 0
        if not completed or (r["person_id"] is not None and r["person_id"] in hidden):
            continue
        key = str(r["person_id"]) if r["person_id"] is not None else f"u{r['user_id']}"
        minutes = _minutes(parse_dt(r["start_at"]), parse_dt(r["end_at"]), parse_dt(r["joined_at"]), r["duration"])
        entry = totals.setdefault(key, [r["person_id"] or 0, r["name"], 0, 0])
        entry[1] = r["name"]
        entry[2] += minutes
        entry[3] += 1
    ranked = sorted(totals.values(), key=lambda e: (-e[2], -e[3], e[1]))
    return [MinutesEntry(e[0], e[1], e[2], e[3]) for e in ranked[:limit] if e[2] > 0]
