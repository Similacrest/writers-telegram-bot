# Moving from Render (v2) to the homelab (v3)

## What data there is to move

The v2 bot keeps almost nothing:

| v2 (Render) | What's in it | v3 |
|---|---|---|
| `db.pickle` (PTB `PicklePersistence`) | Empty `chat_data` / `user_data` entries, one per chat and user the bot has seen. v2 keeps sprints in memory only, so there are no sprints or word counts in it | Chat ids are imported into the chat registry (`/chats`, `/broadcast`) with `scripts/import_legacy_chats.py`. Nothing else is worth keeping |
| `token.pickle.b64` | Google OAuth token | Converted once to `token.json` with `scripts/migrate_google_token.py` |
| Environment variables | Tokens, sheet ids, admins | Mostly the same names; see "Environment" below |
| Google Sheet / Drive | Prompts, config, images | Unchanged; v3 reads the same sheet |

**Render disk caveat:** unless the Render service has a persistent disk, its filesystem is reset on every
deploy and restart, so `db.pickle` only knows the chats seen since the last restart. Import it anyway: v3
adds every chat it hears from, and the import only fills gaps.

v3 itself stores everything in one SQLite file (`/app/data/bot.sqlite3`): sprints, participants, word
reports and the chat registry. The schema is versioned (`PRAGMA user_version`) and upgraded in place on
startup. v3.1 adds the `broadcast_excluded` column, v3.2 the sprint `unit` column and the `chat_settings`
table.

## Cutover

Polling is the simpler choice for the homelab: the bot connects out to Telegram and needs no public
address, TLS certificate or open port. `TELEGRAM_WEBHOOK_TOKEN` and `WEBHOOK_HOST` are then not needed.
(A webhook would need public HTTPS into the homelab; the Mini App dashboard on the roadmap will need that
anyway, so it can be revisited then.)

Only one place may receive the bot's updates. Telegram rejects polling while a webhook is set, and PTB
deletes the webhook when polling starts. If Render is still running, every Render restart sets the webhook
again and the two instances take turns. So stop Render **before** starting the homelab bot:

1. **Pick a quiet time.** v2 sprints live in memory, so a sprint that's running at cutover is lost.
2. **Copy from Render** (Render shell or a one-off job): `db.pickle` and `token.pickle.b64`, plus the
   environment variables from the dashboard.
3. **Suspend the Render service.** Don't delete it yet: it's the rollback.
4. **Prepare the data volume** on the homelab host: a directory or named volume mounted at `/app/data` and
   writable by uid 10001.
5. **Convert the token and import chats** (one-off containers on the same volume):
   ```sh
   docker run --rm -v writers-data:/app/data -v "$PWD/legacy:/legacy:ro" writers-bot:v3.2.0 \
     python scripts/migrate_google_token.py /legacy/token.pickle.b64 /app/data/token.json
   docker run --rm -v writers-data:/app/data -v "$PWD/legacy:/legacy:ro" writers-bot:v3.2.0 \
     python scripts/import_legacy_chats.py /legacy/db.pickle
   ```
6. **Start the bot** with `TELEGRAM_POLLING=1` and `restart: unless-stopped`. Check the log for
   `prompts loaded` and no `config error`, then run the checks from the handover note.
7. **Rollback**, if needed: stop the homelab bot, resume Render (it sets its webhook again on start).
   The homelab database stays as it is for the next attempt.
8. After a week or so without problems: delete the Render service, and `db.pickle` / `token.pickle.b64`
   copies.

## Environment

Same as on Render, except:

| Variable | Homelab value | Why |
|---|---|---|
| `TELEGRAM_POLLING` | `1` | No inbound connection needed |
| `TELEGRAM_WEBHOOK_TOKEN`, `WEBHOOK_HOST`, `PORT` | unset | Only for webhook mode |
| `HOSTNAME` | not used | v2 used it for the webhook URL and the "Як прогрес?" keep-awake posts; both are gone |
| `ALLOWED_CHATS` | the group ids v2 had `welcome_message` rows for | v2 `extended` only answered in those groups and in private chats |
| `ALLOW_PRIVATE_CHATS` | default (on) | Private chats work for everyone, as in v2 `extended` |
| `GOOGLE_TOKEN_FILE` | default `/app/data/token.json` | Replaces `token.pickle.b64` |
| `DB_PATH` | default `/app/data/bot.sqlite3` | |

## Backups

The database is small. Back it up with SQLite's online backup, which is safe while the bot runs (a plain
`cp` of a WAL database can miss recent writes):

```sh
docker exec writers-bot python scripts/backup_db.py /app/data/backup-$(date +%F).sqlite3
```

Then move the copy off the volume as part of the homelab's normal backups. `token.json` is refreshed in
place by the bot and should be backed up too (it's a secret: keep it out of shared storage).
