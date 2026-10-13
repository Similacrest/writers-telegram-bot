"""Per-user commands shared by the adapters: time zone, goals, progress notes after a report, export."""

import csv
import io
from datetime import datetime, tzinfo

from .i18n import WORDS, plural
from .progress import (
    DAILY_KEY,
    MAX_GOAL,
    MONTHLY_KEY,
    TZ_KEY,
    Progress,
    find_timezone,
    new_records,
    progress,
    user_timezone,
)
from .storage import Storage

DAILY_DONE_KEY = "goal_daily_done"  # last day the daily goal was announced
MONTHLY_DONE_KEY = "goal_monthly_done"


class Personal:
    def __init__(self, storage: Storage, platform: str, default_tz: tzinfo):
        self.storage = storage
        self.platform = platform
        self.default_tz = default_tz

    async def progress(self, user_id: str, now: datetime) -> Progress:
        settings = await self.storage.user_settings(self.platform, user_id)
        rows = await self.storage.user_reports(self.platform, user_id)
        return progress(rows, settings, user_timezone(settings, self.default_tz), now)

    # --- /tz ---------------------------------------------------------------------------------------

    async def tz_command(self, user_id: str, arg: str, now: datetime) -> str:
        settings = await self.storage.user_settings(self.platform, user_id)
        if not arg.strip():
            tz = user_timezone(settings, self.default_tz)
            own = TZ_KEY in settings
            local = now.astimezone(tz).strftime("%H:%M")
            return (
                f"Ваш часовий пояс: {tz}{'' if own else ' (типовий)'}, зараз там {local}.\n"
                "Змінити: /tz Europe/Kyiv (або просто /tz Kyiv). Від нього залежить, коли починається день "
                "для цілей і днів поспіль."
            )
        if arg.strip().lower() in {"off", "reset", "default"}:
            await self.storage.set_user_setting(self.platform, user_id, TZ_KEY, None)
            return f"Повернуто типовий часовий пояс: {self.default_tz}."
        name = find_timezone(arg)
        if name is None:
            return "Не знаю такого часового поясу. Приклади: Europe/Kyiv, Europe/Warsaw, America/Toronto."
        await self.storage.set_user_setting(self.platform, user_id, TZ_KEY, name)
        return (
            f"Часовий пояс: {name}, зараз там {now.astimezone(user_timezone({TZ_KEY: name}, self.default_tz)):%H:%M}."
        )

    # --- /goal -------------------------------------------------------------------------------------

    async def goal_command(self, user_id: str, args: list[str], now: datetime) -> str | None:
        """Returns an error/usage text, or None after a change (the caller then shows the goals)."""
        if not args:
            return None
        kinds = {"daily": DAILY_KEY, "день": DAILY_KEY, "monthly": MONTHLY_KEY, "місяць": MONTHLY_KEY}
        kind = kinds.get(args[0].lower())
        value = args[1].lower() if len(args) > 1 else ""
        if kind is None or not value:
            return "Використання: /goal daily 500, /goal monthly 20000, /goal daily off."
        if value in {"off", "0", "ні"}:
            await self.storage.set_user_setting(self.platform, user_id, kind, None)
            return None
        digits = value.replace("_", "").replace(" ", "")
        if not digits.isdigit() or not 0 < int(digits) <= MAX_GOAL:
            return f"Ціль має бути числом від 1 до {MAX_GOAL}."
        await self.storage.set_user_setting(self.platform, user_id, kind, str(int(digits)))
        return None

    # --- after a word report -----------------------------------------------------------------------

    async def report_notes(self, user_id: str, sprint_id: int, now: datetime) -> list[str]:
        """Short public notes after a report: new personal records and goals reached (once per day/month)."""
        settings = await self.storage.user_settings(self.platform, user_id)
        rows = await self.storage.user_reports(self.platform, user_id)
        tz = user_timezone(settings, self.default_tz)
        notes = []
        records = new_records(rows, sprint_id)
        if records.words:
            notes.append(f"Новий особистий рекорд: {plural(records.words, WORDS)} за спринт.")
        if records.wpm:
            notes.append(f"Новий рекорд швидкості: {records.wpm:.1f} сл/хв.")
        p = progress(rows, settings, tz, now)
        local = now.astimezone(tz)
        day, month = local.date().isoformat(), f"{local:%Y-%m}"
        if p.goals.daily and p.today >= p.goals.daily and settings.get(DAILY_DONE_KEY) != day:
            await self.storage.set_user_setting(self.platform, user_id, DAILY_DONE_KEY, day)
            notes.append(f"Денну ціль виконано: {p.today} з {plural(p.goals.daily, WORDS)}.")
        if p.goals.monthly and p.month >= p.goals.monthly and settings.get(MONTHLY_DONE_KEY) != month:
            await self.storage.set_user_setting(self.platform, user_id, MONTHLY_DONE_KEY, month)
            notes.append(f"Місячну ціль виконано: {p.month} з {plural(p.goals.monthly, WORDS)}.")
        return notes

    # --- /export -----------------------------------------------------------------------------------

    async def export_csv(self, user_id: str) -> tuple[bytes, int]:
        rows = await self.storage.user_reports(self.platform, user_id)
        settings = await self.storage.user_settings(self.platform, user_id)
        tz = user_timezone(settings, self.default_tz)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["sprint_id", "chat_id", "start", "minutes", "unit", "result", "start_words", "start_url", "reported_at"]
        )
        for r in rows:
            writer.writerow(
                [
                    r.sprint_id,
                    r.chat_id,
                    r.start_at.astimezone(tz).isoformat(timespec="minutes"),
                    r.duration,
                    r.unit.value,
                    "" if r.words is None else r.words,
                    "" if r.start_words is None else r.start_words,
                    r.start_url or "",
                    r.reported_at.astimezone(tz).isoformat(timespec="minutes") if r.reported_at else "",
                ]
            )
        return buf.getvalue().encode("utf-8-sig"), len(rows)
