"""Reports, works in sprints, ``/progress`` and recounts: everything that credits words.

Every credit goes through :class:`~.works.Works` compare-and-set on the work's total (see there), inside
one storage transaction together with the sprint entry or progress entry it creates. Sprint state is
changed under the sprint engine's chat lock; network calls (counting links) happen before taking it.

Which work a report goes to (PLAN phase 3): joining attaches the works kept for the next sprint; with none,
or in a chat with works off, reports go to the person's unnamed work in the chat's default unit. A typed
number goes to the only unlinked work in the sprint with that unit (without one, the only linked one), so
``520 50%`` fills a words work and a % work. Only when two works share a unit is the person asked.
"""

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum, auto

from .days import bot_day, fillable, period_id
from .i18n import t
from .links import LinkCounter, LinkError
from .models import EntryKind, Outcome, Participant, Sprint, SprintStatus, SprintWork, Unit, Work
from .people import HIDE_NUMBERS, People
from .reportparse import Item, Op, Parsed
from .sprint import MAX_TOTAL_WORDS, MAX_WORDS_PER_SPRINT, SprintEvent, SprintManager, UserRef
from .stats import history
from .storage import Storage, iso
from .works import Works, match_work

logger = logging.getLogger(__name__)

UNDO_TTL = 15 * 60
GOAL_DONE = "goal_done:"  # person setting per work: the period whose goal was announced


@dataclass(frozen=True, slots=True)
class ChatOptions:
    """Per-chat switches that a sprint takes at its start (phase 4 makes them chat settings)."""

    works: bool = False
    percent: bool = False
    default_unit: Unit = Unit.WORDS
    hidden_ok: bool = True  # "hide my numbers" honoured: off shows everyone's numbers in this chat
    work_names: bool = True  # work names on the sprint message

    def to_refs(self) -> dict:
        return {
            "works": self.works,
            "percent": self.percent,
            "unit": self.default_unit.value,
            "hidden_ok": self.hidden_ok,
            "names": self.work_names,
        }

    @classmethod
    def from_refs(cls, refs: dict) -> "ChatOptions":
        o = refs.get("opts") or {}
        return cls(
            works=bool(o.get("works", False)),
            percent=bool(o.get("percent", False)),
            default_unit=Unit(o["unit"]) if o.get("unit") in Unit._value2member_map_ else Unit.WORDS,
            hidden_ok=bool(o.get("hidden_ok", True)),
            work_names=bool(o.get("names", True)),
        )


class Status(StrEnum):
    OK = auto()
    BASELINE = auto()  # only a starting total was recorded
    NOT_JOINED = auto()
    CLOSED = auto()  # not taking reports (any more)
    NOT_STARTED = auto()
    AMBIGUOUS = auto()
    UNKNOWN_WORK = auto()
    BAD_PERCENT = auto()  # above 100, or = / a link for a % work
    PERCENT_OFF = auto()
    TOO_MANY = auto()
    LINK_ERROR = auto()
    LINK_UNKNOWN = auto()  # a link that is none of the works' links
    TOO_OLD = auto()  # /progress more than 3 days back
    NO_PERCENT_SPRINT = auto()  # a % number outside a sprint, with no sprint to correct
    PICK_SPRINT = auto()  # a % correction with several sprints to choose from
    NOTHING = auto()


@dataclass(frozen=True, slots=True)
class Change:
    work_id: int
    label: str | None
    unit: Unit
    amount: int | None  # the sprint's amount for this work after the report (or the entry amount)
    credited: int  # words newly credited (words works)
    counted: int | None = None  # words found behind a link
    baseline: bool = False


@dataclass(frozen=True, slots=True)
class GoalReached:
    person_id: int
    user_id: str
    work_id: int
    label: str | None
    hidden_title: bool
    hide_numbers: bool
    amount: int
    period: str
    progress: int


@dataclass(slots=True)
class Result:
    status: Status
    sprint: Sprint | None = None
    changes: list[Change] = field(default_factory=list)
    outcome: Outcome | None = None
    candidates: list[Work] = field(default_factory=list)  # AMBIGUOUS
    item_index: int | None = None
    hint: str | None = None
    message: str | None = None  # LINK_ERROR text
    goals: list[GoalReached] = field(default_factory=list)
    undo: str | None = None
    sprints: list[tuple[int, datetime]] = field(default_factory=list)  # PICK_SPRINT: (sprint id, start)
    day: date | None = None

    @property
    def ok(self) -> bool:
        return self.status in (Status.OK, Status.BASELINE)


@dataclass(slots=True)
class _Undo:
    user_id: str
    created: float
    sprint_id: int | None = None
    before: Participant | None = None  # snapshot of the participant
    totals: list[tuple[int, int | None, int | None]] = field(default_factory=list)  # (work, before, after)
    entries: list[int] = field(default_factory=list)
    closed_sprint_works: list[tuple] = field(default_factory=list)  # (sprint_id, user_id, work_id, old amount)


def _snapshot(p: Participant) -> Participant:
    return Participant(
        user_id=p.user_id,
        name=p.name,
        joined_at=p.joined_at,
        start_words=p.start_words,
        start_url=p.start_url,
        words=p.words,
        reported_at=p.reported_at,
        person_id=p.person_id,
        works={
            k: SprintWork(w.work_id, w.unit, w.label, w.hidden, w.linked, w.amount, w.reported_at)
            for k, w in p.works.items()
        },
        outcome=p.outcome,
        hide_numbers=p.hide_numbers,
        closed=p.closed,
    )


def _same_url(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    norm = lambda u: u.strip().rstrip("/").lower().replace("http://", "https://")  # noqa: E731
    return norm(a) == norm(b)


def sprint_work(work: Work) -> SprintWork:
    return SprintWork(
        work_id=work.id,
        unit=work.unit,
        label=work.name,
        hidden=work.hide_title,
        linked=bool(work.url) and work.unit == Unit.WORDS,
    )


class Reporting:
    def __init__(
        self,
        storage: Storage,
        people: People,
        works: Works,
        engine: SprintManager,
        links: LinkCounter | None,
        *,
        works_enabled: bool = False,
        now: Callable[[], datetime] | None = None,
    ):
        self.storage = storage
        self.people = people
        self.works = works
        self.engine = engine
        self.links = links
        self.works_enabled = works_enabled  # the module option: works exist at all in this bot
        self.now = now or engine.now
        self.platform = engine.platform
        self._undo: dict[str, _Undo] = {}

    # --- joining ---------------------------------------------------------------------------------

    def preparer(self, user: UserRef):
        """``prepare`` hook for :meth:`SprintManager.join`/``start``: person id, privacy switch, kept works."""

        async def prepare(sprint: Sprint, p: Participant) -> None:
            opts = ChatOptions.from_refs(sprint.refs)
            p.person_id = await self.people.person_id(self.platform, user.user_id, user.name)
            settings = await self.people.settings(p.person_id)
            p.hide_numbers = settings.get(HIDE_NUMBERS) == "1" and opts.hidden_ok
            if opts.works:
                for work in await self.works.for_person(p.person_id):
                    if work.keep and (opts.percent or work.unit == Unit.WORDS):
                        p.works[work.id] = sprint_work(work)
                linked = [w for w in p.works.values() if w.linked]
                if linked and self.links is not None:
                    self.engine.background(
                        self._baselines([w.work_id for w in linked]), f"sprint-{sprint.id}-baseline"
                    )

        return prepare

    async def _baselines(self, work_ids: list[int]) -> None:
        """Count linked works as they join a sprint, so growth from before it isn't credited to it."""
        for work_id in work_ids:
            work = await self.works.get(work_id)
            if work is not None and work.url:
                await self.recount(work, bot_day(self.now()))

    async def toggle_work(self, sprint: Sprint, user_id: str, work_id: int) -> bool | None:
        """Attach or detach a work; also remembers it for the next sprint. Returns the new state, or None
        if the sprint or work is gone."""
        work = await self.works.get(work_id)
        p = sprint.participants.get(user_id)
        if work is None or p is None or work.person_id != p.person_id:
            return None
        async with self.engine.lock(sprint.key):
            if self.engine.get(sprint.id) is None or user_id not in sprint.participants:
                return None
            if work_id in p.works:
                if p.works[work_id].amount is not None:
                    attached = True  # keep what was reported; only stop carrying it over
                else:
                    del p.works[work_id]
                    attached = False
            else:
                p.works[work_id] = sprint_work(work)
                attached = True
            await self.works.set_flag(work, "keep", work_id in p.works and attached)
            event = SprintEvent.PARTICIPANTS_CHANGED if sprint.is_active else SprintEvent.REPORT_UPDATED
            await self.engine.present(sprint, event)
            await self.storage.save_sprint(sprint)
        if attached and work.url and work.unit == Unit.WORDS and sprint.is_active and self.links is not None:
            self.engine.background(self._baselines([work.id]), f"sprint-{sprint.id}-baseline")
        return attached

    async def refresh_privacy(self, person_id: int, hide: bool) -> None:
        """Apply a changed "hide my numbers" switch to open sprints."""
        for sprint in self.engine.open_sprints():
            opts = ChatOptions.from_refs(sprint.refs)
            for p in sprint.participants.values():
                if p.person_id == person_id and p.hide_numbers != (hide and opts.hidden_ok):
                    async with self.engine.lock(sprint.key):
                        p.hide_numbers = hide and opts.hidden_ok
                        event = SprintEvent.PARTICIPANTS_CHANGED if sprint.is_active else SprintEvent.REPORT_UPDATED
                        await self.engine.present(sprint, event)
                        await self.storage.save_sprint(sprint)

    async def refresh_work(self, work: Work) -> None:
        """A work was renamed or its title hidden: update open sprints that show it."""
        for sprint in self.engine.open_sprints():
            for p in sprint.participants.values():
                sw = p.works.get(work.id)
                if sw is None or (sw.label == work.name and sw.hidden == work.hide_title):
                    continue
                async with self.engine.lock(sprint.key):
                    sw.label, sw.hidden = work.name, work.hide_title
                    event = SprintEvent.PARTICIPANTS_CHANGED if sprint.is_active else SprintEvent.REPORT_UPDATED
                    await self.engine.present(sprint, event)
                    await self.storage.save_sprint(sprint)

    # --- resolving items to works ------------------------------------------------------------------

    async def _resolve(
        self,
        items: list[Item],
        person_id: int,
        candidates: list[Work],
        all_works: list[Work],
        opts: ChatOptions,
        choice: dict[int, int],
    ) -> tuple[list[tuple[Item, Work]], Result | None]:
        out: list[tuple[Item, Work]] = []
        for i, item in enumerate(items):
            if item.op in (Op.DONE, Op.FAILED) and not item.hint:
                continue
            if i in choice:
                work = next((w for w in all_works if w.id == choice[i]), None)
                if work is None:
                    return [], Result(Status.UNKNOWN_WORK, hint=item.hint)
                out.append((item, work))
                continue
            if item.hint:
                work = match_work([w for w in all_works if w.name], item.hint) if opts.works else None
                if work is None:
                    return [], Result(Status.UNKNOWN_WORK, hint=item.hint)
                if item.percent and work.unit != Unit.PERCENT:
                    return [], Result(Status.BAD_PERCENT, hint=item.hint)
                out.append((item, work))
                continue
            if item.url:
                same = [w for w in candidates if _same_url(w.url, item.url)] or [
                    w for w in all_works if _same_url(w.url, item.url)
                ]
                if same:
                    out.append((item, same[0]))
                    continue
                named = [w for w in candidates if w.name and w.unit == Unit.WORDS]
                if named:
                    return [], Result(Status.LINK_UNKNOWN)
                out.append((item, await self._unnamed_with_link(person_id, item.url)))
                continue
            unit = self._unit_for(item, candidates, opts)
            if unit == Unit.PERCENT and not opts.percent:
                return [], Result(Status.PERCENT_OFF)
            same_unit = [w for w in candidates if w.unit == unit]
            unlinked = [w for w in same_unit if not w.url]
            linked = [w for w in same_unit if w.url]
            pool = unlinked or linked
            if len(pool) == 1:
                out.append((item, pool[0]))
            elif not pool:
                out.append((item, await self.works.unnamed(person_id, unit)))
            else:
                return [], Result(Status.AMBIGUOUS, candidates=pool, item_index=i)
        return out, None

    @staticmethod
    def _unit_for(item: Item, candidates: list[Work], opts: ChatOptions) -> Unit:
        if item.percent:
            return Unit.PERCENT
        units = {w.unit for w in candidates}
        if Unit.WORDS in units:
            return Unit.WORDS
        if Unit.PERCENT in units:
            return Unit.PERCENT
        return opts.default_unit if opts.percent else Unit.WORDS

    async def _unnamed_with_link(self, person_id: int, url: str) -> Work:
        work = await self.works.unnamed(person_id, Unit.WORDS)
        if not _same_url(work.url, url):
            work = await self.works.set_link(work, url, None)
        return work

    async def _counts(self, pairs: list[tuple[Item, Work]]) -> tuple[dict[int, int], str | None]:
        """Count the links of TOTAL items (network, before any lock)."""
        counts: dict[int, int] = {}
        for item, _work in pairs:
            if item.op == Op.TOTAL and item.url:
                if self.links is None:
                    return {}, t("links.not_counted")
                try:
                    counts[id(item)] = await self.links.count(item.url)
                except LinkError as e:
                    return {}, str(e)
        return counts, None

    # --- reports in a sprint -------------------------------------------------------------------------

    async def report(
        self, sprint: Sprint, user: UserRef, parsed: Parsed, choice: dict[int, int] | None = None
    ) -> Result:
        p = sprint.participants.get(user.user_id)
        if p is None:
            return Result(Status.NOT_JOINED, sprint)
        if sprint.status == SprintStatus.PLANNED:
            return Result(Status.NOT_STARTED, sprint)
        if not self.engine.accepting(sprint, user.user_id):
            return Result(Status.CLOSED, sprint)
        opts = ChatOptions.from_refs(sprint.refs)
        if p.person_id is None:
            p.person_id = await self.people.person_id(self.platform, user.user_id, user.name)
        all_works = await self.works.for_person(p.person_id, include_unnamed=True) if self.works_enabled else []
        by_id = {w.id: w for w in all_works}
        candidates = [by_id[wid] for wid in p.works if wid in by_id]
        pairs, problem = await self._resolve(parsed.items, p.person_id, candidates, all_works, opts, choice or {})
        if problem is not None:
            problem.sprint = sprint
            return problem
        # ✓ / ✗ are about the whole sprint, also when they name a work ("✓ роман" attaches it too).
        outcome = next((i.outcome for i in parsed.items if i.outcome), None)
        if not pairs and outcome is None:
            return Result(Status.NOTHING, sprint)
        if error := self._validate(pairs):
            error.sprint = sprint
            return error
        counts, link_error = await self._counts(pairs)
        if link_error:
            return Result(Status.LINK_ERROR, sprint, message=link_error)

        async with self.engine.lock(sprint.key):
            if self.engine.get(sprint.id) is None or not self.engine.accepting(sprint, user.user_id):
                return Result(Status.CLOSED, sprint)
            p = sprint.participants[user.user_id]
            undo = _Undo(user.user_id, time.monotonic(), sprint.id, _snapshot(p))
            changes: list[Change] = []
            now = self.now()
            async with self.storage.tx() as db:
                for item, work in pairs:
                    sw = p.works.get(work.id)
                    if sw is None:
                        sw = p.works[work.id] = sprint_work(work)
                    if item.op in (Op.DONE, Op.FAILED):
                        continue
                    change = await self._apply(db, sw, work, item, counts.get(id(item)), undo)
                    if change is None:
                        continue
                    sw.reported_at = now
                    changes.append(change)
                if outcome is not None:
                    p.outcome = outcome
                elif changes:
                    p.outcome = None
                p.reported_at = now
                p.name = user.name
                await self.storage.save_sprint_in(db, sprint)
            goals = await self._goals(p, [c.work_id for c in changes if c.unit == Unit.WORDS and c.credited])
            for g in goals:
                sprint.refs.setdefault("goals", []).append({"u": g.user_id, "w": g.work_id})
            event = (
                SprintEvent.REPORT_UPDATED
                if sprint.status == SprintStatus.REPORTING
                else SprintEvent.PARTICIPANTS_CHANGED
            )
            await self.engine.present(sprint, event)
            if goals:
                await self.storage.save_sprint(sprint)
        token = self._remember(undo)
        status = Status.BASELINE if changes and all(c.baseline for c in changes) else Status.OK
        return Result(status, sprint, changes, outcome, goals=goals, undo=token)

    def _validate(self, pairs: list[tuple[Item, Work]]) -> Result | None:
        for item, work in pairs:
            if work.unit == Unit.PERCENT:
                if item.op == Op.TOTAL or (item.value or 0) > 100:
                    return Result(Status.BAD_PERCENT, hint=work.name)
            elif item.op in (Op.SET, Op.ADD) and (item.value or 0) > MAX_WORDS_PER_SPRINT:
                return Result(Status.TOO_MANY)
            elif item.op == Op.TOTAL and item.value is not None and item.value > MAX_TOTAL_WORDS:
                return Result(Status.TOO_MANY)
        return None

    async def _apply(
        self, db, sw: SprintWork, work: Work, item: Item, counted: int | None, undo: _Undo
    ) -> Change | None:
        old = sw.amount or 0
        if work.unit == Unit.PERCENT:
            new = item.value if item.op == Op.SET else min(100, old + (item.value or 0))
            sw.amount = new
            return Change(work.id, work.name, Unit.PERCENT, new, 0)
        if item.op in (Op.SET, Op.ADD):
            new = item.value if item.op == Op.SET else old + item.value
            new = min(new, MAX_WORDS_PER_SPRINT)
            tc = await self.works.move_total_in(db, work.id, new - old)
            undo.totals.append((work.id, tc.before, tc.after))
            sw.amount = new
            return Change(work.id, work.name, Unit.WORDS, new, max(0, new - old))
        total = counted if counted is not None else item.value
        tc = await self.works.set_total_in(db, work.id, total)
        undo.totals.append((work.id, tc.before, tc.after))
        if tc.before is None:
            return Change(work.id, work.name, Unit.WORDS, sw.amount, 0, counted, baseline=True)
        sw.amount = min(old + tc.credited, MAX_WORDS_PER_SPRINT)
        return Change(work.id, work.name, Unit.WORDS, sw.amount, tc.credited, counted)

    async def set_outcome(self, sprint: Sprint, user: UserRef, outcome: Outcome) -> Result:
        p = sprint.participants.get(user.user_id)
        if p is None:
            return Result(Status.NOT_JOINED, sprint)
        if not self.engine.accepting(sprint, user.user_id):
            return Result(Status.CLOSED if sprint.status != SprintStatus.PLANNED else Status.NOT_STARTED, sprint)
        async with self.engine.lock(sprint.key):
            undo = _Undo(user.user_id, time.monotonic(), sprint.id, _snapshot(p))
            p.outcome = outcome
            p.reported_at = self.now()
            event = (
                SprintEvent.REPORT_UPDATED
                if sprint.status == SprintStatus.REPORTING
                else SprintEvent.PARTICIPANTS_CHANGED
            )
            await self.engine.present(sprint, event)
            await self.storage.save_sprint(sprint)
        return Result(Status.OK, sprint, outcome=outcome, undo=self._remember(undo))

    # --- undo ------------------------------------------------------------------------------------

    def _remember(self, undo: _Undo) -> str:
        now = time.monotonic()
        self._undo = {k: v for k, v in self._undo.items() if now - v.created < UNDO_TTL}
        token = secrets.token_hex(5)
        self._undo[token] = undo
        return token

    async def undo(self, token: str, user_id: str) -> bool:
        record = self._undo.get(token)
        if record is None or record.user_id != user_id or time.monotonic() - record.created > UNDO_TTL:
            return False
        del self._undo[token]
        sprint = self.engine.get(record.sprint_id) if record.sprint_id is not None else None
        if record.sprint_id is not None and sprint is None:
            return False

        async def revert(db) -> None:
            for work_id, before, after in reversed(record.totals):
                if before == after:
                    continue
                if not await self.works._cas(db, work_id, after, before):
                    current = await self.works._read_total(db, work_id)
                    if current is not None and before is not None and after is not None:
                        await self.works._cas(db, work_id, current, max(0, current + before - after))
            for entry_id in record.entries:
                await db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            for sid, uid, wid, amount in record.closed_sprint_works:
                await db.execute(
                    "UPDATE sprint_works SET amount = ? WHERE sprint_id = ? AND user_id = ? AND work_id = ?",
                    (amount, sid, uid, wid),
                )

        if sprint is None:
            async with self.storage.tx() as db:
                await revert(db)
            return True
        async with self.engine.lock(sprint.key):
            if self.engine.get(sprint.id) is None or user_id not in sprint.participants:
                return False
            async with self.storage.tx() as db:
                await revert(db)
                current = sprint.participants[user_id]
                restored = record.before
                restored.closed = current.closed
                restored.hide_numbers = current.hide_numbers
                sprint.participants[user_id] = restored
                await self.storage.save_sprint_in(db, sprint)
            event = (
                SprintEvent.REPORT_UPDATED
                if sprint.status == SprintStatus.REPORTING
                else SprintEvent.PARTICIPANTS_CHANGED
            )
            await self.engine.present(sprint, event)
        return True

    # --- the end of a sprint -----------------------------------------------------------------------

    async def after_finish(self, sprint: Sprint) -> None:
        """Recount every linked work in the sprint and credit the growth to it."""
        if self.links is None:
            return
        todo = [(p.user_id, sw.work_id) for p in sprint.participants.values() for sw in p.works.values() if sw.linked]
        counts: dict[int, int] = {}
        for _user_id, work_id in todo:
            work = await self.works.get(work_id)
            if work is None or not work.url or work_id in counts:
                continue
            try:
                counts[work_id] = await self.links.count(work.url)
            except LinkError as e:
                logger.info("sprint %s: could not recount work %s: %s", sprint.id, work_id, e)
        if not counts:
            return
        goals: list[GoalReached] = []
        async with self.engine.lock(sprint.key):
            if self.engine.get(sprint.id) is None:
                return
            touched: dict[str, list[int]] = {}
            async with self.storage.tx() as db:
                for user_id, work_id in todo:
                    p = sprint.participants.get(user_id)
                    sw = p.works.get(work_id) if p else None
                    if sw is None or work_id not in counts:
                        continue
                    tc = await self.works.set_total_in(db, work_id, counts.pop(work_id))
                    if tc.credited:
                        sw.amount = min((sw.amount or 0) + tc.credited, MAX_WORDS_PER_SPRINT)
                        sw.reported_at = self.now()
                        touched.setdefault(user_id, []).append(work_id)
                    elif sw.amount is None and tc.before is not None:
                        sw.amount = 0
                await self.storage.save_sprint_in(db, sprint)
            for user_id, work_ids in touched.items():
                goals += await self._goals(sprint.participants[user_id], work_ids)
            for g in goals:
                sprint.refs.setdefault("goals", []).append({"u": g.user_id, "w": g.work_id})
            await self.engine.present(sprint, SprintEvent.REPORT_UPDATED)
            await self.storage.save_sprint(sprint)

    # --- goals -----------------------------------------------------------------------------------

    async def _goals(self, p: Participant, work_ids: list[int]) -> list[GoalReached]:
        if p.person_id is None or not work_ids:
            return []
        works = [w for w in (await self.works.get_many(work_ids)).values() if w.goal is not None]
        if not works:
            return []
        return await self.goals_reached(p.person_id, p.user_id, works)

    async def goals_reached(self, person_id: int, user_id: str, works: list[Work]) -> list[GoalReached]:
        """Goals newly reached in their current period; each is announced once per period."""
        h = await history(self.storage, person_id)
        settings = await self.people.settings(person_id)
        today = bot_day(self.now())
        reached = []
        for work in works:
            done = h.work_period(work, today, work.goal.period)
            pid = period_id(today, work.goal.period)
            if done >= work.goal.amount and settings.get(GOAL_DONE + str(work.id)) != pid:
                await self.people.set(person_id, GOAL_DONE + str(work.id), pid)
                reached.append(
                    GoalReached(
                        person_id,
                        user_id,
                        work.id,
                        work.name,
                        work.hide_title,
                        settings.get(HIDE_NUMBERS) == "1",
                        work.goal.amount,
                        work.goal.period.value,
                        done,
                    )
                )
        return reached

    # --- outside sprints ---------------------------------------------------------------------------

    async def progress(
        self,
        user: UserRef,
        parsed: Parsed,
        *,
        chat_id: str | None = None,
        default_unit: Unit = Unit.WORDS,
        percent: bool = True,
        choice: dict[int, int] | None = None,
        sprint_choice: int | None = None,
    ) -> Result:
        """``/progress`` outside a sprint: numbers add, ``=``/links are totals, ✓ marks a writing day,
        and a % number corrects one of your sprints in the last 3 days."""
        person_id = await self.people.person_id(self.platform, user.user_id, user.name)
        today = bot_day(self.now())
        day = parsed.day or today
        if not fillable(day, today):
            return Result(Status.TOO_OLD, day=day)
        opts = ChatOptions(works=self.works_enabled, percent=percent, default_unit=default_unit)
        all_works = await self.works.for_person(person_id, include_unnamed=True) if self.works_enabled else []
        named = [w for w in all_works if w.name]
        candidates = named or [w for w in all_works if w.name is None]
        pairs, problem = await self._resolve(parsed.items, person_id, candidates, all_works, opts, choice or {})
        if problem is not None:
            return problem
        marks = [i for i in parsed.items if i.op == Op.DONE]
        if any(i.op == Op.FAILED for i in parsed.items) and not pairs and not marks:
            return Result(Status.NOTHING)
        percent_pairs = [(i, w) for i, w in pairs if w.unit == Unit.PERCENT and i.op not in (Op.DONE, Op.FAILED)]
        if percent_pairs:
            return await self._correct_percent(user, person_id, percent_pairs[0], today, sprint_choice)
        if error := self._validate(pairs):
            return error
        counts, link_error = await self._counts(pairs)
        if link_error:
            return Result(Status.LINK_ERROR, message=link_error)
        undo = _Undo(user.user_id, time.monotonic())
        changes: list[Change] = []
        now = self.now()
        async with self.storage.tx() as db:
            for item, work in pairs:
                if item.op in (Op.DONE, Op.FAILED):
                    if item.op == Op.DONE:
                        undo.entries.append(
                            await self._entry(db, person_id, work, day, 0, EntryKind.MARK, chat_id, now)
                        )
                        changes.append(Change(work.id, work.name, work.unit, None, 0))
                    continue
                if item.op in (Op.SET, Op.ADD):
                    tc = await self.works.move_total_in(db, work.id, item.value)
                    credited = item.value
                    baseline = False
                else:
                    tc = await self.works.set_total_in(db, work.id, counts.get(id(item), item.value))
                    credited, baseline = tc.credited, tc.before is None
                undo.totals.append((work.id, tc.before, tc.after))
                if credited:
                    undo.entries.append(
                        await self._entry(db, person_id, work, day, credited, EntryKind.PROGRESS, chat_id, now)
                    )
                changes.append(
                    Change(work.id, work.name, work.unit, credited, credited, counts.get(id(item)), baseline)
                )
            if marks and not any(i.op == Op.DONE and i.hint for i in parsed.items) and not pairs:
                work = await self.works.unnamed(person_id, Unit.WORDS) if not named else named[0]
                undo.entries.append(await self._entry(db, person_id, work, day, 0, EntryKind.MARK, chat_id, now))
                changes.append(Change(work.id, None, work.unit, None, 0))
        goals = []
        credited_works = [c.work_id for c in changes if c.credited and c.unit == Unit.WORDS]
        if credited_works:
            works = [w for w in (await self.works.get_many(credited_works)).values() if w.goal is not None]
            if works:
                goals = await self.goals_reached(person_id, user.user_id, works)
        status = Status.BASELINE if changes and all(c.baseline for c in changes) else Status.OK
        return Result(
            status, None, changes, Outcome.DONE if marks else None, goals=goals, undo=self._remember(undo), day=day
        )

    async def _entry(self, db, person_id, work: Work, day: date, amount: int, kind: EntryKind, chat_id, now) -> int:
        cur = await db.execute(
            """INSERT INTO entries (person_id, work_id, unit, day, amount, kind, platform, chat_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                person_id,
                work.id,
                work.unit.value,
                day.isoformat(),
                amount,
                kind.value,
                self.platform,
                chat_id,
                iso(now),
            ),
        )
        return cur.lastrowid

    async def _correct_percent(
        self, user: UserRef, person_id: int, pair: tuple[Item, Work], today: date, sprint_choice: int | None
    ) -> Result:
        item, work = pair
        if item.op == Op.TOTAL or (item.value or 0) > 100:
            return Result(Status.BAD_PERCENT, hint=work.name)
        rows = await self.storage.fetchall(
            """SELECT s.id, s.start_at FROM sprint_works sw JOIN sprints s ON s.id = sw.sprint_id
               WHERE sw.work_id = ? AND sw.user_id = ? AND s.platform = ? AND s.status IN ('reporting', 'closed')
               ORDER BY s.start_at DESC""",
            (work.id, user.user_id, self.platform),
        )
        recent = [(r["id"], datetime.fromisoformat(r["start_at"])) for r in rows]
        recent = [(sid, at) for sid, at in recent if fillable(bot_day(at), today)]
        if sprint_choice is not None:
            recent = [(sid, at) for sid, at in recent if sid == sprint_choice]
        if not recent:
            return Result(Status.NO_PERCENT_SPRINT, hint=work.name)
        if len(recent) > 1:
            return Result(Status.PICK_SPRINT, sprints=recent, hint=work.name)
        sprint_id = recent[0][0]
        open_sprint = self.engine.get(sprint_id)
        if open_sprint is not None:
            p = open_sprint.participants.get(user.user_id)
            if p is not None and work.id in p.works:
                async with self.engine.lock(open_sprint.key):
                    undo = _Undo(user.user_id, time.monotonic(), sprint_id, _snapshot(p))
                    sw = p.works[work.id]
                    sw.amount = item.value if item.op == Op.SET else min(100, (sw.amount or 0) + item.value)
                    sw.reported_at = self.now()
                    await self.engine.present(open_sprint, SprintEvent.REPORT_UPDATED)
                    await self.storage.save_sprint(open_sprint)
                return Result(
                    Status.OK,
                    open_sprint,
                    [Change(work.id, work.name, Unit.PERCENT, sw.amount, 0)],
                    undo=self._remember(undo),
                )
        row = await self.storage.fetchone(
            "SELECT amount FROM sprint_works WHERE sprint_id = ? AND user_id = ? AND work_id = ?",
            (sprint_id, user.user_id, work.id),
        )
        old = row["amount"] if row else None
        new = item.value if item.op == Op.SET else min(100, (old or 0) + item.value)
        undo = _Undo(user.user_id, time.monotonic(), closed_sprint_works=[(sprint_id, user.user_id, work.id, old)])
        async with self.storage.tx() as db:
            await db.execute(
                "UPDATE sprint_works SET amount = ?, reported_at = ? WHERE sprint_id = ? AND user_id = ? AND work_id = ?",
                (new, iso(self.now()), sprint_id, user.user_id, work.id),
            )
        return Result(Status.OK, None, [Change(work.id, work.name, Unit.PERCENT, new, 0)], undo=self._remember(undo))

    # --- recounts --------------------------------------------------------------------------------

    async def recount(self, work: Work, day: date) -> int | None:
        """Count a linked work outside a sprint and credit its growth to ``day``. None if it failed."""
        if self.links is None or not work.url:
            return None
        try:
            count = await self.links.count(work.url)
        except LinkError as e:
            logger.info("could not recount work %s: %s", work.id, e)
            return None
        async with self.storage.tx() as db:
            tc = await self.works.set_total_in(db, work.id, count)
            if tc.credited:
                await self._entry(db, work.person_id, work, day, tc.credited, EntryKind.RECOUNT, None, self.now())
        return tc.credited

    async def daily_recount(self) -> int:
        """At the day's reset: recount linked works that are not in a running sprint (that sprint will
        count them itself) and credit the growth to the day that just ended."""
        busy = {
            sw.work_id
            for s in self.engine.open_sprints()
            if s.is_active
            for p in s.participants.values()
            for sw in p.works.values()
        }
        day = bot_day(self.now()) - timedelta(days=1)
        credited = 0
        for work in await self.works.linked():
            if work.id in busy:
                continue
            credited += await self.recount(work, day) or 0
        return credited
