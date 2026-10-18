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
| `dice` | `stickers`, `packs`, `max_stickers` (animated dice stickers, see below), `commands` (a command per die in the menu, default on; off in the environment without `PREVIEW_FEATURES`); `randomness`, `random_org_key` (true random dice) | the two sticker packs, made once with `scripts/upload_dice_packs.py`, for `stickers`; a random.org API key for `randomness` | a command for each die (`/d2`, `/d4`, `/d6`, `/d8`, `/d10`, `/d12`, `/d20`, `/d100`, also `!к12`), `/2d6+3 Атака`, `/r 4d6kh3`, `/d20adv`: [notation](#dice) |
| `smash` | | | `/smash` |
| `achievements` | | storage | Achievements made from a sticker, a photo or an image file: `/newachievement` (reply to the picture, or in private: the bot asks for it), `/award` (`!нагорода`), `/unaward`, `/achievements`; posted as [pictures](#achievements) |
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
- **Ukrainian command names** are written with an exclamation mark, `!спринт`, `!прогрес`, `!профіль`, `!промпт`,
  `!картинка`, `!підрахуй`, `!топ`, `!допомога`, `!експорт`, `!забудьмене`, `!к12` (`/спринт` works too, but `!` is what
  /help shows and encourages). They work where the bot sees all messages (Telegram doesn't treat Cyrillic as commands).

**`/help`** is generated from the commands the bot has, for the person and the chat, so a new command or module shows up
without anyone writing help text. The first page lists the groups (one per module, then "Ваші дані", the chat's
settings, the bot's admin tools) with their commands' names and a button for each group; a group's page has every
command in full: its name and arguments, its Ukrainian name, what it does, and for whom (chat admins, bot admins). Only
what is on there is listed (a chat with `/top` or achievements off doesn't list them), with the chat's values (its
default sprint length, its prompt categories), the chat admins' commands only for chat admins and the bot's admin
commands only for super-admins in the private chat. In a group it is a private answer. (A bot without the redesign, as in the environment without `PREVIEW_FEATURES`, keeps
one flat page without buttons, still generated.) The **command menus** follow
the same rules: one for private chats, one for group members, one for chat admins (`all_chat_administrators`), one for
super-admins, and per chat where its settings change them (refreshed when a setting changes).

Private answers in groups are ephemeral messages (Bot API 10.2) where Telegram allows: after a button
press, after an ephemeral command, or at any time when the bot is a chat admin. Otherwise they are ordinary
messages whose buttons only that person can press, deleted after a few minutes. Make the bot a chat admin
(with "delete messages") for the quiet experience.

### Achievements

Chat admins make achievements from a picture (a sticker, a photo or an image file) and award them by hand. To make
one, reply to the picture with `/newachievement Назва — опис`, or send `/newachievement` to the bot in private: it asks
for the picture, the name (unless the command had it) and, for an admin of several chats, the chat. A sticker or a photo
sent to the bot on its own starts nothing. Apart from creating one, the raw picture is never posted; the bot draws
game-style pictures (Pillow, with the bundled DejaVu Sans font) with the picture as the icon (an animated sticker's
thumbnail; a drawn medal if a pack's sticker is gone or the file cannot be read). A sticker of a pack is checked in its
pack before each award (the admin is asked for a new picture if it was removed); a photo or an image file is kept by its
`file_id`:

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
after it rolls; text after the expression is the caption. Every die also has a command of its own in the menu and
in /help (`/d2` the coin, `/d4`, `/d6`, `/d8`, `/d10`, `/d12`, `/d20`, `/d100`), and the Ukrainian way works with the
exclamation mark or the slash: `!к12`, `!2к6+3 Атака`, `!r 4d6kh3`.

| | v2 (until v4.1) | now |
|---|---|---|
| Dice, bonus, caption | `/2d6+3 Атака` | the same; spaces allowed: `/r 2d6 + 3 Атака` |
| Cyrillic `к` for `d` | `/4к6` | the same |
| Advantage / disadvantage | `/d20adv`, `/d20dis` (the whole roll twice, better / worse kept) | the same; `/2d20kh1` and `/2d20kl1` (with an optional `+N`) mean exactly that, dice stickers included. The die that counts is shown first |
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

**Dice stickers** (`dice = { stickers = true }`, on with `PREVIEW_FEATURES`): the dice of a roll also land as animated
stickers on their results. They are two packs of 120 video stickers (`src/writers_bot/assets/dice_packs`, made by
`scripts/dice_render`, see its README): the highest roll of a die has golden rays, the lowest a red halo, a dull die and
dust. Which sticker a die gets (`core/dicestickers.py`):

- **How many.** One or two dice get stickers on their own; three or four only with `-s` after the dice (`/3d6 -s Атака`);
  more than `max_stickers` (4: `dice = { stickers = true, max_stickers = 4 }`), or a die without stickers (d13), and the
  roll is only the line. `-q` silences them (`/d20 -q`). A bare `/d20` is just the sticker; a bonus or a caption adds the line.
- **Which die.** A die with an odd number of sides has the sticker of the next even one (d3 is a d4, d11 a d12); a d2 is
  the coin (a 5 kopiyok); a d100 is a tens die and a d10.
- **Crits.** A d4, d6 or d10 on its own has the crit effects on its lowest and highest roll; rolled with dice of another
  kind (`/d20+d4`) it has the crit-less version. The d8, d20 and d100 always have them.
- **Advantage.** `/d20adv`: both d20s land; the larger one has the advantage look (a quiet green halo), the smaller one is
  plain; with `dis` the smaller one has the disadvantage look (magenta) and the larger one is plain. A tie, and a 1 or
  20 (they are the plain d20's crits), are plain. A d12 works the same way, in blue for advantage and red for disadvantage.
- **The d12** is blue, except for its red 1. On its own, a 1 is the red disadvantage 1 and a 12 the blue advantage 12,
  which carry the crit effects. Every die type has a colour of its own: d4 purple, d6 red, d8 grey, d10 green, d12 blue
  (red for a 1 and for the second duality die), d20 orange, d100 pink tens die and a green d10, the coin silver.
- **Daggerheart's duality dice** (`/2d12`): the first d12 is blue, the second red. If the blue one is larger it is from the
  blue advantage set, if the red one is larger from the red disadvantage set, and on a tie both are; a die showing 1 or 12
  is the crit-less version (crits work differently there). A d6 for advantage (`/2d12+d6 -s`) is crit-less too.

**The packs are shared, and the bot never uploads them.** Telegram wants a bot-made pack's name to end in
`_by_<username of the bot that made it>`, so make the packs with a bot of their own, once, with the helper script (the
owner is a person who has started that bot):

```
TELEGRAM_TOKEN=<token of the dice bot> uv run python scripts/upload_dice_packs.py --owner <user id>
```

It makes `dice1_by_<bot>` and `dice2_by_<bot>` (no version in the names; a complete pack is left alone, a broken one is made
again with `--replace`) and prints the line for the bots' configs. Every bot with stickers names the same two packs:
`dice = { stickers = true, packs = ["dice1_by_dicebot", "dice2_by_dicebot"] }` (names or `t.me/addstickers/…` links), or
`DICE_PACKS=dice1_by_dicebot,dice2_by_dicebot` in the environment. At start a bot reads the packs, checks them against the
manifest (a pack with the wrong number of stickers, or put out of order, is not used, with a warning in the log) and then
sends the stickers by file id, finding a sticker by its place in the pack. Without `packs`, rolls come as text only. Each
sticker has emoji and search keywords for people (see `scripts/dice_render/tags.py`): the die's marker first (🧊 d20, 🕛 d12,
💯 d100 ...), then 🌟 crit, 💀 fumble, 🟢 advantage, 🟣 disadvantage, 🔴 / 🔵 for the d12's colour, ⬆️ / ⬇️ for the highest and
lowest roll and the value's digits; the search finds `d20 17`.

**True randomness** (`dice = { randomness = "random.org", random_org_key = "env:RANDOM_ORG_KEY" }`; in the environment
`DICE_RANDOMNESS=random.org` and `RANDOM_ORG_KEY`). The dice then come from [random.org](https://www.random.org/)'s
atmospheric noise instead of Python's generator. The bot keeps a pool of 1 KiB fetched with the JSON-RPC API
(`generateBlobs`; a free key is 1000 requests and 250 000 bits a day, so about 30 refills) and tops it up in the
background; a roll never waits for the network. If the pool is empty (random.org is down or the quota is spent) the roll
uses the operating system's cryptographic generator and the log says so once. The default is `"pseudo"`.

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
| `DICE_PACKS` | empty | The two dice sticker packs (names or links, comma-separated), for the dice stickers (`PREVIEW_FEATURES`) |
| `DICE_RANDOMNESS`, `RANDOM_ORG_KEY` | `pseudo` | `random.org` and a key: true random dice |
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
