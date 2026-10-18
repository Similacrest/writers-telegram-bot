"""``/export``: everything stored about a person, as CSV files in one ZIP."""

import csv
import io
import zipfile
from collections.abc import Awaitable, Callable

from .people import People
from .storage import Storage
from .works import Works

ExtraFiles = Callable[[int], Awaitable[dict[str, bytes]]]


def _csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM so spreadsheet apps detect UTF-8


async def export_person(
    storage: Storage, people: People, works: Works, person_id: int, extra: list[ExtraFiles] = ()
) -> tuple[bytes, int]:
    """The ZIP and the number of rows in it (0: nothing to export)."""
    accounts = await people.accounts(person_id)
    sprint_rows = []
    for a in accounts:
        rows = await storage.fetchall(
            """SELECT s.id, s.platform, s.chat_id, s.start_at, s.duration, s.status, p.joined_at, p.outcome,
                      p.words, p.start_words, p.start_url, sw.work_id, sw.label, sw.unit, sw.amount, sw.reported_at
               FROM participants p JOIN sprints s ON s.id = p.sprint_id
               LEFT JOIN sprint_works sw ON sw.sprint_id = p.sprint_id AND sw.user_id = p.user_id
               WHERE s.platform = ? AND p.user_id = ? ORDER BY s.start_at, s.id""",
            (a.platform, a.user_id),
        )
        sprint_rows += [
            [
                r["id"],
                r["platform"],
                r["chat_id"],
                r["start_at"],
                r["duration"],
                r["status"],
                r["joined_at"],
                r["outcome"] or "",
                r["work_id"] or "",
                r["label"] or "",
                r["unit"] or "",
                "" if r["amount"] is None else r["amount"],
                r["reported_at"] or "",
                "" if r["words"] is None else r["words"],
                "" if r["start_words"] is None else r["start_words"],
                r["start_url"] or "",
            ]
            for r in rows
        ]
    work_rows = [
        [
            w.id,
            w.name or "",
            w.unit.value,
            w.url or "",
            int(w.hide_title),
            int(w.keep),
            "" if w.total is None else w.total,
            w.goal.amount if w.goal else "",
            w.goal.period.value if w.goal else "",
            w.created_at.isoformat() if w.created_at else "",
        ]
        for w in await works.for_person(person_id, include_unnamed=True)
    ]
    entries = await storage.fetchall("SELECT * FROM entries WHERE person_id = ? ORDER BY day, id", (person_id,))
    entry_rows = [
        [
            e["day"],
            e["work_id"] or "",
            e["unit"],
            e["amount"],
            e["kind"],
            e["platform"] or "",
            e["chat_id"] or "",
            e["created_at"],
        ]
        for e in entries
    ]
    settings = await people.settings(person_id)
    files = {
        "sprints.csv": _csv(
            [
                "sprint_id",
                "platform",
                "chat_id",
                "start",
                "minutes",
                "status",
                "joined",
                "outcome",
                "work_id",
                "work",
                "unit",
                "amount",
                "reported_at",
                "words_v3",
                "start_words_v3",
                "start_url_v3",
            ],
            sprint_rows,
        ),
        "works.csv": _csv(
            ["work_id", "name", "unit", "url", "hide_title", "keep", "total", "goal", "goal_period", "created"],
            work_rows,
        ),
        "progress.csv": _csv(
            ["day", "work_id", "unit", "amount", "kind", "platform", "chat_id", "created"], entry_rows
        ),
        "settings.csv": _csv(["key", "value"], sorted([k, v] for k, v in settings.items())),
        "accounts.csv": _csv(
            ["platform", "user_id", "name"], [[a.platform, a.user_id, a.name or ""] for a in accounts]
        ),
    }
    count = len(sprint_rows) + len(work_rows) + len(entry_rows)
    for hook in extra:
        more = await hook(person_id)
        for name, data in more.items():
            files[name] = data
            count += max(0, data.count(b"\n") - 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue(), count
