"""Communities: chats that belong together and share /top, prompts, broadcasts, settings and achievements.

A Telegram chat that belongs to a Telegram Community (Bot API 10.2: ``ChatFullInfo.community``, and the
``community_chat_added`` / ``community_chat_removed`` service messages) is linked automatically. Super-admins
can also link chats by hand (``/community``), for groups that don't use Telegram Communities; a hand-made
link is never undone by the automatic check.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .storage import Storage, iso


@dataclass(slots=True)
class Community:
    id: int
    name: str
    telegram_id: int | None = None
    chats: list[tuple[str, str]] = field(default_factory=list)  # (platform, chat id)


class Communities:
    def __init__(self, storage: Storage):
        self.storage = storage

    async def get(self, community_id: int) -> Community | None:
        row = await self.storage.fetchone("SELECT * FROM communities WHERE id = ?", (community_id,))
        if row is None:
            return None
        chats = await self.storage.fetchall(
            "SELECT platform, chat_id FROM community_chats WHERE community_id = ? ORDER BY platform, chat_id",
            (community_id,),
        )
        return Community(row["id"], row["name"], row["telegram_id"], [(c["platform"], c["chat_id"]) for c in chats])

    async def all(self) -> list[Community]:
        rows = await self.storage.fetchall("SELECT id FROM communities ORDER BY name, id")
        return [c for r in rows if (c := await self.get(r["id"])) is not None]

    async def of_chat(self, platform: str, chat_id: str) -> Community | None:
        row = await self.storage.fetchone(
            "SELECT community_id FROM community_chats WHERE platform = ? AND chat_id = ?", (platform, chat_id)
        )
        return await self.get(row["community_id"]) if row else None

    async def chats_with(self, platform: str, chat_id: str) -> list[str]:
        """This chat's community's chats on ``platform`` (or just this chat)."""
        community = await self.of_chat(platform, chat_id)
        if community is None:
            return [chat_id]
        return [c for p, c in community.chats if p == platform]

    async def create(self, name: str, telegram_id: int | None = None) -> Community:
        async with self.storage.tx() as db:
            cur = await db.execute(
                "INSERT INTO communities (name, telegram_id, created_at) VALUES (?, ?, ?)",
                (name.strip() or "—", telegram_id, iso(datetime.now(UTC))),
            )
            community_id = cur.lastrowid
        return await self.get(community_id)

    async def rename(self, community_id: int, name: str) -> None:
        async with self.storage.tx() as db:
            await db.execute("UPDATE communities SET name = ? WHERE id = ?", (name.strip(), community_id))

    async def delete(self, community_id: int) -> None:
        async with self.storage.tx() as db:
            await db.execute("DELETE FROM communities WHERE id = ?", (community_id,))

    async def add(self, community_id: int, platform: str, chat_id: str, *, auto: bool = False) -> None:
        async with self.storage.tx() as db:
            await db.execute(
                """INSERT INTO community_chats (community_id, platform, chat_id, auto) VALUES (?, ?, ?, ?)
                   ON CONFLICT (platform, chat_id) DO UPDATE SET community_id = excluded.community_id,
                       auto = excluded.auto""",
                (community_id, platform, chat_id, int(auto)),
            )

    async def remove(self, platform: str, chat_id: str, *, only_auto: bool = False) -> bool:
        query = "DELETE FROM community_chats WHERE platform = ? AND chat_id = ?"
        if only_auto:
            query += " AND auto = 1"
        async with self.storage.tx() as db:
            cur = await db.execute(query, (platform, chat_id))
        return cur.rowcount > 0

    # --- Telegram Communities ----------------------------------------------------------------------

    async def link_telegram(self, chat_id: str, telegram_id: int, name: str) -> Community:
        """The chat belongs to this Telegram Community; a hand-made link is kept."""
        row = await self.storage.fetchone(
            "SELECT auto FROM community_chats WHERE platform = 'telegram' AND chat_id = ?", (chat_id,)
        )
        community_row = await self.storage.fetchone(
            "SELECT id, name FROM communities WHERE telegram_id = ?", (telegram_id,)
        )
        if community_row is None:
            community = await self.create(name, telegram_id)
        else:
            community = await self.get(community_row["id"])
            if name and community_row["name"] != name:
                await self.rename(community.id, name)
        if row is None or row["auto"]:
            await self.add(community.id, "telegram", chat_id, auto=True)
        return await self.get(community.id)

    async def unlink_telegram(self, chat_id: str) -> bool:
        return await self.remove("telegram", chat_id, only_auto=True)

    async def mark_checked(self, chat_id: str) -> None:
        async with self.storage.tx() as db:
            await db.execute(
                "UPDATE chats SET community_checked = ? WHERE platform = 'telegram' AND chat_id = ?",
                (iso(datetime.now(UTC)), chat_id),
            )

    async def last_checked(self, chat_id: str) -> datetime | None:
        row = await self.storage.fetchone(
            "SELECT community_checked FROM chats WHERE platform = 'telegram' AND chat_id = ?", (chat_id,)
        )
        return datetime.fromisoformat(row["community_checked"]) if row and row["community_checked"] else None

    # --- community settings ------------------------------------------------------------------------

    async def settings(self, community_id: int) -> dict[str, str]:
        rows = await self.storage.fetchall(
            "SELECT key, value FROM community_settings WHERE community_id = ?", (community_id,)
        )
        return {r["key"]: r["value"] for r in rows}

    async def set_setting(self, community_id: int, key: str, value: str | None) -> None:
        async with self.storage.tx() as db:
            if value is None:
                await db.execute(
                    "DELETE FROM community_settings WHERE community_id = ? AND key = ?", (community_id, key)
                )
            else:
                await db.execute(
                    """INSERT INTO community_settings (community_id, key, value) VALUES (?, ?, ?)
                       ON CONFLICT (community_id, key) DO UPDATE SET value = excluded.value""",
                    (community_id, key, value),
                )
