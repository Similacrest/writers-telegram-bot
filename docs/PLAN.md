# Refactor plan (v3)

## In scope for this refactor

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
   their API (HTML fallback for single-user instances). **Ellipsus: deferred**, since it has no public API
   and its editor syncs text over a WebSocket (see "Ideas" #1).

## 20 further improvements (not in this refactor, for prioritisation)

These come from comparable writing bots (Sprinto, Writer-Bot, Salsa Chip/Winnie, WriterStats,
IRC word-war bots) and from what Bot API 9.x–10.x now allows.

| # | Idea | Inspired by | Effort |
|---|------|-------------|--------|
| 1 | **Ellipsus support**: Reader-link fetcher once we have a sample public link, or accept the exported `.docx` and count it | user request | M |
| 2 | **Daily / monthly / project goals** (`/goal daily 1000`) with progress shown after every report, resetting in the writer's timezone | Writer-Bot | M |
| 3 | **Writing streaks**: consecutive days with a report, streak shown in `/mystats`, gentle reminder when a streak is at risk | Duolingo-style, Writer-Bot | S |
| 4 | **Projects**: named works with a target word count (`/project new "Novel" 80000`), sprint reports credited to a project, % complete | Writer-Bot | M |
| 5 | **WPM in the sprint scoreboard**, plus personal bests (words per sprint, fastest WPM) | Sprinto | S |
| 6 | **Scheduled & recurring sprints** ("every weekday at 20:00", `/sprint at 21:30`) | Sprinto | M |
| 7 | **Chained sprints / Pomodoro mode**: 25 on / 5 off × N with one join | Salsa Chip `/chain`, Pomodoro | M |
| 8 | **Join with a starting word count** (`/join 12034`) and report the end total, so the bot does the subtraction | Sprinto, Salsa Chip | S |
| 9 | **Achievements / XP / levels** (first sprint, 10k words, 7-day streak), with a small random award per sprint as a fun touch | Writer-Bot XP, Salsa Chip raptors | M |
| 10 | **Server- and global-level leaderboards** by week/month, opt-in for privacy | Salsa Chip | S |
| 11 | **NaNoWriMo-style events**: time-boxed chat challenge with a shared goal and a progress bar for the whole group | NaNoWriMo / community events | M |
| 12 | **Team word wars**: split participants into teams, team totals at the end | IRC word-war bots | S |
| 13 | **Random generators** beyond prompts: character names, places, plot twists, dice rolls (the README already promises dice) | Writer-Bot generators | S |
| 14 | **Welcome flow for new members** (README promises it): greeting + quick-start, using Telegram join-request queries (Bot API 10.1) | existing README | S |
| 15 | **Per-user timezone** (`/tz Europe/Kyiv`) so goals, streaks and scheduled sprints line up for everyone; Telegram `date_time` entities (Bot API 9.5) for local times | Winnie, Bot API 9.5 | S |
| 16 | **Ephemeral replies** (Bot API 10.2) for "you joined" / "report saved" so group chats stay quiet; needs a PTB release with support or raw `do_api_request` | Bot API 10.2 | S |
| 17 | **Mini App dashboard**: charts of words over time, goals, projects, inside Telegram | Telegram Mini Apps, WriterStats | L |
| 18 | **Google Docs / Drive word count** for shared links (the bot already has Google credentials), plus AO3 and plain `.txt`/`.docx` uploads | general demand | M |
| 19 | **i18n**: English UI alongside Ukrainian, chosen per chat; prompts already support per-language sheets | multi-language bots | M |
| 20 | **Ops quality**: `/health` endpoint, structured logs, Sentry-style error reporting, CSV export of a user's history (`/export`), data deletion (`/forgetme`) | GDPR / ops hygiene | S |

S = up to a day, M = a few days, L = a week or more.
