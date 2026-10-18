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
| `sprints` | `reports`, `works`, `percent`, `web` | storage; link counting with works | `/sprint`; with `reports`: `/progress`, `/top`, `/profile`; with `works`: `/works` (`/твори`) |
| `greeter` | `texts = {"<chat id>" = "Вітаємо, {name}!"}`, `from_sheet` | | Welcome messages for new members |
| `dice` | `stickers` (animated dice stickers, see below) | a super-admin to own the sticker packs, for `stickers` | `/d20`, `/2d6+3 Атака`, `/r 4d6kh3`, `/d20adv`, `/d100`: [notation](#dice) |
| `smash` | | | `/smash` |
| `achievements` | | storage | Achievements made from the admins' sticker pack: `/newachievement` (reply to a sticker, or send one in private), `/award` (`/нагорода`), `/unaward`, `/achievements`; posted as [pictures](#achievements) |
| `replies` | `entries = [{command = "imafan", answers = […]}, {phrase = "…", answers = […]}]`, `legacy` | | Answers to commands and phrases (`legacy`: `/imafan` from the sheet and the two slogans); with storage, chat admins manage them (`/replies`, the web app) |

At most one bot has storage (the sprints and anything that keeps data about people); the chat registry,
super-admin tools, your-data commands and the web app live in that bot. Other bots run without a database.

### Sprints (`reports = true`)

- **Join** with the button under the sprint message. Your works that aren't paused are in the sprint; a
  private card (shown when there is something to say, or with "Мої твори") switches them. Joining never
  needs you to share anything.
- **Report** by replying to the sprint message or the board: `500` (your count for this sprint; sending it
  again replaces it), `+300`, `=5000` (the work's total; the bot credits the difference), a link, `40%`
  for a % work, `✓`, `✗`; several works by name: `+300 роман +200 есе`. Or the buttons at the end:
  **Звітувати**, **✓ Готово**, **✗ Не вийшло**. Report replies are deleted once recorded (when the bot is
  an admin; otherwise it reacts 👍) and the confirmation, with an Undo button, is private.
- **Late reports** from the participants until their next sprint or 12 hours after the end; the board is
  edited as they arrive.
- **The board**: words, then "% від плану", then ✓ and ✗. One line per person, no medals or ranks.
- **Works** (`works = true`): up to five, each a name, an optional link (counted at the start and the end,
  and daily at 03:00 Kyiv time), a unit (words or %, with `percent = true`), a goal (per day, week or
  month), "hide the title", and **paused** or not. A paused work isn't taken into sprints and doesn't get
  numbers without a name (`500` goes to the works you're writing; `500 роман` still reaches a paused one).
  Switch works anywhere with **`/works`** (in a group: a private card of on/paused toggles, the sprint's card
  while you're in one; in the private chat: the works menu); add and edit them in the private chat or the web
  app. When a number could go to two works, the question shows the syntax that avoids it (`300 Роман 200
  Есе`) the first few times, and a "Пауза" button. Each word is credited once: every work keeps its last
  counted total, changed by compare-and-set.
- **`/progress`**: `+400`, `=5000`, a link, `✓`, optionally a work and an earlier day (`вчора`,
  `позавчора`, `17.10`, up to 3 days back). Without arguments: your progress privately, with a Share button.
  A `%` is a share of a sprint's plan, so `/progress 60% план` corrects your latest sprint with that work,
  and the confirmation says which ("Виправив ваш спринт сьогодні о 14:00"), with Undo.
- **Getting started**: three skippable steps in the private chat (your first work, a goal, whether the board
  shows your numbers; typed answers are replies to the step's message), offered on the first sprint card, on `/start` and in `/profile` until done. Progress
  is kept per person (`/profile` and the web app show "1 з 3 кроків" with a Continue button); steps done
  elsewhere (a work added in the menu) count.
- **`/profile`**: works with goals and month totals, the streak with the last 7 days (missed days within 3
  days can be marked), completed sprints and minutes, and settings: "hide my numbers", the unit for new works,
  My data (export as a ZIP of CSV files, delete everything). `/export` and `/forgetme` stay as commands.
- **`/top [days]`**: minutes of completed sprints in this chat; people who hide their numbers aren't on it.
- **Ukrainian command names** (`/спринт`, `/прогрес`, `/профіль`, `/промпт`, `/картинка`, `/підрахуй`,
  `/топ`, `/допомога`, `/експорт`, `/забудьмене`) work where the bot sees all messages.

**`/help`** is built for the person and the chat: only what is on there (a chat with `/top` or achievements
off doesn't list them), grouped in sections, with the chat's values (its default sprint length, its prompt
categories), the chat admins' commands only for chat admins and the bot's admin commands only for
super-admins in the private chat. In a group it is a private answer. The **command menus** follow the same
rules: one for private chats, one for group members, one for chat admins (`all_chat_administrators`), one for
super-admins, and per chat where its settings change them (refreshed when a setting changes).

Private answers in groups are ephemeral messages (Bot API 10.2) where Telegram allows: after a button
press, after an ephemeral command, or at any time when the bot is a chat admin. Otherwise they are ordinary
messages whose buttons only that person can press, deleted after a few minutes. Make the bot a chat admin
(with "delete messages") for the quiet experience.

### Achievements

Chat admins make achievements from stickers of their own pack and award them by hand. Apart from creating
one, the raw sticker is never posted; the bot draws game-style pictures (Pillow, with the bundled DejaVu
Sans font) with the sticker as the icon (an animated sticker's thumbnail; a drawn medal if the sticker is
gone):

- **Awarding** posts one picture, "Досягнення отримано" with the name, description, who, when and how many
  have it, captioned "Alice отримує «Нічна сова»".
- **`/achievements`** in a group is the board of the community (or chat): who has the most, with their
  icons; the caption lists every achievement with its count. **`/myachievements`** posts your collection in
  the chat (the ones you don't have yet greyed out); `/achievements alice`, `@alice` or part of a name (the
  closest match, people with achievements first), or the command as a reply to someone, posts theirs. In
  the private chat, `/achievements` (and `/profile` → Мої досягнення) is your collection.

### Dice

Rolls use the [d20](https://pypi.org/project/d20/) library's notation (the one Avrae uses), with the v2
forms still accepted. A command that starts with dice (`/d20`, `/2d6+3`) or `/r` / `/roll` and anything
after it rolls; text after the expression is the caption.

| | v2 (until v4.1) | now |
|---|---|---|
| Dice, bonus, caption | `/2d6+3 Атака` | the same; spaces allowed: `/r 2d6 + 3 Атака` |
| Cyrillic `к` for `d` | `/4к6` | the same |
| Advantage / disadvantage | `/d20adv`, `/d20dis` (the whole roll twice, better / worse kept) | the same; also `/r 2d20kh1`, `/r 2d20kl1` |
| Bonus as a letter | `/2d6b2`, `/2d6m1` | the same (read as `+2`, `-1`) |
| Minimum per die | `/d6mi2`: each die rolled **between** 2 and 6 | `mi2`: each die **raised to** at least 2 (the d20 meaning); negative minimums aren't accepted |
| Keep / drop | — (the suffix became the caption) | `/4d6kh3`, `kl`, `ph`, `pl`, by comparison `/4d6k>3`, `p<3`, both ends `8d6kh1kl1`; Roll20's `dl`/`dh` read as `pl`/`ph` |
| Exploding, rerolls, clamping | — | `/r 4d6e>5`, `/r 1d20ro1`, `rr<2`, `ra6`, `mi10`, `ma3` |
| Arithmetic, several dice | `+N` / `-N` only | `/r (1d6+2)*2`, `/r 2d10/2`, `/r d20+d4`, `/r (1d6, 1d8)kh1`, `/r 1d20 [вогонь] + 5` (annotations) |
| Percentile | `/d100` | the same; also `/d%` |
| Not supported | | Fudge dice (`4dF`); counting successes in a pool (`10d10k>7` adds the kept dice up) |
| Too many dice (over 500) | silently one die | "Забагато кубиків: до 500 за раз." |
| Not a valid roll | rolled defaults anyway | "Не зрозумів кидок…" with examples |
| Output | `2d6+3 9: [1, 5]+3` | `2d6 (1, 5) + 3 = 9`: highest roll bold, ones underlined, dropped dice struck out |

**Dice stickers** (`dice = { stickers = true }`, on with `PREVIEW_FEATURES`): a roll of one d4, d6, d8, d10,
d12, d20 or d100 also gets an animated sticker of the die landing on the result (a d100 is the two
percentile dice, tens and ones, landing together). The roll takes 0.6 s and the result then holds still for 2
s (Telegram loops stickers and allows 3 s). The highest result gets golden rays and sparkles, the lowest a
heavy landing, a red glow and dust; a d10 on its own has them on 1 and 10, while the d10s of a d100 are plain
except on 01 and 00 (100). A bare `/d20` is just the sticker. A roll of several dice gets a sticker per die,
up to `max_stickers` (4 by default: `dice = { stickers = true, max_stickers = 4 }`); more dice than that,
or a die without stickers (d3), and it is only the line. The 160 stickers are
made by `scripts/dice_stickers.py` (`uv run --group art python scripts/dice_stickers.py`; needs ffmpeg,
which the `art` dependency group brings) and kept in `src/writers_bot/assets/dice`. At start the bot looks for
its packs (`d20_v2_by_<bot username>` and so on; the number changes with the animations) and uploads the missing ones once, owned by the super-admin
with the lowest id, as Telegram requires a person as a pack's owner; after that it sends them by file id.

**New dice sticker packs (v4.2.2, not used by the bot yet).** `src/writers_bot/assets/dice_packs` holds two packs of
120 stickers (pack 1: d20 and the higher / lower of two d20s, the d12 in four colour variants, the d10 and
non-crit d4 / d6 / d10 extremes; pack 2: d100, d4, d6, d8 and a 5 kopiyok coin as a d2) with a `manifest.json`. They
are made by `scripts/dice_render` (`uv run --group render python scripts/dice_render/render.py --pack 1 --pack 2`;
needs headless Chromium, see its README) and check against Telegram's limits with `packs.py`.

### Chat settings and communities (v4.0)

Chat admins change their chat's settings with **`/settings`**: a short private card with four sections
(Спринти · Звіти й приватність · Промпти й відповіді · Привітання й досягнення, each saying how many it has
changed), each opened in place, and a button to the web app's Chat tab. The settings: the default sprint
length, works and % on or off, the default unit, hidden ✓ reports, work names on the sprint message, `/top`,
deleting report messages, the welcome text, the bot's default replies, the shared prompts, achievements.
Changes apply from the next sprint. The **welcome text** is typed in the private chat: its button is a link
there that carries the chat, so nothing is typed into the group.

**Own replies** (`/replies`): write the answer as an ordinary message (formatting and line breaks kept),
then reply to it with `/replies add /imafan` or `/replies add фраза`; the one-line form
`/replies add /imafan = текст` works too.

A chat that belongs to a **Telegram Community** (Bot API 10.2) is linked automatically; super-admins can
also link chats by hand: `/community` in the private chat is a card with buttons (new, rename, delete, add a
chat from a list, remove one), the web app has a Communities tab for super-admins, and the text commands
remain (`/community new <name>`, `/community add <id> <chat id>…`, `/community list`).
A community shares `/top`, one prompt set and its settings (`/community set <id> <setting> <value>`; a
chat's own value wins), and gets one broadcast, in its largest chat.

**Prompt databases** (`prompts = { source = "db" }`): the sheet and the Drive folders are imported once into
a shared set; each chat (or community) has its own set on top. Categories are named in English with an
optional Ukrainian alias (`horror` / `жахи`). Chat admins add prompts (`/addprompt жахи текст`, a picture
with the caption `/addimage пейзаж`, uploaded to Drive when the bot's Google account may write there, else
kept as the Telegram photo), members suggest them (`/suggest`), admins accept them (`/suggestions`, the web
app), and chats can submit prompts to the shared set, which super-admins review with `/review`. On
`/suggestions` an admin can accept a prompt and propose it for the shared set in one tap; a suggestion whose
category looks like a misspelling of an existing one ("коммедія") asks where it belongs, and one that differs
only in case goes into the existing category.

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
