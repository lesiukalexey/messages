from __future__ import annotations

import fcntl
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml


LOCK_PATH = Path("/home/admin/messages-runtime/learned-answers.lock")
BOT_USERNAME = "learnDataBot"
logger = logging.getLogger(__name__)


def save_learned_answer(
    path: Path, question: str, answer: str, lock_path: Path = LOCK_PATH
) -> None:
    question = " ".join(question.split())[:500]
    answer = answer.strip()[:5000]
    if not question or not answer:
        raise ValueError("A question and answer are required")

    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.parent.chmod(0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with path.open("r+", encoding="utf-8") as profile:
            document = yaml.safe_load(profile) or {}
            if not isinstance(document, dict):
                raise ValueError("The profile YAML root must be a mapping")
            learned = document.setdefault("learned_answers", {})
            if not isinstance(learned, dict):
                raise ValueError("learned_answers must be a YAML mapping")
            learned[question] = answer
            rendered = yaml.safe_dump(
                document,
                allow_unicode=True,
                sort_keys=False,
                width=100,
                default_flow_style=False,
            )
            profile.seek(0)
            profile.write(rendered)
            profile.truncate()
            profile.flush()
            os.fsync(profile.fileno())


class LearningBot:
    def __init__(
        self, token: str, store: Any, profile_path: Path, lock_path: Path = LOCK_PATH
    ) -> None:
        self.token = token
        self.store = store
        self.profile_path = profile_path
        self.lock_path = lock_path

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
        try:
            save_learned_answer(
                self.profile_path,
                pending["question"],
                answer,
                self.lock_path,
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
