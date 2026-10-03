from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .black_castle_scene import black_castle_caption_and_keyboard


BOT_USERNAME = "KnigaIgraBot"
logger = logging.getLogger(__name__)


class BlackCastleBot:
    def __init__(
        self, token: str, owner_store: Any, game_store: Any, scene_path: Path
    ) -> None:
        self.token = token
        self.owner_store = owner_store
        self.game_store = game_store
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

    def _load_state(self, player_id: int) -> dict[str, Any] | None:
        return self.game_store.get_player_state(player_id)

    def _new_state(self) -> dict[str, Any]:
        mastery = random.randint(1, 6) + 6
        stamina = random.randint(1, 6) + random.randint(1, 6) + 12
        luck = random.randint(1, 6) + 6
        return {
            "step": 1,
            "view": "step",
            "characteristics": {
                "mastery": mastery,
                "max_mastery": mastery,
                "stamina": stamina,
                "max_stamina": stamina,
                "luck": luck,
                "max_luck": luck,
            },
            "items": ["Меч", "Фляга", "Заплечный мешок"],
            "gold": 15,
            "water_sips": 2,
            "bag_capacity": 7,
            "direct_message_id": 0,
        }

    def _get_or_create_state(self, player_id: int) -> dict[str, Any]:
        state = self._load_state(player_id)
        if state is None:
            state = self._new_state()
            self._save_state(player_id, state)
        return state

    def _save_state(self, player_id: int, state: dict[str, Any]) -> None:
        self.game_store.save_player_state(player_id, state)

    def _screen(self, state: dict[str, Any]) -> tuple[str, list[list[dict[str, str]]], bool]:
        scene = json.loads(self.scene_path.read_text(encoding="utf-8"))
        view = state.get("view", "step")
        step = state.get("step", 1)
        if view == "step" and step == 1:
            caption, keyboard = black_castle_caption_and_keyboard(self.scene_path)
            return caption, keyboard, True

        if view == "preface":
            return str(scene["preface"]), [[{
                "text": "Продолжить",
                "callback_data": "blackcastle:continue",
            }]], False

        if view == "stats":
            values = state["characteristics"]
            text = (
                "Характеристики\n\n"
                f"МАСТЕРСТВО: {values['mastery']}/{values['max_mastery']}\n"
                f"ВЫНОСЛИВОСТЬ: {values['stamina']}/{values['max_stamina']}\n"
                f"УДАЧА: {values['luck']}/{values['max_luck']}"
            )
            return text, [[{
                "text": "К шагу 1",
                "callback_data": "blackcastle:continue",
            }]], False

        if view == "inventory":
            items = "\n".join(f"• {item}" for item in state["items"])
            text = (
                "Инвентарь\n\n"
                f"{items}\n"
                f"Фляга: {state['water_sips']} глотка; каждый восстанавливает 2 ВЫНОСЛИВОСТИ.\n"
                f"Заплечный мешок: 0/{state['bag_capacity']} предметов.\n"
                f"Золотые: {state['gold']}"
            )
            return text, [[{
                "text": "К шагу 1",
                "callback_data": "blackcastle:continue",
            }]], False

        if view == "step" and isinstance(step, int):
            return (
                f"Параграф {step}\n\nТекст этого параграфа будет добавлен позже.",
                [[{"text": "К шагу 1", "callback_data": "blackcastle:step:1"}]],
                False,
            )

        state["step"] = 1
        state["view"] = "step"
        return self._screen(state)

    @staticmethod
    def _preface_parts(text: str, limit: int = 950) -> list[str]:
        parts: list[str] = []
        remaining = text
        while len(remaining) > limit:
            cut = remaining.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            parts.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        parts.append(remaining)
        return parts

    def _inline_screen(
        self, state: dict[str, Any]
    ) -> tuple[str, list[list[dict[str, str]]]]:
        text, keyboard, _ = self._screen(state)
        if state.get("view") == "preface":
            parts = self._preface_parts(text)
            part = max(0, min(int(state.get("preface_part", 0)), len(parts) - 1))
            text = parts[part]
            if part + 1 < len(parts):
                keyboard = [[{
                    "text": "Продолжить",
                    "callback_data": "blackcastle:preface_next",
                }]]
            else:
                keyboard = [[{
                    "text": "Продолжить",
                    "callback_data": "blackcastle:continue",
                }]]
        return text, keyboard

    async def _delete_message(self, chat_id: int, message_id: int) -> None:
        if not message_id:
            return
        try:
            await self._call("deleteMessage", {
                "chat_id": chat_id,
                "message_id": message_id,
            })
        except Exception as exc:
            logger.info("Could not replace previous BlackCastle bot message (%s)", type(exc).__name__)

    async def _send_direct_screen(
        self, chat_id: int, player_id: int, state: dict[str, Any], previous_message_id: int = 0
    ) -> None:
        await self._delete_message(chat_id, previous_message_id)
        text, keyboard, has_photo = self._screen(state)
        markup = {"inline_keyboard": keyboard}
        if has_photo:
            photo_id = self.game_store.get_setting("kniga_igra_black_castle_photo_file_id")
            if not photo_id:
                await self._send_message(chat_id, "Сцена сейчас недоступна. Попробуй написать позже.")
                logger.warning("BlackCastle bot has no registered opening photo")
                return
            message = await self._call("sendPhoto", {
                "chat_id": chat_id,
                "photo": photo_id,
                "caption": text,
                "reply_markup": markup,
            })
        else:
            message = await self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": markup,
            })
        state["direct_message_id"] = int(message.get("message_id", 0))
        self._save_state(player_id, state)

    async def _edit_inline_screen(self, inline_message_id: str, state: dict[str, Any]) -> None:
        text, keyboard = self._inline_screen(state)
        await self._call("editMessageCaption", {
            "inline_message_id": inline_message_id,
            "caption": text,
            "reply_markup": {"inline_keyboard": keyboard},
        })

    async def _answer_inline_query(self, query: dict[str, Any]) -> None:
        query_id = query.get("id")
        query_text = str(query.get("query") or "").strip().casefold()
        prefix = "blackcastle_player_"
        if not isinstance(query_id, str) or not query_text.startswith(prefix):
            return
        try:
            player_id = int(query_text[len(prefix):])
        except ValueError:
            return
        state = self._get_or_create_state(player_id)
        photo_id = self.game_store.get_setting("kniga_igra_black_castle_photo_file_id")
        results: list[dict[str, Any]] = []
        if photo_id:
            caption, keyboard = self._inline_screen(state)
            results.append({
                "type": "photo",
                "id": f"black_castle_player_{player_id}",
                "photo_file_id": photo_id,
                "caption": caption,
                "reply_markup": {"inline_keyboard": keyboard},
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
            callback_id = callback.get("id")
            if isinstance(callback_id, str):
                await self._call("answerCallbackQuery", {"callback_query_id": callback_id})
            sender = callback.get("from") or {}
            player_id = sender.get("id")
            action = str(callback.get("data") or "")
            if not isinstance(player_id, int) or not action.startswith("blackcastle:"):
                return
            state = self._get_or_create_state(player_id)
            if action == "blackcastle:preface":
                state["view"] = "preface"
                state["preface_part"] = 0
            elif action == "blackcastle:preface_next":
                state["preface_part"] = int(state.get("preface_part", 0)) + 1
            elif action == "blackcastle:stats":
                state["view"] = "stats"
            elif action == "blackcastle:inventory":
                state["view"] = "inventory"
            elif action == "blackcastle:continue":
                state["view"] = "step"
                state["step"] = 1
            elif action.startswith("blackcastle:step:"):
                try:
                    state["step"] = int(action.rsplit(":", 1)[1])
                    state["view"] = "step"
                except ValueError:
                    return
            else:
                return
            self._save_state(player_id, state)
            inline_message_id = callback.get("inline_message_id")
            if isinstance(inline_message_id, str):
                await self._edit_inline_screen(inline_message_id, state)
                return
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            previous_message_id = message.get("message_id")
            if isinstance(chat_id, int):
                await self._send_direct_screen(
                    chat_id,
                    player_id,
                    state,
                    previous_message_id if isinstance(previous_message_id, int) else 0,
                )
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
            and sender_id in self.owner_store.learning_owner_ids()
            and isinstance(photos, list)
            and photos
            and isinstance(photos[-1], dict)
            and isinstance(photos[-1].get("file_id"), str)
        ):
            self.game_store.set_setting(
                "kniga_igra_black_castle_photo_file_id", photos[-1]["file_id"]
            )
            await self._send_message(chat_id, "Фото для BlackCastle сохранено.")
            logger.info("Registered the BlackCastle opening photo for the game bot")
            return

        state = self._get_or_create_state(sender_id)
        previous_message_id = state.get("direct_message_id", 0)
        await self._send_direct_screen(
            chat_id,
            sender_id,
            state,
            previous_message_id if isinstance(previous_message_id, int) else 0,
        )

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
                offset = int(self.game_store.get_setting("kniga_igra_update_offset", "0") or 0)
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
                    self.game_store.set_setting("kniga_igra_update_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("BlackCastle bot polling failed (%s)", type(exc).__name__)
                await asyncio.sleep(5)
