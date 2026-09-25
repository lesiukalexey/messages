from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml


BOT_USERNAME = "learnDataBot"
logger = logging.getLogger(__name__)
_PRIVATE_QUESTION = re.compile(
    r"password|passwd|secret|token|credential|security code|verification code|"
    r"one.time password|\botp\b|\b2fa\b|bank account",
    re.I,
)
_QUESTION_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "do", "for", "have", "how", "i", "in",
    "is", "of", "or", "the", "to", "what", "with", "you", "your",
    "у", "в", "на", "и", "или", "что", "как", "какой", "какая", "какие",
    "ли", "есть", "мне", "ты", "вы", "ваш", "мой", "моя", "мои",
})


def _profile_lock(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _read_profile(path: Path, lock_path: Path) -> dict[str, Any]:
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_SH)
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise ValueError("The profile YAML root must be a mapping")
    return document


def _write_profile_contents(path: Path, document: dict[str, Any]) -> None:
    rendered = yaml.safe_dump(
        document,
        allow_unicode=True,
        sort_keys=False,
        width=100,
        default_flow_style=False,
    )
    with path.open("r+", encoding="utf-8") as profile:
        profile.seek(0)
        profile.write(rendered)
        profile.truncate()
        profile.flush()
        os.fsync(profile.fileno())


def _question_key(question: str) -> str:
    return " ".join(question.casefold().split())


def _question_terms(question: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+|[а-яёіїєґ]+", question.casefold())
        if len(token) > 1 and token not in _QUESTION_STOP_WORDS
    }


def _same_question_topic(left: str, right: str) -> bool:
    if _question_key(left) == _question_key(right):
        return True
    left_terms = _question_terms(left)
    right_terms = _question_terms(right)
    shared = len(left_terms & right_terms)
    return shared >= 2 and shared / max(len(left_terms), len(right_terms)) >= 0.66


def _memory_conflicts_with_owner(item: dict[str, Any], question: str, answer: str) -> bool:
    if str(item.get("answer") or "").strip() == answer:
        return False
    variants = item.get("variants", [])
    candidates = [str(item.get("question") or "")]
    if isinstance(variants, list):
        candidates.extend(value for value in variants if isinstance(value, str))
    return any(_same_question_topic(candidate, question) for candidate in candidates)


def _pending_profile_questions(path: Path, lock_path: Path) -> list[str]:
    document = _read_profile(path, lock_path)
    pending = document.get("pending_learning_questions", [])
    if not isinstance(pending, list):
        return []
    return [
        question.strip()[:500]
        for question in pending
        if isinstance(question, str)
        and question.strip()
        and not _PRIVATE_QUESTION.search(question)
    ]


def save_learned_answer(
    path: Path, question: str, answer: str, lock_path: Path | None = None
) -> None:
    question = " ".join(question.split())[:500]
    answer = answer.strip()[:5000]
    if not question or not answer:
        raise ValueError("A question and answer are required")

    lock_path = lock_path or _profile_lock(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(document, dict):
            raise ValueError("The profile YAML root must be a mapping")
        learned = document.setdefault("learned_answers", {})
        if not isinstance(learned, dict):
            raise ValueError("learned_answers must be a YAML mapping")
        owner_answers = document.setdefault("owner_learned_answers", {})
        if not isinstance(owner_answers, dict):
            raise ValueError("owner_learned_answers must be a YAML mapping")
        prior_owner_questions = [
            key for key in owner_answers if isinstance(key, str)
        ]
        for known_question in list(learned):
            if not isinstance(known_question, str):
                continue
            if any(
                _same_question_topic(known_question, owner_question)
                for owner_question in prior_owner_questions
            ):
                continue
            if _same_question_topic(known_question, question):
                learned.pop(known_question, None)
        learned[question] = answer
        owner_answers[question] = answer
        memory = document.get("ai_memory", [])
        if isinstance(memory, list):
            document["ai_memory"] = [
                item
                for item in memory
                if not isinstance(item, dict)
                or not _memory_conflicts_with_owner(item, question, answer)
            ]
        included = document.get("ai_memory_included", {})
        if isinstance(included, dict):
            document["ai_memory_included"] = {
                known_question: known_answer
                for known_question, known_answer in included.items()
                if not isinstance(known_question, str)
                or known_answer == answer
                or not _same_question_topic(known_question, question)
            }
        pending = document.get("pending_learning_questions", [])
        if isinstance(pending, list):
            answered_key = _question_key(question)
            document["pending_learning_questions"] = [
                item
                for item in pending
                if not isinstance(item, str) or _question_key(item) != answered_key
            ]
        _write_profile_contents(path, document)


class LearningBot:
    def __init__(
        self,
        token: str,
        store: Any,
        profile_path: Path,
        lock_path: Path | None = None,
        category_profile_paths: dict[str, Path] | None = None,
        profile_paths: dict[str, Path] | None = None,
    ) -> None:
        self.token = token
        self.store = store
        self.profile_path = profile_path
        self.lock_path = lock_path or _profile_lock(profile_path)
        self.category_profile_paths = {
            "recruiters": profile_path,
            **(category_profile_paths or {}),
        }
        self.profile_paths = profile_paths or {}

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        def send() -> Any:
            request = Request(
                f"https://api.telegram.org/bot{self.token}/{method}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=40) as response:
                result = json.loads(response.read())
            if not result.get("ok"):
                raise RuntimeError("Telegram Bot API request failed")
            return result.get("result")

        return await asyncio.to_thread(send)

    async def _send_message(self, chat_id: int, text: str) -> Any:
        return await self._call("sendMessage", {"chat_id": chat_id, "text": text})

    async def publish_next_question(self) -> None:
        chat_id = self.store.setting("learn_bot_owner_chat_id", "")
        if not chat_id:
            return
        paths = self.profile_paths or {"": self.profile_path}
        for profile_id, profile_path in paths.items():
            try:
                for question in _pending_profile_questions(profile_path, _profile_lock(profile_path)):
                    self.store.enqueue_learning_question(
                        question,
                        category="recruiters",
                        profile_id=profile_id,
                        source_platform="job_apply",
                        source_account_id=profile_id,
                    )
            except Exception as exc:
                logger.warning("Could not read a profile's learning questions (%s)", type(exc).__name__)
        queued = self.store.claim_next_learning_question()
        if queued is None:
            return
        try:
            sent = await self._send_message(int(chat_id), queued["question"])
        except Exception as exc:
            self.store.retry_learning_question(int(queued["id"]))
            logger.warning("Could not send a learning question (%s)", type(exc).__name__)
            return
        self.store.mark_learning_question_awaiting(int(queued["id"]), int(sent["message_id"]))
        logger.info("Sent one unanswered question to the owner's learning bot chat")

    async def process_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        sender_id = sender.get("id")
        chat_id = chat.get("id")
        text = message.get("text")
        if (
            not isinstance(sender_id, int)
            or not isinstance(chat_id, int)
            or chat.get("type") != "private"
            or sender_id not in self.store.learning_owner_ids()
            or chat_id != sender_id
            or not isinstance(text, str)
            or not text.strip()
        ):
            return

        if text.strip().split(maxsplit=1)[0].split("@", maxsplit=1)[0] == "/start":
            self.store.set_setting("learn_bot_owner_chat_id", str(chat_id))
            await self._send_message(
                chat_id,
                "Пришлю сюда вопросы, на которые не нашёл ответ. Ответь на вопрос сообщением в этом чате.",
            )
            await self.publish_next_question()
            return

        if text.startswith("/") or chat_id != int(
            self.store.setting("learn_bot_owner_chat_id", "0")
        ):
            return
        answer = text.strip()
        if len(answer) > 5000:
            return
        pending = self.store.awaiting_learning_question()
        if pending is None:
            return
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        if reply_to is not None and reply_to != pending["channel_message_id"]:
            return
        question_id = int(pending["id"])
        if not self.store.claim_learning_answer(question_id):
            return
        category = str(pending["category"])
        profile_id = str(pending["profile_id"] or "")
        if category == "recruiters" and profile_id:
            profile_path = self.profile_paths.get(profile_id)
        else:
            profile_path = self.category_profile_paths.get(category)
        if profile_path is None:
            self.store.retry_learning_answer(question_id)
            logger.error("No answer file configured for learning question category %s", category)
            return
        try:
            save_learned_answer(
                profile_path,
                pending["question"],
                answer,
                _profile_lock(profile_path),
            )
        except Exception:
            self.store.retry_learning_answer(question_id)
            raise
        self.store.finish_learning_question(question_id)
        logger.info("Saved an owner answer from the learning bot")
        await self._send_message(chat_id, "Сохранил ответ.")
        await self.publish_next_question()

    async def run_forever(self) -> None:
        try:
            bot = await self._call("getMe", {})
            if str(bot.get("username", "")).casefold() != BOT_USERNAME.casefold():
                raise RuntimeError("Configured token does not belong to the learning bot")
            webhook = await self._call("getWebhookInfo", {})
            if webhook.get("url"):
                raise RuntimeError("Learning bot already has a webhook configured")
            logger.info("Learning bot token validated")
        except Exception as exc:
            logger.error("Learning bot is unavailable (%s)", type(exc).__name__)
            return

        while True:
            try:
                await self.publish_next_question()
                offset = int(self.store.setting("learn_bot_update_offset", "0") or 0)
                updates = await self._call(
                    "getUpdates",
                    {"offset": offset, "timeout": 20, "allowed_updates": ["message"]},
                )
                for update in updates or []:
                    await self.process_update(update)
                    offset = int(update["update_id"]) + 1
                    self.store.set_setting("learn_bot_update_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Learning bot polling failed (%s)", type(exc).__name__)
                await asyncio.sleep(5)
