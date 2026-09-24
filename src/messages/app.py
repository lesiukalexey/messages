from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
from telethon import TelegramClient, events, functions, types, utils

from .calendar import GoogleCalendar
from .config import Settings
from .llm import Responder
from .runtime import load_environment
from .recruiter_answers import RecruiterAnswers
from .store import Store

RUNTIME_ROOT = Path("/home/admin/messages-runtime")
NOTIFICATION_BOT_USERNAME = "@NotificationFastBot"
DEFAULT_STYLE = "Write like a concise, practical, informal Telegram conversation."
MEETING_SIGNAL = re.compile(
    r"(встреч|встрет|пересеч|увид|выйд|заед|прид|кофе|обед|ужин|созвон|звон|"
    r"meet|catch up|coffee|lunch|dinner|interview)",
    re.IGNORECASE,
)
EXPLICIT_CLOCK = re.compile(
    r"(?:\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b|"
    r"\b(?:в|к|на|около)\s*(?:[01]?\d|2[0-3])(?:[-–][0-5]\d)?\b)",
    re.IGNORECASE,
)
REALTOR_MENTION = re.compile(
    r"\b(?:ри[эе]лтор\w*|маклер\w*|агент\s+по\s+(?:недвижим\w*|аренд\w*)|"
    r"realtor\w*|real\s+estate\s+(?:agent|broker))\b",
    re.IGNORECASE,
)
PROPERTY_TERMS = (
    r"(?:квартир\w*|апартамент\w*|жиль\w*|недвижим\w*|дом\w*|"
    r"apartment\w*|flat\w*|house\w*|home\w*|property\w*|housing)"
)
PROPERTY_DEALS = (
    r"(?:аренд\w*|сдач\w*|сдава\w*|сдам|сдаю|сдать|снять|сниму|продаж\w*|"
    r"продат\w*|продаю|продам|куплю|покуп\w*|rent\w*|lease\w*|sell\w*|"
    r"sale\w*|buy\w*|purchase\w*)"
)
PROPERTY_DEAL = re.compile(
    rf"(?:\b{PROPERTY_TERMS}\b.{{0,100}}\b{PROPERTY_DEALS}\b|"
    rf"\b{PROPERTY_DEALS}\b.{{0,100}}\b{PROPERTY_TERMS}\b)",
    re.IGNORECASE,
)
ACKNOWLEDGEMENTS = {
    "ага": "👍", "да": "👍", "давай": "👍", "договорились": "👍",
    "ладно": "👍", "ок": "👍", "окей": "👍", "понял": "👍", "поняла": "👍",
    "принято": "👍", "хорошо": "👍", "ясно": "👍", "спасибо": "🙏",
    "отлично": "🔥", "класс": "🔥", "круто": "🔥", "супер": "🔥",
    "ok": "👍", "okay": "👍", "sure": "👍", "gotit": "👍",
    "thanks": "🙏", "great": "🔥", "awesome": "🔥",
}
REACTION_EMOJIS = {"👍", "🔥", "❤️", "🙏", "😂", "🙂"}


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


TYPO_WORD = re.compile(r"(?<![\w@./:-])[^\W\d_]{3,}(?![\w./:-])", re.UNICODE)
CYRILLIC_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ"


def occasionally_introduce_typo(text: str) -> str:
    """Add a natural-sized typo to about one eligible word in every hundred."""
    result = text
    for match in reversed(list(TYPO_WORD.finditer(text))):
        word = match.group()
        if random.random() >= 0.01:
            continue
        positions = [index for index, character in enumerate(word) if character.isalpha()]
        if len(positions) < 3:
            continue
        position = random.choice(positions)
        if random.random() < 0.5:
            changed = word[:position] + word[position + 1:]
        else:
            character = word[position]
            alphabet = "abcdefghijklmnopqrstuvwxyz" if character.isascii() else CYRILLIC_ALPHABET
            choices = [letter.upper() if character.isupper() else letter for letter in alphabet]
            choices = [letter for letter in choices if letter.casefold() != character.casefold()]
            replacement = random.choice(choices)
            changed = word[:position] + replacement + word[position + 1:]
        result = result[:match.start()] + changed + result[match.end():]
    return result


def acknowledgement_reaction(message: str) -> str | None:
    normalized = re.sub(r"[\s.!?,;:…()]+", "", message.casefold())
    return ACKNOWLEDGEMENTS.get(normalized)


def real_estate_topic(message: str) -> str | None:
    if REALTOR_MENTION.search(message):
        return "realtor_or_real_estate_agent"
    if PROPERTY_DEAL.search(message):
        return "residential_property_rental_or_sale"
    return None


def latest_assistant_asked_question(history: list[dict[str, str]]) -> bool:
    latest_assistant = next(
        (message for message in reversed(history) if message.get("role") == "assistant"),
        None,
    )
    return bool(latest_assistant and "?" in latest_assistant.get("text", ""))


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


class DialogFilterGate:
    """Resolve a Telegram dialog filter from its peers and supported flags."""

    def __init__(self, client: TelegramClient, title: str) -> None:
        self.client = client
        self.title = title
        self.ready = False
        self.loaded = False
        self.folder_id: int | None = None
        self.peer_ids: set[int] = set()
        self.dirty = True
        self.last_check = 0.0
        self.lock = asyncio.Lock()

    def invalidate(self) -> None:
        self.dirty = True

    def contains(self, peer_id: int) -> bool:
        return self.ready and peer_id in self.peer_ids

    async def refresh(self, force: bool = False) -> bool:
        now = asyncio.get_running_loop().time()
        if not force and not self.dirty and now - self.last_check < 30:
            return self.ready
        if not force and not self.ready and now - self.last_check < 5:
            return False
        async with self.lock:
            now = asyncio.get_running_loop().time()
            if not force and not self.dirty and now - self.last_check < 30:
                return self.ready
            if not force and not self.ready and now - self.last_check < 5:
                return False
            try:
                result = await self.client(functions.messages.GetDialogFiltersRequest())
                folder = next(
                    (
                        item for item in result.filters
                        if (getattr(getattr(item, "title", None), "text", "") or "")
                        .strip().casefold() == self.title.casefold()
                    ),
                    None,
                )
                was_loaded = self.loaded
                previous_folder_id = self.folder_id
                previous_peer_ids = self.peer_ids
                peer_ids: set[int] = set()
                if folder is not None:
                    peers = [*getattr(folder, "pinned_peers", []), *getattr(folder, "include_peers", [])]
                    peer_ids.update(utils.get_peer_id(peer) for peer in peers)

                    if getattr(folder, "contacts", False) or getattr(folder, "non_contacts", False):
                        folders_to_scan = [0]
                        if not getattr(folder, "exclude_archived", False):
                            folders_to_scan.append(1)
                        for standard_folder_id in folders_to_scan:
                            async for dialog in self.client.iter_dialogs(folder=standard_folder_id):
                                entity = dialog.entity
                                if not isinstance(entity, types.User):
                                    continue
                                is_contact = bool(getattr(entity, "contact", False))
                                if (is_contact and getattr(folder, "contacts", False)) or (
                                    not is_contact and getattr(folder, "non_contacts", False)
                                ):
                                    peer_ids.add(dialog.id)

                    excluded = {
                        utils.get_peer_id(peer)
                        for peer in getattr(folder, "exclude_peers", [])
                    }
                    peer_ids.difference_update(excluded)
                    self.folder_id = folder.id
                else:
                    self.folder_id = None
                self.peer_ids = peer_ids
                self.ready = True
                self.loaded = True
                self.dirty = False
                if folder is None and (not was_loaded or previous_folder_id is not None):
                    logging.getLogger(__name__).warning(
                        "Telegram dialog filter '%s' was not found",
                        self.title,
                    )
                elif previous_folder_id != self.folder_id or previous_peer_ids != self.peer_ids:
                    logging.getLogger(__name__).info(
                        "Telegram folder '%s' refreshed; %d dialogs included",
                        self.title,
                        len(self.peer_ids),
                    )
            except Exception as exc:
                self.ready = False
                self.dirty = True
                logging.getLogger(__name__).warning(
                    "Could not read Telegram folder '%s'; private replies are paused (%s)",
                    self.title,
                    type(exc).__name__,
                )
            finally:
                self.last_check = now
        return self.ready


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

    def opening(self, account_id: str, peer_id: int, limit: int = 12) -> list[dict[str, str]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT text, outgoing, date FROM messages
                   WHERE account_id = %s AND dialog_id = %s AND text <> ''
                   ORDER BY date ASC LIMIT %s""",
                (account_id, peer_id, limit),
            )
            rows = cursor.fetchall()
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


async def live_chat_history(
    client: TelegramClient, event: events.NewMessage.Event, limit: int = 24
) -> list[dict[str, str]]:
    messages = await client.get_messages(await event.get_input_chat(), limit=limit)
    ordered = list(reversed(messages))
    result: list[dict[str, str]] = []
    for message in ordered:
        if message.id == event.message.id:
            continue
        text = (message.message or "").strip()
        if not text:
            continue
        result.append(
            {
                "role": "assistant" if message.out else "contact",
                "text": text[:4000],
                "time": message.date.isoformat() if message.date else "",
            }
        )
    return result[-23:]


class RecoveredMessageEvent:
    def __init__(self, client: TelegramClient, message: Any) -> None:
        self.client = client
        self.message = message
        self.chat_id = message.chat_id
        self.raw_text = message.raw_text or ""
        self.is_private = True

    async def get_sender(self) -> Any:
        return await self.message.get_sender()

    async def get_input_chat(self) -> Any:
        return await self.message.get_input_chat()

    async def respond(self, text: str) -> Any:
        return await self.client.send_message(self.chat_id, text)


def meeting_context_present(history: list[dict[str, str]], current_message: str) -> bool:
    text = "\n".join([*(item.get("text", "") for item in history[-12:]), current_message])
    return bool(MEETING_SIGNAL.search(text))


def explicit_time_present(history: list[dict[str, str]], current_message: str) -> bool:
    text = "\n".join([*(item.get("text", "") for item in history[-12:]), current_message])
    return bool(EXPLICIT_CLOCK.search(text))


def availability_question(message: str) -> bool:
    return bool(
        re.search(
            r"(?:\b(?:когда|во сколько|коли)\b.{0,60}\b(?:свобод\w*|вільн\w*|удоб\w*|зручн\w*|можеш\w*)"
            r"|\b(?:when|what time)\b.{0,60}\b(?:free|available)\b)",
            message,
            re.IGNORECASE,
        )
    )


def established_availability_date(
    history: list[dict[str, str]], current_message: str, now: datetime
) -> date | None:
    recent = [item.get("text", "") for item in history[-12:]] + [current_message]
    today_words = re.compile(r"\b(?:сегодня|today|this (?:morning|afternoon|evening))\b", re.IGNORECASE)
    tomorrow_words = re.compile(r"\b(?:завтра|tomorrow)\b", re.IGNORECASE)
    for message in reversed(recent):
        if tomorrow_words.search(message):
            return now.date() + timedelta(days=1)
        if today_words.search(message):
            return now.date()
    return None


def safe_availability_reply(
    current_message: str,
    slots: list[str] | None,
    day: date,
    today: date,
) -> str:
    russian = bool(re.search(r"[А-Яа-яЁёІЇЄҐіїєґ]", current_message))
    if russian:
        date_label = (
            "сегодня" if day == today else "завтра"
            if day == today + timedelta(days=1) else day.strftime("%d.%m")
        )
    else:
        date_label = (
            "today" if day == today else "tomorrow"
            if day == today + timedelta(days=1) else day.strftime("%d.%m")
        )
    if slots:
        times = [datetime.fromisoformat(value).strftime("%H:%M") for value in slots]
        if russian:
            if len(times) > 1:
                return f"{date_label.capitalize()} могу в {', '.join(times[:-1])} или {times[-1]}. Какой вариант тебе подходит?"
            return f"{date_label.capitalize()} могу в {times[0]}. Тебе подходит?"
        if len(times) > 1:
            return f"I'm free {date_label} at {', '.join(times[:-1])} or {times[-1]}. Which works for you?"
        return f"I'm free {date_label} at {times[0]}. Does that work for you?"
    if slots == []:
        if russian:
            return f"{date_label.capitalize()} больше нет свободного времени для встречи. Давай посмотрим другой день?"
        return f"I don't have another open time for a meeting {date_label}. Shall we look at another day?"
    if russian:
        return "Не удалось проверить свободное время в календаре. Давай попробуем позже?"
    return "I couldn't check my calendar availability. Could we try again later?"


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
    interrupted_messages = store.recover_interrupted_messages()
    if interrupted_messages:
        logger.info("Recovered %s recent interrupted Telegram messages", len(interrupted_messages))
    history = History()
    calendar = GoogleCalendar(settings.google_token_file, settings.timezone)
    responder = Responder(settings)
    recruiter_answers = RecruiterAnswers(settings.recruiter_answers_file)
    if not settings.codex_binary.is_file():
        logger.warning("Codex CLI is not installed at CODEX_BINARY; replies will fail until installed")
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
    await client.start()
    me = await client.get_me()
    gate = BioGate(client, me.id)
    await gate.refresh(force=True)
    manual_folder = DialogFilterGate(client, "Manual")
    await manual_folder.refresh(force=True)
    auto_folder = DialogFilterGate(client, "Auto")
    await auto_folder.refresh(force=True)

    async def reply_policy_block(peer_id: int, force: bool = True) -> str | None:
        if not await manual_folder.refresh(force=force):
            return "Telegram Manual folder state is unavailable"
        if manual_folder.contains(peer_id):
            return "contact is in Telegram Manual folder"
        if not await auto_folder.refresh(force=force):
            return "Telegram Auto folder state is unavailable"
        await gate.refresh(force=force)
        if gate.error:
            return "global bio switch is unreadable"
        if not gate.enabled and not auto_folder.contains(peer_id):
            return "global bio switch is off and contact is not in Telegram Auto folder"
        return None
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
                "/category @username friends|recruiters|realtors — override auto classification\n"
                "/category remove @username — clear manual assignment\n"
                "/contacts [friends|recruiters|realtors] — list assigned chats\n"
                "/dialogs [page] — list exported chats and current categories\n"
                "New chats become recruiters for hiring, realtors for property rentals/sales, or friends otherwise.\n"
                "Chats in the Telegram folder 'Manual' are ignored.\n"
                "Chats in folder 'Auto' can receive replies even when bio is `free`, except realtors.\n"
                "Realtors and real estate rental/sale conversations never receive automatic replies.\n"
                "Edit your Telegram bio to toggle: `free` = OFF for other chats; empty/other = ON."
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
                return f"Cleared manual category for {entity.username or entity.id}; the next message will be classified automatically."
            if len(parts) != 3 or parts[2].casefold() not in ("friends", "recruiters", "realtors"):
                return "Use /category @username friends, recruiters, or realtors to override automatic classification."
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
            return f"{entity.username or entity.id} manually assigned to {category}."
        if command == "/contacts":
            category = parts[1].casefold() if len(parts) > 1 else None
            if category not in (None, "friends", "recruiters", "realtors"):
                return "Use /contacts, /contacts friends, /contacts recruiters, or /contacts realtors."
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
                category = store.contact_category(row["dialog_id"]) or "not yet classified"
                name = row["name"] or row["username"] or str(row["dialog_id"])
                identity = f"@{row['username']}" if row["username"] else f"id:{row['dialog_id']}"
                lines.append(f"• {name} ({identity}) — {category}")
            return f"Chats, page {page}:\n" + "\n".join(lines)
        return "Unknown command. Send /help."

    async def contacts_text(category: str | None, db: Store) -> str:
        rows = db.contacts(category)
        if not rows:
            return "No contacts classified yet. New chats are classified automatically; use /category to override."
        grouped: dict[str, list[str]] = {
            "friends": [], "recruiters": [], "realtors": []
        }
        for row in rows:
            grouped[row["category"]].append(_format_contact(row))
        blocks = []
        for name in ((category,) if category else ("friends", "recruiters", "realtors")):
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
        if isinstance(update, (types.UpdateDialogFilter, types.UpdateDialogFilters)):
            manual_folder.invalidate()
            await manual_folder.refresh(force=True)
            auto_folder.invalidate()
            await auto_folder.refresh(force=True)

    async def notify_conversation_started(
        peer_id: int, sender: types.User, category: str, session_started_at: str
    ) -> None:
        if not store.claim_conversation_notification(
            settings.account_id, peer_id, session_started_at
        ):
            return
        display_name = " ".join(
            part for part in (sender.first_name, sender.last_name) if part
        ).strip() or sender.username or f"Telegram user {peer_id}"
        username = f" (@{sender.username})" if sender.username else ""
        category_label = "рекрутер" if category == "recruiters" else "друг"
        notification = (
            f"ИИ начал новый диалог: {display_name}{username} "
            f"(категория: {category_label})."
        )
        try:
            await client.send_message(NOTIFICATION_BOT_USERNAME, notification)
        except Exception as notification_error:
            store.finish_conversation_notification(
                settings.account_id, peer_id, session_started_at, success=False
            )
            store.audit(
                peer_id,
                "conversation_notification_failed",
                type(notification_error).__name__,
            )
            logger.warning(
                "Could not notify %s for a new conversation (%s)",
                NOTIFICATION_BOT_USERNAME,
                type(notification_error).__name__,
            )
        else:
            store.finish_conversation_notification(
                settings.account_id, peer_id, session_started_at, success=True
            )
            store.audit(
                peer_id,
                "conversation_notification_sent",
                NOTIFICATION_BOT_USERNAME,
            )

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event: events.NewMessage.Event) -> None:
        peer_id = event.chat_id
        if event.is_private:
            try:
                await client.send_read_acknowledge(
                    await event.get_input_chat(), max_id=event.message.id
                )
            except Exception as exc:
                logger.warning(
                    "Could not mark incoming private message as read (%s)",
                    type(exc).__name__,
                )
        if not event.is_private or not event.raw_text.strip():
            return
        sender = await event.get_sender()
        if not isinstance(sender, types.User) or sender.bot or sender.deleted or sender.is_self:
            return
        if not store.claim_message(settings.account_id, peer_id, event.message.id):
            return
        message_date = event.message.date
        if message_date and datetime.now(UTC) - message_date > timedelta(minutes=30):
            store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
            store.audit(peer_id, "skipped", "incoming message is older than 30 minutes")
            return
        current_category = store.contact_category(peer_id)
        exclusion_reason = real_estate_topic(event.raw_text)
        if current_category == "realtors" or exclusion_reason:
            if exclusion_reason and current_category != "realtors":
                display_name = " ".join(
                    part for part in (sender.first_name, sender.last_name) if part
                ).strip()
                store.set_contact_category(
                    peer_id,
                    "realtors",
                    sender.username or "",
                    display_name,
                    source="automatic",
                )
                store.audit(peer_id, "contact_auto_categorized", "realtors")
            store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
            store.audit(
                peer_id,
                "skipped",
                "automatic messages are disabled for realtor contacts",
            )
            return
        block_reason = await reply_policy_block(peer_id, force=True)
        if block_reason:
            store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
            store.audit(peer_id, "skipped", block_reason)
            return
        category = store.contact_category(peer_id)
        category_source = store.contact_category_source(peer_id)
        auto_detect_category = category is None or category_source == "automatic"
        category = category or "friends"

        async with locks.setdefault(peer_id, asyncio.Lock()):
            try:
                block_reason = await reply_policy_block(peer_id, force=True)
                if block_reason:
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "skipped", block_reason)
                    return
                session_started_at = store.record_incoming_session(
                    settings.account_id, peer_id, event.message.date
                )
                try:
                    context = await live_chat_history(client, event)
                except Exception as exc:
                    logger.warning(
                        "Could not load recent Telegram context; using exported history (%s)",
                        type(exc).__name__,
                    )
                    context = history.latest(settings.account_id, peer_id)
                opening = history.opening(settings.account_id, peer_id)
                pending_duration = store.pending_calendar_duration(settings.account_id, peer_id)
                pending_meeting_context = (
                    {
                        "start_at": pending_duration["start_at"],
                        "provisional_duration_minutes": pending_duration[
                            "provisional_duration_minutes"
                        ],
                    }
                    if pending_duration else None
                )
                now = datetime.now(ZoneInfo(settings.timezone))
                model = store.setting("model", settings.default_model)
                prepared_answers = (
                    recruiter_answers.match(event.raw_text) if category == "recruiters" else []
                )
                plan = await responder.plan(
                    model=model,
                    category=category,
                    history=context,
                    current_message=event.raw_text,
                    now=now,
                    style_profile=style_profile(),
                    opening_history=opening,
                    auto_detect_category=auto_detect_category,
                    prepared_answers=prepared_answers,
                    pending_meeting_duration=pending_meeting_context,
                )
                detected_category = plan.pop("detected_category", category)
                if auto_detect_category and detected_category == "realtors":
                    display_name = " ".join(
                        part for part in (sender.first_name, sender.last_name) if part
                    ).strip()
                    store.set_contact_category(
                        peer_id,
                        "realtors",
                        sender.username or "",
                        display_name,
                        source="automatic",
                    )
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "contact_auto_categorized", "realtors")
                    store.audit(
                        peer_id,
                        "skipped",
                        "automatic messages are disabled for realtor contacts",
                    )
                    return
                if auto_detect_category:
                    resolved_category = (
                        "recruiters"
                        if category == "recruiters" or detected_category == "recruiters"
                        else "friends"
                    )
                    if store.contact_category(peer_id) != resolved_category:
                        display_name = " ".join(
                            part for part in (sender.first_name, sender.last_name) if part
                        )
                        store.set_contact_category(
                            peer_id,
                            resolved_category,
                            sender.username or "",
                            display_name,
                            source="automatic",
                        )
                        store.audit(peer_id, "contact_auto_categorized", resolved_category)
                    if resolved_category != category:
                        category = resolved_category
                        prepared_answers = (
                            recruiter_answers.match(event.raw_text)
                            if category == "recruiters" else []
                        )
                        plan = await responder.plan(
                            model=model,
                            category=category,
                            history=context,
                            current_message=event.raw_text,
                            now=now,
                            style_profile=style_profile(),
                            opening_history=opening,
                            auto_detect_category=False,
                            prepared_answers=prepared_answers,
                            pending_meeting_duration=pending_meeting_context,
                        )
                plan.pop("detected_category", None)
                acknowledgement = acknowledgement_reaction(event.raw_text)
                if acknowledgement and not latest_assistant_asked_question(context):
                    plan["should_reply"] = False
                    plan["should_react"] = True
                    plan["reaction_emoji"] = acknowledgement
                duration_followup_reply: str | None = None
                duration_update_succeeded = False
                pending_duration = store.pending_calendar_duration(settings.account_id, peer_id)
                if pending_duration and plan.get("duration_stated"):
                    requested_duration = int(plan.get("duration_minutes") or 0)
                    previous_duration = int(pending_duration["provisional_duration_minutes"])
                    conflict_at: str | None = None
                    if not 5 <= requested_duration <= 720:
                        calendar_result = (
                            "DURATION_INVALID; the requested duration is outside 5 minutes to 12 "
                            "hours; leave the existing event unchanged and ask for a valid length."
                        )
                    elif not calendar.configured:
                        calendar_result = (
                            "DURATION_CHECK_FAILED; calendar access is unavailable; leave the "
                            "existing event unchanged."
                        )
                    else:
                        block_reason = await reply_policy_block(peer_id, force=True)
                        if block_reason:
                            store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                            store.audit(
                                peer_id,
                                "skipped",
                                f"{block_reason} before calendar duration update",
                            )
                            return
                        try:
                            duration_update_succeeded, conflict_at = await asyncio.to_thread(
                                calendar.update_duration,
                                pending_duration["event_id"],
                                pending_duration["start_at"],
                                requested_duration,
                            )
                        except Exception as exc:
                            store.audit(
                                peer_id,
                                "calendar_event_duration_update_failed",
                                type(exc).__name__,
                            )
                            calendar_result = (
                                "DURATION_CHECK_FAILED; calendar access failed; leave the existing "
                                "event unchanged."
                            )
                        else:
                            if duration_update_succeeded:
                                store.clear_pending_calendar_duration(settings.account_id, peer_id)
                                store.audit(
                                    peer_id,
                                    "calendar_event_duration_updated",
                                    f"{previous_duration}->{requested_duration} minutes",
                                )
                                calendar_result = (
                                    f"DURATION_UPDATED; changed the existing event from "
                                    f"{previous_duration} to {requested_duration} minutes."
                                )
                            elif conflict_at:
                                conflict_time = datetime.fromisoformat(
                                    conflict_at
                                ).astimezone(ZoneInfo(settings.timezone))
                                store.audit(
                                    peer_id,
                                    "calendar_event_duration_conflict",
                                    f"requested={requested_duration}; conflict_at={conflict_time.isoformat()}",
                                )
                                calendar_result = (
                                    f"DURATION_CONFLICT; the longer duration overlaps another "
                                    f"calendar event starting at {conflict_time.isoformat()}; "
                                    f"leave the current {previous_duration}-minute event unchanged."
                                )
                            else:
                                calendar_result = (
                                    "DURATION_CHECK_FAILED; the calendar returned no conflict time; "
                                    "leave the existing event unchanged."
                                )
                    duration_followup_reply = await responder.compose_with_calendar_result(
                        model=model,
                        category=category,
                        history=context,
                        current_message=event.raw_text,
                        plan=plan,
                        calendar_result=calendar_result,
                        now=now,
                        style_profile=style_profile(),
                        prepared_answers=prepared_answers,
                    )
                start = None if duration_followup_reply is not None else plan.get("start")
                action = (
                    "duration_update"
                    if duration_followup_reply is not None
                    else plan.get("calendar_action", "none")
                )
                if pending_duration and duration_followup_reply is None:
                    start = None
                    action = "none"
                meeting_in_progress = bool(plan.get("meeting_in_progress")) or meeting_context_present(
                    context, event.raw_text
                )
                assistant_accepts = bool(plan.get("assistant_accepts_meeting"))
                if start and (
                    meeting_in_progress
                    or action in ("check", "create")
                    or plan.get("confirmed_agreement")
                    or assistant_accepts
                ):
                    action = (
                        "create"
                        if plan.get("confirmed_agreement") or assistant_accepts
                        else "check"
                    )
                if action in ("check", "create") and not meeting_in_progress:
                    action = "none"
                    store.audit(peer_id, "calendar_action_suppressed", "no clear meeting context")
                calendar_result = "No calendar action is needed."
                availability_reply: str | None = None
                target_day = (
                    established_availability_date(context, event.raw_text, now)
                    if availability_question(event.raw_text)
                    else None
                )
                if target_day is not None:
                    action = "none"
                    duration = int(
                        plan.get("duration_minutes") or (60 if category == "friends" else 30)
                    )
                    if not calendar.configured:
                        availability_reply = safe_availability_reply(
                            event.raw_text, None, target_day, now.date()
                        )
                        store.audit(peer_id, "calendar_availability_failed", "authorization missing")
                    else:
                        try:
                            slots = await asyncio.to_thread(
                                calendar.available_slots,
                                target_day,
                                duration,
                                now,
                            )
                            availability_reply = safe_availability_reply(
                                event.raw_text, slots, target_day, now.date()
                            )
                            store.audit(
                                peer_id,
                                "calendar_availability_checked",
                                f"suggested_slot_count={len(slots)}",
                            )
                        except Exception as exc:
                            logger.warning(
                                "Calendar slot search failed: %s", type(exc).__name__
                            )
                            availability_reply = safe_availability_reply(
                                event.raw_text, None, target_day, now.date()
                            )
                            store.audit(
                                peer_id,
                                "calendar_availability_failed",
                                type(exc).__name__,
                            )
                if (
                    target_day is None
                    and meeting_in_progress
                    and explicit_time_present(context, event.raw_text)
                    and not start
                ):
                    action = "check"
                    calendar_result = (
                        "TIME_UNRESOLVED; no availability was checked and no time was confirmed. "
                        "Ask only for the missing date or time based on the recent conversation."
                    )
                    store.audit(peer_id, "calendar_availability_unknown", "could not resolve requested time")
                if action in ("check", "create"):
                    duration = int(plan.get("duration_minutes") or 0)
                    if not start or duration <= 0:
                        calendar_result = (
                            "TIME_UNRESOLVED; availability is unknown and no time was confirmed. "
                            "Ask only for the missing date or time based on recent context."
                        )
                    elif not calendar.configured:
                        calendar_result = (
                            "CALENDAR_UNAVAILABLE; availability could not be checked, so do not "
                            "confirm the proposed time."
                        )
                        store.audit(peer_id, "calendar_availability_failed", "authorization missing")
                    else:
                        try:
                            is_free, interval = await asyncio.to_thread(
                                calendar.check, start, duration
                            )
                        except Exception as exc:
                            logger.warning(
                                "Calendar availability check failed: %s", type(exc).__name__
                            )
                            calendar_result = (
                                "AVAILABILITY_UNKNOWN; do not claim the proposed time is free "
                                "or confirmed."
                            )
                            store.audit(
                                peer_id,
                                "calendar_availability_failed",
                                type(exc).__name__,
                            )
                            is_free, interval = False, ""
                        if calendar_result.startswith("AVAILABILITY_UNKNOWN"):
                            pass
                        elif not is_free:
                            calendar_result = "BUSY; the proposed time is unavailable and no event was created."
                            store.audit(peer_id, "calendar_availability_checked", "busy")
                        elif action == "create":
                            block_reason = await reply_policy_block(peer_id, force=True)
                            if block_reason:
                                store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                                store.audit(peer_id, "skipped", f"{block_reason} before calendar write")
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
                                    contact_name=(
                                        " ".join(
                                            part for part in (sender.first_name, sender.last_name)
                                            if part
                                        ).strip()
                                        or f"Telegram user {peer_id}"
                                    ),
                                    telegram_username=sender.username or "",
                                )
                                store.record_calendar_event(
                                    settings.account_id, peer_id, event.message.id, event_id
                                )
                            if not plan.get("duration_stated"):
                                store.set_pending_calendar_duration(
                                    settings.account_id,
                                    peer_id,
                                    event_id,
                                    start,
                                    duration,
                                )
                                calendar_result = (
                                    "DURATION_PENDING_ASK; calendar event successfully created "
                                    f"with a provisional duration of {duration} minutes because "
                                    "the contact did not state a duration. Ask how long the "
                                    "meeting should be."
                                )
                                store.audit(
                                    peer_id,
                                    "calendar_duration_followup_requested",
                                    f"provisional_duration={duration} minutes",
                                )
                            else:
                                calendar_result = "FREE; calendar event successfully created."
                            store.audit(peer_id, "calendar_event_created", event_id)
                        else:
                            calendar_result = f"FREE at {interval}; no event created yet."
                            store.audit(peer_id, "calendar_availability_checked", "free")
                text_reply_required = (
                    duration_followup_reply is not None
                    or availability_reply is not None
                    or action in ("check", "duration_update")
                    or (
                        action == "create"
                        and calendar_result != "FREE; calendar event successfully created."
                    )
                )
                if not plan.get("should_reply", True) and not text_reply_required:
                    if not plan.get("should_react"):
                        store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                        store.audit(peer_id, "skipped", "responder found no safe contextual reply")
                        return
                    block_reason = await reply_policy_block(peer_id, force=True)
                    if block_reason:
                        store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                        store.audit(peer_id, "skipped", f"{block_reason} before reaction")
                        return
                    emoji = plan.get("reaction_emoji")
                    if emoji not in REACTION_EMOJIS:
                        emoji = "👍"
                    await client(functions.messages.SendReactionRequest(
                        peer=await event.get_input_chat(),
                        msg_id=event.message.id,
                        reaction=[types.ReactionEmoji(emoticon=emoji)],
                    ))
                    store.message_state(settings.account_id, peer_id, event.message.id, "sent")
                    store.audit(
                        peer_id,
                        "reacted",
                        json.dumps(
                            {"incoming_message_id": event.message.id, "emoji": emoji},
                            ensure_ascii=False,
                        ),
                    )
                    await notify_conversation_started(
                        peer_id, sender, category, session_started_at
                    )
                    return
                if duration_followup_reply is not None:
                    reply = duration_followup_reply
                elif availability_reply is not None:
                    reply = availability_reply
                elif action in ("check", "create"):
                    reply = await responder.compose_with_calendar_result(
                        model=model,
                        category=category,
                        history=context,
                        current_message=event.raw_text,
                        plan=plan,
                        calendar_result=calendar_result,
                        now=now,
                        style_profile=style_profile(),
                        prepared_answers=prepared_answers,
                    )
                else:
                    reply = plan["reply"]
                reply = occasionally_introduce_typo(reply.strip())
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
                letter_count = sum(character.isalpha() for character in reply)
                delay_seconds = max(1.0, letter_count / 10.0)
                if random.random() < 0.3:
                    delay_seconds += random.uniform(1.0, 10.0)
                await asyncio.sleep(delay_seconds)
                block_reason = await reply_policy_block(peer_id, force=True)
                if block_reason:
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "skipped", f"{block_reason} before send")
                    return
                sent = await event.respond(reply)
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
                await notify_conversation_started(
                    peer_id, sender, category, session_started_at
                )
            except Exception as exc:
                store.message_state(settings.account_id, peer_id, event.message.id, "failed")
                store.audit(
                    peer_id,
                    "failed",
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                )
                logger.exception("Could not answer incoming Telegram message")

    await client.catch_up()
    for peer_id, message_id in interrupted_messages:
        try:
            message = await client.get_messages(peer_id, ids=message_id)
            if message is None or message.out or not (message.raw_text or "").strip():
                store.message_state(settings.account_id, peer_id, message_id, "failed")
                store.audit(peer_id, "interrupted_message_unavailable", f"message_id={message_id}")
                continue
            await on_message(RecoveredMessageEvent(client, message))
        except Exception as exc:
            store.message_state(settings.account_id, peer_id, message_id, "failed")
            store.audit(
                peer_id,
                "interrupted_message_recovery_failed",
                f"message_id={message_id}; {type(exc).__name__}",
            )
            logger.exception("Could not recover interrupted Telegram message %s", message_id)

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
        await client.disconnect()


async def contacts_text(category: str | None, store: Store) -> str:
    rows = store.contacts(category)
    if not rows:
        return "No contacts classified yet. New chats are classified automatically; use /category to override."
    grouped: dict[str, list[str]] = {"friends": [], "recruiters": [], "realtors": []}
    for row in rows:
        name = row["display_name"] or row["username"] or str(row["peer_id"])
        suffix = f" (@{row['username']})" if row["username"] else ""
        grouped[row["category"]].append(f"• {name}{suffix}")
    categories = (category,) if category else ("friends", "recruiters", "realtors")
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
