"""People and their platform accounts.

Works, goals, streaks and personal settings belong to a person (``person_id``), not to a platform account,
so linking a Telegram and a Discord account later (phase 6) needs no migration: both accounts point to one
person. Until then every account gets its own person the first time it needs one.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from .models import OPEN_STATUSES, Unit
from .storage import Storage, iso

HIDE_NUMBERS = "hide_numbers"  # "1": the board shows ✓ instead of numbers; off /top
NEW_WORK_UNIT = "new_work_unit"  # unit for new works; else the chat's default
DATA_NOTICE = "data_notice"  # "1": told once that sprint numbers are stored
CAN_DM = "can_dm"  # "1": has a private chat with the bot (needed for goal messages)


@dataclass(frozen=True, slots=True)
class Account:
    platform: str
    user_id: str
    person_id: int
    name: str | None


class People:
    def __init__(self, storage: Storage):
        self.storage = storage

    async def person_id(self, platform: str, user_id: str, name: str | None = None) -> int:
        """The person behind an account, created on first use. Keeps the display name current."""
        row = await self.storage.fetchone(
            "SELECT person_id, name FROM accounts WHERE platform = ? AND user_id = ?", (platform, user_id)
        )
        if row is not None:
            if name and row["name"] != name:
                async with self.storage.tx() as db:
                    await db.execute(
                        "UPDATE accounts SET name = ? WHERE platform = ? AND user_id = ?", (name, platform, user_id)
                    )
            return row["person_id"]
        async with self.storage.tx() as db:
            # Re-check inside the lock: two first messages can race.
            async with db.execute(
                "SELECT person_id FROM accounts WHERE platform = ? AND user_id = ?", (platform, user_id)
            ) as cur:
                again = await cur.fetchone()
            if again is not None:
                return again["person_id"]
            cur = await db.execute("INSERT INTO people (created_at) VALUES (?)", (iso(datetime.now(UTC)),))
            person = cur.lastrowid
            await db.execute(
                "INSERT INTO accounts (platform, user_id, person_id, name) VALUES (?, ?, ?, ?)",
                (platform, user_id, person, name),
            )
        return person

    async def find(self, platform: str, user_id: str) -> int | None:
        row = await self.storage.fetchone(
            "SELECT person_id FROM accounts WHERE platform = ? AND user_id = ?", (platform, user_id)
        )
        return row["person_id"] if row else None

    async def accounts(self, person_id: int) -> list[Account]:
        rows = await self.storage.fetchall("SELECT * FROM accounts WHERE person_id = ?", (person_id,))
        return [Account(r["platform"], r["user_id"], r["person_id"], r["name"]) for r in rows]

    async def name(self, person_id: int) -> str:
        row = await self.storage.fetchone(
            "SELECT name FROM accounts WHERE person_id = ? AND name IS NOT NULL LIMIT 1", (person_id,)
        )
        return row["name"] if row else "—"

    # --- settings --------------------------------------------------------------------------------

    async def settings(self, person_id: int) -> dict[str, str]:
        rows = await self.storage.fetchall("SELECT key, value FROM person_settings WHERE person_id = ?", (person_id,))
        return {r["key"]: r["value"] for r in rows}

    async def set(self, person_id: int, key: str, value: str | None) -> None:
        async with self.storage.tx() as db:
            if value is None:
                await db.execute("DELETE FROM person_settings WHERE person_id = ? AND key = ?", (person_id, key))
            else:
                await db.execute(
                    """INSERT INTO person_settings (person_id, key, value) VALUES (?, ?, ?)
                       ON CONFLICT (person_id, key) DO UPDATE SET value = excluded.value""",
                    (person_id, key, value),
                )

    async def hides_numbers(self, person_id: int) -> bool:
        return (await self.settings(person_id)).get(HIDE_NUMBERS) == "1"

    async def new_work_unit(self, person_id: int) -> Unit | None:
        value = (await self.settings(person_id)).get(NEW_WORK_UNIT)
        return Unit(value) if value in Unit._value2member_map_ else None

    # --- your data -------------------------------------------------------------------------------

    async def forget(self, person_id: int) -> int:
        """Delete everything about a person: works, entries, settings, sprint entries in finished sprints,
        and their accounts' private chat records and v3.3 settings. Open sprints keep their entries until
        they close (the caller says so). Returns the number of sprint entries removed."""
        accounts = await self.accounts(person_id)
        placeholders = ",".join("?" * len(OPEN_STATUSES))
        removed = 0
        async with self.storage.tx() as db:
            for a in accounts:
                cur = await db.execute(
                    f"""DELETE FROM participants WHERE user_id = ? AND sprint_id IN (
                            SELECT id FROM sprints WHERE platform = ? AND status NOT IN ({placeholders}))""",  # noqa: S608
                    (a.user_id, a.platform, *[st.value for st in OPEN_STATUSES]),
                )
                removed += cur.rowcount
                await db.execute(
                    "DELETE FROM user_settings WHERE platform = ? AND user_id = ?", (a.platform, a.user_id)
                )
                await db.execute(
                    "DELETE FROM chats WHERE platform = ? AND chat_id = ? AND type = 'private'",
                    (a.platform, a.user_id),
                )
            await db.execute("DELETE FROM people WHERE id = ?", (person_id,))  # cascades to the rest
        return removed

    async def forget_account(self, platform: str, user_id: str) -> int:
        """``forget`` for an account that may not have a person yet."""
        person = await self.find(platform, user_id)
        if person is not None:
            return await self.forget(person)
        placeholders = ",".join("?" * len(OPEN_STATUSES))
        async with self.storage.tx() as db:
            cur = await db.execute(
                f"""DELETE FROM participants WHERE user_id = ? AND sprint_id IN (
                        SELECT id FROM sprints WHERE platform = ? AND status NOT IN ({placeholders}))""",  # noqa: S608
                (user_id, platform, *[st.value for st in OPEN_STATUSES]),
            )
            await db.execute("DELETE FROM user_settings WHERE platform = ? AND user_id = ?", (platform, user_id))
            await db.execute(
                "DELETE FROM chats WHERE platform = ? AND chat_id = ? AND type = 'private'", (platform, user_id)
            )
        return cur.rowcount
