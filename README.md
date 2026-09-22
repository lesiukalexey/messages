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

To export available Telegram history into a protected database outside the
repository, run:

```bash
messages-export
```

On Raspberry, the default backend is a separate MariaDB database named
`messages`. Connection settings are loaded from the protected
`/home/admin/messages-runtime/messages.env`. Set `STORAGE_BACKEND=sqlite` only
when a local SQLite export is explicitly needed.

The exporter resumes safely: messages are keyed by `(dialog_id, message_id)`
and are not duplicated on later runs. Use `--limit` for a small test export or
`--since 2026-01-01` to limit the time range. It includes private chats,
groups, and channels returned by the authorized account.

The initial run only initializes the database and starts the Telegram update
loop. Contact policies are `off`, `draft`, or `auto`; `auto` is reserved for a
future reviewed implementation and is not enabled by this scaffold.

## Safety boundaries

- Never commit `.env`, Telegram sessions, login codes, or private chat exports.
- New contacts are disabled until explicitly configured.
- Uncertain intent or identity must produce a draft, not a sent reply.
- The service is for personal conversations, not bulk messaging or outreach.
- Treat the export directory and Telegram session as sensitive account data.
