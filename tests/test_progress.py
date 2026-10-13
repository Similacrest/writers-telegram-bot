from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from writers_bot.core.models import ChatKey, Participant, ReportRow, Sprint, SprintStatus, SprintUnit
from writers_bot.core.personal import Personal
from writers_bot.core.progress import find_timezone, new_records, progress, streak_length

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)  # 23:00 in Kyiv


def row(sprint_id, start, words, minutes=30, unit=SprintUnit.WORDS, status=SprintStatus.CLOSED):
    return ReportRow(sprint_id, "-1", status, unit, start, minutes, "Ann", start, None, None, words, start)


def test_find_timezone():
    assert find_timezone("Europe/Kyiv") == "Europe/Kyiv"
    assert find_timezone("europe/warsaw") == "Europe/Warsaw"
    assert find_timezone("Kyiv") == "Europe/Kyiv"
    assert find_timezone("kiev") == "Europe/Kyiv"
    assert find_timezone("Toronto") == "America/Toronto"
    assert find_timezone("Narnia") is None


def test_streak():
    today = NOW.date()
    days = {today - timedelta(days=i) for i in (1, 2, 3)}
    assert streak_length(days, today) == 3  # today has no words yet: the streak is still alive
    assert streak_length(days | {today}, today) == 4
    assert streak_length({today - timedelta(days=2)}, today) == 0


def test_progress_uses_the_writers_day():
    rows = [
        row(1, NOW - timedelta(hours=1), 300),  # 22:00 Kyiv today
        row(2, datetime(2026, 10, 12, 21, 30, tzinfo=UTC), 200),  # 00:30 Kyiv on the 13th: today too
        row(3, datetime(2026, 10, 12, 20, 0, tzinfo=UTC), 100, minutes=5),  # 23:00 on the 12th
        row(4, NOW, 40, unit=SprintUnit.PERCENT),  # percentages are not words
        row(5, NOW, None),  # no report
        row(6, datetime(2026, 9, 30, 12, tzinfo=UTC), 1000, minutes=2),  # last month; too short for wpm
    ]
    p = progress(rows, {"goal_daily": "1000"}, KYIV, NOW)
    assert (p.today, p.month, p.streak) == (500, 600, 2)
    assert p.goals.daily == 1000 and p.goals.monthly is None
    assert p.best_words == 1000 and p.best_wpm == 20.0  # 100 words in 5 minutes
    assert progress(rows, {}, UTC, NOW).today == 300


def test_new_records():
    rows = [row(1, NOW, 300), row(2, NOW, 500, minutes=10)]
    r = new_records(rows, 2)
    assert r.words == 500 and r.wpm == 50.0
    assert new_records(rows[:1], 1).words is None  # first sprint is not a record
    assert new_records(rows, 1).words is None


async def test_personal_commands_and_notes(storage):
    personal = Personal(storage, "test", KYIV)
    assert "Kyiv" in await personal.tz_command("1", "", NOW)
    assert "Toronto" in await personal.tz_command("1", "toronto", NOW)
    assert (await storage.user_settings("test", "1"))["tz"] == "America/Toronto"
    assert await personal.goal_command("1", ["daily", "500"], NOW) is None
    assert "від 1" in await personal.goal_command("1", ["daily", "-5"], NOW)

    async def add(sid, words, start):
        sprint = Sprint(0, ChatKey("test", "-1"), SprintStatus.CLOSED, 30, 30, 0, "1", start, start,
                        start + timedelta(minutes=30),
                        participants={"1": Participant("1", "Ann", start, words=words, reported_at=start)})  # fmt: skip
        await storage.create_sprint(sprint)
        return sprint.id

    await add(1, 200, NOW - timedelta(hours=2))
    second = await add(2, 400, NOW - timedelta(hours=1))
    notes = await personal.report_notes("1", second, NOW)
    assert any("рекорд" in n for n in notes) and any("Денну ціль" in n for n in notes)
    again = await personal.report_notes("1", second, NOW)
    assert not any("Денну ціль" in n for n in again)  # announced once a day

    data, count = await personal.export_csv("1")
    assert count == 2 and b"sprint_id" in data
    assert await storage.forget_user("test", "1") == 2
    assert await storage.user_settings("test", "1") == {}
    assert await storage.user_reports("test", "1") == []
