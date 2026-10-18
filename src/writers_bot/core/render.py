"""Message texts of the v2 sprint flow and the word counter, shared by the adapters. A
:class:`~.fmt.Formatter` maps them to HTML (Telegram) or Markdown (Discord)."""

from datetime import datetime

from .fmt import Formatter
from .i18n import Markup
from .models import Sprint, SprintStatus
from .wordcount import TextStats

BAR_WIDTH = 20


def progress_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    fraction = min(1.0, max(0.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def sprint_text(f: Formatter, sprint: Sprint, now: datetime) -> Markup:
    """The v2 sprint message: a headline, the time span, participants and a progress bar."""
    status = sprint.status
    if status == SprintStatus.PLANNED:
        minutes_left = max(1, round((sprint.start_at - now).total_seconds() / 60))
        head, when = f.t("sprint.head_planned_v2", minutes=f.plural(minutes_left, "minutes")), "sprint.when_planned"
    elif status == SprintStatus.RUNNING:
        head, when = f.t("sprint.head_running"), "sprint.when_running"
    elif status == SprintStatus.CANCELLED:
        head, when = f.t("sprint.head_cancelled"), "sprint.when_cancelled"
    elif sprint.stopped_early:
        head, when = f.t("sprint.head_stopped"), "sprint.when_stopped"
    else:
        head, when = f.t("sprint.head_finished"), "sprint.when_finished"
    names = f.join([f.mention(p.user_id, p.name) for p in sprint.participants.values()]) or "—"
    lines = [
        f.bold(head),
        "",
        f.t(
            when,
            duration=f.bold(f.plural(sprint.duration, "minutes")),
            start=f.time(sprint.start_at),
            end=f.time(sprint.end_at),
        ),
        "",
        f.join([f.bold(f.t("sprint.participants")), names], " "),
    ]
    if status == SprintStatus.RUNNING:
        total = max(1.0, (sprint.end_at - sprint.start_at).total_seconds())
        elapsed = (now - sprint.start_at).total_seconds()
        left = max(0, round((sprint.end_at - now).total_seconds() / 60))
        lines.append(
            f.t(
                "sprint.bar",
                bar=f.code(progress_bar(elapsed / total)),
                percent=round(100 * elapsed / total),
                left=left,
            )
        )
    if status in (SprintStatus.PLANNED, SprintStatus.RUNNING):
        baselines = [p for p in sprint.participants.values() if p.start_words is not None]
        if baselines:
            lines += ["", f.t("sprint.baselines", list=f.join([f"{p.name} — {p.start_words}" for p in baselines]))]
    return f.lines(lines)


def text_stats_text(f: Formatter, stats: TextStats, source: str | None = None) -> Markup:
    lines = []
    if source:
        lines.append(f.join([source, ":"], ""))
    lines += [
        f.bold(f.plural(stats.words, "words")),
        f.plural(stats.characters, "characters"),
        f.plural(stats.letters, "letters"),
    ]
    return f.lines(lines)
