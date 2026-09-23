# Messages

Personal Telegram assistant running through an authorized Telethon user session.
It replies autonomously to one-to-one conversations categorized as `friends` or
`recruiters`.

## Global switch

The Telegram profile bio controls the assistant:

- Bio equal to `free` (ignoring case and surrounding whitespace): OFF.
- Empty bio or any other bio: ON.

If the profile cannot be read, the worker fails closed. Only categorized
contacts are eligible even when the assistant is on. Groups, channels, bots,
Saved Messages, and uncategorized contacts are ignored.

## Settings in Telegram

Send commands to **Saved Messages** from the same account running the assistant:

```text
/help
/model
/model gpt-5-mini
/category @username friends
/category @username recruiters
/category remove @username
/contacts
/contacts friends
/contacts recruiters
/dialogs
/dialogs 2
```

The selected model is stored in the protected assistant database. The choices
come from `OPENAI_MODELS`; add another API model ID there, restart the worker,
and it appears in `/model`. The default is `gpt-5-mini`. OpenAI receives the
incoming message and up to 23 recent messages from that same chat to produce the
reply. Do not enable this integration until an API key is configured and this
data flow is acceptable.

Contacts start uncategorized and are not answered. Assign each chat explicitly
to `friends` or `recruiters` with its Telegram username. The categories are
separate in `/contacts` and receive different model context. Use `/dialogs` to
page through exported one-to-one chats and see which ones still need a category.

## Calendar

Google Calendar is used for availability checks and agreed meeting events. The
assistant checks free/busy before saying a time is available. It creates a
private event only when the conversation clearly confirms a meeting; event
creation is idempotent per incoming Telegram message. It does not invite
attendees or disclose other event details. Default duration is 60 minutes for
friends and 30 minutes for recruiters when none was specified.

1. In Google Cloud Console, enable Calendar API and create an OAuth client of
   type Desktop app.
2. Copy its downloaded JSON to
   `/home/admin/messages-runtime/google-calendar/client_secret.json` and set
   permissions to `600`.
3. Start an SSH port forward from your workstation:
   `ssh -L 8765:localhost:8765 admin@192.168.31.46`
4. In another SSH session, run `messages-calendar-auth`. Open the printed link
   in a browser and complete consent. The refresh token is saved outside Git
   with permissions `600`.

The OAuth request uses the free/busy and owned-event scopes. The provider token
is not required for Telegram replies; until connected, any time-dependent reply
will say that availability could not be checked.

## Runtime configuration

On Raspberry Pi, shared settings live in
`/home/admin/messages-runtime/messages.env`; Telegram API credentials live in
`/home/admin/messages-runtime/accounts/<account>.env`. The assistant reads the
account settings selected by `TELEGRAM_ACCOUNT_ID` (default `personal`). Set
these variables in protected runtime storage, never in Git:

```dotenv
OPENAI_API_KEY=...
OPENAI_MODELS=gpt-5-mini,gpt-5.1,gpt-4.1-mini
OPENAI_MODEL=gpt-5-mini
```

Copy the derived style profile to
`/home/admin/messages-runtime/profiles/communication-style.md` with permissions
`600`. The worker reads chat history from the existing account-separated MySQL
export and keeps categories, model selection, idempotency state, and audit
records in `/home/admin/messages-runtime/assistant.sqlite3`.

Install/reinstall the package from the repository with
`/home/admin/messages-runtime/venv/bin/pip install -e .`. Install
`deploy/messages@.service` as `/etc/systemd/system/messages@.service`, then run
`sudo systemctl enable --now messages@personal.service`. Add another account
instance only when that Telegram account's bio and contact categories are
configured independently.

## History export

To refresh account history for response context, use:

```bash
messages-export --account personal
```

The exporter stores text and message metadata only. It skips media files,
groups, channels, bots, deleted users, and Saved Messages.

## Safety and data handling

- Never commit API keys, Telegram sessions, login codes, OAuth files, OAuth
  tokens, or private chat exports.
- Do not use the account for mass messaging, unsolicited outreach, or
  rate-limit evasion.
- Check calendar availability without exposing event titles or details.
- Keep raw private chat exports in protected runtime storage only.
