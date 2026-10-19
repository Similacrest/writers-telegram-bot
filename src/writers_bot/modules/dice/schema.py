"""The dice module's table (roll variables, ``variables = true``)."""

from ...core.storage import ModuleSchema

SCHEMA = ModuleSchema(
    "dice",
    {
        1: [
            """CREATE TABLE dice_vars (
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,            -- in the private chat: the person's own id
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,               -- lower case, without the $
                steps TEXT NOT NULL,              -- a JSON list of rolls, at most 8
                updated_at TEXT NOT NULL,
                PRIMARY KEY (platform, chat_id, user_id, name)
            )""",
            "CREATE INDEX dice_vars_user ON dice_vars (platform, user_id)",
        ]
    },
)
