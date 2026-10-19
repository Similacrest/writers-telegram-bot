"""The dice stickers a bot has already sent: clip name -> Telegram's ``file_id`` (for this bot) and ``file_unique_id``.

The clips are not in a sticker pack: a bot uploads a clip the first time a roll needs it and remembers the ``file_id`` it
gets back, so later rolls send by id. The cache is a small JSON file that can be copied, with the same bot, to another
host or after a lost database. A ``file_id`` belongs to one bot; a ``file_unique_id`` is the same for every bot, so after
a file made for another bot only the unique ids are kept (they are what tells a roll sticker from any other sticker).

    {"version": 1, "bot_id": 123, "stickers": {"d20_17": {"file_id": "...", "unique_id": "..."}}}
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class StickerCache:
    def __init__(self, path: Path | None = None):
        self.path = path  # None: only for as long as the bot runs
        self.bot_id: int | None = None
        self._file_ids: dict[str, str] = {}
        self._unique: dict[str, str] = {}  # clip name -> file_unique_id
        self._known: set[str] = set()

    def load(self, bot_id: int) -> None:
        """Read the file, if there is one; the file ids only if it was made for this bot."""
        self.bot_id = bot_id
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            rows = data["stickers"]
            mine = data.get("bot_id") == bot_id
            for name, row in rows.items():
                self._unique[name] = row["unique_id"]
                if mine and row.get("file_id"):
                    self._file_ids[name] = row["file_id"]
            self._known = set(self._unique.values())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            logger.warning("the dice sticker cache %s cannot be read: starting without it", self.path, exc_info=True)
            self._file_ids, self._unique, self._known = {}, {}, set()
            return
        if not mine:
            logger.info("the dice sticker cache %s is of another bot: its file ids are not used", self.path)

    def file_id(self, name: str) -> str | None:
        return self._file_ids.get(name)

    def is_roll_sticker(self, unique_id: str) -> bool:
        """Is this sticker (by ``file_unique_id``) one of the bot's dice stickers?"""
        return unique_id in self._known

    def put(self, name: str, file_id: str, unique_id: str) -> None:
        self._file_ids[name] = file_id
        self._unique[name] = unique_id
        self._known.add(unique_id)
        self.save()

    def forget(self, name: str) -> None:
        """Telegram refused this file id: drop it (the unique id stays) so the clip is uploaded again."""
        self._file_ids.pop(name, None)
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        rows = {
            name: {"file_id": self._file_ids.get(name), "unique_id": unique} for name, unique in self._unique.items()
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps({"version": 1, "bot_id": self.bot_id, "stickers": rows}, indent=1), encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except OSError:
            logger.warning("the dice sticker cache %s cannot be written", self.path, exc_info=True)

    def __len__(self) -> int:
        return len(self._file_ids)
