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
14. **`extended` branch features kept**: dice rolls (`/d20`, `/2d6+1`, `/r 4к6`, `adv`/`dis`), `/smash`,
    `/imafan`, per-chat welcome messages, slogan replies, private chats open to everyone when
    `ALLOWED_CHATS` is set, the "Я переродився" `/start` greeting.
15. **Broadcast exclusions**: `/bcexclude` / `/bcinclude` keep chosen chats out of every broadcast.

## Discord: three ways to use it

**E1. A separate Discord server.** Works with v3 as is: the Discord bot is its own process, with its own
rows in the database (`platform = discord`) and its own boards. Missing: per-server settings, which
today come from environment variables and the shared Google Sheet. They arrive with the settings store in
phase 3.

**E2. One community on a Telegram group (or several) and a Discord server, with shared boards.**
- A *community* groups chats across platforms: `communities(id, name)` and
  `community_chats(community_id, platform, chat_id)`. Super-admins link chats; later chat admins do it
  in the dashboard.
- *People* are linked across platforms by the users themselves: `/link` on one side gives a short-lived
  code, `/link <code>` on the other side joins both accounts to one `person_id`. Nobody is merged by
  matching names.
- `/top` and `/mystats` in a linked chat count every chat of the community and add up a person's
  accounts. Goals, streaks and achievements (phases 2 and 4) are kept per person.
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

### Phase 1: run well on the homelab (v3.2)
- Cut over from Render ([MIGRATION.md](MIGRATION.md)).
- `/health` endpoint (or a heartbeat file for polling mode) for the homelab's monitoring; structured logs.
- Errors reported to super-admins in a private chat (rate-limited), instead of only to the log.
- Scheduled backups via `scripts/backup_db.py` (the job itself runs on the ops side).
- `/export` (a CSV of your own sprints and reports) and `/forgetme` (delete your reports and stats, with a
  confirmation step).

### Phase 2: personal progress (v3.3)
- **Per-user time zone** (15): `/tz Europe/Kyiv`, defaulting to `BOT_TIMEZONE`. Comes first, because
  "today" for goals and streaks depends on it.
- **Goals** (2): `/goal daily 500`, `/goal monthly 20000`; progress shown after each report and in
  `/mystats`.
- **Streaks** (3): consecutive days with at least one report, in the writer's time zone. Shown in
  `/mystats`. No reminders (they would be noise in group chats).
- **Personal bests** (5): most words and best words per minute in one sprint, mentioned in the results when
  beaten.
- **Ephemeral replies** (16) for "report saved", "you're already in the sprint" and similar, so group chats
  stay quiet. Needs a PTB release with Bot API 10.2 (or a raw `do_api_request` call); reactions remain the
  fallback.
- **Google Docs word count** (18): `docs.google.com/document/d/<id>` links that are shared as "anyone with
  the link", exported as plain text through the Drive API with the bot's existing credentials. Works for
  `/wc`, `/words` and links remembered for a sprint.

### Phase 3: settings store and dashboard (v4.0)
- **Settings in the database** instead of environment variables and the config sheet: per chat (welcome
  text, slogan replies on/off, report window, default sprint length, broadcast opt-out, achievements on/off)
  and per user (time zone, goals, visibility on boards). The sheet stays as a one-time import source.
- **Prompt database**: prompts and categories in SQLite, imported from the current sheet. Chats use the
  shared set or their own. Image prompts can stay on Drive.
- **Dashboard** (17), a Telegram Mini App served by the bot:
  - for everyone: your sprints, reports and stats with charts; time zone and goals; export and delete
    your data;
  - for chat admins: the chat's options, its prompt set (add, edit, delete, import from a sheet), and
    its achievements.
  - Telegram users sign in through Mini App `initData`, validated on the server. Admin rights are checked
    with `getChatMember` on every change. Discord users get the same pages later through Discord OAuth.
  - Needs public HTTPS into the homelab (a reverse proxy or a tunnel); that's an ops decision before this
    phase starts.

### Phase 4: achievements (v4.1)
- **Achievements** (9), defined by chat admins in the dashboard: a name and a rule from a fixed set (total
  words, words in one sprint, streak length, number of sprints, a goal reached). A few defaults per chat,
  which admins can switch off. Earned achievements are listed in `/mystats` and announced once in the chat.
  No XP and no random awards.

### Phase 5: one community across Telegram and Discord (v4.2)
- E1 per-server settings come with phase 3.
- E2: communities, `/link` for accounts, shared boards, then shared sprints with both adapters in one
  process.
- E3: Matterbridge configuration and setup notes on top of E2.
