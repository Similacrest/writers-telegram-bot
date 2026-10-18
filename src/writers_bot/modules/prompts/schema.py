"""Tables of the prompts module (``source = "db"``)."""

from ...core.storage import ModuleSchema

SCHEMA = ModuleSchema(
    "prompts",
    {
        1: [
            """CREATE TABLE prompt_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,            -- shared | chat | community
                owner TEXT NOT NULL,           -- '' | 'telegram:<chat id>' | '<community id>'
                UNIQUE (kind, owner)
            )""",
            """CREATE TABLE prompt_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_id INTEGER NOT NULL REFERENCES prompt_sets (id) ON DELETE CASCADE,
                name TEXT NOT NULL,            -- English
                alias TEXT,                    -- Ukrainian
                kind TEXT NOT NULL DEFAULT 'text',
                UNIQUE (set_id, kind, name)
            )""",
            """CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES prompt_categories (id) ON DELETE CASCADE,
                text TEXT,
                file_id TEXT,                  -- a Telegram photo (image prompts not on Drive)
                url TEXT,                      -- a Drive link (image prompts)
                status TEXT NOT NULL DEFAULT 'active',  -- active | suggested | submitted | rejected
                author_platform TEXT,
                author_id TEXT,
                origin INTEGER,                -- the chat prompt a submission to the shared set came from
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX prompts_category ON prompts (category_id, status)",
            "CREATE TABLE prompts_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        ]
    },
)
