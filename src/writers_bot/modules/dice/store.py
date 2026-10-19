"""Roll variables of the dice module: for each person in each chat, a name and a series of rolls (``core.dicevars``)."""

import json
from datetime import UTC, datetime

from ...core.dicevars import MAX_PER_SCOPE, VarError
from ...core.storage import Storage, iso


class VarStore:
    def __init__(self, storage: Storage):
        self.storage = storage

    async def get(self, platform: str, chat_id: str, user_id: str, name: str) -> list[str] | None:
        row = await self.storage.fetchone(
            "SELECT steps FROM dice_vars WHERE platform = ? AND chat_id = ? AND user_id = ? AND name = ?",
            (platform, chat_id, user_id, name),
        )
        return json.loads(row["steps"]) if row else None

    async def all(self, platform: str, chat_id: str, user_id: str) -> dict[str, list[str]]:
        """A person's variables in a chat: name -> steps, by name."""
        rows = await self.storage.fetchall(
            "SELECT name, steps FROM dice_vars WHERE platform = ? AND chat_id = ? AND user_id = ? ORDER BY name",
            (platform, chat_id, user_id),
        )
        return {r["name"]: json.loads(r["steps"]) for r in rows}

    async def set(self, platform: str, chat_id: str, user_id: str, name: str, steps: list[str]) -> bool:
        """Keep a variable (replacing one of the same name); True when it was new. Raises VarError past the limit."""
        existing = await self.get(platform, chat_id, user_id, name)
        if existing is None and len(await self.all(platform, chat_id, user_id)) >= MAX_PER_SCOPE:
            raise VarError("dice.var_too_many", max=MAX_PER_SCOPE)
        async with self.storage.tx() as db:
            await db.execute(
                """INSERT INTO dice_vars (platform, chat_id, user_id, name, steps, updated_at) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (platform, chat_id, user_id, name) DO UPDATE SET steps = excluded.steps,
                   updated_at = excluded.updated_at""",
                (platform, chat_id, user_id, name, json.dumps(steps, ensure_ascii=False), iso(datetime.now(UTC))),
            )
        return existing is None

    async def unset(self, platform: str, chat_id: str, user_id: str, name: str) -> bool:
        async with self.storage.tx() as db:
            cur = await db.execute(
                "DELETE FROM dice_vars WHERE platform = ? AND chat_id = ? AND user_id = ? AND name = ?",
                (platform, chat_id, user_id, name),
            )
        return cur.rowcount > 0

    async def unset_all(self, platform: str, chat_id: str, user_id: str) -> int:
        async with self.storage.tx() as db:
            cur = await db.execute(
                "DELETE FROM dice_vars WHERE platform = ? AND chat_id = ? AND user_id = ?",
                (platform, chat_id, user_id),
            )
        return cur.rowcount

    async def chats_of(self, platform: str, user_id: str) -> list[tuple[str, int]]:
        """The chats where a person has variables, with how many (most variables first)."""
        rows = await self.storage.fetchall(
            "SELECT chat_id, COUNT(*) AS n FROM dice_vars WHERE platform = ? AND user_id = ? GROUP BY chat_id ORDER BY n DESC",
            (platform, user_id),
        )
        return [(r["chat_id"], r["n"]) for r in rows]

    async def everything(self, platform: str, user_id: str) -> list[tuple[str, str, list[str]]]:
        """For the export: (chat id, name, steps) of everything a person has kept."""
        rows = await self.storage.fetchall(
            "SELECT chat_id, name, steps FROM dice_vars WHERE platform = ? AND user_id = ? ORDER BY chat_id, name",
            (platform, user_id),
        )
        return [(r["chat_id"], r["name"], json.loads(r["steps"])) for r in rows]

    async def forget(self, platform: str, user_id: str) -> None:
        async with self.storage.tx() as db:
            await db.execute("DELETE FROM dice_vars WHERE platform = ? AND user_id = ?", (platform, user_id))
