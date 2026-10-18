# writers-telegram-bot

A bot for writing communities on Telegram (and Discord), with prompts, a word counter and writing sprints.

A bot is a set of **modules** (prompts, word counter, sprints, greeter, dice, smash, replies,
achievements) served on Telegram and/or Discord, described by a config file. Without a config file the
environment describes one bot with the v2 modules, so by default the bot does what the v2 bot did. The
redesigned sprints (works, goals, streaks, the web app) are switched on per bot in the config file, or with
`PREVIEW_FEATURES=1`. See [the plan](docs/PLAN.md) for the design and
[the workflow review](docs/WORKFLOW-REVIEW.md) for what modelled use turned up.

All user-facing texts are in [`src/writers_bot/locale/uk.toml`](src/writers_bot/locale/uk.toml), each with
a comment saying where it appears; edit them there.

## Modules

| Module | Options (config file) | Needs | |
|---|---|---|---|
| `prompts` | `source` (`sheet` or `db`), `categories` (allow `/prompt <category>` with the sheet) | the Google Sheet; storage with `db` | `/prompt`, `/image`, `/image_<category>`, `/promptstats`; `/reload` with the sheet; with `db`: `/addprompt`, `/addimage`, `/suggest`, `/suggestions`, `/review` |
| `wordcount` | | link counting | `/count` (hidden alias `/wc`): text, a reply, or a link (Telegraph, WriteFreely/Write.as, Ellipsus Reader, Google Docs shared by link) |
| `sprints` | `reports`, `works`, `percent`, `web` | storage; link counting with works | `/sprint`; with `reports`: `/progress`, `/top`, `/profile` |
| `greeter` | `texts = {"<chat id>" = "Вітаємо, {name}!"}`, `from_sheet` | | Welcome messages for new members |
| `dice` | | | `/d20`, `/2d6+1`, `/r 4к6 Caption`, `/d20adv` |
| `smash` | | | `/smash` |
| `replies` | `entries = [{command = "imafan", answers = […]}, {phrase = "…", answers = […]}]`, `legacy` | | Answers to commands and phrases (`legacy`: `/imafan` from the sheet and the two slogans); with storage, chat admins manage them (`/replies`, the web app) |

At most one bot has storage (the sprints and anything that keeps data about people); the chat registry,
super-admin tools, your-data commands and the web app live in that bot. Other bots run without a database.

### Sprints (`reports = true`)

- **Join** with the button under the sprint message. A private card shows your works as toggles (kept for
  the next sprint); joining never needs you to share anything.
- **Report** by replying to the sprint message or the board: `500` (your count for this sprint; sending it
  again replaces it), `+300`, `=5000` (the work's total; the bot credits the difference), a link, `40%`
  for a % work, `✓`, `✗`; several works by name: `+300 роман +200 есе`. Or the buttons at the end:
  **Звітувати**, **✓ Готово**, **✗ Не вийшло**. Report replies are deleted once recorded (when the bot is
  an admin; otherwise it reacts 👍) and the confirmation, with an Undo button, is private.
- **Late reports** until your next sprint or 12 hours after the end; the board is edited as they arrive.
- **The board**: words, then "% від плану", then ✓ and ✗. One line per person, no medals or ranks.
- **Works** (`works = true`): up to five, each a name, an optional link (counted at the start and the end,
  and daily at 03:00 Kyiv time), a unit (words or %, with `percent = true`), a goal (per day, week or
  month), "hide the title" and "keep for the next sprint". Manage them in the private chat
  (`/profile` → Твори) or the web app. Each word is credited once: every work keeps its last counted total,
  changed by compare-and-set.
- **`/progress`**: `+400`, `=5000`, a link, `✓`, optionally a work and an earlier day (`вчора`,
  `позавчора`, `17.10`, up to 3 days back). Without arguments: your progress privately, with a Share button.
- **`/profile`**: works with goals and month totals, the streak with the last 7 days (missed days within 3
  days can be marked), completed sprints and minutes, and settings: "hide my numbers", the unit for new works,
  My data (export as a ZIP of CSV files, delete everything). `/export` and `/forgetme` stay as commands.
- **`/top [days]`**: minutes of completed sprints in this chat; people who hide their numbers aren't on it.
- **Ukrainian command names** (`/спринт`, `/прогрес`, `/профіль`, `/промпт`, `/картинка`, `/підрахуй`,
  `/топ`, `/допомога`, `/експорт`, `/забудьмене`) work where the bot sees all messages.

Private answers in groups are ephemeral messages (Bot API 10.2) where Telegram allows: after a button
press, after an ephemeral command, or at any time when the bot is a chat admin. Otherwise they are ordinary
messages whose buttons only that person can press, deleted after a few minutes. Make the bot a chat admin
(with "delete messages") for the quiet experience.

### Chat settings and communities (v4.0)

Chat admins change their chat's settings with **`/settings`** (a private card with toggles, and a button to
the web app's Chat tab): the default sprint length, works and % on or off, the default unit, hidden ✓
reports, work names on the sprint message, `/top`, deleting report messages, the welcome text, the bot's
default replies, the shared prompts. Changes apply from the next sprint.

A chat that belongs to a **Telegram Community** (Bot API 10.2) is linked automatically; super-admins can
also link chats by hand (`/community new <name>`, `/community add <id> <chat id>…`, `/community` to list).
A community shares `/top`, one prompt set and its settings (`/community set <id> <setting> <value>`; a
chat's own value wins), and gets one broadcast, in its largest chat.

**Prompt databases** (`prompts = { source = "db" }`): the sheet and the Drive folders are imported once into
a shared set; each chat (or community) has its own set on top. Categories are named in English with an
optional Ukrainian alias (`horror` / `жахи`). Chat admins add prompts (`/addprompt жахи текст`, a picture
with the caption `/addimage пейзаж`, uploaded to Drive when the bot's Google account may write there, else
kept as the Telegram photo), members suggest them (`/suggest`), admins accept them (`/suggestions`, the web
app), and chats can submit prompts to the shared set, which super-admins review with `/review`.

### The web app (optional)

The bot with storage can serve a web app, opened as a Telegram Mini App (from the Панель button on sprint
messages) or as a website with Telegram Login or Discord: the sprint panel (a field per work), your works,
progress (charts, the last 30 days with missed days to mark, your latest sprints), settings with export and
deletion; for chat admins the chat's settings, replies and prompts. Without `web.url` (or with the tunnel
down) everything works in chat and the buttons are hidden.

Setup (ops): public HTTPS to the web app's port, the bot's Main Mini App set to the URL in @BotFather
(Mini App methods only work on that domain since Bot API 10.2), and the domain set with `/setdomain` for
Telegram Login. Discord sign-in needs `web.discord_client_id` and `web.discord_client_secret` (or
`DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`) and the redirect `https://<web url>/auth/discord/callback`.

### Admin: chats and broadcasts (super-admins, private chat)

- `/chats [groups|private|all]`: every chat the bot is in, with ids and last activity (CSV over 60).
- `/broadcast groups|private|all`: as a reply to the message to broadcast, or with the text after the
  target. A preview and a confirmation come first; chats that removed or blocked the bot are skipped later.
- `/bcexclude <chat id> ...`, `/bcinclude <chat id> ...`.
- `/community …`: communities of chats (above).

Telegram has no method that lists a bot's chats, so the bot records chats as updates arrive. Older chats
can be imported once from the v2 `db.pickle`: `python scripts/import_legacy_chats.py db.pickle`.

## Configuration

### A config file (`BOT_CONFIG=bots.toml` or `--config`)

```toml
[common]
db = "data/bot.sqlite3"

[web]                                   # optional
url = "https://writers.example.org"
listen = "0.0.0.0:8081"
secret = "env:WEB_SECRET"              # 16+ characters; signs sessions and launch links
discord_client_id = "env:DISCORD_CLIENT_ID"          # optional: Discord sign-in
discord_client_secret = "env:DISCORD_CLIENT_SECRET"

[bots.main]
modules = ["prompts", "wordcount", "sprints", "dice"]
prompts = { categories = true }
sprints = { reports = true, works = true, percent = true }
telegram = { token = "env:TG_MAIN", super_admins = [123456], polling = true }
discord = { token = "env:DISCORD_MAIN" }

[bots.greeter]
modules = ["greeter"]
greeter = { texts = { "-1001234567890" = "Вітаємо, {name}!" } }
telegram = { token = "env:TG_GREETER" }
```

`"env:NAME"` takes a value from the environment. Telegram target keys: `token`, `super_admins`,
`allowed_chats`, `allow_private` (default on), `polling` (default on), `webhook_host`, `webhook_secret`,
`webhook_listen`, `webhook_port`, `webhook_path`, `ephemeral_replies` (default on), `ephemeral_commands`.
Discord: `token`, `guild_ids`, `allowed_guilds`. The Google Sheet and the ops settings below still come from
the environment.

One process runs every bot of the file (`writers-bot`), or one bot (`writers-bot --bot greeter`), each in
its own container from the same image if you like.

### The environment (no config file)

| Variable | Default | |
|---|---|---|
| `TELEGRAM_TOKEN` | required | Bot token |
| `TELEGRAM_WEBHOOK_TOKEN` | required for webhooks | Secret Telegram sends with every update |
| `WEBHOOK_HOST`, `WEBHOOK_PATH`, `WEBHOOK_LISTEN`, `PORT` | | Webhook mode |
| `TEST_ENV` or `TELEGRAM_POLLING` | off | Long polling instead of a webhook |
| `BOT_SUPERADMINS` | empty | Comma-separated user ids with admin rights everywhere |
| `ALLOWED_CHATS` | empty = all chats | Comma-separated group/channel ids the bot answers in |
| `EXTRAS` | off | Adds the `dice`, `smash`, `greeter` (from the sheet) and `replies` (legacy) modules |
| `ALLOW_PRIVATE_CHATS` | follows `EXTRAS` | Private chats work even when `ALLOWED_CHATS` is set |
| `PREVIEW_FEATURES` | off | The redesigned sprints (reports, works, %), the admin tools, the web app, and the Discord bot |
| `WEB_APP_URL`, `WEB_APP_SECRET`, `WEB_APP_LISTEN` | off | The web app, with `PREVIEW_FEATURES` |
| `EPHEMERAL_REPLIES` | follows `PREVIEW_FEATURES` | Private answers in groups as ephemeral messages |
| `EPHEMERAL_COMMANDS` | off | Mark personal commands as ephemeral in the group menu |
| `DISCORD_TOKEN`, `DISCORD_GUILD_IDS`, `DISCORD_ALLOWED_GUILDS` | | Discord (`writers-bot-discord`, needs `PREVIEW_FEATURES`) |
| `DB_PATH` | `data/bot.sqlite3` | SQLite database; back it up with `scripts/backup_db.py` |
| `BOT_TIMEZONE` | `Europe/Kyiv` | Fallback time zone for times in messages |
| `SPRINT_REPORT_WINDOW` | `30` | Minutes before a v2-style sprint closes (the redesign keeps late reports for 12 h) |
| `HEALTH_PORT` / `HEALTH_HOST` | off (`8080` in the image) / `127.0.0.1` | `GET /health`: JSON with `ok`, version, uptime and each bot's state |
| `LOG_FORMAT` | `text` | `json` for one JSON object per line |
| `ERROR_ALERTS` | on | Errors from the log go to the super-admins in private (rate-limited) |
| `SPREADSHEET_ID`, `CONFIG_SHEET_NAME`, `PROMPT_SHEETS`, `PROMPT_RANGE` | | Google Sheet with prompts and config |
| `GOOGLE_DRIVE_BASE_FOLDER_ID` | | Drive folder whose sub-folders hold image prompts |
| `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_TOKEN_FILE` | `token.json` | Google credentials (JSON only) |

## Running

```sh
uv sync --extra discord
TELEGRAM_TOKEN=… TEST_ENV=1 uv run writers-bot-telegram
BOT_CONFIG=bots.toml uv run writers-bot
```

Docker: the image runs `writers-bot-telegram` as a non-root user; mount a volume at `/app/data` (database
and `token.json`). With a config file, mount it and set `BOT_CONFIG` (or run `writers-bot --config …`).

## Development

```sh
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run ruff format .
```

Layout: `core/` holds the platform-independent parts (sprint engine, works and reporting, storage, stats,
link fetching, texts); `modules/` the modules; `telegram_bot/` the Telegram runtime (commands, private
asks, admin tools); `discord_bot/` the Discord target; `web/` the web app. `tests/fake_telegram.py` is an
in-process Bot API used by the workflow tests (`tests/test_wf_*.py`); set `WF_TRANSCRIPTS=dir` to write
what everyone saw in each workflow.

See [docs/PLAN.md](docs/PLAN.md) for the roadmap and [docs/MIGRATION.md](docs/MIGRATION.md) for moving
from Render to self-hosting.
