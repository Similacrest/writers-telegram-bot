"""SQLite storage for sprints, participants and word reports."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import (
    OPEN_STATUSES,
    ChatKey,
    ChatRecord,
    ChatStatus,
    Participant,
    Sprint,
    SprintStatus,
    SprintUnit,
    TopEntry,
    UserStats,
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
}
SCHEMA_VERSION = max(MIGRATIONS)


REACHABLE = [ChatStatus.MEMBER.value, ChatStatus.UNKNOWN.value]


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).isoformat()


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        async with self._db.execute("PRAGMA user_version") as cur:
            version = max(BASE_VERSION, (await cur.fetchone())[0])
        for target in sorted(v for v in MIGRATIONS if v > version):
            for statement in MIGRATIONS[target]:
                await self._db.execute(statement)
            await self._db.execute(f"PRAGMA user_version = {target}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("storage is not open")
        return self._db

    async def create_sprint(self, sprint: Sprint) -> int:
        cur = await self.db.execute(
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
        await self._save_participants(sprint)
        await self.db.commit()
        return sprint.id

    async def save_sprint(self, sprint: Sprint) -> None:
        await self.db.execute(
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
        await self._save_participants(sprint)
        await self.db.commit()

    async def _save_participants(self, sprint: Sprint) -> None:
        await self.db.execute(
            f"DELETE FROM participants WHERE sprint_id = ? AND user_id NOT IN ({','.join('?' * len(sprint.participants))})",  # noqa: S608
            (sprint.id, *sprint.participants),
        )
        await self.db.executemany(
            """INSERT INTO participants (sprint_id, user_id, name, joined_at, start_words, start_url, words, reported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (sprint_id, user_id) DO UPDATE SET name = excluded.name,
                   start_words = excluded.start_words, start_url = excluded.start_url,
                   words = excluded.words, reported_at = excluded.reported_at""",
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
                )
                for p in sprint.participants.values()
            ],
        )

    async def load_open_sprints(self, platform: str) -> list[Sprint]:
        placeholders = ",".join("?" * len(OPEN_STATUSES))
        async with self.db.execute(
            f"SELECT * FROM sprints WHERE platform = ? AND status IN ({placeholders}) ORDER BY id",  # noqa: S608
            (platform, *[s.value for s in OPEN_STATUSES]),
        ) as cur:
            rows = await cur.fetchall()
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
                unit=SprintUnit(row["unit"]),
            )
            async with self.db.execute("SELECT * FROM participants WHERE sprint_id = ?", (sprint.id,)) as cur:
                for p in await cur.fetchall():
                    sprint.participants[p["user_id"]] = Participant(
                        user_id=p["user_id"],
                        name=p["name"],
                        joined_at=_dt(p["joined_at"]),
                        start_words=p["start_words"],
                        start_url=p["start_url"],
                        words=p["words"],
                        reported_at=_dt(p["reported_at"]),
                    )
            sprints.append(sprint)
        return sprints

    async def user_stats(self, platform: str, user_id: str, chat_id: str | None = None) -> UserStats:
        query = """SELECT COUNT(*) AS sprints, COUNT(p.words) AS reported, COALESCE(SUM(p.words), 0) AS words,
                       COALESCE(MAX(p.words), 0) AS best, COALESCE(SUM(s.duration), 0) AS minutes
                   FROM participants p JOIN sprints s ON s.id = p.sprint_id
                   WHERE s.platform = ? AND p.user_id = ? AND s.status IN ('reporting', 'closed')
                     AND s.unit = 'words'"""
        params: list = [platform, user_id]
        if chat_id is not None:
            query += " AND s.chat_id = ?"
            params.append(chat_id)
        async with self.db.execute(query, params) as cur:
            row = await cur.fetchone()
        return UserStats(row["sprints"], row["reported"], row["words"], row["best"], row["minutes"])

    async def top(self, platform: str, chat_id: str, since: datetime, limit: int = 10) -> list[TopEntry]:
        async with self.db.execute(
            """SELECT p.user_id, MAX(p.name) AS name, SUM(p.words) AS words, COUNT(*) AS sprints
               FROM participants p JOIN sprints s ON s.id = p.sprint_id
               WHERE s.platform = ? AND s.chat_id = ? AND s.start_at >= ? AND p.words IS NOT NULL
                 AND s.unit = 'words'
               GROUP BY p.user_id ORDER BY words DESC LIMIT ?""",
            (platform, chat_id, _iso(since), limit),
        ) as cur:
            return [TopEntry(r["user_id"], r["name"], r["words"], r["sprints"]) for r in await cur.fetchall()]

    # --- per-chat settings ----------------------------------------------------------------------

    async def get_chat_setting(self, platform: str, chat_id: str, key: str) -> str | None:
        async with self.db.execute(
            "SELECT value FROM chat_settings WHERE platform = ? AND chat_id = ? AND key = ?", (platform, chat_id, key)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_chat_setting(self, platform: str, chat_id: str, key: str, value: str) -> None:
        await self.db.execute(
            """INSERT INTO chat_settings (platform, chat_id, key, value) VALUES (?, ?, ?, ?)
               ON CONFLICT (platform, chat_id, key) DO UPDATE SET value = excluded.value""",
            (platform, chat_id, key, value),
        )
        await self.db.commit()

    async def sprint_unit(self, platform: str, chat_id: str) -> SprintUnit:
        value = await self.get_chat_setting(platform, chat_id, "sprint_unit")
        return SprintUnit(value) if value in SprintUnit._value2member_map_ else SprintUnit.WORDS

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
                   last_seen = MAX(chats.last_seen, excluded.last_seen)"""
        )
        await self.db.execute(
            f"""INSERT INTO chats (platform, chat_id, type, title, username, status, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (platform, chat_id) {conflict}""",  # noqa: S608
            (
                chat.platform,
                chat.chat_id,
                chat.type,
                chat.title,
                chat.username,
                chat.status.value,
                _iso(chat.first_seen) or now,
                now,
            ),
        )
        await self.db.commit()

    async def set_chat_status(self, platform: str, chat_id: str, status: ChatStatus) -> None:
        await self.db.execute(
            "UPDATE chats SET status = ? WHERE platform = ? AND chat_id = ?", (status.value, platform, chat_id)
        )
        await self.db.commit()

    async def set_broadcast_excluded(self, platform: str, chat_ids: list[str], excluded: bool) -> int:
        """Returns how many of ``chat_ids`` are known chats."""
        cur = await self.db.executemany(
            "UPDATE chats SET broadcast_excluded = ? WHERE platform = ? AND chat_id = ?",
            [(int(excluded), platform, chat_id) for chat_id in chat_ids],
        )
        await self.db.commit()
        return cur.rowcount

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
        async with self.db.execute(query, params) as cur:
            return [
                ChatRecord(
                    platform=r["platform"],
                    chat_id=r["chat_id"],
                    type=r["type"],
                    title=r["title"],
                    username=r["username"],
                    status=ChatStatus(r["status"]),
                    first_seen=_dt(r["first_seen"]),
                    last_seen=_dt(r["last_seen"]),
                    broadcast_excluded=bool(r["broadcast_excluded"]),
                )
                for r in await cur.fetchall()
            ]
