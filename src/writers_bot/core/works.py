"""Works: what a person writes. A name, an optional link, a unit and an optional goal; up to five each.

Every work keeps its last counted total. Each path that credits words reads it and writes the new value
with a compare-and-set (``UPDATE … WHERE total IS <what was read>``), so two sprints ending together, or a
recount racing a report, can't credit the same words twice, even from two processes.

Each person also has an unnamed work per unit, created on first use. It takes reports when they have no
works (or the chat has works off); it is never listed and doesn't count towards the five.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from .i18n import t
from .models import MAX_WORKS, Goal, GoalPeriod, Unit, Work
from .storage import Storage, iso, parse_dt

MAX_NAME = 40
MAX_GOAL = 1_000_000
CAS_RETRIES = 5


class WorkError(ValueError):
    """Shown to the user as is."""


@dataclass(frozen=True, slots=True)
class TotalChange:
    """Result of moving a work's total."""

    before: int | None
    after: int | None
    credited: int  # words to credit (never negative)


def _work(r) -> Work:
    goal = None
    if r["goal_amount"] and r["goal_period"] in GoalPeriod._value2member_map_:
        goal = Goal(r["goal_amount"], GoalPeriod(r["goal_period"]))
    return Work(
        id=r["id"],
        person_id=r["person_id"],
        name=r["name"],
        unit=Unit(r["unit"]),
        url=r["url"],
        hide_title=bool(r["hide_title"]),
        keep=bool(r["keep"]),
        total=r["total"],
        total_at=parse_dt(r["total_at"]),
        created_at=parse_dt(r["created_at"]),
        goal=goal,
    )


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise WorkError(t("works.empty_name"))
    if len(name) > MAX_NAME:
        raise WorkError(t("works.long_name", max=MAX_NAME))
    if name[0] in "+=%" or name[0].isdigit():
        raise WorkError(t("works.bad_name"))
    return name


def match_work(works: list[Work], query: str) -> Work | None:
    """A work by name: exact (any case), else the only one starting with ``query``."""
    q = " ".join(query.split()).casefold()
    if not q:
        return None
    named = [w for w in works if w.name]
    for w in named:
        if w.name.casefold() == q:
            return w
    starts = [w for w in named if w.name.casefold().startswith(q)]
    if len(starts) == 1:
        return starts[0]
    words = [w for w in named if any(part.startswith(q) for part in w.name.casefold().split())]
    return words[0] if len(words) == 1 else None


class Works:
    def __init__(self, storage: Storage):
        self.storage = storage

    async def for_person(self, person_id: int, *, include_unnamed: bool = False) -> list[Work]:
        rows = await self.storage.fetchall(
            "SELECT * FROM works WHERE person_id = ? AND deleted = 0 ORDER BY name IS NULL, id", (person_id,)
        )
        works = [_work(r) for r in rows]
        return works if include_unnamed else [w for w in works if not w.unnamed]

    async def get(self, work_id: int) -> Work | None:
        row = await self.storage.fetchone("SELECT * FROM works WHERE id = ?", (work_id,))
        return _work(row) if row else None

    async def get_many(self, ids) -> dict[int, Work]:
        ids = list(ids)
        if not ids:
            return {}
        rows = await self.storage.fetchall(
            f"SELECT * FROM works WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608
            ids,
        )
        return {r["id"]: _work(r) for r in rows}

    async def create(
        self,
        person_id: int,
        name: str,
        unit: Unit,
        *,
        url: str | None = None,
        hide_title: bool = False,
        keep: bool = True,
    ) -> Work:
        name = clean_name(name)
        existing = await self.for_person(person_id)
        if len(existing) >= MAX_WORKS:
            raise WorkError(t("works.too_many", max=MAX_WORKS))
        if any(w.name.casefold() == name.casefold() for w in existing):
            raise WorkError(t("works.duplicate"))
        async with self.storage.tx() as db:
            cur = await db.execute(
                """INSERT INTO works (person_id, name, unit, url, hide_title, keep, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (person_id, name, unit.value, url, int(hide_title), int(keep), iso(datetime.now(UTC))),
            )
            work_id = cur.lastrowid
        return await self.get(work_id)

    async def unnamed(self, person_id: int, unit: Unit) -> Work:
        row = await self.storage.fetchone(
            "SELECT * FROM works WHERE person_id = ? AND name IS NULL AND unit = ? AND deleted = 0",
            (person_id, unit.value),
        )
        if row is not None:
            return _work(row)
        async with self.storage.tx() as db:
            cur = await db.execute(
                "INSERT INTO works (person_id, name, unit, keep, created_at) VALUES (?, NULL, ?, 0, ?)",
                (person_id, unit.value, iso(datetime.now(UTC))),
            )
            work_id = cur.lastrowid
        return await self.get(work_id)

    async def rename(self, work: Work, name: str) -> Work:
        name = clean_name(name)
        others = [w for w in await self.for_person(work.person_id) if w.id != work.id]
        if any(w.name.casefold() == name.casefold() for w in others):
            raise WorkError(t("works.duplicate"))
        await self._update(work.id, name=name)
        return await self.get(work.id)

    async def set_link(self, work: Work, url: str | None, total: int | None) -> Work:
        """A new link starts a new count: the total becomes what the link holds now (no credit)."""
        await self._update(
            work.id, url=url, total=total, total_at=iso(datetime.now(UTC)) if total is not None else None
        )
        return await self.get(work.id)

    async def set_flag(self, work: Work, flag: str, value: bool) -> Work:
        if flag not in ("hide_title", "keep"):
            raise ValueError(flag)
        await self._update(work.id, **{flag: int(value)})
        return await self.get(work.id)

    async def set_unit(self, work: Work, unit: Unit) -> Work:
        if unit == Unit.PERCENT:  # goals are in words
            await self._update(work.id, unit=unit.value, goal_amount=None, goal_period=None)
        else:
            await self._update(work.id, unit=unit.value)
        return await self.get(work.id)

    async def set_goal(self, work: Work, goal: Goal | None) -> Work:
        if goal is not None:
            if work.unit != Unit.WORDS:
                raise WorkError(t("works.goal_words_only"))
            if not 0 < goal.amount <= MAX_GOAL:
                raise WorkError(t("works.goal_range", max=MAX_GOAL))
        await self._update(
            work.id,
            goal_amount=goal.amount if goal else None,
            goal_period=goal.period.value if goal else None,
        )
        return await self.get(work.id)

    async def delete(self, work: Work) -> None:
        """Soft delete: past sprints still show what was written."""
        await self._update(work.id, deleted=1, keep=0)

    async def _update(self, work_id: int, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        async with self.storage.tx() as db:
            await db.execute(f"UPDATE works SET {sets} WHERE id = ?", (*fields.values(), work_id))  # noqa: S608

    # --- totals (compare and set) ------------------------------------------------------------------

    @staticmethod
    async def _read_total(db: aiosqlite.Connection, work_id: int) -> int | None:
        async with db.execute("SELECT total FROM works WHERE id = ?", (work_id,)) as cur:
            row = await cur.fetchone()
        return row["total"] if row else None

    @staticmethod
    async def _cas(db: aiosqlite.Connection, work_id: int, expected: int | None, new: int | None) -> bool:
        cur = await db.execute(
            "UPDATE works SET total = ?, total_at = ? WHERE id = ? AND total IS ?",
            (new, iso(datetime.now(UTC)), work_id, expected),
        )
        return cur.rowcount == 1

    async def set_total_in(self, db: aiosqlite.Connection, work_id: int, total: int) -> TotalChange:
        """The work now holds ``total`` words (a ``=N`` report or a recount). Credits the growth since the
        last counted total; the first total ever only sets the baseline."""
        for _ in range(CAS_RETRIES):
            before = await self._read_total(db, work_id)
            if await self._cas(db, work_id, before, total):
                credited = 0 if before is None else max(0, total - before)
                return TotalChange(before, total, credited)
        raise RuntimeError(f"work {work_id}: total keeps changing")

    async def move_total_in(self, db: aiosqlite.Connection, work_id: int, delta: int) -> TotalChange:
        """A typed number changed what was credited by ``delta``: the total moves with it (if it's known),
        so the next recount credits only the rest."""
        for _ in range(CAS_RETRIES):
            before = await self._read_total(db, work_id)
            if before is None:
                return TotalChange(None, None, max(0, delta))
            after = max(0, before + delta)
            if await self._cas(db, work_id, before, after):
                return TotalChange(before, after, max(0, delta))
        raise RuntimeError(f"work {work_id}: total keeps changing")

    async def set_total(self, work_id: int, total: int) -> TotalChange:
        async with self.storage.tx() as db:
            return await self.set_total_in(db, work_id, total)

    async def linked(self) -> list[Work]:
        rows = await self.storage.fetchall(
            "SELECT * FROM works WHERE deleted = 0 AND url IS NOT NULL AND unit = 'words' ORDER BY id"
        )
        return [_work(r) for r in rows]
