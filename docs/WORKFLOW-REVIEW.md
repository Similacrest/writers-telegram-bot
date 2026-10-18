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
