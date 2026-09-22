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

The initial run only initializes the database and starts the Telegram update
loop. Contact policies are `off`, `draft`, or `auto`; `auto` is reserved for a
future reviewed implementation and is not enabled by this scaffold.

## Safety boundaries

- Never commit `.env`, Telegram sessions, login codes, or private chat exports.
- New contacts are disabled until explicitly configured.
- Uncertain intent or identity must produce a draft, not a sent reply.
- The service is for personal conversations, not bulk messaging or outreach.

