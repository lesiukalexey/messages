# messages

Personal Telegram assistant based on Telethon.

The first version is deliberately draft-first: contacts default to `off`, no
message is sent automatically, and every decision is recorded in the audit log.

## Local setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
```

Fill `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env`. Keep the Telethon
session outside Git. Start the worker with:

```bash
messages
```

To export available Telegram history for one Telegram account into a protected
database outside the repository, run:

```bash
messages-export --account personal
```

On Raspberry, the default backend is a separate MariaDB database named
`messages`. Connection settings are loaded from the protected
`/home/admin/messages-runtime/messages.env`. Each account has its own protected
file at `/home/admin/messages-runtime/accounts/<account>.env` with its own
`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and optional
`TELEGRAM_SESSION_PATH`. The account name is stored in every row, and sessions
are separate by default. Set `STORAGE_BACKEND=sqlite` only when a local SQLite
export is explicitly needed.

The exporter resumes safely: messages are keyed by
`(account_id, dialog_id, message_id)` and are not duplicated on later runs. Use
`--limit` for a small test export or `--since 2026-01-01` to limit the time
range. Only one-to-one dialogs with real, non-bot users are exported; groups,
channels, bots, deleted users, and Saved Messages are skipped. Dialog usernames
and available phone numbers are stored; Telegram may leave either value empty
when it is not visible to the account. Each dialog also stores the first and
last message timestamps, total message count, and the timestamp of the last
count recalculation.

The initial run only initializes the database and starts the Telegram update
loop. Contact policies are `off`, `draft`, or `auto`; `auto` is reserved for a
future reviewed implementation and is not enabled by this scaffold.

## Safety boundaries

- Never commit `.env`, Telegram sessions, login codes, or private chat exports.
- New contacts are disabled until explicitly configured.
- Uncertain intent or identity must produce a draft, not a sent reply.
- The service is for personal conversations, not bulk messaging or outreach.
- Treat the export directory and Telegram session as sensitive account data.
