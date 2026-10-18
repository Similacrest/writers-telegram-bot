"""Tables of the achievements module."""

from ...core.storage import ModuleSchema

SCHEMA = ModuleSchema(
    "achievements",
    {
        1: [
            """CREATE TABLE achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,               -- 'chat:telegram:<id>' or 'community:<id>'
                name TEXT NOT NULL,
                description TEXT,
                sticker_set TEXT,                  -- the admins' pack; the bot only maps a sticker to an achievement
                sticker_unique_id TEXT,
                sticker_file_id TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE UNIQUE INDEX achievements_name ON achievements (owner, name) WHERE deleted = 0",
            """CREATE TABLE awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                achievement_id INTEGER NOT NULL REFERENCES achievements (id) ON DELETE CASCADE,
                person_id INTEGER,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT,
                chat_id TEXT,
                awarded_by TEXT,
                awarded_at TEXT NOT NULL,
                UNIQUE (achievement_id, platform, user_id)
            )""",
            "CREATE INDEX awards_user ON awards (platform, user_id)",
        ],
        2: [
            """CREATE TABLE seen_users (  -- to find people by @username or name for /achievements <name>
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT,
                name TEXT,
                PRIMARY KEY (platform, user_id)
            )""",
        ],
    },
)
