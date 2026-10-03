from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .black_castle_scene import black_castle_caption_and_keyboard


BOT_USERNAME = "KnigaIgraBot"
logger = logging.getLogger(__name__)


class BlackCastleBot:
    def __init__(self, token: str, store: Any, scene_path: Path) -> None:
        self.token = token
        self.store = store
        self.scene_path = scene_path

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

    async def _send_scene(self, chat_id: int) -> None:
        photo_id = self.store.setting("kniga_igra_black_castle_photo_file_id", "")
        if not photo_id:
            await self._send_message(chat_id, "Сцена сейчас недоступна. Попробуй написать позже.")
            logger.warning("BlackCastle bot has no registered opening photo")
            return
        caption, buttons = black_castle_caption_and_keyboard(self.scene_path)
        await self._call("sendPhoto", {
            "chat_id": chat_id,
            "photo": photo_id,
            "caption": caption,
            "reply_markup": {"inline_keyboard": [buttons]},
        })

    async def _answer_inline_query(self, query: dict[str, Any]) -> None:
        query_id = query.get("id")
        query_text = str(query.get("query") or "").strip().casefold()
        if not isinstance(query_id, str) or query_text != "black_castle_opening":
            return
        photo_id = self.store.setting("kniga_igra_black_castle_photo_file_id", "")
        results: list[dict[str, Any]] = []
        if photo_id:
            caption, buttons = black_castle_caption_and_keyboard(self.scene_path)
            results.append({
                "type": "photo",
                "id": "black_castle_opening",
                "photo_file_id": photo_id,
                "caption": caption,
                "reply_markup": {"inline_keyboard": [buttons]},
            })
        await self._call("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": results,
            "cache_time": 0,
            "is_personal": True,
        })

    async def process_update(self, update: dict[str, Any]) -> None:
        query = update.get("inline_query")
        if isinstance(query, dict):
            await self._answer_inline_query(query)
            return

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            if callback.get("data") == "black_castle_noop":
                callback_id = callback.get("id")
                if isinstance(callback_id, str):
                    await self._call("answerCallbackQuery", {"callback_query_id": callback_id})
            return

        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        sender_id = sender.get("id")
        chat_id = chat.get("id")
        if (
            not isinstance(sender_id, int)
            or not isinstance(chat_id, int)
            or sender.get("is_bot")
            or chat.get("type") != "private"
            or chat_id != sender_id
        ):
            return

        text = str(message.get("text") or message.get("caption") or "").strip()
        command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0] if text else ""
        photos = message.get("photo")
        if (
            command == "/blackcastle_photo"
            and sender_id in self.store.learning_owner_ids()
            and isinstance(photos, list)
            and photos
            and isinstance(photos[-1], dict)
            and isinstance(photos[-1].get("file_id"), str)
        ):
            self.store.set_setting(
                "kniga_igra_black_castle_photo_file_id", photos[-1]["file_id"]
            )
            await self._send_message(chat_id, "Фото для BlackCastle сохранено.")
            logger.info("Registered the BlackCastle opening photo for the game bot")
            return

        await self._send_scene(chat_id)

    async def run_forever(self) -> None:
        try:
            bot = await self._call("getMe", {})
            if str(bot.get("username", "")).casefold() != BOT_USERNAME.casefold():
                raise RuntimeError("Configured token does not belong to the BlackCastle bot")
            if not bot.get("supports_inline_queries"):
                logger.warning("BlackCastle bot inline mode is disabled; folder replies will fail closed")
            webhook = await self._call("getWebhookInfo", {})
            if webhook.get("url"):
                raise RuntimeError("BlackCastle bot already has a webhook configured")
            logger.info("BlackCastle bot token validated")
        except Exception as exc:
            logger.error("BlackCastle bot is unavailable (%s)", type(exc).__name__)
            return

        while True:
            try:
                offset = int(self.store.setting("kniga_igra_update_offset", "0") or 0)
                updates = await self._call(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 20,
                        "allowed_updates": ["message", "inline_query", "callback_query"],
                    },
                )
                for update in updates or []:
                    await self.process_update(update)
                    offset = int(update["update_id"]) + 1
                    self.store.set_setting("kniga_igra_update_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("BlackCastle bot polling failed (%s)", type(exc).__name__)
                await asyncio.sleep(5)
