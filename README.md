# Messages

Personal Telegram assistant running through an authorized Telethon user session.
It replies autonomously to eligible one-to-one conversations while their category
is `unknown`, `friends`, or `recruiters`. Realtor contacts are excluded.

## Global switch

The Telegram profile bio controls the assistant:

- Bio equal to `free` (ignoring case and surrounding whitespace): OFF.
- Empty bio or any other bio: ON.

If the profile cannot be read, the worker fails closed. Groups, channels, bots,
and Saved Messages are ignored. A new contact starts as `unknown`. The assistant
uses each new message and the conversation to assign `friends`, `recruiters`, or
`realtors` only when the relationship becomes clear. Existing dialogs are treated
as friends unless they already have an explicit category.

## Settings in Telegram

Send commands to **Saved Messages** from the same account running the assistant:

```text
/help
/model
/model gpt-5.6-luna
/category @username friends
/category @username recruiters
/category @username unknown
/category remove @username
/contacts
/contacts unknown
/contacts friends
/contacts recruiters
/dialogs
/dialogs 2
```

The selected model is stored in the protected assistant database. The choices
come from `CODEX_MODELS`; add another model ID supported by the installed Codex
CLI, restart the worker, and it appears in `/model`. The default is
`gpt-5.6-luna`. The worker starts `codex exec` using the existing Codex CLI
login for the `admin` account, as Job Apply does. Codex receives the incoming
message and up to 23 recent messages from that same chat to produce the reply.
No OpenAI API key is needed.

New contacts can receive replies while their category remains `unknown`. Clear
recruiting or job opportunity conversations go to `recruiters`; clear personal
conversations go to `friends`. The assistant keeps evaluating later messages
until the category is clear. You can override a contact with `/category`.
Manual choices stay fixed.
Automatically classified friends can be promoted to recruiters if later
messages make the hiring context clear. The categories are separate in
`/contacts` and receive different model context.

## Calendar

Google Calendar is used for availability checks and agreed meeting events. The
assistant checks free/busy before saying a time is available. When asked when
Alexey is free, it reuses a date established in recent chat context and offers
up to three calendar-verified slots between 09:00 and 22:00 local time for that
day. It creates a private event
only when the conversation clearly confirms a meeting; event
creation is idempotent per incoming Telegram message. It does not invite
attendees or disclose other event details. Default duration is 60 minutes for
friends and 30 minutes for recruiters when none was specified.
If Calendar is not authorized, a check fails, or the requested time is busy,
the worker will not confirm that meeting time. Recent context is fetched
directly from Telegram so dates such as “today” carry across turns.

1. Reuse the existing Desktop OAuth client at
   `/home/alex/.ai/home/.local/mail/oauth-client.json`, or create a Desktop app
   client in Google Cloud Console with Calendar API enabled.
2. Copy the client JSON to
   `/home/admin/messages-runtime/google-calendar/client_secret.json` and set
   permissions to `600`. The existing home-workspace tokens are read-only and
   need fresh consent for the assistant's scopes; do not copy them as the bot's
   Calendar token.
3. Start an SSH port forward from your workstation:
   `ssh -L 8765:localhost:8765 admin@192.168.31.46`
4. In another SSH session, run `messages-calendar-auth`. Open the printed link
   in a browser and complete consent. The refresh token is saved outside Git
   with permissions `600`.

The OAuth request uses the free/busy and owned-event scopes. The provider token
is not required for ordinary Telegram replies; meeting times cannot be confirmed
until Calendar is connected and a free/busy check succeeds.
Read-only Calendar tokens used by other local tools do not provide these scopes;
they cannot authorize free/busy checks or event creation for this assistant.

## Runtime configuration

On Raspberry Pi, shared settings live in
`/home/admin/messages-runtime/messages.env`; Telegram API credentials live in
`/home/admin/messages-runtime/accounts/<account>.env`. The assistant reads the
account settings selected by `TELEGRAM_ACCOUNT_ID` (default `personal`). Set
these variables in protected runtime storage, never in Git:

```dotenv
CODEX_BINARY=/home/admin/.codex/packages/standalone/current/bin/codex
CODEX_HOME=/home/admin/.codex
CODEX_MODELS=gpt-5.6-luna,gpt-5.6-terra,gpt-5.6-sol
CODEX_MODEL=gpt-5.6-luna
```

To send unanswered questions to Alexey, set the BotFather token for
`@learnDataBot` as `LEARNING_BOT_TOKEN` in
`/home/admin/messages-runtime/messages.env` and keep that file owner-readable
only (`chmod 600`). No channel-admin rights are needed. Alexey must send `/start`
to `@learnDataBot` from one of the assistant's two owned Telegram accounts.
Only the `personal` worker polls Bot API updates; it accepts replies from those
registered account IDs and writes answers to the protected Job Apply YAML.
Never put the token in Git, chat, or logs.

## Job Apply recruiter reply API

Job Apply can request a prepared recruiter reply after it discovers a new Djinni
message. This service does not poll Djinni or send messages there; Job Apply
owns browser authentication, delivery, and retry state. The API accepts only
configured profile IDs whose YAML declares the configured Alexey persona. The
default allowlist contains `lesiuk.alexey@gmail.com` and
`lesuk.aleksey@gmail.com`; `values` remain profile-specific, while shareable
learned answers are merged only for matching non-empty persona IDs.

Set `REPLY_API_TOKEN` (at least 32 random characters), `REPLY_API_HOST`,
`REPLY_API_PORT`, and, when needed, the JSON `JOB_APPLY_PROFILES` mapping in
`/home/admin/messages-runtime/messages.env`. Give the same token to Job Apply
through its protected runtime configuration; do not put it in Git or logs.
The API binds to the private Raspberry Pi interface and requires a Bearer token.
Keep port 8095 off public ingress.

Install `deploy/messages-reply-api.service` as
`/etc/systemd/system/messages-reply-api.service`, then run
`sudo systemctl enable --now messages-reply-api.service`. The singleton service
shares the protected assistant database with the Telegram workers. Those
workers continue to deliver queued owner questions through `@learnDataBot` and
save the answer to the selected profile YAML under the shared file lock.

### API contract v1

`POST /v1/djinni/replies` accepts `Authorization: Bearer <token>` and JSON:

```json
{
  "profile_id": "lesiuk.alexey@gmail.com",
  "persona_id": "alexey-lesiuk",
  "thread_id": "thread-id",
  "message_id": "message-id",
  "recruiter_name": "Recruiter",
  "vacancy_context": "Backend engineer role",
  "history": [
    {"speaker": "recruiter", "text": "Are you available for an interview?"},
    {"speaker": "candidate", "text": "Yes, what time works?"}
  ],
  "incoming_message": "Could we meet tomorrow at 15:00?"
}
```

History is bounded to 24 recruiter/candidate turns. Repeated profile/thread/
message keys return the saved decision; reusing a key with different content is
rejected. A successful response includes `version`, `outcome` (`reply`,
`owner_attention`, or `no_reply`), `reply_text`,
`learning_question_queued`, and `calendar_status`. Retryable service errors use
HTTP 503 with `retryable: true`; Job Apply retains the message and uses its
existing fallback notification. API logs and audit records do not include
message text. Calendar events are created only after a confirmed agreement and
a successful availability check; no event details are returned to Job Apply.

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
