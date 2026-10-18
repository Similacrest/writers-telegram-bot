"""Texts of the redesigned sprint flow (v3.4): the sprint message with who is in, the board, and the
private views (join card, confirmations, goals, /top, the share line).

The board has a words section and a % section ("% від плану"), each sorted high to low, then ✓ and ✗.
One line per person per section, summed over their works. No medals and no winner. People who hide their
numbers get ✓ in place of numbers; works that hide their title are never named in public.
"""

from datetime import date, datetime, timedelta

from .days import bot_day
from .fmt import Formatter
from .i18n import Markup, raw
from .models import MinutesEntry, Outcome, Participant, Sprint, SprintStatus, Unit
from .render import progress_bar
from .reporting import Change, ChatOptions, GoalReached


def works_label(f: Formatter, p: Participant, opts: ChatOptions) -> Markup | None:
    """Works shown next to a name on the sprint message: names, or a count for hidden titles."""
    if p.hide_numbers or not opts.works or not opts.work_names or not p.works:
        return None
    if all(w.label is None for w in p.works.values()):
        return None  # just the unnamed work
    names = [w.label for w in p.works.values() if w.label and not w.hidden]
    hidden = sum(1 for w in p.works.values() if w.label and w.hidden)
    parts: list = list(names)
    if hidden and names:
        parts.append(f.t("sprint.more_works", n=hidden))
    elif hidden:
        parts.append(f.plural(hidden, "works"))
    return f.join(parts)


def _who(f: Formatter, p: Participant, mention: bool) -> Markup:
    return f.mention(p.user_id, p.name) if mention else Markup(f.escape(p.name))


def sprint_message(
    f: Formatter, sprint: Sprint, now: datetime, *, mention: bool = True, hint: str = "sprint.hint_running"
) -> Markup:
    opts = ChatOptions.from_refs(sprint.refs)
    status = sprint.status
    if status == SprintStatus.PLANNED:
        minutes_left = max(1, round((sprint.start_at - now).total_seconds() / 60))
        head, when = f.t("sprint.head_planned", minutes=f.plural(minutes_left, "minutes")), "sprint.when_planned"
    elif status == SprintStatus.RUNNING:
        head, when = f.t("sprint.head_running"), "sprint.when_running"
    elif status == SprintStatus.CANCELLED:
        head, when = f.t("sprint.head_cancelled"), "sprint.when_cancelled"
    elif sprint.stopped_early:
        head, when = f.t("sprint.head_stopped"), "sprint.when_stopped"
    else:
        head, when = f.t("sprint.head_finished"), "sprint.when_finished"
    lines = [
        f.bold(head),
        f.t(
            when,
            duration=f.bold(f.plural(sprint.duration, "minutes")),
            start=f.time(sprint.start_at),
            end=f.time(sprint.end_at),
        ),
    ]
    if status == SprintStatus.RUNNING:
        total = max(1.0, (sprint.end_at - sprint.start_at).total_seconds())
        elapsed = (now - sprint.start_at).total_seconds()
        left = max(0, round((sprint.end_at - now).total_seconds() / 60))
        lines.append(f.t("sprint.bar_short", bar=f.code(progress_bar(elapsed / total)), left=left))
    people = []
    for p in sprint.participants.values():
        label = works_label(f, p, opts)
        people.append(f.join([_who(f, p, mention), label], " — ") if label else _who(f, p, mention))
    lines += ["", f.bold(f.t("sprint.participants")), *(people or ["—"])]
    if status == SprintStatus.PLANNED:
        lines += ["", f.t("sprint.hint_planned")]
    elif status == SprintStatus.RUNNING:
        lines += ["", f.t(hint)]
    return f.lines(lines)


def _amount(f: Formatter, unit: Unit, value: int) -> Markup:
    return Markup(f.escape(f"{value}%")) if unit == Unit.PERCENT else f.plural(value, "words")


def board_text(
    f: Formatter, sprint: Sprint, now: datetime, *, mention: bool = False, hint: str = "board.hint"
) -> Markup:
    final = sprint.status == SprintStatus.CLOSED
    span = f.join(
        [f.plural(sprint.duration, "minutes"), f.join([f.time(sprint.start_at), f.time(sprint.end_at)], "–")]
    )
    key = "board.title_closed" if final else "board.title_stopped" if sprint.stopped_early else "board.title_open"
    lines: list = [f.bold(f.t(key, span=span))]
    sections: dict[Unit, list[tuple[int, Participant]]] = {Unit.WORDS: [], Unit.PERCENT: []}
    done, failed, writing = [], [], []
    for p in sprint.participants.values():
        shown = False
        if not p.hide_numbers:
            for unit in (Unit.WORDS, Unit.PERCENT):
                value = p.amounts(unit)
                if value is None and unit == Unit.WORDS and not p.works and p.words is not None:
                    value = p.words  # v3.3 report
                if value is not None and (value > 0 or p.outcome is None):
                    sections[unit].append((value, p))
                    shown = True
        if shown:
            continue
        if p.outcome == Outcome.FAILED:
            failed.append(p)
        elif p.completed:
            done.append(p)
        elif p.has_report:
            failed.append(p)
        else:
            writing.append(p)
    for unit, heading in ((Unit.WORDS, "board.words"), (Unit.PERCENT, "board.percent")):
        rows = sorted(sections[unit], key=lambda e: (-e[0], e[1].reported_at or e[1].joined_at))
        if rows:
            lines += ["", f.bold(f.t(heading))]
            lines += [f.join([_who(f, p, mention), f.bold(_amount(f, unit, value))], " — ") for value, p in rows]
    marks = []
    if done:
        marks.append(f.join(["✓", f.join([_who(f, p, mention) for p in done])], " "))
    if failed:
        marks.append(f.join(["✗", f.join([_who(f, p, mention) for p in failed])], " "))
    if marks:
        lines += ["", *marks]
    summary = []
    words_rows = sections[Unit.WORDS]
    if len(words_rows) > 1:
        summary.append(f.t("board.total", total=f.bold(f.plural(sum(v for v, _ in words_rows), "words"))))
    if goal_line := goals_summary(f, sprint):
        summary.append(goal_line)
    if summary:
        lines += ["", f.join(summary, " · ")]
    if not final:
        if writing:
            lines += ["", f.t("board.writing", names=f.join([_who(f, p, mention) for p in writing]))]
        lines += ["", f.t(hint, until=f.time(sprint.report_until or now))]
    elif not any(sections.values()) and not done and not failed:
        lines += ["", f.t("board.nobody")]
    return f.lines(lines)


def goals_summary(f: Formatter, sprint: Sprint) -> Markup | None:
    """One line with the goals reached during this sprint."""
    parts = []
    for g in sprint.refs.get("goals", []):
        p = sprint.participants.get(g.get("u"))
        if p is None:
            continue
        sw = p.works.get(g.get("w"))
        title = (
            f.t("board.goal_hidden")
            if sw is None or sw.hidden or not sw.label
            else f.t("common.quoted", text=sw.label)
        )
        parts.append(f.join([p.name, title], " — "))
    return f.t("board.goals", list=f.join(parts)) if parts else None


def goal_dm_text(f: Formatter, g: GoalReached) -> Markup:
    title = f.t("common.quoted", text=g.label) if g.label else f.t("goals.untitled")
    return f.t(
        "goals.reached",
        work=title,
        progress=g.progress,
        goal=f.plural(g.amount, "words"),
        period=f.t(f"goals.per_{g.period}"),
    )


def works_card(
    f: Formatter, sprint: Sprint, p: Participant, has_works: bool, *, first_time: bool, hides: bool
) -> Markup:
    """The private card after joining. ``hides``: the person's own "hide my numbers" switch."""
    opts = ChatOptions.from_refs(sprint.refs)
    lines = [f.bold(f.t("card.joined"))]
    if has_works:
        lines.append(f.t("card.pick_works"))
    if p.hide_numbers:
        lines.append(f.t("card.numbers_hidden"))
    elif hides and not opts.hidden_ok:
        lines.append(f.t("card.numbers_shown_here"))
    if first_time:
        lines.append(f.t("card.data_notice"))
    return f.lines(lines)


def sprint_when(f: Formatter, at: datetime, tz, now: datetime) -> Markup:
    """When a sprint started, for a confirmation: "сьогодні о 14:00", "вчора о 14:00", "16.10 о 14:00"."""
    local, today = at.astimezone(tz), bot_day(now)
    time = local.strftime("%H:%M")
    day = bot_day(at)
    if day == today:
        return f.t("confirm.when_today", time=time)
    if day == today - timedelta(days=1):
        return f.t("confirm.when_yesterday", time=time)
    return f.t("confirm.when_date", date=local.strftime("%d.%m"), time=time)


def confirmation(
    f: Formatter, changes: list[Change], outcome: Outcome | None, *, corrected: Markup | None = None
) -> Markup:
    """The private confirmation of a report. ``corrected``: a % outside a sprint went to the sprint that
    started then (:func:`sprint_when`), which the confirmation says explicitly."""
    parts, notes = [], []
    for c in changes:
        name = f.t("common.quoted", text=c.label) if c.label else None
        if c.baseline:
            notes.append(
                f.t("confirm.baseline_named", work=name, total=f.plural(c.counted, "words"))
                if name and c.counted is not None
                else f.t("confirm.baseline", total=f.plural(c.counted, "words"))
                if c.counted is not None
                else f.t("confirm.baseline_plain")
            )
            continue
        if c.amount is None:
            parts.append(f.join(["✓", name], " ") if name else Markup("✓"))
            continue
        text = _amount(f, c.unit, c.amount)
        if name:
            text = f.join([name, text], ": ")
        if c.counted is not None:
            text = f.join([text, f.t("confirm.counted", n=c.counted)], " ")
        parts.append(text)
    if outcome == Outcome.DONE and not parts:
        parts.append(f.t("confirm.done"))
    if outcome == Outcome.FAILED:
        parts.append(f.t("confirm.failed"))
    if corrected is not None and parts:
        head = f.t("confirm.corrected", when=corrected, list=f.join(parts, "; "))
    else:
        head = f.t("confirm.recorded", list=f.join(parts, "; ")) if parts else None
    # never an empty message (Telegram refuses those): e.g. the same report again
    return f.join([x for x in [head, *notes] if x], " ") or f.t("confirm.nothing_new")


def minutes_top_text(f: Formatter, entries: list[MinutesEntry], days: int) -> Markup:
    if not entries:
        return f.t("top.empty", days=days)
    lines = [f.bold(f.t("top.title", days=days))]
    for e in entries:
        lines.append(
            f.t(
                "top.line",
                name=e.name,
                minutes=f.bold(f.plural(e.minutes, "minutes")),
                sprints=f.plural(e.sprints, "sprints"),
            )
        )
    return f.lines(lines)


def week_strip(f: Formatter, days: list[tuple[date, bool]]) -> Markup:
    names = raw("profile.weekdays")
    return f.join([f"{names[d.weekday()]}{'✓' if on else '·'}" for d, on in days], " ")


def share_line(f: Formatter, name: str, period: tuple[str, int, int, int], streak: int, goal: Markup | None) -> Markup:
    noun = f.t(f"share.period_{period[0]}")
    words, sprints, minutes = period[1], period[2], period[3]
    if words:
        parts = [f.t("share.words", words=f.bold(f.plural(words, "words")), period=noun)]
    elif sprints:
        parts = [
            f.t(
                "share.sprints",
                sprints=f.plural(sprints, "sprints"),
                minutes=f.plural(minutes, "minutes"),
                period=noun,
            )
        ]
    else:
        parts = [f.t("share.writing", period=noun)]
    if goal:
        parts.append(goal)
    if streak > 1:
        parts.append(f.t("share.streak", days=f.plural(streak, "days")))
    return f.t("share.line", name=name, text=f.join(parts, " · "))
