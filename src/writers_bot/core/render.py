"""Message texts shared by the adapters. A :class:`Formatter` maps them to HTML (Telegram) or Markdown (Discord)."""

from datetime import datetime, tzinfo
from typing import Protocol

from .i18n import CHARACTERS, LETTERS, MINUTES, SPRINTS, WORDS, plural
from .models import Participant, Sprint, SprintStatus, TopEntry, UserStats
from .wordcount import TextStats

BAR_WIDTH = 20


class Formatter(Protocol):
    tz: tzinfo

    def escape(self, text: str) -> str: ...
    def bold(self, text: str) -> str: ...
    def code(self, text: str) -> str: ...
    def mention(self, user_id: str, name: str) -> str: ...
    def time(self, when: datetime) -> str: ...


def progress_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    fraction = min(1.0, max(0.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _participants(f: Formatter, sprint: Sprint) -> str:
    return ", ".join(f.mention(p.user_id, p.name) for p in sprint.participants.values()) or "—"


def sprint_text(f: Formatter, sprint: Sprint, now: datetime) -> str:
    duration = plural(sprint.duration, MINUTES)
    span = f"з {f.time(sprint.start_at)} до {f.time(sprint.end_at)}"
    status = sprint.status

    if status == SprintStatus.PLANNED:
        minutes_left = max(1, round((sprint.start_at - now).total_seconds() / 60))
        head = f"⏳ Спринт почнеться за {plural(minutes_left, MINUTES)}!"
        verb = "Писатимемо"
    elif status == SprintStatus.RUNNING:
        head, verb = "✍️ Спринт розпочався!", "Пишемо"
    elif status == SprintStatus.CANCELLED:
        head, verb = "🚫 Спринт скасовано до його початку", "Не писали"
    elif sprint.stopped_early:
        head, verb = "⏹ Спринт зупинено", "Писали всього"
    else:
        head, verb = "🏁 Спринт завершено!", "Писали"

    lines = [
        f.bold(head),
        "",
        f"{verb} {f.bold(duration)}, {span}.",
        "",
        f"{f.bold('Учасники:')} {_participants(f, sprint)}",
    ]

    if status == SprintStatus.RUNNING:
        total = max(1.0, (sprint.end_at - sprint.start_at).total_seconds())
        elapsed = (now - sprint.start_at).total_seconds()
        left = max(0, round((sprint.end_at - now).total_seconds() / 60))
        lines.append(f"{f.code(progress_bar(elapsed / total))} {round(100 * elapsed / total):d}% · ще {left} хв.")

    if status in (SprintStatus.PLANNED, SprintStatus.RUNNING):
        baselines = [p for p in sprint.participants.values() if p.start_words is not None]
        if baselines:
            lines.append("")
            lines.append("Старт: " + ", ".join(f"{f.escape(p.name)} — {p.start_words}" for p in baselines))
    return "\n".join(lines)


TELEGRAM_REPORT_HINT = (
    "Відповідайте на це повідомлення числом слів (або посиланням на текст), або надішліть /words 500."
)


def report_text(f: Formatter, sprint: Sprint, now: datetime, hint: str = TELEGRAM_REPORT_HINT) -> str:
    final = sprint.status == SprintStatus.CLOSED
    title = "🏆 Підсумки спринту" if final else "📝 Скільки написали?"
    span = f"{plural(sprint.duration, MINUTES)}, {f.time(sprint.start_at)}–{f.time(sprint.end_at)}"
    lines = [f"{f.bold(title)} ({span})", ""]

    board = sprint.leaderboard()
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(board):
        prefix = medals[i] if i < len(medals) else f"{i + 1}."
        wpm = sprint.wpm(p)
        wpm_text = f" · {wpm:.1f} сл/хв" if wpm is not None else ""
        lines.append(f"{prefix} {f.mention(p.user_id, p.name)} — {f.bold(plural(p.words, WORDS))}{wpm_text}")
    if board:
        total = sum(p.words or 0 for p in board)
        lines += ["", f"Разом: {f.bold(plural(total, WORDS))}"]

    waiting = [p for p in sprint.participants.values() if p.words is None]
    if not final:
        if waiting:
            lines += ["", "Чекаємо на звіт: " + ", ".join(f.mention(p.user_id, p.name) for p in waiting)]
        lines += [
            "",
            f"{hint} Звіти приймаються до {f.time(sprint.report_until or now)}.",
        ]
    elif not board:
        lines.append("Ніхто не прозвітував.")
    return "\n".join(lines)


def text_stats_text(f: Formatter, stats: TextStats, source: str | None = None) -> str:
    head = f"{f.escape(source)}:\n" if source else ""
    return (
        f"{head}{f.bold(plural(stats.words, WORDS))}\n"
        f"{plural(stats.characters, CHARACTERS)}\n"
        f"{plural(stats.letters, LETTERS)}"
    )


def user_stats_text(f: Formatter, name: str, stats: UserStats) -> str:
    if not stats.sprints:
        return f"{f.escape(name)}, у вас ще немає завершених спринтів тут."
    avg = stats.words / stats.reported_sprints if stats.reported_sprints else 0
    return "\n".join(
        [
            f.bold(f"Статистика: {name}"),
            f"Спринтів: {stats.sprints} (зі звітом: {stats.reported_sprints})",
            f"Хвилин у спринтах: {stats.minutes}",
            f"Написано: {f.bold(plural(stats.words, WORDS))}",
            f"Найкращий спринт: {plural(stats.best_words, WORDS)}",
            f"У середньому: {plural(round(avg), WORDS)} за спринт",
        ]
    )


def top_text(f: Formatter, entries: list[TopEntry], days: int) -> str:
    if not entries:
        return f"За останні {days} дн. ніхто не звітував про слова."
    lines = [f.bold(f"Топ за {days} дн.:")]
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. {f.escape(e.name)} — {f.bold(plural(e.words, WORDS))} ({plural(e.sprints, SPRINTS)})")
    return "\n".join(lines)


def participant_label(p: Participant) -> str:
    return p.name
