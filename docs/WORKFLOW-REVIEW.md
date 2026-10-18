# Workflow review

Between the phases of the v4 work, user workflows were modelled as tests against an in-process fake of
the Bot API (`tests/fake_telegram.py`): people starting, joining and reporting on sprints, managing works,
using /progress and /profile, admins changing settings, and so on. Each test checks what everyone sees;
`WF_TRANSCRIPTS=dir uv run pytest tests/test_wf_*.py` writes the transcripts. This file records the
workflows that felt convoluted, spammy or unnatural: the ones fixed on the way, and the ones left as
questions.

## Round 1: the sprint redesign (v3.4), `tests/test_wf_phase3.py`, 34 workflows

### Fixed
1. **Two "sprint finished" messages.** The edited sprint message and the board both said the sprint was
   over. The board now replaces the sprint message, so a finished sprint leaves one message.
2. **The Report button's question lingered.** Unanswered, the public "Alice, скільки написали?" stayed
   until the sprint closed (12 hours). It now goes after 5 minutes.
3. **The data notice went public.** When the bot isn't a chat admin, starting a sprint with /sprint
   posted the one-time "your numbers are stored" card publicly for 3 minutes. The notice alone is now never
   public; it waits for a private moment. A card with works to pick is still posted (it needs the buttons).
4. **/progress hours after a sprint fed that sprint.** `/progress +100` added to a sprint you had already
   reported. After the end it is now your late report only until you've reported.
5. **The works menu cluttered the private chat.** Every rename, link or goal left a question, your answer and
   a new card. The question and the answer are now deleted and the menu message updated in place.
6. **"/forgetme" said 0 records deleted** after deleting progress entries. It now just says the data is gone.
7. **The board's hint** was two long sentences on every board; shortened.

### Still open
1. **Report button → a public question.** Telegram can't force a reply to an ephemeral message, so the
   button posts "Alice, скільки написали?" in the chat (deleted after the answer or 5 minutes). Plain
   replies and the ✓/✗ buttons avoid it; the web app panel has a field per work.
2. **Two works with the same unit ask every time.** A bare `500` asks "Куди записати 500?" (one more tap per
   report). Naming the work (`500 роман`) avoids it, but people have to learn that; "Розділити" leads to
   the public question from 1.
3. **Without admin rights the bot can't confirm privately.** Reports get a 👍 only, so there's no Undo,
   and mistakes (`150%`) get a public error reply for a minute.
4. **% outside a sprint is hard to explain.** `/progress 60% план` corrects your latest sprint with that
   work (or asks which one); a % has no meaning outside a sprint.
5. **"Late reports until your next sprint"** refuses a report for an older sprint once you've joined a
   new one. It's the rule, but it surprises.
6. **Works live in the private chat.** The group's Works button opens the private chat, a context switch in
   the middle of a sprint.
7. **Hidden numbers are visible as such.** People who hide their numbers appear as "✓ Bob" on the board, so
   it's visible that they hide them (not the numbers).
8. **The first goal needs a private chat** with the bot, so it can tell you when you reach it: an extra
   step on the website.

## Round 2: settings, communities, replies, prompt databases, the dashboard (v4.0), `tests/test_wf_phase4.py` and `tests/test_webapp_js.py`, 26 + 4 workflows

### Fixed
1. **The daily community check undid service messages.** A chat added to a Telegram Community (service
   message) was unlinked minutes later when the first `getChat` check ran. The service message now counts
   as the check.
2. **Admins never learned about suggested prompts.** `/suggest` went into a queue nobody was told about.
   The /settings card now says how many suggestions are waiting.
3. **Members saw admin tabs in the web app**, and the Prompts tab opened first. Admin tabs (Chat,
   Prompts) now show only to people who administer a chat with the bot, after the personal tabs.

### Still open
1. **The /settings card is long.** Eight to ten toggles plus the −10/−5/+5/+10 row for the sprint length
   in one private card. It works, but the web app's Chat tab (with explanations) is easier.
2. **`/replies add /trigger = answer` is a syntax to learn.** It's the only way in chat; the web app has a
   form.
3. **The welcome text is typed into the group.** The bot asks for it with a public question (the admin's
   answer and the question are deleted afterwards), because the bot can't tell which chat a private message
   is about.
4. **Submitting a chat's prompt to the shared set** is only in the web app.
5. **Categories created by suggestions keep the member's spelling** ("комедія" next to "Жахи" from the
   sheet); admins can rename them in the web app.
6. **Community management is a command** (`/community add <id> <chat>`); there is no web page for it, and
   chat ids come from `/chats`.
7. **`/bcexclude` remains.** The plan says there is no broadcast opt-out; the admin-side exclusion list
   from v3.1 was kept as an admin tool.

## Round 3: achievements (v4.1) and whole journeys, `tests/test_wf_phase5.py`, 21 workflows

### Fixed
1. **A vanished sticker was handled in public.** When the admins had removed an achievement's sticker
   from their pack, the bot posted "the sticker is gone, send a new one" in the group, before the award
   line. It now posts the award first and asks the admin in the private chat (in the group only if the bot
   can't write to them).
2. **The works card at every sprint.** Someone with one kept work got the "what are you writing?" card at
   each sprint, with nothing to choose. The card now shows only when there's a choice (two or more works,
   or a work not kept) or for the first-sprint notice; "Мої твори" still opens it.

### Still open
1. **An award is two messages** (the sticker, then the line), since stickers can't carry text. That's the
   plan's design; in a busy chat it's the most visible thing the bot posts after the board.
2. **The `/sprint` command stays in the chat.** Everything else of a sprint ends up as one board message,
   but the command that started it is the user's own message and isn't deleted (as in v2).
3. **Creating an achievement in a group** means replying to a sticker with a command
   (`/newachievement Назва — опис`); the private chat flow (send a sticker, answer with the name) is
   friendlier, but not discoverable from the group.
4. **The board's "✓ Bob" for hidden numbers and "✗ Carol"** are on separate lines below the numbers;
   people who prefer not to share are visibly grouped (see round 1, item 7).

## Answers to rounds 1–3 (v4.2)

The owner's answers to the open items above, and what v4.2 did (see PLAN.md, "Done in v4.2"):

| Item | Answer | Now |
|---|---|---|
| R1.1 Report button → public question | — | unchanged |
| R1.2 Two works, one unit: "Куди записати?" | Fine if people learn the split syntax, or to pause works | tip with their own works in the question (first 3 times); works can be paused; "Пауза" button in the question |
| R1.3 No admin rights → no private confirmation | — | unchanged |
| R1.4 % outside a sprint | Confirm it explicitly, ephemerally | the confirmation names the sprint; a short-lived reply where ephemeral isn't possible |
| R1.5 Late reports until the next sprint | Only for participants, of the last sprint they joined | unchanged (as before) |
| R1.6 Works live in the private chat | Switching should work in public too | `/works` and `/profile` → Твори in groups: on/paused toggles |
| R1.7 Hidden numbers visible as "✓ Bob" | — | unchanged |
| R1.8 The first goal needs a private chat | Needs an onboarding step, and keeping track of it | three steps in the private chat, progress kept and shown |
| R2.1–R2.7 admin tools | Suggestions asked for, then all accepted | built in v4.2 (below); `/bcexclude` kept |
| R3.1 An award is two messages | No raw sticker outside creation; rendered cards | one picture with the caption; pictures for lists and the board |

### Suggestions for the admin tools (R2), all built in v4.2

1. **The long /settings card** → a short card with four sections (Спринти · Звіти й приватність ·
   Промпти й відповіді · Привітання й досягнення), each opening its own few toggles in place, plus the
   web app button. Only the section you open is long.
2. **`/replies add /trigger = answer`** → write the answer as an ordinary message, then reply to it with
   `/replies add /imafan` (the replied text becomes the answer, formatting kept). No `=` syntax, and
   multi-line answers work. The `/replies` card gets an "Як додати" line showing exactly that.
3. **Typing the welcome text into the group** → the /settings button "Привітання" becomes a deep link to
   the private chat that carries the chat (`/start welcome_<chat>`; the bot checks the admin rights again
   there), and the admin types the text privately. The same link works for long replies.
4. **Submitting to the shared set only in the web app** → a third button on `/suggestions` items,
   "Прийняти й запропонувати всім".
5. **Category spelling from suggestions** → on accepting, if the category is close to an existing one
   (case, a typo, the English/Ukrainian alias), the card asks "Додати до «Комедія»?" before creating a new
   one.
6. **Community management is commands only** → a Communities page in the web app for super-admins (chats
   as checkboxes per community), and `/community` replies with buttons instead of ids.
7. **`/bcexclude`** → keep as an admin tool, or drop it if "no opt-out" should also bind admins.

## Round 4: v4.2, `tests/test_wf_phase6.py`, 49 workflows (and 1 web-app rendering check)

Pausing and switching works, the split tip, % corrections, getting started, achievement pictures, dice
(notation and stickers), `/help` and the command menus, the admin tools.

### Fixed
1. **The tip's example numbers** were 60/40 of the number ("240 Роман 160 Есе"), which reads like a
   calculation; they are now rounded ("300 Роман 200 Есе").
2. **Confirmations of ephemeral commands** went nowhere without admin rights: `/progress` sent as an
   ephemeral command got only the 👍. They are now ephemeral replies to the command.
3. **The transcripts** didn't show pictures and stickers; the fake API logs them now.
4. **A stray question from two menus**: with a getting-started step and the works menu both waiting for
   typed text, the step took the work's name; the newer question now gets the answer.
5. **Looking at someone's achievements notified them** (the caption mentioned them); it now names them in
   bold.
6. **`/help` in a v2 bot** offered "your data in the private chat", which a v2 bot doesn't have; the line now
   shows only where it's true, and the sprint line names a chat's own length only where chats have settings.
7. **`/help` in a group** is now a private answer (a v2 bot keeps the public reply).

### Still open
1. **The first `/start` is two messages** (the help, then step 1 of getting started). Merging them would
   hide the help; it happens once.
2. **A stray message during a typed step becomes the answer.** While step 1 waits (30 minutes), any text in
   the private chat is taken as the work's name. "Не зараз" stops the waiting; a wrong name is fixed with
   /works. A ForceReply question would avoid it but can't carry the Skip / Later buttons.
3. **A roll with a bonus or a caption is two messages** (the sticker, then the line); a bare `/d20` is one.
   Stickers can't have captions.
4. **Dice stickers loop.** Telegram plays video stickers in a loop, so the die tumbles again every 3 s while
   on screen (the landed face is held for the last half). The built-in 🎲 plays once, but is d6 only and
   its value is Telegram's.
5. **The dice packs need a super-admin** as their owner (a Telegram rule); without one, rolls are text.
6. **160 sticker files** (about 30 MB) are in the repository and the image; they are uploaded to Telegram
   once.

## Summary

- 34 + 26 + 21 + 49 modelled workflows (plus 5 web-app rendering checks), all passing; 20 problems fixed on
  the way.
- The open items above are design questions rather than bugs: mostly Telegram limits (no force-reply to
  an ephemeral message, ephemeral messages only for admin bots or right after a button press) and
  command syntaxes that the web app covers better.
