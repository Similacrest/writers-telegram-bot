#!/usr/bin/env python
"""One-time import of chats the old bot knew about (from its ``db.pickle``) into the chat registry.

    DB_PATH=/app/data/bot.sqlite3 python scripts/import_legacy_chats.py db.pickle

Imported chats are marked "unknown" until the bot hears from them or a broadcast reaches them; chats
that turn out to be gone are then marked and skipped. Users from ``user_data`` are added as private
chats only with --include-users (they may never have opened a private chat with the bot).
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime

from writers_bot.core.models import ChatRecord, ChatStatus
from writers_bot.core.storage import Storage
from writers_bot.telegram_bot.legacy import guess_chat_type, read_legacy_ids


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pickle_file")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/bot.sqlite3"))
    parser.add_argument("--include-users", action="store_true", help="also add user_data ids as private chats")
    args = parser.parse_args()

    chat_ids, user_ids = read_legacy_ids(args.pickle_file)
    ids = set(chat_ids) | (set(user_ids) if args.include_users else set())
    storage = Storage(args.db)
    await storage.open()
    try:
        epoch = datetime(2000, 1, 1, tzinfo=UTC)  # never newer than a real sighting
        for chat_id in sorted(ids):
            await storage.upsert_chat(
                ChatRecord("telegram", str(chat_id), guess_chat_type(chat_id), status=ChatStatus.UNKNOWN,
                           first_seen=epoch, last_seen=epoch),
                only_new=True,
            )  # fmt: skip
    finally:
        await storage.close()
    groups = sum(1 for i in ids if i < 0)
    print(
        f"found {len(ids)} chats ({groups} groups/channels, {len(ids) - groups} private); new ones added to {args.db}"
    )
    if not args.include_users and user_ids:
        print(f"{len(user_ids)} user ids not imported as private chats (use --include-users)")


if __name__ == "__main__":
    asyncio.run(main())
