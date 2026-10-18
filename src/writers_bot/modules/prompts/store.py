"""Prompts in the database: a shared bot-wide set, and a set per chat (or per community).

``/prompt`` picks from the chat's set plus, if the chat uses it, the shared set. Categories are free-form,
named in English with an optional Ukrainian alias (``horror`` / ``жахи``); ``/prompt жахи`` works.
Members suggest prompts, which the chat's admins accept or reject; a chat's prompt can be submitted to the
shared set, which super-admins accept or reject.
"""

import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ...core.storage import Storage, iso

SHARED = ("shared", "")
ACTIVE, SUGGESTED, SUBMITTED, REJECTED = "active", "suggested", "submitted", "rejected"
MAX_TEXT = 1000
MAX_CATEGORY = 40

_TRANSLIT = dict(
    zip(
        "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя'ʼ’",  # i18n: input syntax (transliteration table)
        ["a", "b", "v", "h", "g", "d", "e", "ie", "zh", "z", "y", "i", "i", "i", "k", "l", "m", "n", "o", "p",
         "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "iu", "ia", "", "", ""],
        strict=True,
    )
)  # fmt: skip


def slug(text: str) -> str:
    """A Latin category name from a Ukrainian header: "Персонаж" → "personazh"."""
    out = "".join(_TRANSLIT.get(ch, ch) for ch in text.casefold())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:MAX_CATEGORY] or "category"


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    set_id: int
    name: str
    alias: str | None
    kind: str

    @property
    def label(self) -> str:
        return self.alias or self.name


@dataclass(frozen=True, slots=True)
class Prompt:
    id: int
    category: Category
    text: str | None
    file_id: str | None
    url: str | None
    status: str
    author_id: str | None


class PromptDB:
    def __init__(self, storage: Storage):
        self.storage = storage

    # --- sets ------------------------------------------------------------------------------------

    async def set_id(self, kind: str, owner: str) -> int:
        row = await self.storage.fetchone("SELECT id FROM prompt_sets WHERE kind = ? AND owner = ?", (kind, owner))
        if row:
            return row["id"]
        async with self.storage.tx() as db:
            await db.execute("INSERT OR IGNORE INTO prompt_sets (kind, owner) VALUES (?, ?)", (kind, owner))
        row = await self.storage.fetchone("SELECT id FROM prompt_sets WHERE kind = ? AND owner = ?", (kind, owner))
        return row["id"]

    async def shared(self) -> int:
        return await self.set_id(*SHARED)

    async def chat_set(self, platform: str, chat_id: str, community_id: int | None) -> int:
        """A community shares one set; other chats have their own."""
        if community_id is not None:
            return await self.set_id("community", str(community_id))
        return await self.set_id("chat", f"{platform}:{chat_id}")

    # --- categories ------------------------------------------------------------------------------

    @staticmethod
    def _cat(r) -> Category:
        return Category(r["id"], r["set_id"], r["name"], r["alias"], r["kind"])

    async def categories(self, set_ids: list[int], kind: str = "text", *, with_prompts: bool = True) -> list[Category]:
        if not set_ids:
            return []
        query = f"""SELECT c.* FROM prompt_categories c WHERE c.set_id IN ({",".join("?" * len(set_ids))}) AND c.kind = ?"""  # noqa: S608
        if with_prompts:
            query += " AND EXISTS (SELECT 1 FROM prompts p WHERE p.category_id = c.id AND p.status = 'active')"
        rows = await self.storage.fetchall(query + " ORDER BY c.set_id, c.id", (*set_ids, kind))
        return [self._cat(r) for r in rows]

    async def category(self, set_id: int, name: str, kind: str = "text", alias: str | None = None) -> Category:
        """The set's category by name or alias, created if missing."""
        name = " ".join(name.split())[:MAX_CATEGORY]
        if not name:
            raise ValueError("empty category")
        for c in await self.categories([set_id], kind, with_prompts=False):
            if name.casefold() in (c.name.casefold(), (c.alias or "").casefold()):
                return c
        latin = name if re.fullmatch(r"[A-Za-z0-9 _-]+", name) else slug(name)
        alias = alias if alias is not None else (None if latin == name else name)
        async with self.storage.tx() as db:
            await db.execute(
                "INSERT OR IGNORE INTO prompt_categories (set_id, name, alias, kind) VALUES (?, ?, ?, ?)",
                (set_id, latin.casefold(), alias, kind),
            )
        row = await self.storage.fetchone(
            "SELECT * FROM prompt_categories WHERE set_id = ? AND kind = ? AND name = ?",
            (set_id, kind, latin.casefold()),
        )
        return self._cat(row)

    async def rename(self, category_id: int, name: str | None = None, alias: str | None = None) -> None:
        sets, values = [], []
        if name:
            sets.append("name = ?")
            values.append(slug(name) if not re.fullmatch(r"[A-Za-z0-9 _-]+", name) else name.casefold())
        if alias is not None:
            sets.append("alias = ?")
            values.append(alias or None)
        if sets:
            async with self.storage.tx() as db:
                await db.execute(
                    f"UPDATE prompt_categories SET {', '.join(sets)} WHERE id = ?",  # noqa: S608 - fixed column names
                    (*values, category_id),
                )

    @staticmethod
    def find(categories: list[Category], query: str) -> list[Category]:
        """Categories (possibly from several sets) matching a name or alias: exact, else the only prefix."""
        q = query.strip().casefold()
        if not q:
            return []
        exact = [c for c in categories if q in (c.name.casefold(), (c.alias or "").casefold())]
        if exact:
            return exact
        starts = [c for c in categories if c.name.casefold().startswith(q) or (c.alias or "").casefold().startswith(q)]
        labels = {c.label.casefold() for c in starts}
        return starts if len(labels) == 1 else []

    # --- prompts ---------------------------------------------------------------------------------

    @staticmethod
    def _prompt(r, c: Category) -> Prompt:
        return Prompt(r["id"], c, r["text"], r["file_id"], r["url"], r["status"], r["author_id"])

    async def add(
        self,
        set_id: int,
        category: str,
        *,
        text: str | None = None,
        file_id: str | None = None,
        url: str | None = None,
        kind: str = "text",
        status: str = ACTIVE,
        author: tuple[str, str] | None = None,
        origin: int | None = None,
    ) -> Prompt:
        if text is not None:
            text = text.strip()
            if not text or len(text) > MAX_TEXT:
                raise ValueError("bad text")
        cat = await self.category(set_id, category, kind)
        async with self.storage.tx() as db:
            cur = await db.execute(
                """INSERT INTO prompts (category_id, text, file_id, url, status, author_platform, author_id, origin, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cat.id,
                    text,
                    file_id,
                    url,
                    status,
                    author[0] if author else None,
                    author[1] if author else None,
                    origin,
                    iso(datetime.now(UTC)),
                ),
            )
            prompt_id = cur.lastrowid
        return await self.get(prompt_id)

    async def get(self, prompt_id: int) -> Prompt | None:
        row = await self.storage.fetchone(
            """SELECT p.*, c.set_id, c.name, c.alias, c.kind FROM prompts p JOIN prompt_categories c ON c.id = p.category_id
               WHERE p.id = ?""",
            (prompt_id,),
        )
        if row is None:
            return None
        return self._prompt(row, Category(row["category_id"], row["set_id"], row["name"], row["alias"], row["kind"]))

    async def by_status(self, set_id: int, status: str = ACTIVE, kind: str | None = None) -> list[Prompt]:
        query = """SELECT p.*, c.set_id, c.name, c.alias, c.kind FROM prompts p JOIN prompt_categories c ON c.id = p.category_id
                   WHERE c.set_id = ? AND p.status = ?"""
        params: list = [set_id, status]
        if kind:
            query += " AND c.kind = ?"
            params.append(kind)
        rows = await self.storage.fetchall(query + " ORDER BY c.name, p.id", params)
        return [
            self._prompt(r, Category(r["category_id"], r["set_id"], r["name"], r["alias"], r["kind"])) for r in rows
        ]

    async def set_status(self, prompt_id: int, status: str) -> None:
        async with self.storage.tx() as db:
            await db.execute("UPDATE prompts SET status = ? WHERE id = ?", (status, prompt_id))

    async def delete(self, prompt_id: int) -> None:
        async with self.storage.tx() as db:
            await db.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))

    async def submit(self, prompt: Prompt, author: tuple[str, str] | None) -> Prompt:
        """Propose a chat's prompt for the shared set."""
        shared = await self.shared()
        return await self.add(
            shared,
            prompt.category.alias or prompt.category.name,
            text=prompt.text,
            file_id=prompt.file_id,
            url=prompt.url,
            kind=prompt.category.kind,
            status=SUBMITTED,
            author=author,
            origin=prompt.id,
        )

    async def pick(self, categories: list[Category]) -> Prompt | None:
        """A random active prompt from these categories (merged: they may come from several sets)."""
        ids = [c.id for c in categories]
        if not ids:
            return None
        rows = await self.storage.fetchall(
            f"SELECT * FROM prompts WHERE category_id IN ({','.join('?' * len(ids))}) AND status = 'active'",  # noqa: S608
            ids,
        )
        if not rows:
            return None
        r = random.choice(rows)
        cat = next(c for c in categories if c.id == r["category_id"])
        return self._prompt(r, cat)

    async def stats(self, set_ids: list[int]) -> dict[str, int]:
        out: dict[str, int] = {}
        for kind in ("text", "image"):
            for c in await self.categories(set_ids, kind):
                row = await self.storage.fetchone(
                    "SELECT COUNT(*) AS n FROM prompts WHERE category_id = ? AND status = 'active'", (c.id,)
                )
                key = ("image-" if kind == "image" else "") + c.label
                out[key] = out.get(key, 0) + row["n"]
        return out

    # --- the one-time import ---------------------------------------------------------------------

    async def meta(self, key: str) -> str | None:
        row = await self.storage.fetchone("SELECT value FROM prompts_meta WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self.storage.tx() as db:
            await db.execute("INSERT OR REPLACE INTO prompts_meta (key, value) VALUES (?, ?)", (key, value))

    async def import_sheet(self, data, lang: str = "ua") -> int:
        """The Google Sheet's prompt columns and Drive folders go into the shared set, once."""
        if await self.meta("imported"):
            return 0
        shared = await self.shared()
        count = 0
        for header, prompts in data.texts.get(lang, {}).items():
            cat = await self.category(shared, slug(header), "text", alias=header)
            async with self.storage.tx() as db:
                await db.executemany(
                    "INSERT INTO prompts (category_id, text, status, created_at) VALUES (?, ?, 'active', ?)",
                    [(cat.id, p[:MAX_TEXT], iso(datetime.now(UTC))) for p in prompts if p.strip()],
                )
            count += len(prompts)
        for folder, files in data.images.items():
            if folder == "all":
                continue
            cat = await self.category(shared, folder, "image")
            async with self.storage.tx() as db:
                await db.executemany(
                    "INSERT INTO prompts (category_id, url, status, created_at) VALUES (?, ?, 'active', ?)",
                    [
                        (cat.id, f.get("webContentLink") or f.get("webViewLink"), iso(datetime.now(UTC)))
                        for f in files
                        if f.get("webContentLink") or f.get("webViewLink")
                    ],
                )
            count += len(files)
        await self.set_meta("imported", iso(datetime.now(UTC)))
        return count
