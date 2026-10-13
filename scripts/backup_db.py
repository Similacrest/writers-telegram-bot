#!/usr/bin/env python
"""Consistent copy of the bot's SQLite database while the bot is running.

    python scripts/backup_db.py /backups/bot-$(date +%F).sqlite3

Uses SQLite's online backup, so it is safe with the WAL journal the bot uses (a plain ``cp`` of the
file can miss recent writes). Reads ``DB_PATH`` unless ``--db`` is given.
"""

import argparse
import os
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/bot.sqlite3"))
    args = parser.parse_args()

    source_path, target = Path(args.db), Path(args.target)
    if not source_path.exists():
        raise SystemExit(f"no database at {source_path}")
    if target.exists():
        raise SystemExit(f"{target} already exists")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
        version = dest.execute("PRAGMA user_version").fetchone()[0]
        ok = dest.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dest.close()
        source.close()
    print(f"{source_path} -> {target} (schema {version}, integrity {ok})")


if __name__ == "__main__":
    main()
