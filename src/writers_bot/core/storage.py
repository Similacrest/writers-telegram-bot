"""SQLite storage: sprints and their participants, the chat registry and settings.

One connection is shared by the whole process, so writes go through :meth:`Storage.tx`, which holds a lock
for the whole transaction: otherwise another coroutine's commit could land in the middle of it. Modules
bring their own tables with :class:`ModuleSchema`; their versions are kept in ``module_versions``.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import (
    OPEN_STATUSES,
    ChatKey,
    ChatRecord,
    ChatStatus,
    Outcome,
    Participant,
    ReportRow,
    Sprint,
    SprintStatus,
    SprintWork,
    Unit,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    thread_id TEXT,
    status TEXT NOT NULL,
    duration INTEGER NOT NULL,
    planned_duration INTEGER NOT NULL,
    delay INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    report_until TEXT,
    stopped_early INTEGER NOT NULL DEFAULT 0,
    refs TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS sprints_chat ON sprints (platform, chat_id, thread_id, status);
CREATE TABLE IF NOT EXISTS participants (
    sprint_id INTEGER NOT NULL REFERENCES sprints (id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    start_words INTEGER,
    start_url TEXT,
    words INTEGER,
    reported_at TEXT,
    PRIMARY KEY (sprint_id, user_id)
);
CREATE INDEX IF NOT EXISTS participants_user ON participants (user_id);
CREATE TABLE IF NOT EXISTS chats (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT,
    username TEXT,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (platform, chat_id)
);
CREATE TABLE IF NOT EXISTS module_versions (
    module TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
"""

# Schema changes after the first release, applied in order to existing databases. Each key is the
# user_version the database has after that step.
BASE_VERSION = 2
MIGRATIONS: dict[int, list[str]] = {
    3: ["ALTER TABLE chats ADD COLUMN broadcast_excluded INTEGER NOT NULL DEFAULT 0"],
    4: [
        "ALTER TABLE sprints ADD COLUMN unit TEXT NOT NULL DEFAULT 'words'",
        """CREATE TABLE chat_settings (
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (platform, chat_id, key)
        )""",
    ],
    5: [
        """CREATE TABLE user_settings (
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (platform, user_id, key)
        )""",
        "CREATE INDEX participants_user_platform ON participants (user_id, sprint_id)",
    ],
    # v3.4: people, works, progress entries, per-work sprint entries
    6: [
        "CREATE TABLE people (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL)",
        """CREATE TABLE accounts (
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
            name TEXT,
            PRIMARY KEY (platform, user_id)
        )""",
        "CREATE INDEX accounts_person ON accounts (person_id)",
        """CREATE TABLE person_settings (
            person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (person_id, key)
        )""",
        """CREATE TABLE works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
            name TEXT,
            unit TEXT NOT NULL,
            url TEXT,
            hide_title INTEGER NOT NULL DEFAULT 0,
            keep INTEGER NOT NULL DEFAULT 1,
            total INTEGER,
            total_at TEXT,
            created_at TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0,
            goal_amount INTEGER,
            goal_period TEXT
        )""",
        "CREATE INDEX works_person ON works (person_id, deleted)",
        """CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
            work_id INTEGER REFERENCES works (id) ON DELETE SET NULL,
            unit TEXT NOT NULL,
            day TEXT NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,
            platform TEXT,
            chat_id TEXT,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX entries_person_day ON entries (person_id, day)",
        "ALTER TABLE participants ADD COLUMN person_id INTEGER",
        "ALTER TABLE participants ADD COLUMN outcome TEXT",
        "ALTER TABLE participants ADD COLUMN hide_numbers INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE participants ADD COLUMN closed INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX participants_person ON participants (person_id)",
        """CREATE TABLE sprint_works (
            sprint_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            work_id INTEGER NOT NULL,
            unit TEXT NOT NULL,
            label TEXT,
            hidden INTEGER NOT NULL DEFAULT 0,
            linked INTEGER NOT NULL DEFAULT 0,
            amount INTEGER,
            reported_at TEXT,
            PRIMARY KEY (sprint_id, user_id, work_id),
            FOREIGN KEY (sprint_id, user_id) REFERENCES participants (sprint_id, user_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX sprint_works_work ON sprint_works (work_id)",
        "ALTER TABLE chats ADD COLUMN member_count INTEGER",
    ],
}
SCHEMA_VERSION = max(MIGRATIONS)


@dataclass(frozen=True, slots=True)
class ModuleSchema:
    """A module's own tables: ``migrations[n]`` brings the module's tables to version ``n``."""

    module: str
    migrations: dict[int, list[str]] = field(default_factory=dict)


REACHABLE = [ChatStatus.MEMBER.value, ChatStatus.UNKNOWN.value]


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).isoformat()


iso = _iso
parse_dt = _dt


class Storage:
    def __init__(self, path: str | Path, modules: list[ModuleSchema] | None = None):
        self.path = Path(path)
        self.modules = list(modules or [])
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA busy_timeout = 5000")  # another process (Discord) may hold the lock
        await self._db.executescript(SCHEMA)
        await self._migrate()
        for module in self.modules:
            await self._migrate_module(module)
        await self._db.commit()

    async def _migrate(self) -> None:
        async with self._db.execute("PRAGMA user_version") as cur:
            version = max(BASE_VERSION, (await cur.fetchone())[0])
        for target in sorted(v for v in MIGRATIONS if v > version):
            for statement in MIGRATIONS[target]:
                await self._db.execute(statement)
            await self._db.execute(f"PRAGMA user_version = {target}")

    async def _migrate_module(self, module: ModuleSchema) -> None:
        async with self._db.execute("SELECT version FROM module_versions WHERE module = ?", (module.module,)) as cur:
            row = await cur.fetchone()
        version = row["version"] if row else 0
        for target in sorted(v for v in module.migrations if v > version):
            for statement in module.migrations[target]:
                await self._db.execute(statement)
            await self._db.execute(
                """INSERT INTO module_versions (module, version) VALUES (?, ?)
                   ON CONFLICT (module) DO UPDATE SET version = excluded.version""",
                (module.module, target),
            )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("storage is not open")
        return self._db

    @contextlib.asynccontextmanager
    async def tx(self) -> AsyncIterator[aiosqlite.Connection]:
        """One write transaction; committed on success, rolled back on an exception. Not re-entrant."""
        async with self._lock:
            try:
                yield self.db
            except BaseException:
                await self.db.rollback()
                raise
            await self.db.commit()

    async def fetchall(self, query: str, params=()) -> list[aiosqlite.Row]:
        async with self.db.execute(query, params) as cur:
            return list(await cur.fetchall())

    async def fetchone(self, query: str, params=()) -> aiosqlite.Row | None:
        async with self.db.execute(query, params) as cur:
            return await cur.fetchone()

    async def ping(self) -> None:
        await self.fetchone("SELECT 1")

    # --- sprints ---------------------------------------------------------------------------------

    async def create_sprint(self, sprint: Sprint) -> int:
        async with self.tx() as db:
            cur = await db.execute(
                """INSERT INTO sprints (platform, chat_id, thread_id, status, duration, planned_duration, delay,
                       created_by, created_at, start_at, end_at, report_until, stopped_early, refs, unit)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sprint.key.platform,
                    sprint.key.chat_id,
                    sprint.key.thread_id,
                    sprint.status.value,
                    sprint.duration,
                    sprint.planned_duration,
                    sprint.delay,
                    sprint.created_by,
                    _iso(sprint.created_at),
                    _iso(sprint.start_at),
                    _iso(sprint.end_at),
                    _iso(sprint.report_until),
                    int(sprint.stopped_early),
                    json.dumps(sprint.refs),
                    sprint.unit.value,
                ),
            )
            sprint.id = cur.lastrowid
            await self._save_participants(db, sprint)
        return sprint.id

    async def save_sprint(self, sprint: Sprint) -> None:
        async with self.tx() as db:
            await self.save_sprint_in(db, sprint)

    async def save_sprint_in(self, db: aiosqlite.Connection, sprint: Sprint) -> None:
        """``save_sprint`` inside a transaction the caller holds."""
        await db.execute(
            """UPDATE sprints SET status = ?, duration = ?, start_at = ?, end_at = ?, report_until = ?,
                   stopped_early = ?, refs = ? WHERE id = ?""",
            (
                sprint.status.value,
                sprint.duration,
                _iso(sprint.start_at),
                _iso(sprint.end_at),
                _iso(sprint.report_until),
                int(sprint.stopped_early),
                json.dumps(sprint.refs),
                sprint.id,
            ),
        )
        await self._save_participants(db, sprint)

    async def _save_participants(self, db: aiosqlite.Connection, sprint: Sprint) -> None:
        await db.execute(
            f"DELETE FROM participants WHERE sprint_id = ? AND user_id NOT IN ({','.join('?' * len(sprint.participants))})",  # noqa: S608
            (sprint.id, *sprint.participants),
        )
        await db.executemany(
            """INSERT INTO participants (sprint_id, user_id, name, joined_at, start_words, start_url, words,
                   reported_at, person_id, outcome, hide_numbers, closed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (sprint_id, user_id) DO UPDATE SET name = excluded.name,
                   start_words = excluded.start_words, start_url = excluded.start_url,
                   words = excluded.words, reported_at = excluded.reported_at, person_id = excluded.person_id,
                   outcome = excluded.outcome, hide_numbers = excluded.hide_numbers, closed = excluded.closed""",
            [
                (
                    sprint.id,
                    p.user_id,
                    p.name,
                    _iso(p.joined_at),
                    p.start_words,
                    p.start_url,
                    p.words,
                    _iso(p.reported_at),
                    p.person_id,
                    p.outcome.value if p.outcome else None,
                    int(p.hide_numbers),
                    int(p.closed),
                )
                for p in sprint.participants.values()
            ],
        )
        for p in sprint.participants.values():
            await db.execute(
                f"DELETE FROM sprint_works WHERE sprint_id = ? AND user_id = ? AND work_id NOT IN ({','.join('?' * len(p.works))})",  # noqa: S608
                (sprint.id, p.user_id, *p.works),
            )
        await db.executemany(
            """INSERT INTO sprint_works (sprint_id, user_id, work_id, unit, label, hidden, linked, amount, reported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (sprint_id, user_id, work_id) DO UPDATE SET unit = excluded.unit,
                   label = excluded.label, hidden = excluded.hidden, linked = excluded.linked,
                   amount = excluded.amount, reported_at = excluded.reported_at""",
            [
                (
                    sprint.id,
                    p.user_id,
                    w.work_id,
                    w.unit.value,
                    w.label,
                    int(w.hidden),
                    int(w.linked),
                    w.amount,
                    _iso(w.reported_at),
                )
                for p in sprint.participants.values()
                for w in p.works.values()
            ],
        )

    async def load_open_sprints(self, platform: str) -> list[Sprint]:
        placeholders = ",".join("?" * len(OPEN_STATUSES))
        rows = await self.fetchall(
            f"SELECT * FROM sprints WHERE platform = ? AND status IN ({placeholders}) ORDER BY id",  # noqa: S608
            (platform, *[s.value for s in OPEN_STATUSES]),
        )
        sprints = []
        for row in rows:
            sprint = Sprint(
                id=row["id"],
                key=ChatKey(row["platform"], row["chat_id"], row["thread_id"]),
                status=SprintStatus(row["status"]),
                duration=row["duration"],
                planned_duration=row["planned_duration"],
                delay=row["delay"],
                created_by=row["created_by"],
                created_at=_dt(row["created_at"]),
                start_at=_dt(row["start_at"]),
                end_at=_dt(row["end_at"]),
                report_until=_dt(row["report_until"]),
                stopped_early=bool(row["stopped_early"]),
                refs=json.loads(row["refs"] or "{}"),
                unit=Unit(row["unit"]),
            )
            for p in await self.fetchall("SELECT * FROM participants WHERE sprint_id = ?", (sprint.id,)):
                sprint.participants[p["user_id"]] = Participant(
                    user_id=p["user_id"],
                    name=p["name"],
                    joined_at=_dt(p["joined_at"]),
                    start_words=p["start_words"],
                    start_url=p["start_url"],
                    words=p["words"],
                    reported_at=_dt(p["reported_at"]),
                    person_id=p["person_id"],
                    outcome=Outcome(p["outcome"]) if p["outcome"] else None,
                    hide_numbers=bool(p["hide_numbers"]),
                    closed=bool(p["closed"]),
                )
            for w in await self.fetchall("SELECT * FROM sprint_works WHERE sprint_id = ?", (sprint.id,)):
                p = sprint.participants.get(w["user_id"])
                if p is not None:
                    p.works[w["work_id"]] = SprintWork(
                        work_id=w["work_id"],
                        unit=Unit(w["unit"]),
                        label=w["label"],
                        hidden=bool(w["hidden"]),
                        linked=bool(w["linked"]),
                        amount=w["amount"],
                        reported_at=_dt(w["reported_at"]),
                    )
            sprints.append(sprint)
        return sprints

    async def user_reports(self, platform: str, user_id: str) -> list[ReportRow]:
        """Every sprint the user took part in on this platform, oldest first (v3.3 report columns)."""
        rows = await self.fetchall(
            """SELECT s.id, s.chat_id, s.status, s.unit, s.start_at, s.duration, p.name, p.joined_at,
                      p.start_words, p.start_url, p.words, p.reported_at
               FROM participants p JOIN sprints s ON s.id = p.sprint_id
               WHERE s.platform = ? AND p.user_id = ? ORDER BY s.start_at, s.id""",
            (platform, user_id),
        )
        return [
            ReportRow(
                sprint_id=r["id"],
                chat_id=r["chat_id"],
                status=SprintStatus(r["status"]),
                unit=Unit(r["unit"]),
                start_at=_dt(r["start_at"]),
                duration=r["duration"],
                name=r["name"],
                joined_at=_dt(r["joined_at"]),
                start_words=r["start_words"],
                start_url=r["start_url"],
                words=r["words"],
                reported_at=_dt(r["reported_at"]),
            )
            for r in rows
        ]

    # --- per-user settings (v3.3, per platform account) --------------------------------------------

    async def user_settings(self, platform: str, user_id: str) -> dict[str, str]:
        rows = await self.fetchall(
            "SELECT key, value FROM user_settings WHERE platform = ? AND user_id = ?", (platform, user_id)
        )
        return {r["key"]: r["value"] for r in rows}

    async def set_user_setting(self, platform: str, user_id: str, key: str, value: str | None) -> None:
        async with self.tx() as db:
            if value is None:
                await db.execute(
                    "DELETE FROM user_settings WHERE platform = ? AND user_id = ? AND key = ?",
                    (platform, user_id, key),
                )
            else:
                await db.execute(
                    """INSERT INTO user_settings (platform, user_id, key, value) VALUES (?, ?, ?, ?)
                       ON CONFLICT (platform, user_id, key) DO UPDATE SET value = excluded.value""",
                    (platform, user_id, key, value),
                )

    # --- per-chat settings ----------------------------------------------------------------------

    async def get_chat_setting(self, platform: str, chat_id: str, key: str) -> str | None:
        row = await self.fetchone(
            "SELECT value FROM chat_settings WHERE platform = ? AND chat_id = ? AND key = ?", (platform, chat_id, key)
        )
        return row["value"] if row else None

    async def chat_settings(self, platform: str, chat_id: str) -> dict[str, str]:
        rows = await self.fetchall(
            "SELECT key, value FROM chat_settings WHERE platform = ? AND chat_id = ?", (platform, chat_id)
        )
        return {r["key"]: r["value"] for r in rows}

    async def set_chat_setting(self, platform: str, chat_id: str, key: str, value: str | None) -> None:
        async with self.tx() as db:
            if value is None:
                await db.execute(
                    "DELETE FROM chat_settings WHERE platform = ? AND chat_id = ? AND key = ?",
                    (platform, chat_id, key),
                )
            else:
                await db.execute(
                    """INSERT INTO chat_settings (platform, chat_id, key, value) VALUES (?, ?, ?, ?)
                       ON CONFLICT (platform, chat_id, key) DO UPDATE SET value = excluded.value""",
                    (platform, chat_id, key, value),
                )

    async def sprint_unit(self, platform: str, chat_id: str) -> Unit:
        value = await self.get_chat_setting(platform, chat_id, "sprint_unit")
        return Unit(value) if value in Unit._value2member_map_ else Unit.WORDS

    # --- chats the bot is in ---------------------------------------------------------------------

    async def upsert_chat(self, chat: ChatRecord, *, only_new: bool = False) -> None:
        """Record a chat sighting. ``only_new`` adds it only if unknown so far (importing old ids)."""
        now = _iso(chat.last_seen or datetime.now(UTC))
        conflict = (
            "DO NOTHING"
            if only_new
            else """DO UPDATE SET type = excluded.type,
                   title = COALESCE(excluded.title, chats.title),
                   username = COALESCE(excluded.username, chats.username),
                   status = excluded.status,
                   last_seen = MAX(chats.last_seen, excluded.last_seen),
                   member_count = COALESCE(excluded.member_count, chats.member_count)"""
        )
        async with self.tx() as db:
            await db.execute(
                f"""INSERT INTO chats (platform, chat_id, type, title, username, status, first_seen, last_seen,
                       member_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (platform, chat_id) {conflict}""",  # noqa: S608
                (
                    chat.platform,
                    chat.chat_id,
                    chat.type,
                    chat.title,
                    chat.username,
                    chat.status.value,
                    _iso(chat.first_seen) or now,
                    now,
                    chat.member_count,
                ),
            )

    async def set_chat_status(self, platform: str, chat_id: str, status: ChatStatus) -> None:
        async with self.tx() as db:
            await db.execute(
                "UPDATE chats SET status = ? WHERE platform = ? AND chat_id = ?", (status.value, platform, chat_id)
            )

    async def set_member_count(self, platform: str, chat_id: str, count: int) -> None:
        async with self.tx() as db:
            await db.execute(
                "UPDATE chats SET member_count = ? WHERE platform = ? AND chat_id = ?", (count, platform, chat_id)
            )

    async def set_broadcast_excluded(self, platform: str, chat_ids: list[str], excluded: bool) -> int:
        """Returns how many of ``chat_ids`` are known chats."""
        async with self.tx() as db:
            cur = await db.executemany(
                "UPDATE chats SET broadcast_excluded = ? WHERE platform = ? AND chat_id = ?",
                [(int(excluded), platform, chat_id) for chat_id in chat_ids],
            )
        return cur.rowcount

    async def get_chat(self, platform: str, chat_id: str) -> ChatRecord | None:
        row = await self.fetchone("SELECT * FROM chats WHERE platform = ? AND chat_id = ?", (platform, chat_id))
        return self._chat(row) if row else None

    @staticmethod
    def _chat(r) -> ChatRecord:
        return ChatRecord(
            platform=r["platform"],
            chat_id=r["chat_id"],
            type=r["type"],
            title=r["title"],
            username=r["username"],
            status=ChatStatus(r["status"]),
            first_seen=_dt(r["first_seen"]),
            last_seen=_dt(r["last_seen"]),
            broadcast_excluded=bool(r["broadcast_excluded"]),
            member_count=r["member_count"],
        )

    async def list_chats(
        self, platform: str, *, private: bool | None = None, reachable_only: bool = False
    ) -> list[ChatRecord]:
        query = "SELECT * FROM chats WHERE platform = ?"
        params: list = [platform]
        if private is True:
            query += " AND type = 'private'"
        elif private is False:
            query += " AND type != 'private'"
        if reachable_only:
            query += f" AND status IN ({','.join('?' * len(REACHABLE))})"
            params += REACHABLE
        query += " ORDER BY type = 'private', last_seen DESC"
        return [self._chat(r) for r in await self.fetchall(query, params)]
