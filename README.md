# writers-telegram-bot

A bot for writing communities on Telegram, with prompts, a word counter and writing sprints.

By default the bot does what the v2 bot did. Everything added since then that users see is behind
**`PREVIEW_FEATURES=1`** while it is redesigned (see [the plan](docs/PLAN.md)); the rewrite itself (security,
stability, forum topics, sprints that survive restarts, health checks, error alerts) is always on.

- **Prompts**: random text prompts (one per category) and image prompts, managed in Google Sheets / Google
  Drive.
- **Word counter**: counts plain text, or the text of a [Telegraph](https://telegra.ph) page.
- **Writing sprints** with a live, pinned timer, join/leave buttons and a repeat button, one sprint per chat
  (or per forum topic).

## Commands

| Telegram | |
|---|---|
| `/prompt`, `/prompt_ua` | One random prompt from every category (every sheet column is a category) |
| `/image`, `/image_<category>` | Random image prompt; categories are Drive sub-folders |
| `/wc` (reply, text or a Telegraph link) | Words, characters, letters |
| `/sprint [minutes] [delay]` | Start a sprint (1–120 min, delay 0–10, defaults 30 / 2) |
| `/help`, `/start` | Help (`help_message` from the config sheet, or a built-in text) |
| `/stats`, `/reload`, `/debuginfo` | Prompt counts, reload prompts (admins), ids |
| `/d20`, `/2d6+1`, `/r 4к6 Caption`, `/d20adv` | Dice rolls (`EXTRAS`) |
| `/smash`, `/imafan` | Keyboard smash; a random `imafan_message` from the config sheet (`EXTRAS`) |

**Extras** (`EXTRAS=1`, off by default; these come from the v2 `extended` branch): dice rolls, `/smash`,
`/imafan`, greeting new members in groups that have a `welcome_message` row for their chat id in the config
sheet (`{name}` is replaced by the new members), "Слава Україні" / "Слава нації" replies, the "Я переродився"
`/start` greeting, and private chats staying open when `ALLOWED_CHATS` is set.

### Preview (`PREVIEW_FEATURES=1`)

| Telegram | Discord | |
|---|---|---|
| | the whole Discord bot | `writers-bot-discord` refuses to start without `PREVIEW_FEATURES=1` |
| `/prompt [category]`, `/image [category]` | `/prompt`, `/image` | Prompt from one category |
| `/wc <link>` | `/wc text` | Also WriteFreely / Write.as, public [Ellipsus](https://ellipsus.com) Reader links (`ellipsus.com/read/…`) and Google Docs shared as "anyone with the link" |
| `/sprint [minutes] [delay] [link]` | `/sprint` | A link is counted at the start and again at the end |
| `/join [link]` (or `/sprint <link>` while a sprint is on) | `/join [link]` | Join the current sprint, optionally with the text to count |
| `/words …` or reply to the results | `/words` or the report button | Word reports after a sprint (see below); without the preview a sprint ends with the v2 "finished" message and a repeat button |
| `/sprintmode [words\|percent]` | `/sprintmode` | Per chat: report words, or percent of the task done (chat admins change it) |
| `/mystats`, `/top [days]` | `/mystats`, `/top` | Statistics: today's words, goals, days in a row, fastest sprint; the chat top |
| `/goal daily 500`, `/goal monthly 20000`, `/goal daily off` | `/goal` | Word goals from sprint reports; reaching one is announced once |
| `/tz Europe/Kyiv` (or `/tz Kyiv`) | `/tz` | Your time zone: when your day starts for goals and days in a row |
| `/export` | `/export` | Your sprints as CSV (sent to your private chat) |
| `/forgetme` (private chat) | `/forgetme confirm:ТАК` | Delete your reports, stats and settings, after a confirmation |
| `/sprinthelp` | | How sprints and reports work |
| `/chats`, `/broadcast`, `/bcexclude`, `/bcinclude` | | Admin: chat list and broadcasts (below) |

Also with the preview: personal bests are announced after reports, and personal replies in Telegram groups
are ephemeral (`EPHEMERAL_REPLIES`).

### Admin: chats and broadcasts (preview; Telegram, `BOT_SUPERADMINS` only, in a private chat with the bot)

- `/chats [groups|private|all]`: every chat the bot is in (groups, supergroups, channels and private chats),
  with ids and last activity; more than 60 entries come as a CSV file.
- `/broadcast groups|private|all`: send it **as a reply** to the message to broadcast (any type: text,
  photo, formatting are kept), or `/broadcast all Текст`. The bot shows a preview and the recipient count
  and sends only after you confirm. Chats that removed or blocked the bot are marked and skipped afterwards.
- `/bcexclude <chat id> ...` keeps chats out of every broadcast, `/bcinclude <chat id> ...` takes them back;
  `/bcexclude` alone lists the excluded chats. `/chats` marks them too.

Telegram has no method that lists a bot's chats, so the bot records chats as it receives updates from them
and when it is added or removed. Chats from before this version are imported once from the old
`db.pickle`: `python scripts/import_legacy_chats.py db.pickle` (add `--include-users` to also try users
who only used the bot in groups as private chats).

### Word reports (preview)

When a sprint ends, the bot posts a results message and accepts reports for `SPRINT_REPORT_WINDOW` minutes:

- `500` or `+500`: words written in this sprint;
- a link: the bot counts the words in the text;
- `=12500`: the manuscript total now, if a starting count was given.

Before or during the sprint a participant can set a starting count: `/words 12000` (before the start),
`/words =12000` or `/words <link>`; or give the link when starting (`/sprint 30 2 <link>`) or joining
(`/join <link>`). A link is counted again
automatically when the sprint ends and the difference is recorded; if that fails (site down), `/words` with
no argument tries again.

On Telegram, replies to the results message (or to the "Звітувати" prompt) count as reports.
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
| `ALLOWED_CHATS` | empty = all chats | Comma-separated group/channel ids the bot answers in |
| `EXTRAS` | off | Switch for the `extended`-branch extras (see Commands) |
| `ALLOW_PRIVATE_CHATS` | follows `EXTRAS` | Private chats work even when `ALLOWED_CHATS` is set (off: only super-admins) |
| `SLOGAN_REPLIES` | follows `EXTRAS` | Answer "Слава Україні" / "Слава нації" |
| `EPHEMERAL_REPLIES` | follows `PREVIEW_FEATURES` | In groups, replies meant for one person (errors, `/mystats`, `/goal`) are sent as ephemeral messages (Bot API 10.2), visible only to them. Works when the bot is a chat admin or the user sent an ephemeral command; otherwise a normal reply |
| `EPHEMERAL_COMMANDS` | off | Mark `/words`, `/join`, `/mystats`, `/goal`, `/tz`, `/export` as ephemeral commands in groups, so the command itself is hidden from others too |

### Discord (preview)

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
| `DB_PATH` | `data/bot.sqlite3` | SQLite database (sprints, reports, chats); back it up with `scripts/backup_db.py` |
| `BOT_TIMEZONE` | `Europe/Kyiv` | Fallback time zone for times in messages (clients show local time where supported) |
| `SPRINT_REPORT_WINDOW` | `30` | Minutes to accept reports after a sprint |
| `HEALTH_PORT` / `HEALTH_HOST` | off (`8080` in the image) / `127.0.0.1` | `GET /health`: JSON with `ok`, version, uptime, seconds since the last update; 503 when not receiving updates |
| `LOG_FORMAT` | `text` | `json` for one JSON object per log line |
| `ERROR_ALERTS` | on | Telegram: errors from the log go to the super-admins in private (same error at most every 15 minutes, 10 an hour) |
| `PREVIEW_FEATURES` | off | Everything users see that v2 did not have, including the Discord bot (see Commands) |
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
DISCORD_TOKEN=… PREVIEW_FEATURES=1 uv run writers-bot-discord
```

Docker: the image runs `writers-bot-telegram` as a non-root user; mount a volume at `/app/data` (database and
`token.json`). Run `writers-bot-discord` from the same image for Discord (with `PREVIEW_FEATURES=1`).

## Development

```sh
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run ruff format .
```

Layout: `src/writers_bot/core` holds the platform-independent parts (sprint engine, storage, word counting,
link fetching, prompts, message texts); `telegram_bot` and `discord_bot` are thin adapters. Link fetching
goes through `core/netguard.py`, which only connects to public addresses over https.

See [docs/PLAN.md](docs/PLAN.md) for the roadmap and [docs/MIGRATION.md](docs/MIGRATION.md) for moving
from Render to self-hosting.
