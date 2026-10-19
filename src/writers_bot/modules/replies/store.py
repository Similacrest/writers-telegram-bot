"""Reply entries in the database: per chat, plus the bot-wide defaults (``chat_id = '*'``)."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from ...core.storage import Storage, iso

ALL = "*"
MAX_ANSWERS = 20
MAX_ENTRIES = 50


@dataclass(frozen=True, slots=True)
class Entry:
    answers: tuple[str, ...]
    command: str | None = None  # without the slash, lower case
    phrase: str | None = None  # lower case
    chat_id: str | None = None  # None: every chat of this bot
    id: int | None = None

    @property
    def trigger(self) -> str:
        return f"/{self.command}" if self.command else self.phrase or ""


def parse_trigger(text: str) -> tuple[str, str] | None:
    """ "/imafan" → ("command", "imafan"); "слава україні" → ("phrase", …)."""
    text = " ".join(text.split()).casefold()
    if not text:
        return None
    if text.startswith("/"):
        name = text[1:].split("@")[0]
        return ("command", name) if name and name.replace("_", "").isalnum() else None
    return "phrase", text


class ReplyStore:
    def __init__(self, storage: Storage, platform: str = "telegram"):
        self.storage = storage
        self.platform = platform

    def _entry(self, r) -> Entry:
        answers = tuple(json.loads(r["answers"]))
        chat = None if r["chat_id"] == ALL else r["chat_id"]
        if r["kind"] == "command":
            return Entry(answers, command=r["trigger"], chat_id=chat, id=r["id"])
        return Entry(answers, phrase=r["trigger"], chat_id=chat, id=r["id"])

    async def for_chat(self, platform: str, chat_id: str, *, defaults: bool = True) -> list[Entry]:
        chats = [chat_id, ALL] if defaults else [chat_id]
        rows = await self.storage.fetchall(
            f"SELECT * FROM replies WHERE platform = ? AND chat_id IN ({','.join('?' * len(chats))}) ORDER BY chat_id = '*', id",  # noqa: S608
            (platform, *chats),
        )
        return [self._entry(r) for r in rows]

    async def own(self, platform: str, chat_id: str) -> list[Entry]:
        return await self.for_chat(platform, chat_id, defaults=False)

    async def add(self, platform: str, chat_id: str, kind: str, trigger: str, answer: str, by: str | None) -> Entry:
        """Add an answer to a trigger (creating it). Raises ValueError when there are too many."""
        row = await self.storage.fetchone(
            "SELECT * FROM replies WHERE platform = ? AND chat_id = ? AND kind = ? AND trigger = ?",
            (platform, chat_id, kind, trigger),
        )
        async with self.storage.tx() as db:
            if row is None:
                count = await db.execute(
                    "SELECT COUNT(*) FROM replies WHERE platform = ? AND chat_id = ?", (platform, chat_id)
                )
                if (await count.fetchone())[0] >= MAX_ENTRIES:
                    raise ValueError("too many entries")
                await db.execute(
                    """INSERT INTO replies (platform, chat_id, kind, trigger, answers, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        platform,
                        chat_id,
                        kind,
                        trigger,
                        json.dumps([answer], ensure_ascii=False),
                        by,
                        iso(datetime.now(UTC)),
                    ),
                )
            else:
                answers = json.loads(row["answers"])
                if len(answers) >= MAX_ANSWERS:
                    raise ValueError("too many answers")
                answers.append(answer)
                await db.execute(
                    "UPDATE replies SET answers = ? WHERE id = ?", (json.dumps(answers, ensure_ascii=False), row["id"])
                )
        row = await self.storage.fetchone(
            "SELECT * FROM replies WHERE platform = ? AND chat_id = ? AND kind = ? AND trigger = ?",
            (platform, chat_id, kind, trigger),
        )
        return self._entry(row)

    async def remove_answer(self, platform: str, chat_id: str, kind: str, trigger: str, answer: str) -> None:
        """Take one answer off a trigger (the trigger goes with its last answer)."""
        row = await self.storage.fetchone(
            "SELECT * FROM replies WHERE platform = ? AND chat_id = ? AND kind = ? AND trigger = ?",
            (platform, chat_id, kind, trigger),
        )
        if row is None:
            return
        answers = json.loads(row["answers"])
        if answer not in answers:
            return
        answers.remove(answer)
        async with self.storage.tx() as db:
            if answers:
                await db.execute(
                    "UPDATE replies SET answers = ? WHERE id = ?", (json.dumps(answers, ensure_ascii=False), row["id"])
                )
            else:
                await db.execute("DELETE FROM replies WHERE id = ?", (row["id"],))

    async def meta(self, key: str) -> str | None:
        row = await self.storage.fetchone("SELECT value FROM replies_meta WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self.storage.tx() as db:
            await db.execute("INSERT OR REPLACE INTO replies_meta (key, value) VALUES (?, ?)", (key, value))

    async def delete(self, platform: str, chat_id: str, entry_id: int) -> bool:
        async with self.storage.tx() as db:
            cur = await db.execute(
                "DELETE FROM replies WHERE id = ? AND platform = ? AND chat_id = ?", (entry_id, platform, chat_id)
            )
        return cur.rowcount > 0

    async def imported(self, key: str) -> bool:
        return await self.storage.fetchone("SELECT 1 FROM replies_meta WHERE key = ?", (key,)) is not None

    async def mark_imported(self, key: str) -> None:
        async with self.storage.tx() as db:
            await db.execute(
                "INSERT OR REPLACE INTO replies_meta (key, value) VALUES (?, ?)", (key, iso(datetime.now(UTC)))
            )
