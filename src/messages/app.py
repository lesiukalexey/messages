from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
from telethon import TelegramClient, events, functions, types

from .calendar import GoogleCalendar
from .config import Settings
from .llm import Responder
from .runtime import load_environment
from .store import Store

RUNTIME_ROOT = Path("/home/admin/messages-runtime")
DEFAULT_STYLE = "Write like a concise, practical, informal Telegram conversation."


def style_profile() -> str:
    style_path = Path(
        os.getenv(
            "STYLE_PROFILE_FILE", str(RUNTIME_ROOT / "profiles" / "communication-style.md")
        )
    )
    personality_path = Path(
        os.getenv(
            "PERSONALITY_PROFILE_FILE", str(RUNTIME_ROOT / "profiles" / "personality-profile.md")
        )
    )
    profiles = [path.read_text() for path in (personality_path, style_path) if path.is_file()]
    return "\n\n".join(profiles)[:12000] or DEFAULT_STYLE


class BioGate:
    def __init__(self, client: TelegramClient, me_id: int) -> None:
        self.client = client
        self.me_id = me_id
        self.enabled = False
        self.bio = ""
        self.error = "profile has not been read"
        self.dirty = True
        self.last_check = 0.0
        self.lock = asyncio.Lock()

    def invalidate(self) -> None:
        self.dirty = True

    async def refresh(self, force: bool = False) -> bool:
        now = asyncio.get_running_loop().time()
        if not force and not self.dirty and now - self.last_check < 5:
            return self.enabled
        async with self.lock:
            now = asyncio.get_running_loop().time()
            if not force and not self.dirty and now - self.last_check < 5:
                return self.enabled
            try:
                myself = await self.client.get_input_entity("me")
                full = await self.client(functions.users.GetFullUserRequest(id=myself))
                self.bio = (full.full_user.about or "").strip()
                self.enabled = self.bio.casefold() != "free"
                self.error = ""
                self.dirty = False
            except Exception as exc:
                # An unreadable bio never leaves the previous ON state active.
                self.enabled = False
                self.error = type(exc).__name__
                self.dirty = True
                logging.getLogger(__name__).warning("Could not read Telegram bio; assistant is off")
            finally:
                self.last_check = now
        return self.enabled


class History:
    def __init__(self) -> None:
        self.connection = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.getenv("MYSQL_DATABASE", "messages"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def latest(self, account_id: str, peer_id: int, limit: int = 23) -> list[dict[str, str]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT text, outgoing, date FROM messages
                   WHERE account_id = %s AND dialog_id = %s AND text <> ''
                   ORDER BY date DESC LIMIT %s""",
                (account_id, peer_id, limit),
            )
            rows = cursor.fetchall()
        rows.reverse()
        return [
            {
                "role": "assistant" if row["outgoing"] else "contact",
                "text": row["text"][:4000],
                "time": str(row["date"] or ""),
            }
            for row in rows
        ]

    def dialogs(self, account_id: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT dialog_id, name, username FROM dialogs
                   WHERE account_id = %s AND kind = 'user'
                   ORDER BY name LIMIT %s OFFSET %s""",
                (account_id, limit, offset),
            )
            return list(cursor.fetchall())

    def close(self) -> None:
        self.connection.close()


def _format_contact(row: Any) -> str:
    name = row["display_name"] or row["username"] or str(row["peer_id"])
    suffix = f" (@{row['username']})" if row["username"] else ""
    return f"• {name}{suffix}"


async def run() -> None:
    load_environment()
    settings = Settings.from_environment()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger(__name__)
    settings.session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.session_path.parent.chmod(0o700)
    store = Store(settings.database_path, settings.account_id)
    store.initialize()
    history = History()
    calendar = GoogleCalendar(settings.google_token_file, settings.timezone)
    responder = Responder(settings.openai_api_key, settings.timezone) if settings.openai_api_key else None
    if responder is None:
        logger.warning("OPENAI_API_KEY is not configured; replies will be skipped")
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
    await client.start()
    me = await client.get_me()
    gate = BioGate(client, me.id)
    await gate.refresh(force=True)
    locks: dict[int, asyncio.Lock] = {}

    async def send_control(text: str) -> None:
        await client.send_message("me", text)

    async def handle_control(text: str) -> str:
        parts = text.strip().split()
        command = parts[0].split("@", 1)[0].casefold()
        if command in ("/start", "/help", "/settings"):
            enabled = await gate.refresh(force=True)
            model = store.setting("model", settings.default_model)
            state = "ON" if enabled else "OFF"
            cal = "ready" if calendar.configured else "not authorized"
            return (
                f"Assistant: {state} (bio: {gate.bio or '[empty]'})\n"
                f"Model: {model}\nCalendar: {cal}\n\n"
                "Commands (send in Saved Messages):\n"
                "/model — show or choose a model\n"
                "/model MODEL_ID — switch model\n"
                "/category @username friends|recruiters — assign a chat\n"
                "/category remove @username — exclude a chat\n"
                "/contacts [friends|recruiters] — list assigned chats\n"
                "/dialogs [page] — list exported chats to categorize\n"
                "Edit your Telegram bio to toggle: `free` = OFF; empty/other = ON."
            )
        if command == "/model":
            if len(parts) == 1:
                current = store.setting("model", settings.default_model)
                choices = "\n".join(f"• {name}" for name in settings.model_options)
                return f"Current model: {current}\nChoose with /model MODEL_ID\n{choices}"
            if len(parts) != 2:
                return "Use /model or /model MODEL_ID."
            model = parts[1]
            if model not in settings.model_options:
                return "Unknown model. Send /model to see configured choices."
            store.set_setting("model", model)
            store.audit(None, "model_changed", model)
            return f"Conversation model set to {model}."
        if command == "/category":
            if len(parts) == 2 and parts[1].casefold() == "list":
                return await contacts_text(None, store)
            if len(parts) == 3 and parts[1].casefold() == "remove":
                identifier = parts[2]
                try:
                    entity = await resolve_user(client, identifier)
                except Exception:
                    return "I could not find that Telegram user. Use their @username."
                if not isinstance(entity, types.User) or entity.bot or entity.is_self:
                    return "Only real individual Telegram users can be categorized."
                store.set_contact_category(entity.id, None, entity.username or "", entity.first_name or "")
                store.audit(entity.id, "contact_uncategorized", "")
                return f"Removed category for {entity.username or entity.id}; assistant will ignore this chat."
            if len(parts) != 3 or parts[2].casefold() not in ("friends", "recruiters"):
                return "Use /category @username friends or /category @username recruiters."
            identifier, category = parts[1], parts[2].casefold()
            try:
                entity = await resolve_user(client, identifier)
            except Exception:
                return "I could not find that Telegram user. Use their @username."
            if not isinstance(entity, types.User) or entity.bot or entity.deleted or entity.is_self:
                return "Only real individual Telegram users can be categorized."
            store.set_contact_category(
                entity.id,
                category,
                entity.username or "",
                " ".join(part for part in (entity.first_name, entity.last_name) if part) or "",
            )
            store.audit(entity.id, "contact_categorized", category)
            return f"{entity.username or entity.id} assigned to {category}."
        if command == "/contacts":
            category = parts[1].casefold() if len(parts) > 1 else None
            if category not in (None, "friends", "recruiters"):
                return "Use /contacts, /contacts friends, or /contacts recruiters."
            return await contacts_text(category, store)
        if command == "/dialogs":
            try:
                page = max(1, int(parts[1])) if len(parts) > 1 else 1
            except ValueError:
                return "Use /dialogs or /dialogs PAGE."
            rows = history.dialogs(settings.account_id, 25, (page - 1) * 25)
            if not rows:
                return "No more exported one-to-one chats. Run messages-export to refresh the list."
            lines = []
            for row in rows:
                category = store.contact_category(row["dialog_id"]) or "unassigned"
                name = row["name"] or row["username"] or str(row["dialog_id"])
                identity = f"@{row['username']}" if row["username"] else f"id:{row['dialog_id']}"
                lines.append(f"• {name} ({identity}) — {category}")
            return f"Chats, page {page}:\n" + "\n".join(lines)
        return "Unknown command. Send /help."

    async def contacts_text(category: str | None, db: Store) -> str:
        rows = db.contacts(category)
        if not rows:
            return "No categorized contacts yet. Use /category @username friends|recruiters."
        grouped: dict[str, list[str]] = {"friends": [], "recruiters": []}
        for row in rows:
            grouped[row["category"]].append(_format_contact(row))
        blocks = []
        for name in ((category,) if category else ("friends", "recruiters")):
            blocks.append(f"{name.title()} ({len(grouped[name])}):\n" + "\n".join(grouped[name]))
        return "\n\n".join(blocks)

    @client.on(events.NewMessage(outgoing=True))
    async def on_control_message(event: events.NewMessage.Event) -> None:
        if not event.is_private or event.chat_id != me.id or not event.raw_text.startswith("/"):
            return
        try:
            result = await handle_control(event.raw_text)
        except Exception:
            logger.exception("Saved Messages control command failed")
            result = "Command failed. Check the assistant log."
        await send_control(result)

    @client.on(events.Raw)
    async def on_profile_update(update: Any) -> None:
        if isinstance(update, types.UpdateUser) and update.user_id == me.id:
            gate.invalidate()
            await gate.refresh(force=True)
            logger.info("Telegram bio changed; assistant is %s", "on" if gate.enabled else "off")

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event: events.NewMessage.Event) -> None:
        peer_id = event.chat_id
        if not event.is_private or not event.raw_text.strip():
            return
        if not await gate.refresh():
            store.audit(peer_id, "skipped", "global bio switch is off or unreadable")
            return
        category = store.contact_category(peer_id)
        if category not in ("friends", "recruiters"):
            store.audit(peer_id, "skipped", "contact is not categorized")
            return
        if responder is None:
            store.audit(peer_id, "failed", "OPENAI_API_KEY is not configured")
            return
        if not store.claim_message(settings.account_id, peer_id, event.message.id):
            return

        async with locks.setdefault(peer_id, asyncio.Lock()):
            try:
                if not await gate.refresh(force=True):
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "skipped", "global bio switch is off or unreadable")
                    return
                context = history.latest(settings.account_id, peer_id)
                now = datetime.now(ZoneInfo(settings.timezone))
                model = store.setting("model", settings.default_model)
                plan = await responder.plan(
                    model=model,
                    category=category,
                    history=context,
                    current_message=event.raw_text,
                    now=now,
                    style_profile=style_profile(),
                )
                action = plan.get("calendar_action", "none")
                calendar_result = "No calendar action is needed."
                if action in ("check", "create"):
                    start = plan.get("start")
                    duration = int(plan.get("duration_minutes") or 0)
                    if not start or duration <= 0:
                        calendar_result = "The requested time is unclear; ask a follow-up."
                    elif not calendar.configured:
                        calendar_result = "Calendar is not authorized; availability is unknown."
                    else:
                        try:
                            is_free, interval = await asyncio.to_thread(
                                calendar.check, start, duration
                            )
                        except Exception as exc:
                            logger.warning(
                                "Calendar availability check failed: %s", type(exc).__name__
                            )
                            calendar_result = "Availability is unknown; do not claim the time is free."
                            store.audit(
                                peer_id,
                                "calendar_availability_failed",
                                type(exc).__name__,
                            )
                            is_free, interval = False, ""
                        if calendar_result.startswith("Availability is unknown"):
                            pass
                        elif not is_free:
                            calendar_result = "BUSY; no event was created."
                            store.audit(peer_id, "calendar_availability_checked", "busy")
                        elif action == "create" and plan.get("confirmed_agreement"):
                            if not await gate.refresh(force=True):
                                store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                                store.audit(peer_id, "skipped", "assistant switched off before calendar write")
                                return
                            existing = store.calendar_event_exists(
                                settings.account_id, peer_id, event.message.id
                            )
                            if existing:
                                event_id = existing
                            else:
                                event_id = await asyncio.to_thread(
                                    calendar.create,
                                    settings.account_id,
                                    peer_id,
                                    event.message.id,
                                    start,
                                    duration,
                                    str(plan.get("title") or "Meeting"),
                                    plan.get("location"),
                                )
                                store.record_calendar_event(
                                    settings.account_id, peer_id, event.message.id, event_id
                                )
                            calendar_result = "FREE; calendar event successfully created."
                            store.audit(peer_id, "calendar_event_created", event_id)
                        else:
                            calendar_result = f"FREE at {interval}; no event created yet."
                            store.audit(peer_id, "calendar_availability_checked", "free")
                if action in ("check", "create"):
                    reply = await responder.compose_with_calendar_result(
                        model=model,
                        category=category,
                        history=context,
                        current_message=event.raw_text,
                        plan=plan,
                        calendar_result=calendar_result,
                        now=now,
                    )
                else:
                    reply = plan["reply"]
                reply = reply.strip()
                if not reply:
                    raise RuntimeError("model returned an empty reply")
                store.audit(
                    peer_id,
                    "generated",
                    json.dumps(
                        {
                            "incoming_message_id": event.message.id,
                            "category": category,
                            "model": model,
                            "calendar_action": action,
                        },
                        ensure_ascii=False,
                    ),
                )
                if not await gate.refresh(force=True):
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "skipped", "assistant switched off before send")
                    return
                sent = await event.respond(reply, reply_to=event.message.id)
                store.message_state(settings.account_id, peer_id, event.message.id, "sent")
                store.audit(
                    peer_id,
                    "sent",
                    json.dumps(
                        {
                            "incoming_message_id": event.message.id,
                            "sent_message_id": sent.id,
                            "category": category,
                            "model": model,
                            "calendar_action": action,
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as exc:
                store.message_state(settings.account_id, peer_id, event.message.id, "failed")
                store.audit(
                    peer_id,
                    "failed",
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                )
                logger.exception("Could not answer incoming Telegram message")

    poller: asyncio.Task[None] | None = None
    try:
        async def poll_bio() -> None:
            while True:
                await asyncio.sleep(15)
                await gate.refresh(force=True)

        poller = asyncio.create_task(poll_bio())
        logger.info(
            "Telegram assistant started for account %s; bio switch is %s",
            settings.account_id,
            "on" if gate.enabled else "off",
        )
        await client.run_until_disconnected()
    finally:
        if poller:
            poller.cancel()
            try:
                await poller
            except asyncio.CancelledError:
                pass
        history.close()
        store.close()
        if responder:
            await responder.close()
        await client.disconnect()


async def contacts_text(category: str | None, store: Store) -> str:
    rows = store.contacts(category)
    if not rows:
        return "No categorized contacts yet. Use /category @username friends|recruiters."
    grouped: dict[str, list[str]] = {"friends": [], "recruiters": []}
    for row in rows:
        name = row["display_name"] or row["username"] or str(row["peer_id"])
        suffix = f" (@{row['username']})" if row["username"] else ""
        grouped[row["category"]].append(f"• {name}{suffix}")
    categories = (category,) if category else ("friends", "recruiters")
    return "\n\n".join(
        f"{name.title()} ({len(grouped[name])}):\n" + "\n".join(grouped[name])
        for name in categories
    )


def main() -> None:
    asyncio.run(run())


async def resolve_user(client: TelegramClient, identifier: str) -> Any:
    if identifier.isdecimal():
        peer_id = int(identifier)
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            if getattr(dialog.entity, "id", None) == peer_id:
                return dialog.entity
        raise ValueError("Telegram user is not in the account's dialogs")
    target: Any = identifier
    return await client.get_entity(target)
