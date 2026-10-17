# Plan

## Done in v3.0

1. **Dependencies & tooling**: PEP 621 `pyproject.toml` managed by `uv` (replaces Poetry); Python 3.13;
   python-telegram-bot 22.8 (Bot API 10.0), discord.py 2.7 (optional extra), httpx, aiosqlite, google-auth.
   Drop `oauth2client`, `telegraph`, `parsel`, `tqdm`, `pytz`. Ruff for lint/format, pytest for tests.
   Dockerfile on uv, non-root.
2. **Security fixes**
   - No more unpickling the Google token (arbitrary code execution if the file is swapped). JSON token or
     service account; one-shot converter script for the existing `token.pickle.b64`.
   - Webhook mode refuses to start without `TELEGRAM_WEBHOOK_TOKEN` (otherwise anyone can POST fake updates).
   - SSRF guard for the word counter now that it fetches arbitrary WriteFreely hosts: https only, public IPs
     only (checked on every redirect hop), size cap, timeout.
   - HTML-escape every value from the sheet / Drive / users before sending with HTML parse mode.
   - Working chat allowlist (`ALLOWED_CHATS`); unset = allow all + a warning, so behaviour is unchanged.
   - `/reload` admin check fixed (it compared an int to a list of lists, so super-admins never passed).
3. **Async fixes**: `time.sleep()` in the sprint tick blocked the whole bot; sync Telegraph and Google API
   calls ran on the event loop; an un-awaited `callback_query.answer()`; sprint runner moved off PTB's
   job queue onto per-sprint asyncio tasks guarded by a lock.
4. **GitHub issues**
   - #2 race condition on `/sprint`: per-chat lock, sprint registered before the first `await`.
   - #3 forum chats: sprints keyed by `(chat, topic)`; messages go to the right topic.
   - #4 custom prompt categories: sheet columns are read dynamically (add a column = new category),
     `/prompt <category>`, `/image <category>` (the old `/image_*` aliases were broken by swapped args).
5. **Platform-neutral core + Discord**: `core/` (sprints, word count, prompts, storage) with thin
   Telegram and Discord adapters. Discord runs as a separate process with its own token.
6. **Telegram UX**: command menu via `setMyCommands`, `LinkPreviewOptions`/`ReplyParameters`, styled inline
   buttons, live progress bar, report-your-words button with force-reply, sprints survive restarts.
7. **Per-sprint word reports**: `/words 500`, `/words +500`, `/words <link>` (link delta vs. the link
   registered at join), reply to the finish message, leaderboard edited live, `/mystats`, `/top`.
   Stored in SQLite.
8. **Word counter**: Unicode-aware word counting (apostrophes in Ukrainian words, dashes and «» are not
   words), Telegraph via its JSON API with correct block boundaries, WriteFreely/Write.as instances via
   the post page (`e-content`, detected by generator meta or nodeinfo; `.md` URLs normalised), and public
   **Ellipsus Reader links** (`ellipsus.com/read/<id>`) via the reader API, whose Yjs document is decoded
   with `pycrdt`. Ellipsus has no documented API, so that endpoint may change without notice.
9. **Admin chat registry & broadcasts** (Telegram): `/chats` lists every group, channel and private chat the
   bot is in; `/broadcast groups|private|all` copies a message to them after a preview and confirmation.
   Telegram cannot list a bot's chats, so the bot records them as updates arrive; older chats are imported
   once from the v2 `db.pickle`.

## Done in v3.1

10. **Self-hosting**: plan and scripts for moving from Render to the homelab ([MIGRATION.md](MIGRATION.md)):
    polling instead of a webhook, schema versioning with in-place upgrades, online SQLite backups
    (`scripts/backup_db.py`).
11. **Keep-awake removed**: the "Як прогрес?" posts (`SPRINT_KEEPALIVE`) only existed to wake a sleeping
    Render instance.
12. **Links remembered for the sprint**: a link given before or during a sprint (`/words <link>`, or
    `/sprint 30 2 <link>` to start, `/join <link>` for everyone else) is counted again when the sprint ends and the added words are recorded without
    a manual report.
13. **Fewer emoji**: plain headings, numbered leaderboard, text-only buttons; one 👍 reaction to confirm a
    report.
14. **`extended` branch features kept, behind `EXTRAS=1`** (off by default, one codebase instead of a second branch): dice rolls (`/d20`, `/2d6+1`, `/r 4к6`, `adv`/`dis`), `/smash`,
    `/imafan`, per-chat welcome messages, slogan replies, private chats open to everyone when
    `ALLOWED_CHATS` is set, the "Я переродився" `/start` greeting.
15. **Broadcast exclusions**: `/bcexclude` / `/bcinclude` keep chosen chats out of every broadcast.

16. **Percent sprints**: a chat setting (`/sprintmode percent`, chat admins) where people report how much of
    their task they finished (`40%`) instead of words. The board shows percentages; these sprints don't
    count towards word stats and `/top`. Settings live in a new `chat_settings` table, which phase 4 builds
    on.

## v3.3.3: v2 features by default

Only what the v2 bot (`main` + `extended`) offered is on by default: prompts, images, `/wc` for text and
Telegraph, sprints with join/leave/repeat buttons, `/stats`, `/reload`, `/debuginfo` and the `EXTRAS`.
Everything else that users see is behind `PREVIEW_FEATURES=1` while the commands are redesigned: the Discord
bot, word reports and the results board, `/join`, sprint links, percent sprints, `/mystats`, `/top`,
`/sprinthelp`, prompt categories as an argument, WriteFreely/Ellipsus/Google Docs links, admin chat list and
broadcasts, and phases 1–2 below. Without the preview a sprint ends as in v2: a "finished" message with a
repeat button and no report window. The rewrite (items 1–6 and 10–11 above, minus Discord) and the ops parts
of phase 1 stay on. The bot still records the chats it is in, so the registry is complete once `/chats` returns.

## Discord: three ways to use it

**E1. A separate Discord server.** Works with v3 as is: the Discord bot is its own process, with its own
rows in the database (`platform = discord`) and its own boards. Missing: per-server settings, which
today come from environment variables and the shared Google Sheet. They arrive with the settings store in
phase 4.

**E2. One community on a Telegram group (or several) and a Discord server, with shared boards.**
- A *community* groups chats across platforms: `communities(id, name)` and
  `community_chats(community_id, platform, chat_id)`. Super-admins link chats; later chat admins do it
  in the dashboard.
- *People* are linked across platforms by the users themselves, by signing in to the web app with both
  accounts, which joins them to one `person_id`. Nobody is merged by matching names.
- `/top` and `/me` in a linked chat count every chat of the community and add up a person's
  accounts. Works, goals and streaks are kept per person, achievements per community.
- Shared sprints (one sprint posted on both sides, people joining from either) need both adapters to share
  one sprint engine. Simplest: run both clients in one process, with one `SprintManager` and a presenter that
  fans out to linked channels (see phase 6). Until then each side runs its own sprints, and only the boards
  are shared.

**E3. As E2, with Matterbridge relaying messages between Telegram and Discord.**
- Commands don't fire twice. On Telegram, bots don't receive other bots' messages, so text relayed from
  Discord by the bridge bot never reaches our bot. On Discord, our bot only uses slash commands and has no
  message-content intent, so relayed text is ignored there too.
- What breaks is the bot's own output. The bridge copies sprint messages and boards to the other side as
  plain text with no buttons, and the copies go stale as the originals are edited. Fix: configure the
  bridge to skip our bot's messages (Matterbridge `IgnoreNicks` on both sides) and let the bot post
  natively on both sides, which is the shared sprints from E2.
- Relayed messages only carry a display name, which anyone can change, so a relayed reply is never
  accepted as a word report. People report on their own platform, or link their accounts (E2).
- If message reading is ever added on Discord, the bot must ignore messages posted by the bridge's webhooks
  (they have a `webhook_id`).

E3 therefore needs nothing beyond E2 except bridge configuration and a short setup note.

## Roadmap

From the earlier list of 20 ideas, these go ahead: goals (2), streaks (3), personal bests (5),
achievements managed by chat admins (9), per-user time zone (15), ephemeral replies (16), a dashboard (17,
reworked as below), Google Docs word count (18) and ops quality (20). The rest are dropped: `.docx`
uploads, projects, scheduled and chained sprints, cross-chat boards (replaced by communities in E2),
NaNoWriMo events, team word wars, generators beyond dice, the welcome flow beyond the existing welcome
messages, and an English interface.

Phases are in order of dependency. Each ends in a tagged release.

### Phase 1: run well on the homelab (done in v3.3)
Health check, error alerts, JSON logs and backups are on. `/export` and `/forgetme` are behind
`PREVIEW_FEATURES` (off by default since v3.3.2).
- Cut-over guide from Render ([MIGRATION.md](MIGRATION.md)); the cut-over itself is up to ops.
- `/health` endpoint with a Docker `HEALTHCHECK`; JSON logs (`LOG_FORMAT=json`).
- Errors from the log sent to super-admins in private, rate-limited.
- Backups via `scripts/backup_db.py` (scheduling it is on the ops side).
- `/export` (CSV of your sprints, sent privately) and `/forgetme` (with a confirmation step).

### Phase 2: personal progress (built in v3.3, behind `PREVIEW_FEATURES`; partly superseded)
Built and kept behind `PREVIEW_FEATURES` until phase 3 replaces it. The October 2026 review changed it:
- **Per-user time zone** (15): cancelled. `/tz` goes; everything personal uses one bot-wide day that
  starts at 03:00 Kyiv time.
- **Goals** (2): reworked as goals on a work (phase 3).
- **Streaks** (3): reworked in phase 3, with late reports up to 3 days back.
- **Personal bests** (5): cancelled.
- **Ephemeral replies** (16), Bot API 10.2: kept. Personal replies in groups go only to that person when
  Telegram allows it, and fall back to a normal reply otherwise. PTB 22.8 has no methods for this yet, so
  the calls are made directly (`telegram_bot/ephemeral.py`). Ephemeral commands are opt-in
  (`EPHEMERAL_COMMANDS`) until they've been tried in a real group; phase 3 depends on them.
- **Google Docs word count** (18): kept, and on by default from phase 3 with the other link sources.
  Documents shared as "anyone with the link", through the plain-text export. No Google credentials are
  needed, and private documents are reported as not shared.

### Phase 3: sprint redesign (v3.4)
Decided in the October 2026 roadmap review; nothing here has been tried with a focus group yet. Writers
join and report with replies and buttons, nobody is pushed to share their work or to compete, and the
chat stays quiet. It replaces the preview reporting flow. Before the redesign, the extra link sources
(WriteFreely, Ellipsus, Google Docs) come out of `PREVIEW_FEATURES`.
- **Modules first** (before works and the web app, so they aren't built into the old `app.py`): see
  "Architecture: modules" below.
- **Web app from the start**: the bot serves one web app, opened as a Telegram Mini App (Mini App
  `initData`, validated on the server) or as a plain website with Telegram Login and, later, Discord
  OAuth as the fallback. In this phase it holds the sprint panel and your works; phase 4 grows it into
  the dashboard. It needs public HTTPS into the homelab (a reverse proxy or a tunnel), an ops decision that
  now has to be made before this phase. Since Bot API 10.2, Mini App methods only work on the Mini App's
  own domain, so the app has a fixed domain of its own.
- **Minimal settings store first**: per user, board visibility, progress unit and the current works; per
  chat, the default unit. The rest of the settings store is in phase 4.
- **One reporting command**: `/join` and `/words` are removed. You join with the button and report by
  replying to the sprint message (`500`, `+300`, a link) or with the buttons at the end. `/progress`
  covers everything else (below). Richer input goes
  in the sprint panel of the web app (below).
- **Works**: a name and an optional link. One sprint can have several works. Whether a work carries over
  to your next sprint is your choice (a "keep for next sprint" toggle). The work's link is counted again
  at the end, as sprint links are now. Progress per work over time comes later.
- **Goals belong to a work**: "Novel, 1,000 words a day" or "Thesis, 20,000 this month". No work, no goal.
  Sprints and `/progress` both count towards it. A goal reached during a sprint goes on that sprint's
  board summary line (without the number for private writers); otherwise only the writer is told, in
  private. The bot never asks for progress.
- **`/progress`**: inside or outside a sprint, in DM or a group (reply ephemeral or deleted there):
  `+400`, a total, or the work's link, optionally for a named work, and optionally for an earlier day
  up to 3 days back (`/progress +400 yesterday`). During a sprint you joined, it is also your report.
- **Streaks**: days in a row with writing: a completed sprint in any chat, a `/progress` update, or growth
  of a linked work (its link is re-counted once a day at reset). Late updates up to 3 days back fill the
  gap, so missing a chat sprint or forgetting to report doesn't break a streak, and there are no
  reminders.
- **Day boundary**: one bot-wide day starting at 03:00 Kyiv time, for goals, streaks and `/top`.
- **Units**: words or %, chosen per work, defaulting to the chat's unit (replaces the per-chat
  `/sprintmode`); a work without a document is no different. In a sprint, % always means the share of
  what you planned for that sprint, labelled "% of plan" on the board; there is no whole-work percentage.
  Goals are in words.
- **Which work a report goes to**: joining attaches the works marked "keep for next sprint" (none is
  fine). Linked works are re-counted at the end, each with its own delta. A typed number goes to the only
  unlinked work in the sprint with that unit, so `520 50%` in one reply fills a words work and a % work.
  Only when two works share a unit does the bot ask once (`500 → [Novel] [Essay] [Split]`); naming them
  avoids it (`+300 novel +200 essay`). The Report button asks per work. `/progress` follows the same
  rule. Someone with works in both units appears in both board sections.
- **Asking privately**: ephemeral where Telegram allows; otherwise a normal reply whose buttons only that
  person can press, deleted after the tap. Not a DM.
- **Outcomes**: a number, **Done ✓** (no number), or **Didn't make it ✗**. A sprint counts as completed
  with any positive progress: words or % above 0, or Done. A 0, ✗ or no report doesn't count.
- **Visibility**: public (the default) or private. Private numbers count towards your own stats, and the
  board shows ✓ in their place. Joining never needs you to share anything.
- **Board**: a words section and a % section, each sorted high to low, then ✓ and ✗. One line per person
  per section, summed over their works (the split is only in `/mystats`). No medals and no winner. Records and goals go in one summary line on the board, not separate posts.
- **No fixed report window**: late reports are accepted until your next sprint or about 12 hours, and the
  board is edited when they arrive, so someone in the zone can keep writing.
- **Less clutter**: report replies are deleted once recorded (when the bot may delete messages; otherwise
  it reacts), and confirmations are ephemeral where Telegram allows.
- **`/me`** (was `/mystats`): the private view (ephemeral in groups): each work with its goal and month total, the
  streak with the last 7 days (gaps within 3 days can be filled from there), completed sprints and
  minutes, visibility. Buttons for `/progress`, works and settings. No rankings or comparisons.
- **Your data**: `/export` and `/forgetme` come out of preview and are found in `/mystats` → Settings →
  My data (the commands stay as fallbacks, off the group menu). Export covers works, goals and
  `/progress` entries as well as sprints; deletion covers works too. The first sprint you join says
  once that your numbers are stored and where to export or delete them. `/sprinthelp` is removed.
- **Join card**: starting or joining a sprint shows your works as toggles (ephemeral, or a reply only you
  can press), on for the works kept for the next sprint. Toggling a work also changes whether it is kept for
  the next sprint (remembered by default); the card can be reopened from the sprint message (`[My works]`) at any time, and a work added mid-sprint
  has its link counted when added. The sprint message lists who is in and, for public writers, their
  works: names for public works (the default; a work can be made private), otherwise a count. Private writers show no
  works.
- **Bragging**: `/progress` with no arguments posts one public line with your progress for the shortest
  calendar period that has any (today, this week, this month, this year), plus the goal and streak where
  they apply. It's your choice to post, so it shows private writers' numbers too. A %-only writer gets
  sprints and minutes. With arguments, `/progress` reports quietly.
- **Command names**: `/stats` → `/promptstats`, `/mystats` → `/me`, `/wc` → `/count` (`/wc` and `/stats`
  stay as hidden aliases). Every user command gets a Ukrainian alias: `/промпт`, `/картинка`, `/слова`, `/спринт`,
  `/прогрес`, `/я`, `/топ`, `/допомога`, `/експорт`, `/забудьмене`; admin commands get none.
  Telegram doesn't treat Cyrillic as commands, so these are matched as text and only work where the bot
  sees all messages (privacy mode off, or the bot is an admin); they aren't in the menu.
- **`/top`**: total minutes of completed sprints in the chat over N days, private writers included (minutes
  reveal nothing about the work). You get credit from when you joined: joining 20 minutes into a
  30-minute sprint gives 10.

### Phase 4: settings store, communities and dashboard (v4.0)
- **Settings in the database** instead of environment variables and the config sheet, per chat, changed by
  chat admins in the web app (`/settings` opens it); per-user settings arrived in phase 3:
  - sprints: default length, default unit (when % is on), works on/off (off also means no goals on the
    board), private ✓ reports on/off, % unit on/off;
  - in the chat: work names on the sprint message, `/top` on/off, deleting report messages;
  - achievements on/off (phase 5).
  `EXTRAS` features (welcome text, slogans) have no per-chat settings. There is no broadcast opt-out:
  `/broadcast` reaches every chat, except that a community gets it only in its largest chat. The sheet
  stays as a one-time import source.
- **Communities** (Telegram first): `communities` and `community_chats` tables. A chat that belongs to a
  Telegram Community (Bot API 10.2: the bot can see a chat's community through `getChat` and its service
  messages; exact fields to be checked against the changelog when built) is linked automatically, with the
  calls made directly as in `telegram_bot/ephemeral.py`. Super-admins can also link chats by hand
  (`/community`, or in the web app), for groups that don't use Telegram Communities. Shared per community:
  `/top`, prompt sets, broadcasts by community, and settings the community's chats inherit.
- **Dashboard**: the phase 3 web app grows:
  - for everyone: your works, sprints and progress with charts, the streak calendar (with backfill for the
    last 3 days), visibility, export and deletion;
  - for chat admins: the chat's settings, its prompt set and its achievements.
  - Admin rights are checked with `getChatMember` on every change. Discord users sign in with Discord OAuth.
- **Prompt databases per chat** (not a priority; last in this phase or later): prompts and categories in
  SQLite. Chat admins manage their chat's own set (`/addprompt <category> <text>` or the web app's Prompts
  tab) and choose whether the chat also uses the shared bot-wide set; `/prompt` picks from the combined
  pool. Members can suggest prompts, which the chat's admins accept or reject. A chat's prompt can stay in
  the chat or be submitted to the shared set (super-admins accept or reject). A community shares one set.
  - Categories are free-form, named in English, with an optional Ukrainian alias (`horror` / `жахи`);
    `/prompt жахи` works.
  - Image prompts work the same way: an admin sends a picture, and the bot uploads it to the chat's
    folder on Google Drive (needs Drive write access for the bot's account, an ops item).
  - The Google Sheet and Drive folders are imported once into the shared set when the settings move to the
    database, and the sheet is retired; `/reload` goes with it.

### Phase 5: achievements (v4.1)
- **Achievements** (9), made and awarded by hand. No rules, no automation, and no tie to sprints, works
  or stats. No XP.
- Each achievement is a name, an optional description and a **sticker**. The chat admins own the sticker
  pack and manage it themselves (through @Stickers); the bot only stores which sticker belongs to which
  achievement (the set name and the sticker's `file_unique_id`). To create an achievement, an admin sends
  the bot a sticker from the pack with a name, in the web app or in chat. If a sticker is removed from
  the pack, the achievement keeps its name and the bot asks for a new sticker. The pack can also be a
  custom emoji pack, so achievements appear inline in `/me`; whether the bot may send custom emoji in its
  messages is to be checked against the Bot API when this is built.
- **Awarding by command** (chat admins; alias `/нагорода`): reply to the member's message with `/award`, then pick the
  achievement from buttons, or `/award <name>`; `/unaward` takes it back. The bot posts the sticker and one
  line (`Taras отримує «Нічна сова»`). Earned achievements are listed in `/me`.
- Achievements belong to the community where there is one, otherwise to the chat.

### Phase 6: one community across Telegram and Discord, and a Discord pass (v4.2)
- A Discord parity pass once the Telegram sprint redesign has settled, tested in a real server. Discord
  matches or beats Telegram on buttons, menus and ephemeral replies; the differences:
  - **Reporting with modals only**: the Report button opens a modal with a field per work. No replies to
    the sprint message, so the bot keeps running without the message-content intent and ignores bridged
    text (E3).
  - Join card as an ephemeral multi-select menu; `/count` also as a message context-menu command;
    `/award` also as a user context-menu command.
  - **Achievements use server emoji**, not stickers (server sticker slots are few).
  - **Broadcasts** go to the server's `#general` channel (the system channel if there is none). A server is
    a community; its channels are its chats.
  - **Web app**: the website with Discord OAuth; no Discord Activity.
  - **Ukrainian names** through slash-command localizations (`uk`), so each user sees one language.
  - `EXTRAS` slogans and welcome messages are not on Discord (they need privileged intents).
- E1 per-server settings come with phase 4.
- E2: a Discord server joins a community from phase 4. Linking is done in the web app by someone who is an
  admin on both sides. People link their accounts in the web app too: signing in with both Telegram and
  Discord joins them into one person (no `/link` codes). `/me`, works, goals and streaks are then per
  person; `/top` and achievements per community.
- Shared sprints through **channel links**: an admin links a Telegram chat (or forum topic) with a Discord
  channel. A sprint started in either is posted in both, people join from either side, and there is one
  board. Unlinked chats and channels keep their own sprints. Both adapters run in one process with one
  `SprintManager` (one container instead of two; agreed with the owner).
- E3: Matterbridge is on the owner's roadmap. Its configuration (`IgnoreNicks` for the bot on both sides)
  and setup notes come on top of E2; linked channels are the natural pairs to bridge.

### Architecture: modules
The bot is built from modules that can be served as one bot or as several, set in a config file.
- **Modules**: `greeter`, `prompts`, `wordcount`, `sprints`, `works`, `dice`, `achievements`, `extras`
  (smash, imafan, slogans), `admin` (chat list, broadcasts), `data` (export, deletion). Each one brings
  its handlers, commands (menu, `/help`, Ukrainian aliases), settings, migrations and web app pages, and
  adds sections to the shared screens (`/me`, `/help`, the board summary line, web app tabs), so a bot
  shows only what its modules do.
- **Shared core**, not a module: storage (one database for every bot), the settings store, people and
  accounts, communities, ephemeral replies, the web app server.
- **Dependencies** are declared: required, or optional (sprints uses works when available; without
  works the join card is a plain Join button).
- **Calls between modules** go through a service interface. Within one process it's a direct call; when the
  modules run in different bots or processes, the same interface is served over an internal,
  authenticated API. That way a reply to the sprint bot can still record progress on a work kept by the
  works bot.
- **Config file** (TOML): each bot names its modules and its targets, Telegram and/or Discord, each with
  its own token:
  ```toml
  [bots.main]
  modules = ["prompts", "wordcount", "sprints", "works", "achievements", "dice"]
  telegram = { token = "env:TG_MAIN" }
  discord = { token = "env:DISCORD_MAIN" }

  [bots.greeter]
  modules = ["greeter"]
  telegram = { token = "env:TG_GREETER" }
  ```
  One process can run several bots, or each bot can run in its own container from the same image
  (`--bot greeter`); that is up to ops. `PREVIEW_FEATURES` and `EXTRAS` become the choice of modules; the
  default config is the v2 set.

### Decisions
- **Telegram framework: stay on python-telegram-bot** (decided after comparing it with aiogram). Bot API
  features it lacks are called directly and kept in one module, so they can be dropped when it catches up.
