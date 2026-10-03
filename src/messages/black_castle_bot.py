from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

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
                raise RuntimeError(str(result.get("description") or "Telegram Bot API request failed"))
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
            "items": ["Меч", "Фляга"],
            "gold": 15,
            "water_sips": 2,
            "bag_capacity": 7,
            "direct_message_id": 0,
            "direct_message_ids": [],
        }

    def _get_or_create_state(self, player_id: int) -> dict[str, Any]:
        state = self._load_state(player_id)
        if state is None:
            state = self._new_state()
            self._save_state(player_id, state)
        else:
            items = state.get("items")
            if isinstance(items, list):
                filtered_items = [
                    item for item in items
                    if not (
                        isinstance(item, str)
                        and item.strip().casefold() == "заплечный мешок"
                    )
                ]
                if len(filtered_items) != len(items):
                    state["items"] = filtered_items
                    self._save_state(player_id, state)
        return state

    def _save_state(self, player_id: int, state: dict[str, Any]) -> None:
        self.game_store.save_player_state(player_id, state)

    def _screen(self, state: dict[str, Any]) -> tuple[str, list[list[dict[str, str]]], bool]:
        scene = json.loads(self.scene_path.read_text(encoding="utf-8"))
        view = state.get("view", "step")
        step = state.get("step", 1)
        if view == "preface":
            preface = self.game_store.get_book_page("preface")
            preface_text = (
                str(preface["body"])
                if preface is not None
                else str(scene["preface"])
            )
            return preface_text, [[{
                "text": "Продолжить",
                "callback_data": "blackcastle:continue",
            }]], False

        if view == "stats":
            values = state["characteristics"]
            text = (
                "Характеристики\n\n"
                f"МАСТЕРСТВО: {values['mastery']}\n"
                f"ВЫНОСЛИВОСТЬ: {values['stamina']}\n"
                f"УДАЧА: {values['luck']}"
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
            paragraph = self.game_store.get_paragraph(step)
            if paragraph is None:
                text = f"Параграф {step}\n\nЭта страница ещё не добавлена."
                keyboard = [[{"text": "К шагу 1", "callback_data": "blackcastle:step:1"}]]
                return text, keyboard, False

            title = str(paragraph["title"])
            body = paragraph.get("body")
            text = title
            if body:
                text += f"\n\n{body}"
            else:
                text += "\n\nТекст этого параграфа будет добавлен позже."
            question = paragraph.get("question")
            if question:
                text += f"\n\n{question}"

            choices = self.game_store.get_paragraph_choices(step)
            keyboard = []
            for choice in choices:
                required_item = choice.get("required_item")
                if required_item and not self._has_item(state, str(required_item)):
                    continue
                keyboard.append([{
                    "text": str(choice["button_text"]),
                    "callback_data": f"blackcastle:route:{step}:{choice['choice_id']}",
                }])
            if step == 1:
                keyboard.extend([
                    [
                        {"text": "Предисловие", "callback_data": "blackcastle:preface"},
                        {"text": "Характеристики", "callback_data": "blackcastle:stats"},
                    ],
                    [{"text": "Инвентарь", "callback_data": "blackcastle:inventory"}],
                ])
            elif not keyboard:
                keyboard.append([{
                    "text": "К шагу 1",
                    "callback_data": "blackcastle:step:1",
                }])
            return text, keyboard, True

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

    @staticmethod
    def _has_item(state: dict[str, Any], required_item: str) -> bool:
        def key(value: str) -> tuple[str, ...]:
            words = []
            for word in value.casefold().replace("ё", "е").split():
                cleaned = "".join(char for char in word if char.isalnum())
                if cleaned:
                    words.append(cleaned[:4])
            return tuple(words)

        needed = key(required_item)
        for item in state.get("items", []):
            if isinstance(item, str):
                available = key(item)
                if needed and all(any(word.startswith(token[:3]) for word in available) for token in needed):
                    return True
        return False

    @staticmethod
    def _consume_item(state: dict[str, Any], required_item: str) -> bool:
        items = state.get("items", [])
        for index, item in enumerate(items):
            if not isinstance(item, str):
                continue
            if BlackCastleBot._has_item({"items": [item]}, required_item):
                del items[index]
                return True
        return False

    def _paragraph_photo(self, paragraph_number: int) -> str:
        paragraph = self.game_store.get_paragraph(paragraph_number)
        if paragraph and paragraph.get("photo_file_id"):
            return str(paragraph["photo_file_id"])
        return self.game_store.get_setting("kniga_igra_black_castle_photo_file_id")

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
        elif state.get("view") == "step":
            parts = self._preface_parts(text)
            part = max(0, min(int(state.get("page_part", 0)), len(parts) - 1))
            text = parts[part]
            if part + 1 < len(parts):
                keyboard = [[{
                    "text": "Продолжить",
                    "callback_data": "blackcastle:page_next",
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
        old_ids = state.get("direct_message_ids")
        if not isinstance(old_ids, list):
            old_ids = []
        old_ids = [message_id for message_id in old_ids if isinstance(message_id, int) and message_id > 0]
        fallback_id = state.get("direct_message_id")
        if not old_ids and isinstance(fallback_id, int) and fallback_id > 0:
            old_ids.append(fallback_id)
        if previous_message_id > 0 and previous_message_id not in old_ids:
            old_ids.append(previous_message_id)
        for old_id in dict.fromkeys(old_ids):
            await self._delete_message(chat_id, old_id)

        text, keyboard, has_photo = self._screen(state)
        parts = self._preface_parts(text)
        photo_id = self._paragraph_photo(int(state.get("step", 1))) if has_photo else ""
        if has_photo and not photo_id:
            parts = ["Сцена сейчас недоступна. Попробуй написать позже."]
            keyboard = [[{"text": "Обновить", "callback_data": "blackcastle:continue"}]]
            has_photo = False

        message_ids: list[int] = []
        try:
            for part_index, part in enumerate(parts):
                is_last = part_index == len(parts) - 1
                markup = {"inline_keyboard": keyboard} if is_last else None
                if part_index == 0 and has_photo:
                    payload: dict[str, Any] = {
                        "chat_id": chat_id,
                        "photo": photo_id,
                        "caption": part,
                    }
                    if markup:
                        payload["reply_markup"] = markup
                    sent = await self._call("sendPhoto", payload)
                else:
                    payload = {"chat_id": chat_id, "text": part}
                    if markup:
                        payload["reply_markup"] = markup
                    sent = await self._call("sendMessage", payload)
                sent_id = sent.get("message_id")
                if isinstance(sent_id, int) and sent_id > 0:
                    message_ids.append(sent_id)
        except Exception:
            for sent_id in message_ids:
                await self._delete_message(chat_id, sent_id)
            raise

        state["direct_message_ids"] = message_ids
        state["direct_message_id"] = message_ids[-1] if message_ids else 0
        self._save_state(player_id, state)

    async def _edit_inline_screen(self, inline_message_id: str, state: dict[str, Any]) -> None:
        text, keyboard = self._inline_screen(state)
        payload = {"inline_message_id": inline_message_id, "reply_markup": {"inline_keyboard": keyboard}}
        if state.get("view") == "step":
            photo_id = self._paragraph_photo(int(state.get("step", 1)))
            if not photo_id:
                raise RuntimeError("BlackCastle has no paragraph or default photo")
            payload["media"] = {"type": "photo", "media": photo_id, "caption": text}
            await self._call("editMessageMedia", payload)
        else:
            payload["caption"] = text
            await self._call("editMessageCaption", payload)

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
        photo_id = self._paragraph_photo(int(state.get("step", 1)))
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
            elif action == "blackcastle:page_next":
                state["page_part"] = int(state.get("page_part", 0)) + 1
            elif action == "blackcastle:stats":
                state["view"] = "stats"
            elif action == "blackcastle:inventory":
                state["view"] = "inventory"
            elif action == "blackcastle:continue":
                state["view"] = "step"
                state["step"] = 1
                state["page_part"] = 0
            elif action.startswith("blackcastle:step:"):
                try:
                    target_step = int(action.rsplit(":", 1)[1])
                    if target_step != 1 and (state.get("step") != 1 or target_step not in {86, 110}):
                        return
                    if self.game_store.get_paragraph(target_step) is None:
                        return
                    state["step"] = target_step
                    state["view"] = "step"
                    state["page_part"] = 0
                except ValueError:
                    return
            elif action.startswith("blackcastle:route:"):
                try:
                    _, _, source_text, choice_id = action.split(":", 3)
                    source_step = int(source_text)
                except ValueError:
                    return
                if state.get("view") != "step" or state.get("step") != source_step:
                    return
                choice = self.game_store.get_paragraph_choice(source_step, choice_id)
                if choice is None:
                    return
                required_item = choice.get("required_item")
                if required_item and not self._consume_item(state, str(required_item)):
                    return
                target_step = int(choice["target_paragraph"])
                if self.game_store.get_paragraph(target_step) is None:
                    return
                state["step"] = target_step
                state["view"] = "step"
                state["page_part"] = 0
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
        photo_target = None
        if command.startswith("/blackcastle_photo") and len(text.split()) == 2:
            try:
                photo_target = int(text.split()[1])
            except ValueError:
                photo_target = -1
        if (
            command == "/blackcastle_photo"
            and sender_id in self.owner_store.learning_owner_ids()
            and isinstance(photos, list)
            and photos
            and isinstance(photos[-1], dict)
            and isinstance(photos[-1].get("file_id"), str)
        ):
            photo_id = photos[-1]["file_id"]
            if photo_target is None:
                self.game_store.set_setting("kniga_igra_black_castle_photo_file_id", photo_id)
                await self._send_message(chat_id, "Фото по умолчанию для BlackCastle сохранено.")
                logger.info("Registered the default BlackCastle photo for the game bot")
            elif photo_target > 0 and self.game_store.set_paragraph_photo(photo_target, photo_id):
                await self._send_message(chat_id, f"Фото параграфа {photo_target} сохранено.")
                logger.info("Registered a BlackCastle paragraph photo")
            else:
                await self._send_message(chat_id, "Параграф не найден. Укажи номер существующей страницы.")
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
