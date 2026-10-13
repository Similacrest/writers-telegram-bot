# writers-telegram-bot

A bot for writing communities, on Telegram and Discord.

- **Prompts**: random text prompts (one per category, or from one category) and image prompts, managed in
  Google Sheets / Google Drive.
- **Word counter**: counts plain text, or the text behind a link: [Telegraph](https://telegra.ph),
  any [WriteFreely](https://writefreely.org) / Write.as instance, and public
  [Ellipsus](https://ellipsus.com) Reader links (`ellipsus.com/read/…`).
- **Writing sprints** with a live, pinned timer, join/leave buttons, one sprint per chat (or per forum topic),
  then **word reports**: a leaderboard with words per minute, per-user stats and a chat top.

## Commands

| Telegram | Discord | |
|---|---|---|
| `/prompt [category]`, `/prompt_ua` | `/prompt [category]` | Random prompt; every sheet column is a category |
| `/image [category]`, `/image_<category>` | `/image [category]` | Random image prompt; categories are Drive sub-folders |
| `/wc` (reply, text or link) | `/wc text` | Words, characters, letters |
| `/sprint [minutes] [delay]` | `/sprint` | Start a sprint (1–120 min, delay 0–10, defaults 30 / 2) |
| `/words …` or reply to the results | `/words` or the report button | Report words (see below) |
| `/mystats`, `/top [days]` | `/mystats`, `/top` | Statistics |
| `/sprinthelp`, `/help`, `/start` | | Help (`help_message` from the config sheet, or a built-in text) |
| `/stats`, `/reload`, `/debuginfo` | `/reload` | Prompt counts, reload prompts (admins), ids |

### Admin: chats and broadcasts (Telegram, `BOT_SUPERADMINS` only, in a private chat with the bot)

- `/chats [groups|private|all]`: every chat the bot is in (groups, supergroups, channels and private chats),
  with ids and last activity; more than 60 entries come as a CSV file.
- `/broadcast groups|private|all`: send it **as a reply** to the message to broadcast (any type: text,
  photo, formatting are kept), or `/broadcast all Текст`. The bot shows a preview and the recipient count
  and sends only after you press ✅. Chats that removed or blocked the bot are marked and skipped afterwards.

Telegram has no method that lists a bot's chats, so the bot records chats as it receives updates from them
and when it is added or removed. Chats from before this version are imported once from the old
`db.pickle`: `python scripts/import_legacy_chats.py db.pickle` (add `--include-users` to also try users
who only used the bot in groups as private chats).

### Word reports

When a sprint ends, the bot posts a results message and accepts reports for `SPRINT_REPORT_WINDOW` minutes:

- `500` or `+500`: words written in this sprint;
- a link: the bot counts the words in the text;
- `=12500`: the manuscript total now, if a starting count was given.

Before or during the sprint a participant can set a starting count: `/words 12000` (before the start),
`/words =12000` or `/words <link>`. After the sprint, `/words` with no argument recounts the same link and
records the difference.

On Telegram, replies to the results message (or to the "📝 Звітувати слова" prompt) count as reports.
Discord uses `/words` or the button's form.

## Configuration (environment variables)

### Telegram

| Variable | Default | |
|---|---|---|
| `TELEGRAM_TOKEN` | required | Bot token |
| `TELEGRAM_WEBHOOK_TOKEN` | required for webhooks | Secret Telegram sends with every update; the bot refuses to start without it |
| `WEBHOOK_HOST` | `$HOSTNAME` | Public host name for the webhook URL (under Docker `HOSTNAME` is the container id, so set this) |
| `WEBHOOK_PATH` | empty | Path part of the webhook URL |
| `WEBHOOK_LISTEN` / `PORT` | `0.0.0.0` / `80` | Listen address and port |
| `TEST_ENV` or `TELEGRAM_POLLING` | off | Use long polling instead of a webhook |
| `BOT_SUPERADMINS` | empty | Comma-separated user ids with admin rights everywhere |
| `ALLOWED_CHATS` | empty = all chats | Comma-separated chat ids the bot answers in (super-admins can always use it in private) |
| `SPRINT_KEEPALIVE` | off | Every 10 minutes of a sprint, briefly post a link to `WEBHOOK_HOST` so a sleeping host is woken by Telegram's link preview |

### Discord

| Variable | Default | |
|---|---|---|
| `DISCORD_TOKEN` | required | Bot token. No privileged intents are needed |
| `DISCORD_GUILD_IDS` | empty | Guilds to sync slash commands to instantly; empty = global sync (can take up to an hour) |
| `DISCORD_ALLOWED_GUILDS` | empty = all | Guilds the bot answers in |

Invite the bot with the `bot` and `applications.commands` scopes and the Send Messages, Manage Messages
(for pinning; optional) and Read Message History permissions.

### Shared

| Variable | Default | |
|---|---|---|
| `DB_PATH` | `data/bot.sqlite3` | SQLite database (sprints, reports) |
| `BOT_TIMEZONE` | `Europe/Kyiv` | Fallback time zone for times in messages (clients show local time where supported) |
| `SPRINT_REPORT_WINDOW` | `30` | Minutes to accept reports after a sprint |
| `SPREADSHEET_ID` | empty = prompts disabled | Google Sheet with the prompts and config |
| `CONFIG_SHEET_NAME` | empty | Config sheet (rows: key, subkey, comment, value…) |
| `PROMPT_SHEETS` | `ua=База-ua` | Language → sheet name, comma-separated |
| `PROMPT_RANGE` | `B1:ZZ` | Range read from each prompt sheet; each column is a category (header in row 1) |
| `GOOGLE_DRIVE_BASE_FOLDER_ID` | empty | Drive folder whose sub-folders hold image prompts |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | empty | Service-account key (JSON). Takes precedence over the user token |
| `GOOGLE_TOKEN_FILE` | `token.json` | User OAuth token (JSON), refreshed in place |

Get a user token with `scripts/google_auth.py`, or convert the old `token.pickle.b64` once with
`scripts/migrate_google_token.py token.pickle.b64 data/token.json`. The bot never loads pickles.

## Running

```sh
uv sync --extra discord
TELEGRAM_TOKEN=… TEST_ENV=1 uv run writers-bot-telegram
DISCORD_TOKEN=… uv run writers-bot-discord
```

Docker: the image runs `writers-bot-telegram` as a non-root user; mount a volume at `/app/data` (database and
`token.json`). Run `writers-bot-discord` from the same image for Discord.

## Development

```sh
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run ruff format .
```

Layout: `src/writers_bot/core` holds the platform-independent parts (sprint engine, storage, word counting,
link fetching, prompts, message texts); `telegram_bot` and `discord_bot` are thin adapters. Link fetching
goes through `core/netguard.py`, which only connects to public addresses over https.

See [docs/PLAN.md](docs/PLAN.md) for the roadmap.
