"""Achievements, made and awarded by hand: a name, an optional description and a sticker.

They belong to the community where there is one, otherwise to the chat. No rules, no automation, no
ties to sprints or stats.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from ...core.storage import Storage, iso, parse_dt

MAX_NAME = 60
MAX_DESCRIPTION = 300


@dataclass(frozen=True, slots=True)
class Achievement:
    id: int
    owner: str
    name: str
    description: str | None
    sticker_set: str | None
    sticker_unique_id: str | None
    sticker_file_id: str | None


@dataclass(frozen=True, slots=True)
class Award:
    achievement: Achievement
    user_id: str
    user_name: str | None
    chat_id: str | None
    awarded_at: datetime


def owner_for(platform: str, chat_id: str, community_id: int | None) -> str:
    return f"community:{community_id}" if community_id is not None else f"chat:{platform}:{chat_id}"


def clean(name: str, limit: int = MAX_NAME) -> str:
    name = " ".join((name or "").split()).strip("«»\"' ")
    if not name:
        raise ValueError("empty name")
    return name[:limit]


class AchievementStore:
    def __init__(self, storage: Storage):
        self.storage = storage

    @staticmethod
    def _a(r) -> Achievement:
        return Achievement(
            r["id"],
            r["owner"],
            r["name"],
            r["description"],
            r["sticker_set"],
            r["sticker_unique_id"],
            r["sticker_file_id"],
        )

    async def of_owner(self, owner: str) -> list[Achievement]:
        rows = await self.storage.fetchall(
            "SELECT * FROM achievements WHERE owner = ? AND deleted = 0 ORDER BY name COLLATE NOCASE", (owner,)
        )
        return [self._a(r) for r in rows]

    async def get(self, achievement_id: int) -> Achievement | None:
        row = await self.storage.fetchone("SELECT * FROM achievements WHERE id = ? AND deleted = 0", (achievement_id,))
        return self._a(row) if row else None

    async def find(self, owner: str, query: str) -> Achievement | None:
        q = clean(query).casefold()
        items = await self.of_owner(owner)
        exact = [a for a in items if a.name.casefold() == q]
        if exact:
            return exact[0]
        starts = [a for a in items if a.name.casefold().startswith(q)]
        return starts[0] if len(starts) == 1 else None

    async def create(
        self, owner: str, name: str, description: str | None, sticker: tuple[str, str, str] | None, by: str
    ) -> Achievement:
        """``sticker`` is (set name, file_unique_id, file_id). Raises ValueError for a duplicate name."""
        name = clean(name)
        if any(a.name.casefold() == name.casefold() for a in await self.of_owner(owner)):
            raise ValueError("duplicate")
        description = clean(description, MAX_DESCRIPTION) if description and description.strip() else None
        async with self.storage.tx() as db:
            cur = await db.execute(
                """INSERT INTO achievements (owner, name, description, sticker_set, sticker_unique_id, sticker_file_id,
                       created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (owner, name, description, *(sticker or (None, None, None)), by, iso(datetime.now(UTC))),
            )
            achievement_id = cur.lastrowid
        return await self.get(achievement_id)

    async def update(self, achievement_id: int, **fields) -> Achievement | None:
        allowed = {
            k: v
            for k, v in fields.items()
            if k in ("name", "description", "sticker_set", "sticker_unique_id", "sticker_file_id")
        }
        if "name" in allowed:
            allowed["name"] = clean(allowed["name"])
        if allowed.get("description") is not None:
            allowed["description"] = (
                clean(allowed["description"], MAX_DESCRIPTION) if allowed["description"].strip() else None
            )
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            async with self.storage.tx() as db:
                await db.execute(f"UPDATE achievements SET {sets} WHERE id = ?", (*allowed.values(), achievement_id))  # noqa: S608 - fixed column names
        return await self.get(achievement_id)

    async def delete(self, achievement_id: int) -> None:
        async with self.storage.tx() as db:
            await db.execute("UPDATE achievements SET deleted = 1 WHERE id = ?", (achievement_id,))

    # --- awards ----------------------------------------------------------------------------------

    async def award(
        self,
        achievement: Achievement,
        platform: str,
        user_id: str,
        name: str,
        person_id: int | None,
        chat_id: str,
        by: str,
    ) -> bool:
        """False if they already have it."""
        async with self.storage.tx() as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO awards (achievement_id, person_id, platform, user_id, user_name, chat_id, awarded_by, awarded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (achievement.id, person_id, platform, user_id, name, chat_id, by, iso(datetime.now(UTC))),
            )
        return cur.rowcount == 1

    async def unaward(self, achievement: Achievement, platform: str, user_id: str) -> bool:
        async with self.storage.tx() as db:
            cur = await db.execute(
                "DELETE FROM awards WHERE achievement_id = ? AND platform = ? AND user_id = ?",
                (achievement.id, platform, user_id),
            )
        return cur.rowcount == 1

    async def of_user(self, platform: str, user_id: str) -> list[Award]:
        rows = await self.storage.fetchall(
            """SELECT a.*, w.user_id, w.user_name, w.chat_id AS award_chat, w.awarded_at FROM awards w
               JOIN achievements a ON a.id = w.achievement_id
               WHERE w.platform = ? AND w.user_id = ? AND a.deleted = 0 ORDER BY w.awarded_at""",
            (platform, user_id),
        )
        return [
            Award(self._a(r), r["user_id"], r["user_name"], r["award_chat"], parse_dt(r["awarded_at"])) for r in rows
        ]

    async def holders(self, achievement_id: int) -> list[Award]:
        a = await self.get(achievement_id)
        if a is None:
            return []
        rows = await self.storage.fetchall(
            "SELECT * FROM awards WHERE achievement_id = ? ORDER BY awarded_at", (achievement_id,)
        )
        return [Award(a, r["user_id"], r["user_name"], r["chat_id"], parse_dt(r["awarded_at"])) for r in rows]

    async def leaderboard(self, owner: str, limit: int = 10) -> list[tuple[str, str, str | None, list[int]]]:
        """Who has the most of ``owner``'s achievements: (platform, user id, name, achievement ids, oldest
        first), most first; ties by who got there first."""
        rows = await self.storage.fetchall(
            """SELECT w.platform, w.user_id, w.user_name, w.achievement_id, w.awarded_at FROM awards w
               JOIN achievements a ON a.id = w.achievement_id
               WHERE a.owner = ? AND a.deleted = 0 ORDER BY w.awarded_at""",
            (owner,),
        )
        people: dict[tuple[str, str], dict] = {}
        for r in rows:
            entry = people.setdefault((r["platform"], r["user_id"]), {"name": None, "ids": [], "last": None})
            entry["name"] = r["user_name"] or entry["name"]
            entry["ids"].append(r["achievement_id"])
            entry["last"] = r["awarded_at"]
        ranked = sorted(people.items(), key=lambda kv: (-len(kv[1]["ids"]), kv[1]["last"]))
        return [(k[0], k[1], v["name"], v["ids"]) for k, v in ranked[:limit]]

    async def forget(self, platform: str, user_id: str) -> None:
        async with self.storage.tx() as db:
            await db.execute("DELETE FROM awards WHERE platform = ? AND user_id = ?", (platform, user_id))
