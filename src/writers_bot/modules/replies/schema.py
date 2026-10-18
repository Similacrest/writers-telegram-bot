"""Tables of the replies module."""

from ...core.storage import ModuleSchema

SCHEMA = ModuleSchema(
    "replies",
    {
        1: [
            """CREATE TABLE replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,          -- '*' = every chat of the bot (the defaults)
                kind TEXT NOT NULL,             -- command | phrase
                trigger TEXT NOT NULL,          -- lower case; commands without the slash
                answers TEXT NOT NULL,          -- JSON list of HTML texts
                created_by TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (platform, chat_id, kind, trigger)
            )""",
            "CREATE TABLE replies_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        ]
    },
)
