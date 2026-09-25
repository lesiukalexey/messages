from __future__ import annotations

import asyncio
from copy import copy
from difflib import SequenceMatcher
import json
import logging
import os
import random
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
from telethon import TelegramClient, events, functions, types, utils

from .calendar import GoogleCalendar
from .config import Settings
from .llm import Responder
from .learning import save_learned_answer
from .runtime import load_environment
from .recruiter_answers import RecruiterAnswers
from .store import Store
from .web_search import search_web

RUNTIME_ROOT = Path("/home/admin/messages-runtime")
NOTIFICATION_BOT_USERNAME = "@NotificationFastBot"
LEARNING_CHANNEL_USERNAME = "@learnDataBot"
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


def resolve_automatic_category(current: str, detected: str) -> str:
    """Keep a known category while letting an unknown contact become known."""
    if current == "recruiters" or detected == "recruiters":
        return "recruiters"
    if current == "friends" or detected == "friends":
        return "friends"
    return "unknown"


def sanitize_learning_question(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    question = re.sub(
        r"https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|@[A-Za-z0-9_]{3,}",
        "",
        value,
    )
    question = " ".join(question.split()).strip(" \t\r\n-–—")
    if re.search(
        r"password|passcode|secret|security\s+code|2fa|otp|bank(?:ing)?\s+(?:account|card)",
        question,
        re.IGNORECASE,
    ):
        return ""
    return question[:500]


def recruiter_keyword_fallback(message: str, answers: list[dict[str, str]]) -> dict[str, Any] | None:
    """Build a conservative recruiter reply when the model is unavailable."""
    lines: list[str] = []
    reply_in_english = not re.search(r"[а-яёіїєґ]", message, re.IGNORECASE)
    for item in answers:
        question = item.get("question", "").casefold()
        answer = item.get("answer", "").strip()
        if not answer or question.startswith("complete candidate profile"):
            continue
        if any(term in question for term in ("salary", "compensation", "зарплат", "вилка")):
            lines.append(
                f"Salary expectation: {answer}." if reply_in_english
                else f"По зарплате: {answer}."
            )
        elif any(term in question for term in (
            "notice", "available", "date available", "start", "joining", "приступ",
            "доступност", "срок",
        )):
            lines.append(
                f"I can join the project {answer}." if reply_in_english
                else f"Приступить к проекту могу {answer}."
            )
        elif "aws commercial experience" in question:
            lines.append(
                "I have over 20 years of overall backend production experience. AWS is listed "
                "among my backend technologies, but my profile does not specify AWS-specific "
                "years, services, or responsibilities, so I can't answer those details accurately."
                if reply_in_english else
                "У меня более 20 лет общего опыта в backend-разработке на production. "
                "AWS указан среди моих backend-технологий, но профиль не содержит данных "
                "о стаже именно с AWS, конкретных сервисах или моих задачах с ним. "
                "Поэтому точнее ответить на эти детали не могу."
            )
    if not lines:
        return None
    return {
        "reply": "\n".join(lines),
        "should_reply": True,
        "should_react": False,
        "reaction_emoji": "",
        "web_search": False,
        "web_search_query": "",
        "start": None,
        "calendar_action": "none",
    }


def clearly_recruiting_without_model(message: str, answers: list[dict[str, str]]) -> bool:
    """Recognize only clear hiring questions when AI category detection is unavailable."""
    if re.search(
        r"\b(?:recruit(?:er|ing|ment)?|vacanc\w*|job\s+opening|hiring|interview|resume|cv|"
        r"ваканс\w*|рекрут\w*|співбесід\w*|собеседован\w*|резюм\w*|найм\w*)\b",
        message,
        re.IGNORECASE,
    ):
        return True
    fields = set()
    for item in answers:
        question = item.get("question", "").casefold()
        if any(term in question for term in ("salary", "compensation", "зарплат", "вилка")):
            fields.add("salary")
        if any(term in question for term in ("notice", "available", "joining", "приступ", "срок")):
            fields.add("availability")
        if "aws commercial experience" in question:
            fields.add("aws")
    return len(fields) >= 2


def no_model_recruiter_fallback(
    message: str,
    category: str,
    auto_detect_category: bool,
    prepared_answers: list[dict[str, str]],
    recruiter_answers: RecruiterAnswers,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Use keyword answers only for a known or unmistakable recruiter question."""
    if category == "realtors":
        return None, prepared_answers
    answers = prepared_answers
    fallback_category = category
    if category != "recruiters" and auto_detect_category:
        answers = recruiter_answers.for_recruiter_message(message)
        if clearly_recruiting_without_model(message, answers):
            fallback_category = "recruiters"
    if fallback_category != "recruiters":
        return None, prepared_answers
    plan = recruiter_keyword_fallback(message, answers)
    if plan:
        plan["detected_category"] = fallback_category
    return plan, answers


RU_PRESENCE_CHECK = re.compile(
    r"(?:\bau\b|\bты\s+(?:(?:еще|ещё)\s+)?(?:тут|здесь)\b|"
    r"\bя\s+(?:(?:(?:все|всё)\s+)?(?:еще|ещё)\s+)?(?:тут|здесь)\b"
    r".{0,40}\bа\s+ты\b|"
    r"\bжду\s+(?:твоего\s+)?ответа\b.{0,80}\bа\s+ты\b|"
    r"\bти\s+(?:ще\s+)?тут\b|"
    r"\bя\s+ще\s+тут\b.{0,40}\bа\s+ти\b)",
    re.IGNORECASE,
)
EN_PRESENCE_CHECK = re.compile(
    r"(?:\bare\s+you\s+(?:still\s+)?(?:here|there|around)\b|"
    r"\byou\s+still\s+(?:here|there|around)\b|\banyone\s+there\b)",
    re.IGNORECASE,
)


def direct_presence_reply(text: str) -> str | None:
    if EN_PRESENCE_CHECK.search(text):
        return "Yes, I'm here)"
    if RU_PRESENCE_CHECK.search(text):
        return "Да, я тут)"
    return None


def is_direct_question(text: str) -> bool:
    if "?" in text or "？" in text:
        return True
    return bool(re.match(
        r"^\s*(?:а\s+)?(?:кто|что|где|куда|откуда|когда|почему|зачем|как|"
        r"сколько|какой|какая|какие|можно\s+ли|можешь\s+ли|"
        r"who|what|where|when|why|how|which|can you|could you)\b",
        text,
        re.IGNORECASE,
    ))


def day_only_meeting_invitation(text: str) -> bool:
    day = re.search(
        r"\b(?:сегодня|завтра|послезавтра|today|tomorrow)\b",
        text,
        re.IGNORECASE,
    )
    invitation = re.search(
        r"\b(?:давай|давайте|можем|можно|хочешь|хотите|предлагаю|let's|shall we|can we|could we)\b"
        r".{0,80}\b(?:встрет\w*|увид\w*|meet|see each other)\b",
        text,
        re.IGNORECASE,
    )
    return bool(day and invitation and not EXPLICIT_CLOCK.search(text))


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


def personal_context_profile() -> str:
    path = RUNTIME_ROOT / "profiles" / "personal-context.md"
    try:
        return path.read_text(encoding="utf-8")[:18000]
    except FileNotFoundError:
        return ""


TYPO_WORD = re.compile(r"(?<![\w@./:-])[^\W\d_]{3,}(?![\w./:-])", re.UNICODE)
CYRILLIC_VOWELS = set("аеёиоуыэюяіїє")
CYRILLIC_VOICELESS_CONSONANTS = set("пфктсшщхцч")
LATIN_VOWELS = set("aeiou")
LATIN_VOICELESS_CONSONANTS = set("ptkfsxhqc")


def repeats_recent_reply(reply: str, previous_replies: list[str]) -> bool:
    def normalized(value: str) -> str:
        return "".join(re.findall(r"[^\W_]", value.casefold(), re.UNICODE))

    candidate = normalized(reply)
    candidate_words = set(re.findall(r"[^\W_]{2,}", reply.casefold(), re.UNICODE))
    for previous in previous_replies:
        earlier = normalized(previous)
        if not candidate or not earlier:
            continue
        if candidate == earlier:
            return True
        if min(len(candidate), len(earlier)) >= 8 and SequenceMatcher(
            None, candidate, earlier
        ).ratio() >= 0.9:
            return True
        earlier_words = set(re.findall(r"[^\W_]{2,}", previous.casefold(), re.UNICODE))
        union = candidate_words | earlier_words
        if len(union) >= 4 and len(candidate_words & earlier_words) / len(union) >= 0.88:
            return True
    return False


def occasionally_introduce_typo(text: str) -> str:
    """Give each eligible word a 1% chance of a constrained single-letter typo."""
    result = text
    for match in reversed(list(TYPO_WORD.finditer(text))):
        word = match.group()
        if random.random() >= 0.01:
            continue
        letters = [(index, character) for index, character in enumerate(word) if character.isalpha()]
        if len(letters) < 3:
            continue

        replacements: dict[str, list[tuple[int, list[str]]]] = {
            "vowel": [],
            "voiceless": [],
        }
        for index, character in letters:
            folded = character.casefold()
            if character.isascii():
                if folded in LATIN_VOWELS:
                    group = LATIN_VOWELS
                    kind = "vowel"
                elif folded in LATIN_VOICELESS_CONSONANTS:
                    group = LATIN_VOICELESS_CONSONANTS
                    kind = "voiceless"
                else:
                    continue
            elif folded in CYRILLIC_VOWELS:
                group = CYRILLIC_VOWELS
                kind = "vowel"
            elif folded in CYRILLIC_VOICELESS_CONSONANTS:
                group = CYRILLIC_VOICELESS_CONSONANTS
                kind = "voiceless"
            else:
                continue
            choices = [letter for letter in group if letter != folded]
            if character.isupper():
                choices = [letter.upper() for letter in choices]
            replacements[kind].append((index, choices))

        operations = ["delete"]
        operations.extend(kind for kind, positions in replacements.items() if positions)
        operation = random.choice(operations)
        if operation == "delete":
            position = random.choice([index for index, _ in letters])
            changed = word[:position] + word[position + 1:]
        else:
            position, choices = random.choice(replacements[operation])
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


def latest_assistant_asked_finish_by(history: list[dict[str, str]]) -> bool:
    latest_assistant = next(
        (message for message in reversed(history) if message.get("role") == "assistant"),
        None,
    )
    if not latest_assistant:
        return False
    text = latest_assistant.get("text", "").casefold()
    return bool(
        re.search(r"\d{1,2}[:.]\d{2}", text)
        and ("потом занят" in text or "busy after that" in text)
    )


def clear_yes_answer(message: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:да|ага|угу|так|звісно|конечно|думаю\s+да|успеем|ок(?:ей)?|хорошо|yes|yeah|sure|okay?)\b",
            message,
            re.IGNORECASE,
        )
    )


def clear_no_answer(message: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:нет(?:,\s*не\s+успеем)?|не\s+успеем|вряд\s+ли|no(?:,\s*not\s+really)?|not\s+really)[.!?\s]*",
            message,
            re.IGNORECASE,
        )
    )


def counterparty_asks_alexey_to_choose_time(message: str) -> bool:
    normalized = message.casefold()
    has_alternatives = bool(
        re.search(r"\b(?:или|or)\b", normalized)
        or len(re.findall(r"(?<!\d)(?:[01]?\d|2[0-3])(?::[0-5]\d)?(?!\d)", normalized)) > 1
    )
    asks_alexey = bool(
        re.search(r"\b(?:тебе|вам|какой|какое|вариант|which|what|you)\b", normalized)
        and re.search(r"\b(?:удоб\w*|подход\w*|works? for you|suits? you)\b", normalized)
    )
    return has_alternatives and asks_alexey


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
        self._past_reply_cache: dict[str, list[dict[str, Any]]] = {}
        self._past_reply_cache_loaded_at: dict[str, datetime] = {}

    def latest(self, account_id: str, peer_id: int, limit: int = 80) -> list[dict[str, str]]:
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

    def relevant_past_replies(
        self,
        account_id: str,
        peer_id: int,
        incoming: str,
        search_queries: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, str]]:
        """Find similar historical Q/A pairs across the owner's Telegram accounts."""
        stop_words = {
            "это", "как", "что", "где", "когда", "зачем", "почему", "можно", "будет",
            "есть", "был", "была", "были", "для", "или", "если", "тогда", "какой",
            "какая", "какие", "сколько", "чем", "тебе", "тебя", "твой", "твоя", "мне",
            "меня", "его", "её", "они", "она", "оно", "the", "and", "for", "you",
            "your", "are", "was", "what", "when", "where", "why", "how", "can", "could",
            "would", "with", "from", "that", "this", "have", "has", "какбы", "просто",
        }
        token_pattern = re.compile(r"[^\W_]{3,}", re.UNICODE)

        def tokens(value: str) -> set[str]:
            return {word for word in token_pattern.findall(value.lower()) if word not in stop_words}

        query_sets = [tokens(incoming[:4000])]
        query_sets.extend(tokens(value[:400]) for value in (search_queries or []) if value.strip())
        query_sets = [value for value in query_sets if value]
        if not query_sets:
            return []
        history_accounts = ("personal", "personal2")
        cache_key = "|".join(history_accounts)
        cache_age = datetime.now(UTC) - self._past_reply_cache_loaded_at.get(
            cache_key, datetime.min.replace(tzinfo=UTC)
        )
        if cache_key not in self._past_reply_cache or cache_age > timedelta(minutes=10):
            placeholders = ", ".join(["%s"] * len(history_accounts))
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""SELECT account_id, dialog_id, message_id, text, outgoing FROM messages
                        WHERE account_id IN ({placeholders}) AND text <> ''
                        ORDER BY account_id ASC, dialog_id ASC, message_id ASC""",
                    history_accounts,
                )
                rows = cursor.fetchall()

            pairs: list[dict[str, Any]] = []
            current_source: str | None = None
            current_dialog: int | None = None
            question = ""
            replies: list[str] = []

            def save_pair() -> None:
                if current_dialog is not None and question and replies:
                    pairs.append({
                        "previous_question": question[:500],
                        "previous_reply": "\n".join(replies)[:1200],
                        "source_account": current_source,
                        "dialog_id": current_dialog,
                    })

            for row in rows:
                source_account = str(row["account_id"])
                dialog_id = int(row["dialog_id"])
                if (source_account, dialog_id) != (current_source, current_dialog):
                    save_pair()
                    current_source = source_account
                    current_dialog = dialog_id
                    question = ""
                    replies = []
                message_text = str(row["text"] or "").strip()
                if not message_text:
                    continue
                if not row["outgoing"]:
                    save_pair()
                    question = message_text
                    replies = []
                elif question and len(replies) < 3:
                    replies.append(message_text)
            save_pair()
            self._past_reply_cache[cache_key] = pairs
            self._past_reply_cache_loaded_at[cache_key] = datetime.now(UTC)

        scored: list[tuple[float, dict[str, Any]]] = []
        for pair in self._past_reply_cache[cache_key]:
            candidate_tokens = tokens(pair["previous_question"])
            candidate_scores = []
            for query_tokens in query_sets:
                overlap = query_tokens & candidate_tokens
                if overlap and (len(overlap) >= 2 or any(len(word) >= 7 for word in overlap)):
                    candidate_scores.append(
                        len(overlap) / ((len(query_tokens) * len(candidate_tokens)) ** 0.5)
                    )
            if not candidate_scores:
                continue
            score = max(candidate_scores)
            if score >= 0.16:
                same_contact = pair["dialog_id"] == peer_id
                if same_contact:
                    score *= 1.25
                scored.append((score, pair))
        scored.sort(key=lambda item: item[0], reverse=True)

        selected: list[dict[str, str]] = []
        seen_questions: set[str] = set()
        for _, pair in scored:
            key = " ".join(sorted(tokens(pair["previous_question"])))
            if key in seen_questions:
                continue
            seen_questions.add(key)
            selected.append({
                "previous_question": pair["previous_question"],
                "previous_reply": pair["previous_reply"],
                "same_contact": "yes" if pair["dialog_id"] == peer_id else "no",
                "source_account": pair["source_account"],
            })
            if len(selected) >= limit:
                break
        return selected

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
    client: TelegramClient, event: events.NewMessage.Event, limit: int = 80
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
    return result[-80:]


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




def is_quiet_hours(value: datetime, timezone_name: str) -> bool:
    zone = ZoneInfo(timezone_name)
    local = value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    return local.hour < 9

def interval_overlaps_quiet_hours(
    start_at: str, duration_minutes: int, timezone_name: str
) -> bool:
    """Return whether a local calendar interval overlaps midnight through 09:00."""
    try:
        start = datetime.fromisoformat(start_at)
        zone = ZoneInfo(timezone_name)
    except (TypeError, ValueError):
        return False
    start = start.replace(tzinfo=zone) if start.tzinfo is None else start.astimezone(zone)
    end = start + timedelta(minutes=duration_minutes)
    day = start.date()
    while day <= end.date():
        quiet_start = datetime.combine(day, time.min, tzinfo=zone)
        quiet_end = datetime.combine(day, time(9, 0), tzinfo=zone)
        if start < quiet_end and end > quiet_start:
            return True
        day += timedelta(days=1)
    return False


def quiet_hours_reply(current_message: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁёІЇЄҐіїєґ]", current_message))
    if russian:
        return "В это время не получится. Давай выберем другое время?"
    return "That time won't work for me. Could we choose another time?"

def established_availability_date(
    history: list[dict[str, str]], current_message: str, now: datetime
) -> date | None:
    recent = [item.get("text", "") for item in history[-12:]] + [current_message]
    today_words = re.compile(r"\b(?:сегодня|today|this (?:morning|afternoon|evening))\b", re.IGNORECASE)
    tomorrow_words = re.compile(r"\b(?:завтра|tomorrow)\b", re.IGNORECASE)
    weekdays = [
        (re.compile(r"\b(?:понедельник\w*|понеділ\w*|monday)\b", re.IGNORECASE), 0),
        (re.compile(r"\b(?:вторник\w*|вівтор\w*|tuesday)\b", re.IGNORECASE), 1),
        (re.compile(r"\b(?:сред(?:а|у|е|ы|ой)|серед\w*|wednesday)\b", re.IGNORECASE), 2),
        (re.compile(r"\b(?:четверг\w*|четвер\w*|thursday)\b", re.IGNORECASE), 3),
        (re.compile(r"\b(?:пятниц\w*|п[’']ятниц\w*|friday)\b", re.IGNORECASE), 4),
        (re.compile(r"\b(?:суббот\w*|субот\w*|saturday)\b", re.IGNORECASE), 5),
        (re.compile(r"\b(?:воскресень\w*|неділ\w*|sunday)\b", re.IGNORECASE), 6),
    ]
    for message in reversed(recent):
        if tomorrow_words.search(message):
            return now.date() + timedelta(days=1)
        if today_words.search(message):
            return now.date()
        matches = [
            (match.start(), weekday)
            for pattern, weekday in weekdays
            if (match := pattern.search(message))
        ]
        if matches:
            position, weekday = max(matches)
            delta = (weekday - now.weekday()) % 7
            prefix = message[max(0, position - 30):position]
            if re.search(r"\b(?:следующ\w*|next)\s*$", prefix, re.IGNORECASE):
                delta += 7
            return now.date() + timedelta(days=delta)
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
            return f"На {date_label} я уже занят. Давай посмотрим другой день?"
        return f"I don't have another open time for a meeting {date_label}. Shall we look at another day?"
    if russian:
        return "Не могу точно сказать насчёт этого времени. Давай выберем другой вариант?"
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
    personal_context = personal_context_profile()
    if personal_context:
        logger.info("Personal context profile loaded (%s characters)", len(personal_context))
    recruiter_answers = RecruiterAnswers(settings.recruiter_answers_file)
    if not settings.codex_binary.is_file():
        logger.warning("Codex CLI is not installed at CODEX_BINARY; replies will fail until installed")
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
    quiet_status: bool | None = None

    async def refresh_quiet_hours_status(force: bool = False) -> None:
        nonlocal quiet_status
        quiet_now = is_quiet_hours(datetime.now(UTC), settings.timezone)
        if not force and quiet_status is quiet_now:
            return
        try:
            await client(functions.account.UpdateStatusRequest(offline=quiet_now))
        except Exception as exc:
            logger.warning(
                "Could not update Telegram status for quiet hours (%s)", type(exc).__name__
            )
            return
        quiet_status = quiet_now
        logger.info(
            "Telegram presence set to %s for quiet hours",
            "offline" if quiet_now else "normal",
        )

    await client.start()
    await refresh_quiet_hours_status(force=True)
    me = await client.get_me()
    store.register_learning_owner(me.id)
    if store.setting("legacy_contacts_marked_friends_v1", "") != "done":
        legacy_count = 0
        async for dialog in client.iter_dialogs():
            user = dialog.entity
            if not isinstance(user, types.User) or user.bot or user.deleted or user.is_self:
                continue
            display_name = " ".join(
                part for part in (user.first_name, user.last_name) if part
            ).strip()
            store.set_existing_contact_friend(user.id, user.username or "", display_name)
            legacy_count += 1
        store.set_setting("legacy_contacts_marked_friends_v1", "done")
        logger.info("Marked %s existing private dialogs as friends where unlabeled", legacy_count)
    gate = BioGate(client, me.id)
    await gate.refresh(force=True)
    manual_folder = DialogFilterGate(client, "Manual")
    await manual_folder.refresh(force=True)
    auto_folder = DialogFilterGate(client, "Auto")
    await auto_folder.refresh(force=True)

    learning_channel = None
    learning_channel_can_post = False
    try:
        learning_channel = await client.get_entity(LEARNING_CHANNEL_USERNAME)
        permissions = await client.get_permissions(learning_channel, me.id)
        admin_rights = getattr(permissions, "admin_rights", None)
        learning_channel_can_post = bool(
            getattr(permissions, "is_creator", False)
            or getattr(admin_rights, "post_messages", False)
        )
        logger.info(
            "Learning channel resolved for account %s; posting is %s",
            settings.account_id,
            "available" if learning_channel_can_post else "unavailable",
        )
    except Exception as exc:
        logger.warning("Could not access learning channel (%s)", type(exc).__name__)

    async def publish_next_learning_question() -> None:
        if learning_channel is None or not learning_channel_can_post:
            return
        queued = store.claim_next_learning_question()
        if queued is None:
            return
        try:
            posted = await client.send_message(learning_channel, queued["question"])
        except Exception as exc:
            store.retry_learning_question(int(queued["id"]))
            logger.warning("Could not post learning question (%s)", type(exc).__name__)
            return
        store.mark_learning_question_awaiting(int(queued["id"]), posted.id)
        logger.info("Posted one unanswered question to the learning channel")

    async def reply_policy_block(peer_id: int, force: bool = True) -> str | None:
        owner_opt_in = store.conversation_owner_opt_in_active(settings.account_id, peer_id)
        if store.conversation_control_mode(settings.account_id, peer_id) == "manual" and not owner_opt_in:
            return "conversation is being handled manually by Alexey"
        if not await manual_folder.refresh(force=force):
            return "Telegram Manual folder state is unavailable"
        if manual_folder.contains(peer_id) and not owner_opt_in:
            return "contact is in Telegram Manual folder"
        if not await auto_folder.refresh(force=force):
            return "Telegram Auto folder state is unavailable"
        auto_enabled = auto_folder.contains(peer_id)
        await gate.refresh(force=force)
        if gate.error:
            return "global bio switch is unreadable"
        if not gate.enabled and not auto_enabled and not owner_opt_in:
            return "global bio switch is off and contact is not in Telegram Auto folder"
        return None
    locks: dict[int, asyncio.Lock] = {}
    assistant_send_markers: dict[tuple[int, str], datetime] = {}

    async def acknowledge_answered_message(event: events.NewMessage.Event) -> None:
        try:
            await client.send_read_acknowledge(
                await event.get_input_chat(), max_id=event.message.id
            )
        except Exception as exc:
            logger.warning(
                "Could not mark answered incoming private message as read (%s)",
                type(exc).__name__,
            )

    def mark_assistant_send(peer_id: int, text: str) -> None:
        assistant_send_markers[(peer_id, text)] = datetime.now(UTC) + timedelta(minutes=2)

    async def react_to_incoming(
        event: events.NewMessage.Event,
        peer_id: int,
        sender: types.User,
        category: str,
        session_started_at: str,
        emoji: str,
    ) -> bool:
        block_reason = await reply_policy_block(peer_id, force=True)
        if block_reason:
            store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
            store.audit(peer_id, "skipped", f"{block_reason} before reaction")
            return False
        if emoji not in REACTION_EMOJIS:
            emoji = "👍"
        await client(functions.messages.SendReactionRequest(
            peer=await event.get_input_chat(),
            msg_id=event.message.id,
            reaction=[types.ReactionEmoji(emoticon=emoji)],
        ))
        await acknowledge_answered_message(event)
        store.message_state(settings.account_id, peer_id, event.message.id, "sent")
        store.audit(
            peer_id,
            "reacted",
            json.dumps(
                {"incoming_message_id": event.message.id, "emoji": emoji},
                ensure_ascii=False,
            ),
        )
        await notify_conversation_started(peer_id, sender, category, session_started_at)
        return True

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
                "/category @username unknown|friends|recruiters|realtors — override auto classification\n"
                "/category remove @username — clear manual assignment\n"
                "/contacts [unknown|friends|recruiters|realtors] — list assigned chats\n"
                "/dialogs [page] — list exported chats and current categories\n"
                "New chats remain unknown until their conversation shows a category.\n"
                "Chats in the Telegram folder 'Manual' are ignored unless you opt in by ending your message with a period; the bot removes the period.\n"
                "A period opt-in also overrides bio `free` for that conversation, until your next message without a period or 30 minutes of inactivity. `Auto` still overrides `free`.\n"
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
            if len(parts) != 3 or parts[2].casefold() not in ("unknown", "friends", "recruiters", "realtors"):
                return "Use /category @username unknown, friends, recruiters, or realtors to override automatic classification."
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
            if category not in (None, "unknown", "friends", "recruiters", "realtors"):
                return "Use /contacts, /contacts unknown, /contacts friends, /contacts recruiters, or /contacts realtors."
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
                category = store.contact_category(row["dialog_id"]) or "unknown"
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
            "unknown": [], "friends": [], "recruiters": [], "realtors": []
        }
        for row in rows:
            grouped[row["category"]].append(_format_contact(row))
        blocks = []
        for name in ((category,) if category else ("unknown", "friends", "recruiters", "realtors")):
            blocks.append(f"{name.title()} ({len(grouped[name])}):\n" + "\n".join(grouped[name]))
        return "\n\n".join(blocks)

    @client.on(events.NewMessage(outgoing=True))
    async def on_owner_outgoing_message(event: events.NewMessage.Event) -> None:
        if not event.is_private or event.chat_id == me.id:
            return
        text = event.raw_text or ""
        key = (event.chat_id, text)
        marker_expiry = assistant_send_markers.get(key)
        now = datetime.now(UTC)
        if marker_expiry and marker_expiry >= now:
            return
        if marker_expiry:
            assistant_send_markers.pop(key, None)
        control_mode, consume_opt_in_marker = store.record_owner_outgoing(
            settings.account_id,
            event.chat_id,
            event.message.date or now,
            text.endswith("."),
        )
        store.audit(event.chat_id, "conversation_control_changed", control_mode)
        if consume_opt_in_marker and text[:-1]:
            trimmed_length = len(text[:-1].encode("utf-16-le")) // 2
            entities = []
            for original in event.message.entities or []:
                entity = copy(original)
                entity_end = entity.offset + entity.length
                if entity_end > trimmed_length:
                    if entity.offset >= trimmed_length:
                        continue
                    entity.length -= entity_end - trimmed_length
                entities.append(entity)
            try:
                await client.edit_message(
                    event.chat_id,
                    event.message.id,
                    text[:-1],
                    parse_mode=None,
                    formatting_entities=entities,
                )
            except Exception as exc:
                store.audit(
                    event.chat_id,
                    "conversation_opt_in_marker_removal_failed",
                    type(exc).__name__,
                )
                logger.warning(
                    "Could not remove the trailing-period opt-in marker (%s)",
                    type(exc).__name__,
                )
            else:
                store.audit(event.chat_id, "conversation_opt_in_marker_removed")

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

    if learning_channel is not None:
        @client.on(events.NewMessage(chats=learning_channel))
        async def on_learning_channel_message(event: events.NewMessage.Event) -> None:
            sender = await event.get_sender()
            sender_id = getattr(sender, "id", None)
            if not isinstance(sender_id, int) or sender_id not in store.learning_owner_ids():
                return
            if store.is_learning_question_message(event.message.id):
                return
            answer = (event.raw_text or "").strip()
            if not answer or answer.startswith("/") or len(answer) > 5000:
                return
            pending = store.awaiting_learning_question()
            if pending is None:
                return
            reply_to = getattr(getattr(event.message, "reply_to", None), "reply_to_msg_id", None)
            if reply_to is not None and reply_to != pending["channel_message_id"]:
                return
            question_id = int(pending["id"])
            if not store.claim_learning_answer(question_id):
                return
            try:
                save_learned_answer(settings.recruiter_answers_file, pending["question"], answer)
            except Exception as exc:
                store.retry_learning_answer(question_id)
                logger.exception("Could not save a learned answer (%s)", type(exc).__name__)
                return
            store.finish_learning_question(question_id)
            recruiter_answers._mtime_ns = None
            logger.info("Saved an owner answer from the learning channel")
            await publish_next_learning_question()

        await publish_next_learning_question()

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
        category_label = {
            "unknown": "неизвестно", "friends": "друг",
            "recruiters": "рекрутер", "realtors": "риелтор",
        }[category]
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
        if event.is_private and (
            is_quiet_hours(datetime.now(UTC), settings.timezone)
            or is_quiet_hours(event.message.date or datetime.now(UTC), settings.timezone)
        ):
            if event.raw_text.strip() and store.claim_message(
                settings.account_id, peer_id, event.message.id
            ):
                store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                store.audit(peer_id, "skipped", "night quiet hours")
            return
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
        if current_category is None:
            display_name = " ".join(
                part for part in (sender.first_name, sender.last_name) if part
            ).strip()
            store.set_contact_category(
                peer_id, "unknown", sender.username or "", display_name,
                source="automatic",
            )
            store.audit(peer_id, "contact_auto_categorized", "unknown")
        block_reason = await reply_policy_block(peer_id, force=True)
        if block_reason:
            store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
            store.audit(peer_id, "skipped", block_reason)
            return
        category = store.contact_category(peer_id)
        category_source = store.contact_category_source(peer_id)
        auto_detect_category = category is None or category_source == "automatic"
        category = category or "unknown"

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
                recent_outgoing_replies = [
                    item["text"][:600]
                    for item in context
                    if item["role"] == "assistant" and item["text"].strip()
                ][-20:]
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
                day_only_invitation = day_only_meeting_invitation(event.raw_text)
                calendar_related = (
                    availability_question(event.raw_text)
                    or day_only_invitation
                    or meeting_context_present(context, event.raw_text)
                )
                if calendar_related:
                    # Live conversation context and the calendar are authoritative for scheduling.
                    # Do not spend two history-search steps on a calendar request.
                    previous_reply_examples = []
                else:
                    try:
                        search_queries = await responder.expand_history_queries(model, event.raw_text)
                    except Exception as exc:
                        search_queries = []
                        logger.warning("Could not expand history search (%s)", type(exc).__name__)
                    try:
                        previous_reply_examples = history.relevant_past_replies(
                            settings.account_id, peer_id, event.raw_text, search_queries
                        )
                    except Exception as exc:
                        previous_reply_examples = []
                        logger.warning("Could not search past replies (%s)", type(exc).__name__)
                prepared_answers = (
                    recruiter_answers.for_recruiter_message(event.raw_text)
                    if category == "recruiters"
                    else recruiter_answers.learned_answers_context()
                )
                try:
                    plan = await responder.plan(
                        model=model,
                        category=category,
                        history=context,
                        current_message=event.raw_text,
                        now=now,
                        style_profile=style_profile(),
                        previous_reply_examples=previous_reply_examples,
                        recent_outgoing_replies=recent_outgoing_replies,
                        opening_history=opening,
                        auto_detect_category=auto_detect_category,
                        prepared_answers=prepared_answers,
                        pending_meeting_duration=pending_meeting_context,
                        personal_context=personal_context,
                    )
                except Exception as exc:
                    fallback, fallback_answers = no_model_recruiter_fallback(
                        event.raw_text,
                        category,
                        auto_detect_category,
                        prepared_answers,
                        recruiter_answers,
                    )
                    if fallback is None:
                        raise
                    logger.warning(
                        "LLM unavailable; using recruiter keyword fallback (%s)",
                        type(exc).__name__,
                    )
                    plan = fallback
                    prepared_answers = fallback_answers
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
                    resolved_category = resolve_automatic_category(category, detected_category)
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
                            recruiter_answers.for_recruiter_message(event.raw_text)
                            if category == "recruiters"
                            else recruiter_answers.learned_answers_context()
                        )
                        try:
                            plan = await responder.plan(
                                model=model,
                                category=category,
                                history=context,
                                current_message=event.raw_text,
                                now=now,
                                style_profile=style_profile(),
                                previous_reply_examples=previous_reply_examples,
                                recent_outgoing_replies=recent_outgoing_replies,
                                opening_history=opening,
                                auto_detect_category=False,
                                prepared_answers=prepared_answers,
                                pending_meeting_duration=pending_meeting_context,
                                personal_context=personal_context,
                            )
                        except Exception as exc:
                            fallback, fallback_answers = no_model_recruiter_fallback(
                                event.raw_text,
                                category,
                                False,
                                prepared_answers,
                                recruiter_answers,
                            )
                            if fallback is None:
                                raise
                            logger.warning(
                                "LLM unavailable; using recruiter keyword fallback (%s)",
                                type(exc).__name__,
                            )
                            plan = fallback
                            prepared_answers = fallback_answers
                plan.pop("detected_category", None)
                learning_question = sanitize_learning_question(plan.pop("learn_question", None))
                if learning_question and not plan.get("web_search"):
                    if store.enqueue_learning_question(learning_question):
                        store.audit(peer_id, "unknown_question_queued")
                        await publish_next_learning_question()
                web_search_requested = bool(plan.get("web_search"))
                web_search_results: list[dict[str, str]] | None = []
                web_search_query = str(plan.get("web_search_query") or "").strip()[:320]
                if web_search_requested:
                    plan["should_reply"] = True
                    plan["should_react"] = False
                    if not web_search_query:
                        web_search_results = []
                    else:
                        try:
                            web_search_results = await asyncio.to_thread(
                                search_web, web_search_query
                            )
                        except Exception as exc:
                            web_search_results = None
                            logger.warning("Web search failed (%s)", type(exc).__name__)
                    store.audit(
                        peer_id,
                        "web_search_completed",
                        json.dumps(
                            {
                                "status": (
                                    "unavailable" if web_search_results is None
                                    else "results" if web_search_results
                                    else "empty"
                                ),
                                "result_count": len(web_search_results or []),
                                "source_domains": [
                                    item["domain"] for item in (web_search_results or [])
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    )
                acknowledgement = acknowledgement_reaction(event.raw_text)
                if acknowledgement and not latest_assistant_asked_question(context):
                    plan["should_reply"] = False
                    plan["should_react"] = True
                    plan["reaction_emoji"] = acknowledgement
                presence_reply = direct_presence_reply(event.raw_text)
                if presence_reply and not plan.get("should_reply", True):
                    plan["should_reply"] = True
                    plan["should_react"] = False
                    plan["reply"] = presence_reply
                    store.audit(peer_id, "no_reply_overridden", "direct presence check")
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
                    elif interval_overlaps_quiet_hours(
                        pending_duration["start_at"], requested_duration, settings.timezone
                    ):
                        store.clear_pending_calendar_duration(settings.account_id, peer_id)
                        calendar_result = (
                            "QUIET_HOURS_BLOCKED; leave the existing event unchanged. "
                            "Tell the contact this time will not work and ask for another time without naming the blocked interval, boundary, or rejected time. "
                            "Do not mention the calendar or this rule."
                        )
                        store.audit(peer_id, "calendar_quiet_hours_blocked", "duration update")
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
                        previous_reply_examples=previous_reply_examples,
                        recent_outgoing_replies=recent_outgoing_replies,
                        prepared_answers=prepared_answers,
                        personal_context=personal_context,
                        web_search_results=web_search_results if web_search_requested else None,
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
                    store.clear_pending_calendar_duration(settings.account_id, peer_id)
                meeting_in_progress = bool(plan.get("meeting_in_progress")) or meeting_context_present(
                    context, event.raw_text
                )
                assistant_accepts = bool(plan.get("assistant_accepts_meeting"))
                counterparty_choice_request = counterparty_asks_alexey_to_choose_time(
                    event.raw_text
                )
                if counterparty_choice_request:
                    plan["confirmed_agreement"] = False
                    assistant_accepts = False
                    action = "check"
                boundary_question_confirmed = (
                    latest_assistant_asked_finish_by(context) and clear_yes_answer(event.raw_text)
                )
                boundary_question_declined = (
                    latest_assistant_asked_finish_by(context) and clear_no_answer(event.raw_text)
                )
                if boundary_question_confirmed and start:
                    plan["confirmed_agreement"] = True
                    meeting_in_progress = True
                if start and (
                    meeting_in_progress
                    or action in ("check", "create")
                    or plan.get("confirmed_agreement")
                    or assistant_accepts
                ):
                    action = "create" if plan.get("confirmed_agreement") else "check"
                if action in ("check", "create") and not meeting_in_progress:
                    action = "none"
                    store.audit(peer_id, "calendar_action_suppressed", "no clear meeting context")
                calendar_result = "No calendar action is needed."
                availability_reply: str | None = None
                calendar_boundary_reply: str | None = None
                if boundary_question_declined:
                    russian = bool(re.search(r"[А-Яа-яЁёІЇЄҐіїєґ]", event.raw_text))
                    calendar_boundary_reply = (
                        "Тогда давай выберем другое время?"
                        if russian
                        else "Then let's choose another time?"
                    )
                    calendar_result = "The contact declined the finish-by time; ask for another meeting time."
                target_day = (
                    established_availability_date(context, event.raw_text, now)
                    if availability_question(event.raw_text) or day_only_invitation
                    else None
                )
                if target_day is not None:
                    action = "none"
                    duration = int(
                        plan.get("duration_minutes") or (30 if category == "recruiters" else 60)
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
                            if day_only_invitation and slots:
                                prefix = (
                                    "Да, давай. "
                                    if re.search(r"[А-Яа-яЁёІЇЄҐіїєґ]", event.raw_text)
                                    else "Sure. "
                                )
                                availability_reply = prefix + availability_reply
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
                    elif interval_overlaps_quiet_hours(start, duration, settings.timezone):
                        calendar_result = (
                            "QUIET_HOURS_BLOCKED; do not check or create this meeting. "
                            "Tell the contact this time will not work and ask for another time without naming the blocked interval, boundary, or rejected time. "
                            "Do not mention the calendar or this rule."
                        )
                        availability_reply = quiet_hours_reply(event.raw_text)
                        store.audit(peer_id, "calendar_quiet_hours_blocked", "requested interval")
                    elif not calendar.configured:
                        calendar_result = (
                            "CALENDAR_UNAVAILABLE; availability could not be checked, so do not "
                            "confirm the proposed time."
                        )
                        store.audit(peer_id, "calendar_availability_failed", "authorization missing")
                    else:
                        finish_by_confirmation = (
                            latest_assistant_asked_finish_by(context)
                            and clear_yes_answer(event.raw_text)
                            and bool(plan.get("confirmed_agreement") or plan.get("assistant_accepts_meeting"))
                        )
                        try:
                            next_busy = None
                            if action == "create":
                                next_busy = await asyncio.to_thread(calendar.next_busy_start, start)
                            proposed_start = datetime.fromisoformat(start).astimezone(
                                ZoneInfo(settings.timezone)
                            )
                            boundary = (
                                datetime.fromisoformat(next_busy).astimezone(ZoneInfo(settings.timezone))
                                if next_busy else None
                            )
                            available_minutes = (
                                int((boundary - proposed_start).total_seconds() // 60)
                                if boundary else None
                            )
                            needs_finish_by_confirmation = (
                                next_busy is not None
                                and not finish_by_confirmation
                                and available_minutes is not None
                                and available_minutes < duration
                            )
                            if needs_finish_by_confirmation:
                                time_text = boundary.strftime("%H:%M")
                                calendar_result = (
                                    "FINISH_BY_CONFIRMATION_REQUIRED; do not create the meeting yet. "
                                    f"Ask whether we can finish by {time_text}; never reveal private event details."
                                )
                                russian = bool(re.search(r"[А-Яа-яЁёІЇЄҐіїєґ]", event.raw_text))
                                calendar_boundary_reply = (
                                    f"Успеем до {time_text}? Я потом занят."
                                    if russian
                                    else f"Do you think we can finish by {time_text}? I'm busy after that."
                                )
                                store.audit(peer_id, "calendar_finish_by_question", time_text)
                                is_free, interval = False, ""
                            else:
                                if next_busy and finish_by_confirmation and available_minutes is not None:
                                    if available_minutes < 5:
                                        calendar_result = (
                                            "BUSY; there is not enough time before the next same-day commitment. "
                                            "Do not create the meeting; ask for another time."
                                        )
                                        is_free, interval = False, ""
                                    else:
                                        duration = min(duration, available_minutes)
                                        plan["duration_minutes"] = duration
                                        is_free, interval = await asyncio.to_thread(
                                            calendar.check, start, duration
                                        )
                                else:
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
                        if calendar_result.startswith(("AVAILABILITY_UNKNOWN", "FINISH_BY_CONFIRMATION_REQUIRED")):
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
                                calendar_result = "FREE; calendar event successfully created using an internal duration."
                            else:
                                calendar_result = "FREE; calendar event successfully created."
                            store.audit(peer_id, "calendar_event_created", event_id)
                        else:
                            calendar_result = f"FREE at {interval}; no event created yet."
                            store.audit(peer_id, "calendar_availability_checked", "free")
                if (
                    is_direct_question(event.raw_text)
                    and (
                        not plan.get("should_reply", True)
                        or not str(plan.get("reply") or "").strip()
                    )
                    and availability_reply is None
                    and calendar_boundary_reply is None
                    and duration_followup_reply is None
                    and action == "none"
                ):
                    plan["should_reply"] = True
                    plan["should_react"] = False
                    if not str(plan.get("reply") or "").strip():
                        plan["reply"] = await responder.compose_with_calendar_result(
                            model=model,
                            category=category,
                            history=context,
                            current_message=event.raw_text,
                            plan=plan,
                            calendar_result=(
                                "QUESTION_REQUIRES_ANSWER; answer the current question. "
                                "If a needed fact is unknown, say so briefly or ask for the "
                                "specific missing detail. Do not invent facts."
                            ),
                            now=now,
                            style_profile=style_profile(),
                            previous_reply_examples=previous_reply_examples,
                            recent_outgoing_replies=recent_outgoing_replies,
                            prepared_answers=prepared_answers,
                            personal_context=personal_context,
                        )
                    if not str(plan.get("reply") or "").strip():
                        plan["reply"] = (
                            "Не могу точно ответить на этот вопрос."
                            if re.search(r"[А-Яа-яЁёІЇЄҐіїєґ]", event.raw_text)
                            else "I can't answer that accurately."
                        )
                    store.audit(peer_id, "no_reply_overridden", "direct question")
                text_reply_required = (
                    web_search_requested
                    or duration_followup_reply is not None
                    or availability_reply is not None
                    or calendar_boundary_reply is not None
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
                    emoji = plan.get("reaction_emoji")
                    await react_to_incoming(
                        event, peer_id, sender, category, session_started_at, emoji
                    )
                    return
                if web_search_requested:
                    if availability_reply is not None:
                        calendar_result = f"APPROVED CALENDAR RESPONSE: {availability_reply}"
                    reply = await responder.compose_with_calendar_result(
                        model=model,
                        category=category,
                        history=context,
                        current_message=event.raw_text,
                        plan=plan,
                        calendar_result=calendar_result,
                        now=now,
                        style_profile=style_profile(),
                        previous_reply_examples=previous_reply_examples,
                        recent_outgoing_replies=recent_outgoing_replies,
                        prepared_answers=prepared_answers,
                        personal_context=personal_context,
                        web_search_results=web_search_results,
                    )
                elif duration_followup_reply is not None:
                    reply = duration_followup_reply
                elif availability_reply is not None:
                    reply = availability_reply
                elif calendar_boundary_reply is not None:
                    reply = calendar_boundary_reply
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
                        previous_reply_examples=previous_reply_examples,
                        recent_outgoing_replies=recent_outgoing_replies,
                        prepared_answers=prepared_answers,
                        personal_context=personal_context,
                    )
                else:
                    reply = plan["reply"]
                reply = reply.strip()
                if not reply:
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "skipped", "responder returned an empty required reply")
                    return
                if reply in REACTION_EMOJIS:
                    await react_to_incoming(
                        event, peer_id, sender, category, session_started_at, reply
                    )
                    return
                if repeats_recent_reply(reply, recent_outgoing_replies):
                    original_reply = reply
                    try:
                        revised_reply = await responder.rephrase_repeated_reply(
                            model=model,
                            category=category,
                            history=context,
                            current_message=event.raw_text,
                            candidate_reply=reply,
                            recent_outgoing_replies=recent_outgoing_replies,
                            now=now,
                            style_profile=style_profile(),
                            calendar_result=calendar_result,
                        )
                        revised_reply = revised_reply.strip()
                    except Exception as exc:
                        logger.warning(
                            "Could not rephrase a repeated candidate reply (%s)",
                            type(exc).__name__,
                        )
                        revised_reply = ""
                    if revised_reply:
                        reply = revised_reply
                        if repeats_recent_reply(reply, recent_outgoing_replies):
                            store.audit(
                                peer_id,
                                "reply_repetition_persisted",
                                "second candidate also repeated; sent to avoid silence",
                            )
                    else:
                        # A wording collision must not silence a message that needs a reply.
                        reply = original_reply
                        store.audit(
                            peer_id,
                            "reply_rephrase_failed",
                            "sent original candidate to avoid silence",
                        )
                reply = occasionally_introduce_typo(reply)
                if not reply:
                    store.message_state(settings.account_id, peer_id, event.message.id, "skipped")
                    store.audit(peer_id, "skipped", "candidate reply became empty before send")
                    return
                store.audit(
                    peer_id,
                    "generated",
                    json.dumps(
                        {
                            "incoming_message_id": event.message.id,
                            "category": category,
                            "model": model,
                            "calendar_action": action,
                            "web_search": web_search_requested,
                            "web_search_result_count": len(web_search_results or []),
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
                mark_assistant_send(peer_id, reply)
                sent = await event.respond(reply)
                await acknowledge_answered_message(event)
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
                await refresh_quiet_hours_status()
                if not is_quiet_hours(datetime.now(UTC), settings.timezone):
                    await gate.refresh(force=True)

        poller = asyncio.create_task(poll_bio())
        await refresh_quiet_hours_status(force=True)
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
    grouped: dict[str, list[str]] = {"unknown": [], "friends": [], "recruiters": [], "realtors": []}
    for row in rows:
        name = row["display_name"] or row["username"] or str(row["peer_id"])
        suffix = f" (@{row['username']})" if row["username"] else ""
        grouped[row["category"]].append(f"• {name}{suffix}")
    categories = (category,) if category else ("unknown", "friends", "recruiters", "realtors")
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
