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

## Done in v3.4: the sprint redesign (phase 3)

Built as planned in phase 3 below, with these decisions made while building:
- **Modules and the config file** (`BOT_CONFIG`, TOML). Without a file, the environment gives one bot with
  the v2 modules, so the default is still v2; `PREVIEW_FEATURES=1` turns on the redesigned sprints (works,
  %), the admin tools and the web app. The new `writers-bot` entry point runs every bot of a file in one
  process; `writers-bot-telegram` and `writers-bot-discord` run one platform.
- **Texts**: every user-facing text is in `locale/uk.toml` with a comment on where it appears; a test fails
  on Ukrainian strings in the code, missing keys, or keys without a comment.
- **One message per finished sprint**: the board replaces the sprint message (which is deleted), instead of
  a second "finished" message.
- **The Report button** posts a reply question (Telegram can't force a reply to an ephemeral message); it is
  deleted after the answer or after 5 minutes.
- **/progress after a sprint** is your late report only until you've reported; later words are ordinary
  progress.
- **Works are managed** in the private chat (the group button opens it) and in the web app; the question
  and the answer are deleted and the menu message updated, so the chat stays tidy.
- **Bots without admin rights** confirm reports with a 👍 reaction only (no private confirmation, so no
  Undo): Telegram allows ephemeral messages at any time only to admins.
- **The chat's default unit** has no switch yet (it comes with the settings in v4.0); % works are per person.
- **Discord** runs on the new core with the redesigned flow (the Report button opens a modal with a field
  per work, `/progress`, `/top`, `/export`, `/forgetme`); works management and the rest of the parity pass
  stay in phase 6.
- [WORKFLOW-REVIEW.md](WORKFLOW-REVIEW.md) lists what modelled use of these flows turned up.

## Done in v4.0: settings, communities, dashboard (phase 4)

As planned in phase 4, with these decisions:
- **Settings** are module-owned definitions with a value per chat, per community, or the default; a bot
  offers only the settings of its modules and options. `/settings` shows them as a private card in the
  chat (the web app's Chat tab has the same with explanations); admin rights are checked on every change.
  The welcome text is typed as a reply to the bot's question in the group.
- **Communities** link automatically from `ChatFullInfo.community` (checked at most daily) and the
  `community_chat_added` / `community_chat_removed` service messages; hand-made links (`/community`) are
  never undone by the automatic check. Community settings are set with `/community set`.
- **Broadcasts** reach a community once, in its largest chat (member counts refreshed before the preview).
  `/bcexclude` stays as an admin tool.
- **Replies** live in the database with bot-wide defaults (the config file's entries and the legacy ones,
  imported once) that each chat can turn off; `/replies` and the web app manage a chat's own.
- **Prompt databases** as planned; the sheet is imported once and `/reload` goes in `db` mode. Pictures
  go to the chat's Drive folder when the Google account may write there, else stay Telegram photos.
  Submitting to the shared set is in the web app.
- **Dashboard**: progress, the last 30 days (missed days within 3 days can be marked), latest sprints,
  export (in Telegram the file goes to your private chat) and deletion; Chat and Prompts tabs only for chat
  admins. Discord sign-in with OAuth (`identify`).
- Discord servers get settings and communities in phase 6.

## Done in v4.1: achievements (phase 5)

As planned in phase 5, with these decisions:
- An admin makes an achievement by replying to a sticker from their pack with
  `/newachievement Назва — опис`, or by sending the sticker to the bot in private (it asks for the name, and
  for the chat when they administer several).
- `/award` and `/unaward` (also `/нагорода`) in reply to a member's message, by name or from a private
  card with buttons; admin rights are checked at the press. The bot posts the sticker and one line.
- When the sticker is gone from the pack, the award still happens (by name) and the awarding admin is
  asked for a new sticker in private (in the chat if the bot can't write to them).
- `/achievements` lists a chat's achievements; `/profile` lists yours; the web app's Achievements tab lets
  chat admins rename, describe and delete them; export and `/forgetme` include awards. A chat setting turns
  achievements off.

## Done in v4.2: answers to the workflow review

The owner's answers to the open items of `docs/WORKFLOW-REVIEW.md` (rounds 1–3), and two requests:
- **Pausing works** (round 1, item 2): a work is on or paused (the old "keep for the next sprint" switch).
  Paused works aren't taken into sprints and don't get numbers without a name. When a number could go to two
  works, the question teaches the syntax that avoids it (`300 Роман 200 Есе`, `500 Роман`; the first three
  times, until the person names works in a report) and has a "Пауза" button: a card to pause works, after
  which the waiting report is tried again.
- **Switching works in the group** (item 6): `/works` (`/твори`) in a group is a private card of on/paused
  toggles (the sprint's card while you're in one); `/profile` → Твори in a group opens the same card. Adding
  and editing works stays in the private chat and the web app.
- **% outside a sprint** (item 4): the confirmation names the corrected sprint ("Виправив ваш спринт вчора
  о 14:00: «План»: 60%"), ephemeral where possible and otherwise a reply deleted after a minute, with Undo.
  Confirmations of ephemeral commands are now ephemeral replies to them.
- **Late reports** (item 5): unchanged, only the last sprint you joined takes your report.
- **Getting started** (item 8): three skippable steps in the private chat (first work, goal, numbers on
  the board), kept per person, offered on the first sprint card, `/start` and `/profile`, which shows the
  progress ("1 з 3 кроків", also in the web app) with a Continue button.
- **Achievement pictures** (round 3, item 1): the raw sticker is posted only when creating an achievement.
  Awarding posts one rendered "achievement unlocked" card; `/achievements` is the community's board
  (picture) or a person's collection (picture, with locked ones greyed out). Pillow, bundled DejaVu Sans.
  No library renders achievement cards (checked: rank-card libraries such as pilcord and canvacord, and
  pychievements / django-gamification, which track rules and points we don't have).
- **Admin tools** (round 2, all suggestions accepted): `/settings` in four sections; the welcome text typed
  in the private chat (a link that carries the chat); `/replies add /x` as a reply to the answer; "accept and
  propose to everyone" on `/suggestions`; misspelt categories asked about on accepting; communities with
  buttons (`/community`) and a web Communities tab for super-admins. `/bcexclude` stays as an admin tool.
- **Dice effects**: golden rays and sparkles on the highest result, a shudder, red glow and dust on the
  lowest; a d10 alone has them on 1 and 10, a d100's two d10s only on 01 and 00. `/d%` is a d100.
- **`/help` and the command menus** are built per chat and person from the commands themselves (section,
  audience, "shown when" a chat setting is on, values such as the chat's sprint length and prompt
  categories); chat admins get their own menu, and chats whose settings change it get theirs, refreshed on
  every settings change. In a group `/help` is a private answer (a v2 bot keeps the public reply).
- **Dice**: the roller is now the d20 library (keep/drop, exploding, rerolls, arithmetic), with the v2
  syntax translated; see the README for the comparison. With `stickers = true` single-die rolls (d4–d20,
  d100 as tens + ones) get an animated sticker of the die landing on the result, from the bot's own packs,
  generated by `scripts/dice_stickers.py` (no reusable set exists: Telegram's 🎲 is a d6 whose value Telegram
  picks, and the open d20 animations are d20-only and low quality).

## Done in v4.2.1
- `/myachievements` posts your collection; `/achievements <@username | name | part of one>` posts someone's
  (the closest match; the bot remembers the names and usernames it sees in groups; `/forgetme` removes them).
- Getting-started answers count only as replies to the step's message.
- A sticker per die for rolls of up to `max_stickers` (4) dice; the dice stickers roll for 0.6 s, then show
  the result still for 2 s.

## Done in v4.2.2: the new dice sticker packs (assets and tools)
- `scripts/dice_render/` draws the dice with Babylon.js in headless Chromium (the "Dice of Rolling" theme, posed
  so the wanted face looks at the camera: nothing is recorded, so every result is exact) and encodes the WEBMs.
  The third-party files are fetched by `fetch_assets.py` against pinned checksums; see its README.
- Two packs of 120 video stickers each, in `src/writers_bot/assets/dice_packs` with a `manifest.json` (file, die,
  variant, value, effect, emoji). **Pack 1:** d20 (with crits), the higher and the lower of two d20s (quiet green
  / magenta halo, 2 to 19: a tied 1 or 20 is just the plain d20), the d12 in red and blue without crit effects
  (for Xd12; crits work differently in Daggerheart) and in red-for-lower / blue-for-higher with them (for a
  lone d12), the d10, and the lowest and highest of the d4, d6 and d10 without crit effects. **Pack 2:** the d100 (a
  tens die and a d10 landing a moment apart), the d4, d6 and d8 (with crits) and a coin (d2: a 5 kopiyok).
- Differences from v4.2.1's stickers: white numerals and the die's own colours, with the numerals of the faces
  that were not rolled faded; crit effects that run through the whole loop and close it; a slower, shorter roll.
- The bot started using them in v4.2.3 (below).

## Done in v4.2.4: the bot uses the new dice sticker packs (v4.2.3 was never released)
- The dice module uploads the two packs of v4.2.2 (`dice1_v3_by_<bot>`, `dice2_v3_by_<bot>`, 120 stickers each, in the
  order of `manifest.json`: by die, values ascending, the crit 1 before the crit-less 1 and the crit-less highest
  before the crit highest) and answers rolls by the rules in the README ("Dice stickers"): one or two dice get stickers,
  three or four with `-s`, none with `-q`; odd dice take the next even die's stickers, a d2 is the coin; crit-less d4,
  d6 and d10 extremes when rolled with other dice; the higher / lower d20 or d12 of an advantage / disadvantage has its own
  look; `/2d12` follows Daggerheart's duality rules (blue and red, crit-less 1 and 12).
- The choice is a pure function (`core/dicestickers.py`), tested against every roll it can see and against the manifest.
- Stickers have emoji and search keywords (`scripts/dice_render/tags.py`); the bot finds a sticker by its place in the
  pack and checks each place's first emoji (the only one Telegram hands back) at every start.
- The v4.2.1 per-die packs and their files (`assets/dice`, 19 MB) are gone; `d{N}_v2_by_<bot>` packs left on
  Telegram can be deleted in @Stickers. `scripts/dice_stickers.py` stays: `scripts/dice_render` reuses its effects code.

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
- `/top` and `/profile` in a linked chat count every chat of the community and add up a person's
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

From the earlier list of 20 ideas, these go ahead: goals (2), streaks (3), achievements managed by chat
admins (9), ephemeral replies (16), a dashboard (17, reworked as below), Google Docs word count (18) and ops
quality (20). Personal bests (5) and per-user time zones (15) were built and then cancelled (phase 2). The rest are dropped: `.docx`
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

### Phase 3: sprint redesign (done in v3.4)
Decided in the October 2026 roadmap review; nothing here has been tried with a focus group yet. Writers
join and report with replies and buttons, nobody is pushed to share their work or to compete, and the
chat stays quiet. It replaces the preview reporting flow. It is piloted in one chat first, on a separate
test deployment (its own bot and database), before it reaches everyone. Before the redesign, the extra link sources
(WriteFreely, Ellipsus, Google Docs) come out of `PREVIEW_FEATURES`.
- **Modules first** (before works and the web app, so they aren't built into the old `app.py`): see
  "Architecture: modules" below.
- **Web app from the start**: the bot serves one web app, opened as a Telegram Mini App (Mini App
  `initData`, validated on the server) or as a plain website with Telegram Login and, later, Discord
  OAuth as the fallback. In this phase it holds the sprint panel and your works; phase 4 grows it into
  the dashboard. It needs public HTTPS into the homelab (a reverse proxy or a tunnel), an ops decision that
  now has to be made before this phase. Since Bot API 10.2, Mini App methods only work on the Mini App's
  own domain, so the app has a fixed domain of its own. The web app is optional: without it (no domain
  configured, or the tunnel down) everything works in chat and its buttons are hidden. Only the bot with storage serves it (see
  "Architecture: modules"), so there is one domain and one bot token to check `initData` and Telegram
  Login against.
  - **Opening it from a group**: inline `web_app` buttons only work in private chats. In groups the bot
    uses a URL button to its Main Mini App, `t.me/<bot>?startapp=<token>`. The token is short-lived,
    signed by the server, and names the chat and the sprint. Because the launch itself doesn't prove which
    chat it came from, the server also checks membership with `getChatMember`. In a DM a plain `web_app`
    button works.
- **Minimal settings store first**: per user, visibility ("hide my numbers"), the unit for new works and
  the current works; per chat, the default unit. A new work takes your unit for new works if you set one,
  otherwise the chat's default; after that only the work's own unit counts. The rest of the settings store
  is in phase 4; until then the greeter text and the `replies` entries come from the config file.
- **One reporting command**: `/join` and `/words` are removed. You join with the button and report by
  replying to the sprint message (`500`, `+300`, `=5000`, or a link when works are on) or with the
  buttons at the end. `/progress`
  covers everything else (below). Richer input goes
  in the sprint panel of the web app (below).
- **What a number means**: in a sprint, `500` is your count for this sprint (sending it again replaces
  it) and `+300` adds to it. Outside a sprint, `400` and `+400` both add 400. `=5000` is the work's total,
  and the bot records the difference from the last counted total; a link counts as a total. A reply to
  the sprint message counts only when it is nothing but a report (numbers, work names, `+`, `=`, a link);
  any other reply is left alone. Replies from people who haven't joined are ignored and never deleted.
- **Works**: a name and an optional link, up to 5 per person (also the number of fields a Discord modal
  holds). One sprint can have several works. Works, goals and streaks are stored per person (`person_id`)
  from the start, so linking accounts in phase 6 needs no migration. Whether a work carries over
  to your next sprint is your choice (a "keep for next sprint" toggle). The work's link is counted again
  at the end, as sprint links are now. Progress per work over time comes later.
- **Each word is counted once**: every work keeps a last counted total. Every path that credits words (a
  sprint report, `/progress`, the end-of-sprint and daily recounts of a link, a `=` total) starts from it
  and updates it in one transaction that checks the total is still the one it read (compare and set), and
  retries otherwise. So two sprints in different chats ending together, or a recount racing a report,
  can't credit the same words twice. A typed number on a linked work moves the total too, so the next
  recount credits only the rest; growth during two overlapping sprints goes to whichever recounts first.
  A shrinking document credits 0 and lowers the total. Links need the link-counting service, so works
  need it; without works a sprint takes numbers only.
- **Goals belong to a work**: "Novel, 1,000 words a day" or "Thesis, 20,000 this month". No work, no goal.
  Sprints and `/progress` both count towards it. A goal reached during a sprint goes on that sprint's
  board summary line (without the number if you hide your numbers, and as "a goal" if the work hides its
  title); otherwise only the writer is told, by DM. Since a bot can only DM people who have started it,
  setting your first goal asks you to write to the bot privately first ("write me privately to start
  using goals"). The bot never asks for progress.
- **`/progress`**: inside or outside a sprint, in DM or a group (reply ephemeral or deleted there):
  `+400`, `=5000` (a total), or the work's link, optionally for a named work, and optionally for an earlier day
  up to 3 days back (`/progress +400 yesterday`). During a sprint you joined, it is also your report. A % work has no % outside a sprint: there,
  `/progress` on it either corrects your report for one of your sprints in the last 3 days or marks today
  as a writing day (✓) for the streak.
- **Streaks**: days in a row with writing: a completed sprint in any chat, a `/progress` update, or growth
  of a linked work (its link is re-counted once a day at reset). Late updates up to 3 days back fill the
  gap, so missing a chat sprint or forgetting to report doesn't break a streak, and there are no
  reminders.
- **Day boundary**: one bot-wide day starting at 03:00 Kyiv time, for goals, streaks and `/top`.
- **Units**: words or %, chosen per work, defaulting to your unit for new works or else the chat's (replaces the per-chat
  `/sprintmode`); a work without a document is no different. In a sprint, % always means the share of
  what you planned for that sprint, labelled "% of plan" on the board; there is no whole-work percentage.
  Goals are in words.
- **Which work a report goes to**: joining attaches the works marked "keep for next sprint". With none,
  or in a chat with works off, reports go to your unnamed work in the chat's default unit. It has no goal
  and counts for streaks, minutes and the board like any other work. Linked works are re-counted at the end, each with its own delta. A typed number goes to the only
  unlinked work in the sprint with that unit (without one, the only linked one), so `520 50%` in one reply fills a words work and a % work.
  Only when two works share a unit does the bot ask once (`500 → [Novel] [Essay] [Split]`); naming them
  avoids it (`+300 novel +200 essay`). The Report button asks per work. `/progress` follows the same
  rule. Someone with works in both units appears in both board sections.
- **Asking privately**: ephemeral where Telegram allows; otherwise a normal reply whose buttons only that
  person can press, deleted after the tap, or after a few minutes if nobody taps. Not a DM.
- **Outcomes**: a number, **Done ✓** (no number), or **Didn't make it ✗**. A sprint counts as completed
  with any positive progress: words or % above 0, or Done. A 0, ✗ or no report doesn't count.
- **Visibility**: two separate switches, named so they can't be mixed up, both off by default.
  - **Hide my numbers** (per writer): your numbers count towards your own stats, the board shows ✓ in
    their place, and you stay off `/top`.
  - **Hide the title** (per work): the work's name is shown nowhere public; its numbers still go into your
    board line.

  Joining never needs you to share anything. A chat can turn hidden ✓ reports off (phase 4); that only
  affects its future sprints, and people who don't want to share there can skip them.
- **Board**: a words section and a % section, each sorted high to low, then ✓ and ✗. One line per person
  per section, summed over their works (the split is only in `/profile`). No medals and no winner. Records and goals go in one summary line on the board, not separate posts.
- **No fixed report window**: late reports are accepted until your next sprint or about 12 hours, and the
  board is edited when they arrive, so someone in the zone can keep writing.
- **Less clutter**: report replies are deleted once recorded (when the bot may delete messages; otherwise
  it reacts), and confirmations are ephemeral where Telegram allows, with an Undo button that takes the
  report back.
- **`/profile`** (was `/mystats`): the private view (ephemeral in groups): each work with its goal and month total, the
  streak with the last 7 days (gaps within 3 days can be filled from there), completed sprints and
  minutes, visibility. Buttons for `/progress`, works and settings. No rankings or comparisons.
- **Your data**: `/export` and `/forgetme` come out of preview and are found in `/profile` → Settings →
  My data (the commands stay as fallbacks, off the group menu). Export covers works, goals and
  `/progress` entries as well as sprints; deletion covers works too. With one bot holding the database, `/forgetme` deletes everything it
  holds about you, on every platform once your accounts are linked. The first sprint you join says
  once that your numbers are stored and where to export or delete them. `/sprinthelp` is removed.
- **Join card**: starting or joining a sprint shows your works as toggles (ephemeral, or a reply only you
  can press), on for the works kept for the next sprint. Toggling a work also changes whether it is kept for
  the next sprint (remembered by default); the card can be reopened from the sprint message (`[My works]`) at any time, and a work added mid-sprint
  has its link counted when added. The sprint message lists who is in and their works: names, or a count
  for works that hide their title. Writers who hide their numbers show no works.
- **Bragging**: `/progress` with no arguments opens your private panel (ephemeral in groups) with a
  **Share** button. Sharing posts one public line with your progress for the shortest calendar period that
  has any (today, this week, this month, this year), plus the goal and streak where they apply. You choose
  to post it, so it shows your numbers even if you hide them. With nothing this year there is no Share
  button, so a zero is never posted. A %-only writer shares sprints and minutes. With arguments,
  `/progress` reports quietly.
- **Command names**: `/stats` → `/promptstats`, `/mystats` → `/profile`, `/wc` → `/count` (`/wc` and `/stats`
  stay as hidden aliases). Every user command gets a Ukrainian alias: `/промпт`, `/картинка`, `/підрахуй`, `/спринт`,
  `/прогрес`, `/профіль`, `/топ`, `/допомога`, `/експорт`, `/забудьмене`; admin commands get none.
  Telegram doesn't treat Cyrillic as commands, so these are matched as text and only work where the bot
  sees all messages (privacy mode off, or the bot is an admin); they aren't in the menu.
- **`/top`**: total minutes of completed sprints in the chat over N days; writers who hide their numbers
  aren't on it. You get credit from when you joined: joining 20 minutes into a
  30-minute sprint gives 10.

### Phase 4: settings store, communities and dashboard (done in v4.0)
- **Settings in the database** instead of environment variables and the config sheet, per chat, changed by
  chat admins in the web app (`/settings` opens it); per-user settings arrived in phase 3:
  - sprints: default length, default unit (when % is on), works on/off (off also means no goals on the
    board), hidden ✓ reports on/off (affects only future sprints), % unit on/off;
  - in the chat: work names on the sprint message, `/top` on/off, deleting report messages;
  - achievements on/off (phase 5).
  Module settings such as the greeter's welcome text and the `replies` entries are set by chat admins
  too. There is no broadcast opt-out:
  `/broadcast` reaches every chat, except that a community gets it once per platform: in its largest
  Telegram chat and in its Discord server's `#general` (the system channel if there is none). The sheet
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

### Phase 5: achievements (done in v4.1)
- **Achievements** (9), made and awarded by hand. No rules, no automation, and no tie to sprints, works
  or stats. No XP.
- Each achievement is a name, an optional description and a **sticker**. The chat admins own the sticker
  pack and manage it themselves (through @Stickers); the bot only stores which sticker belongs to which
  achievement (the set name and the sticker's `file_unique_id`). To create an achievement, an admin sends
  the bot a sticker from the pack with a name, in a private chat with the bot or in the group; this is
  the same in a community with a Discord server. When an achievement is awarded, the bot checks the pack
  (`getStickerSet`); if the sticker is gone, the achievement keeps its name and the bot asks the awarding
  admin for a new one. No custom emoji: bots can only send them with a username bought on Fragment.
- **Awarding by command** (chat admins; alias `/нагорода`): reply to the member's message with `/award`, then pick the
  achievement from buttons, or `/award <name>`; `/unaward` takes it back. The bot posts the sticker and one
  line (`Taras отримує «Нічна сова»`). Earned achievements are listed in `/profile`.
- Achievements belong to the community where there is one, otherwise to the chat.

### Phase 6: one community across Telegram and Discord, and a Discord pass (v4.3)
- A Discord parity pass once the Telegram sprint redesign has settled, tested in a real server. Discord
  matches or beats Telegram on buttons, menus and ephemeral replies; the differences:
  - **Reporting with modals only**: the Report button opens a modal with a field per work. No replies to
    the sprint message, so the bot keeps running without the message-content intent and ignores bridged
    text (E3).
  - Join card as an ephemeral multi-select menu; `/count` also as a message context-menu command;
    `/award` also as a user context-menu command.
  - **Achievements show the Telegram sticker as an image**: static stickers as they are, animated and
    video stickers by their thumbnail.
  - **Broadcasts** go to the server's `#general` channel (the system channel if there is none). A server on
    its own is a community, and its channels are its chats; a server that joins a Telegram community (E2)
    gets one broadcast in `#general` alongside the community's largest Telegram chat.
  - **Web app**: the website with Discord OAuth; no Discord Activity.
  - **Ukrainian names** through slash-command localizations (`uk`), so each user sees one language.
  - Phrase triggers of `replies` and the `greeter` are not on Discord (they need privileged intents);
    command triggers of `replies` work as slash commands.
- E1 per-server settings come with phase 4.
- E2: a Discord server joins a community from phase 4. Linking is done in the web app by someone who is an
  admin on both sides. People link their accounts in the web app too: signing in with both Telegram and
  Discord joins them into one person (no `/link` codes). `/profile`, works, goals and streaks are then per
  person; `/top` and achievements per community.
- Shared sprints through **channel links**: an admin links a Telegram chat (or forum topic) with a Discord
  channel. A sprint started in either is posted in both, people join from either side, and there is one
  board. Unlinked chats and channels keep their own sprints. Both adapters run in one process with one
  `SprintManager` (one container instead of two; agreed with the owner).
- E3: Matterbridge is on the owner's roadmap. Its configuration (`IgnoreNicks` for the bot on both sides)
  and setup notes come on top of E2; linked channels are the natural pairs to bridge.

### Architecture: modules
The bot is built from modules that can be served as one bot or as several, set in a config file.
- **Modules**: `prompts`, `wordcount`, `sprints` (works are a feature flag of this module), `greeter`
  (welcome messages, the text a chat setting), `dice`, `smash`, `achievements`, `replies` (below). Each
  one brings
  its handlers, commands (menu, `/help`, Ukrainian aliases), settings, migrations and web app pages, and
  adds sections to the shared screens (`/profile`, `/help`, the board summary line, web app tabs), so a bot
  shows only what its modules do.
- **A small runtime** in every bot: config, the platform clients, the command menu and `/help`, the chat
  allowlist, logging, error alerts and `/health`. Nothing else is required: `wordcount`, `dice` and
  `smash` run without a database.
- **Shared services**, started only when a bot's modules need them: storage, the settings store, people
  and accounts, communities, link counting, ephemeral replies and the web app server. **At most one bot
  has storage**, and what builds on it (settings, people, communities, the web app) runs in that bot.
  Admin (chat registry, chat list, broadcasts) and your data (export, deletion) come with storage. Other
  bots don't record their chats, get no broadcasts, and take their module settings from the config file.
  Needs per module:
  `sprints`, `achievements` → storage, settings, people, and the web app when it is configured (they work
  fully in chat without it); `sprints` with works and `wordcount` →
  link counting; `greeter`, `replies` → settings (the config file in a bot without storage); `prompts` →
  its source (the sheet now, storage from phase 4).
- Modules don't call each other; they share only the core and the database.
- **`replies`** replaces `/imafan` and the slogan replies: chat admins set up triggers and answers in
  the web app. A trigger is a command (`/imafan` answers with a random text from its list) or a phrase
  ("Слава Україні" → "Героям слава"). Phrase triggers only work where the bot sees all messages. The
  `imafan_message` texts and the two slogans are imported as entries for the chats that had them.
- `EXTRAS` goes away: its features are modules now, and the hard-coded "Я переродився" `/start` greeting
  is dropped.
- **Config file** (TOML): each bot names its modules and its targets, Telegram and/or Discord, each with
  its own token:
  ```toml
  [bots.main]
  modules = ["prompts", "wordcount", "sprints", "achievements", "dice"]
  sprints = { works = true }
  telegram = { token = "env:TG_MAIN" }
  discord = { token = "env:DISCORD_MAIN" }

  [bots.greeter]
  modules = ["greeter"]
  telegram = { token = "env:TG_GREETER" }
  ```
  One process can run several bots, or each bot can run in its own container from the same image
  (`--bot greeter`); that is up to ops. `PREVIEW_FEATURES` and `EXTRAS` become the choice of modules; the
  default config is the v2 set of modules, with the redesigned `sprints` (works off) once phase 3 ships.

### Decisions
- **Telegram framework: stay on python-telegram-bot** (decided after comparing it with aiogram). Bot API
  features it lacks are called directly and kept in one module, so they can be dropped when it catches up.

## Done in v4.2.6 and v4.2.7 (small fixes)
- v4.2.6: the dice sticker uploads got 300 s timeouts and wait for Telegram after a timeout, a short pack is finished instead of
  remade (live Telegram answered after python-telegram-bot's 5 s default and left the packs at 50 and 80 of 120).
- v4.2.7: Telegram Login on the website uses the widget's redirect flow (`data-auth-url`), because the `data-onauth` callback is
  compiled with eval and the page's CSP forbids it; the page posts the fields from the query string to `/api/session` and
  removes them from the URL.

## Done in v4.3.0
- **Dice stickers, rebuilt**: 866 stickers in 8 packs (`scripts/dice_render/clips.py` has the names and the layout). Dice of a
  kind share stickers: two or three d4 / d6 and two d8 are one sticker with the dice side by side, unordered (8d6 is three
  stickers); two d10 are a d100 picture (a tens die and a ones die); Daggerheart's duality dice are one ordered sticker
  (`2d12_9_4`) with the halos of their rules and no crit effects; two d20 with advantage or disadvantage are one sticker with the
  die that counts haloed (`2d20a_5_17`), the ties plain. A group has the crit effects of the clip (highest of any die, else a 1;
  the die that counts with advantage); a single die with others is crit-less (`d20n_20`). The d3 is a new model (a ball with
  three flats); every die type has its own colour (d3 grey, d4 purple, d6 yellow, d8 black, d10 green, d12 blue, red for its 1 and
  the duality's second die, d20 orange, the d100's tens die pink). The d100's dice are close together, the d4 is tilted, and the
  0 of the d10 and 00 of the tens die, which were upside down, are turned the right way.
- **The limits count stickers, not dice**: one or two on their own, up to `max_stickers` (4) with `-s`.
- **d12**: blue by default, a 1 stays red.
- **No sticker packs.** The bot uploads a clip with `sendSticker` the first time a roll needs it and keeps the `file_id` in a cache
  file (`core/dicecache.py`; copyable for the same bot; unique ids are shared by all bots). A refused id is uploaded again. A person's
  message with one of the roll stickers (found by `file_unique_id`) is a forward: the chat setting `dice.forwards` warns (default),
  deletes or ignores. `protect_content` was rejected. Telegram forces `_by_<bot>` on a bot-made pack, which is what made packs a
  nuisance (a bot just for them, an upload script, 866 uploads); the clips stay in `assets/dice_packs` and the manifest keeps their order.
- **Achievements**: a bare sticker sent to the bot in private no longer starts "new achievement" (v4.2.5 asked for a name for
  every sticker); `/newachievement` in private starts the steps (picture, name, chat). The picture can be a sticker (in a
  pack or not), a photo or an image file, in a group (reply to it) and in private.
- **`/help`** is generated entirely from the commands (`Command.description`, `usage`, `uk`, `module`, `scope`) with a button
  and a page per module (plus your data, the chat's settings, the bot's admin tools); no hand-written help line per command.
  The menu flag `menu=False` keeps a command out of Telegram's menu but not out of /help. A bot without `PREVIEW_FEATURES` keeps
  one flat page.
- **Ukrainian commands** start with `!` (`!промпт`, `!к12`; `/промпт` works too) and /help shows them next to every command.
- **Dice**: each die is a command (`/d2` ... `/d100`, `!к2` ...) in the menu and /help; `adv` / `dis` show the two rolls
  sorted (the lower first) and put the die that counts first in the stickers; `kh1` / `kl1` stay the d20 library's own keep (the
  dice as rolled).
- **True randomness** (`dice.randomness = "random.org"`, `random_org_key`): dice from a pool of random.org bytes, the
  operating system's generator when the pool is empty.

## Done in v4.3.0: no more PREVIEW_FEATURES
- `PREVIEW_FEATURES` is gone. The environment without a config file still describes one v2 bot (`EXTRAS` adds the extended-branch
  modules); everything newer is chosen by the modules' options in a config file (`sprints = { reports = true, works = true }`,
  `dice = { stickers = true }`, the bot's `admin` and `chat_settings` flags, the `[web]` table). The Discord bot and the web app
  no longer need a switch (a token and `WEB_APP_URL` are the choice), `EPHEMERAL_REPLIES` is off in the environment (on by default
  in a file), and /help always has its module submenus. Dice options (`stickers`, `variables`) default off, `commands` on.

## Done in v4.3.1: the pack machinery is gone
- The clips live flat in `assets/dice_clips` (was `dice_packs/pack1..8`); `manifest.json` is a map from a clip's name to its die, variant,
  value and effect (no more emoji and search keywords, no pack order, no 120-per-pack layout); `packs.py` is now `assets.py`, `tags.py` is
  deleted, and `render.py --all` replaces `--pack N`. Kept on purpose: the sorted pool clips, the odd dice taking the next even die's
  clips, the pre-rendered pairs (2d12 duality, 2d20 advantage / disadvantage) and `max_stickers`.
