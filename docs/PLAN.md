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
- *People* are linked across platforms by the users themselves: `/link` on one side gives a short-lived
  code, `/link <code>` on the other side joins both accounts to one `person_id`. Nobody is merged by
  matching names.
- `/top` and `/mystats` in a linked chat count every chat of the community and add up a person's
  accounts. Goals, streaks and achievements (phases 2 and 5) are kept per person.
- Shared sprints (one sprint posted on both sides, people joining from either) need both adapters to share
  one sprint engine. Simplest: run both clients in one process, with one `SprintManager` and a presenter that
  fans out to every chat of the community. Until then each side runs its own sprints, and only the boards
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
- **Minimal settings store first**: per user, board visibility, progress unit and the current works; per
  chat, the default unit. The rest of the settings store is in phase 4.
- **One reporting command**: `/join` and `/words` are removed. You join with the button and report by
  replying to the sprint message (`500`, `+300`, a link) or with the buttons at the end. `/progress`
  covers everything else (below). Richer input goes
  in a small sprint panel (a Mini App, the first piece of the phase 4 dashboard).
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
- **`/mystats`**: the private view (ephemeral in groups): each work with its goal and month total, the
  streak with the last 7 days (gaps within 3 days can be filled from there), completed sprints and
  minutes, visibility. Buttons for `/progress`, works and settings. No rankings or comparisons.
- **Your data**: `/export` and `/forgetme` come out of preview and are found in `/mystats` → Settings →
  My data (the commands stay as fallbacks, off the group menu). Export covers works, goals and
  `/progress` entries as well as sprints; deletion covers works too. The first sprint you join says
  once that your numbers are stored and where to export or delete them. `/sprinthelp` is removed.
- **`/top`**: total minutes of completed sprints in the chat over N days, private writers included (minutes
  reveal nothing about the work). You get credit from when you joined: joining 20 minutes into a
  30-minute sprint gives 10.

### Phase 4: settings store and dashboard (v4.0)
- **Settings in the database** instead of environment variables and the config sheet: per chat (welcome
  text, slogan replies on/off, report window, default sprint length, broadcast opt-out, achievements on/off)
  and per user (time zone, goals, visibility on boards). The sheet stays as a one-time import source.
- **Prompt database**: prompts and categories in SQLite, imported from the current sheet. Chats use the
  shared set or their own. Image prompts can stay on Drive.
- **Communities** (the data model, for Telegram alone to begin with): `communities` and `community_chats`
  tables; super-admins link chats to a community (`/community`, later in the dashboard). Information is
  shared per community: boards and `/top`, prompt sets, announcements and `/broadcast` by community, and
  settings that a community's chats inherit. Telegram's own Communities (Bot API 10.2) are used as a source
  for linking chats once the calls are made directly, the way ephemeral messages are in
  `telegram_bot/ephemeral.py`.
- **Dashboard** (17), a Telegram Mini App served by the bot:
  - for everyone: your sprints, reports and stats with charts; time zone and goals; export and delete
    your data;
  - for chat admins: the chat's options, its prompt set (add, edit, delete, import from a sheet), and
    its achievements.
  - Telegram users sign in through Mini App `initData`, validated on the server. Admin rights are checked
    with `getChatMember` on every change. Discord users get the same pages later through Discord OAuth.
  - Needs public HTTPS into the homelab (a reverse proxy or a tunnel); that's an ops decision before this
    phase starts.

### Phase 5: achievements (v4.1)
- **Achievements** (9), defined by chat admins in the dashboard: a name and a rule from a fixed set (total
  words, words in one sprint, streak length, number of sprints, a goal reached). A few defaults per chat,
  which admins can switch off. Earned achievements are listed in `/mystats` and announced once in the chat.
  No XP and no random awards.

### Phase 6: one community across Telegram and Discord, and a Discord pass (v4.2)
- A Discord parity pass once the Telegram sprint redesign has settled: the same flow with Discord's
  buttons and modals, tested in a real server.
- E1 per-server settings come with phase 4.
- E2: Discord chats join the communities from phase 4, `/link` for accounts, shared boards, then shared
  sprints with both adapters in one process.
- E3: Matterbridge configuration and setup notes on top of E2.

### Decisions
- **Telegram framework: stay on python-telegram-bot** (decided after comparing it with aiogram). Bot API
  features it lacks are called directly and kept in one module, so they can be dropped when it catches up.
