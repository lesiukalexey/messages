from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

BOT_USERNAME = "KnigaIgraBot"
ROUTE_BUTTON_MAX_LENGTH = 50
ROUTE_BUTTON_SUFFIX = re.compile(r"\s+[—–-]\s*\d+\s*$")
logger = logging.getLogger(__name__)


class BlackCastleBot:
    def __init__(
        self,
        token: str,
        owner_store: Any,
        game_store: Any,
        scene_path: Path,
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
            "direct_message_has_photo": None,
            "folder_screen_message_id": 0,
            "folder_screen_message_ids": [],
            "folder_screen_tracking_initialized": False,
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

    @staticmethod
    def _format_telegram_text(text: str) -> str:
        """Add readable Telegram HTML formatting while keeping book text intact."""
        title, separator, body = text.partition("\n\n")
        if not separator:
            return html.escape(text)

        title = title.strip()
        is_heading = bool(re.fullmatch(
            r"Шаг \d+|Характеристики|Инвентарь|Характеристики и инвентарь|Книга-игра",
            title,
        ))
        if not is_heading:
            body = text

        icons = {
            "Шаг 1": "📖",
            "Характеристики": "🎲",
            "Инвентарь": "🎒",
            "Характеристики и инвентарь": "🎲",
            "Книга-игра": "📚",
        }
        icon = icons.get(title, "📖")
        formatted = [f"{icon} <b>{html.escape(title)}</b>"] if is_heading else []
        is_list_screen = title in {
            "Характеристики", "Инвентарь", "Характеристики и инвентарь"
        } and is_heading

        for block in re.split(r"\n\s*\n", body.strip()):
            block = block.strip()
            if not block:
                continue
            if block == "Вы пойдете:":
                formatted.append(f"<b>{html.escape(block)}</b>")
                continue

            lines = block.splitlines()
            if is_list_screen and len(lines) > 1:
                formatted_lines = []
                for line in lines:
                    match = re.match(r"^(\s*(?:•\s*)?[^:]+:)(\s*)(.*)$", line)
                    if match:
                        prefix = html.escape(match.group(1))
                        value = html.escape(match.group(3))
                        formatted_lines.append(f"<b>{prefix}</b> {value}".rstrip())
                    else:
                        formatted_lines.append(html.escape(line))
                formatted.append("\n".join(formatted_lines))
                continue

            prompt = None
            if (
                len(lines) > 1
                and len(lines[-1].strip()) <= 140
                and lines[-1].strip().endswith((":", "?"))
            ):
                prompt = lines.pop().strip()

            prose = " ".join(line.strip() for line in lines).strip()
            if not prose and prompt is None and block.strip().endswith((":", "?")):
                prompt = block.strip()

            if prose:
                sentences = re.findall(
                    r".+?[.!?…](?:[»”\"’]+)?(?:\s*[—–-]\s*\d{1,3}\.?)?(?=\s|$)|.+$",
                    prose,
                ) or [prose]
                groups = []
                start = 0
                route_reference = re.compile(r"[—–-]\s*\d{1,3}\.?\s*$")
                while start < len(sentences):
                    end = min(start + 3, len(sentences))
                    while (
                        end < len(sentences)
                        and route_reference.search(sentences[end - 1])
                        and route_reference.search(sentences[end])
                    ):
                        end += 1
                    groups.append(sentences[start:end])
                    start = end
                formatted.append("\n\n".join(
                    html.escape(" ".join(sentence.strip() for sentence in group))
                    for group in groups
                ))
            if prompt is not None:
                formatted.append(f"<b>{html.escape(prompt)}</b>")

        return "\n\n".join(formatted)

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
            }]], True

        if view in {"stats", "inventory", "status"}:
            values = state["characteristics"]
            carried_items = [
                item for item in state.get("items", []) if isinstance(item, str)
            ]
            equipment_names = {"меч", "фляга", "заплечный мешок"}
            bag_items = [
                item for item in carried_items
                if item.strip().casefold() not in equipment_names
            ]
            bag_listing = "\n".join(f"• {item}" for item in bag_items) or "Пусто"
            text = (
                "Характеристики и инвентарь\n\n"
                "Характеристики:\n"
                f"МАСТЕРСТВО: {values['mastery']}\n"
                f"ВЫНОСЛИВОСТЬ: {values['stamina']}\n"
                f"УДАЧА: {values['luck']}\n\n"
                "Инвентарь:\n"
                "Снаряжение: меч\n"
                f"Фляга: {state['water_sips']} глотка; каждый восстанавливает 2 ВЫНОСЛИВОСТИ.\n"
                f"Заплечный мешок: {len(bag_items)}/{state['bag_capacity']} предметов:\n"
                f"{bag_listing}\n"
                f"Золотые: {state['gold']}"
            )
            return text, [[{
                "text": f"К шагу {step}",
                "callback_data": "blackcastle:back",
            }]], False

        if view == "step" and isinstance(step, int):
            paragraph = self.game_store.get_paragraph(step)
            if paragraph is None:
                heading = f"Шаг {step}"
                text = f"{heading}\n\nЭта страница ещё не добавлена."
                keyboard = [[{"text": "К шагу 1", "callback_data": "blackcastle:step:1"}]]
                return text, keyboard, False

            title = f"Шаг {step}"
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
            luck_checks = state.get("luck_checks")
            luck_result = (
                luck_checks.get(str(step))
                if isinstance(luck_checks, dict)
                else None
            )
            keyboard = []
            for choice in choices:
                required_item = choice.get("required_item")
                if required_item and not self._has_item(state, str(required_item)):
                    continue
                target = int(choice["target_paragraph"])
                source_label = ROUTE_BUTTON_SUFFIX.sub(
                    "", str(choice["button_text"])
                ).strip()
                is_luck_success_route = bool(
                    re.fullmatch(
                        r"Если(?: вы)? удачливы|Проверить удачу",
                        source_label,
                        re.IGNORECASE,
                    )
                ) and bool(
                    body
                    and re.search(r"ПРОВЕРЬТЕ СВОЮ УДАЧУ", body, re.IGNORECASE)
                )
                if is_luck_success_route and isinstance(luck_result, dict):
                    if not luck_result.get("lucky"):
                        continue
                    button_text = self._route_button_text(
                        f"Удача улыбнулась вам", target
                    )
                else:
                    button_text = self._route_button_text(
                        str(choice["button_text"]), target
                    )
                keyboard.append([{
                    "text": button_text,
                    "callback_data": f"blackcastle:route:{step}:{choice['choice_id']}",
                }])
            if step == 1:
                keyboard.append([{
                    "text": "Предисловие",
                    "callback_data": "blackcastle:preface",
                }])
            elif not keyboard:
                keyboard.append([{
                    "text": "К шагу 1",
                    "callback_data": "blackcastle:step:1",
                }])
            keyboard.append([{
                "text": "Характеристики и инвентарь",
                "callback_data": "blackcastle:status",
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

    @staticmethod
    def _route_button_text(label: str, target_paragraph: int) -> str:
        wording = ROUTE_BUTTON_SUFFIX.sub("", str(label)).strip() or "Продолжить"
        if re.match(r"если(?: вы)? удачливы", wording, re.IGNORECASE):
            wording = "Проверить удачу"
        if re.search(r"\bпобед(?:ил|или)\w*\b", wording, re.IGNORECASE):
            wording = "Вступить в бой"
        spell = re.fullmatch(
            r"(?:заклинание|заклятие)?\s*(левитации|огня|иллюзии|силы|слабости|копии|исцеления|плавания)",
            wording,
            re.IGNORECASE,
        ) or re.search(
            r"(?:заклин\w*|заклят\w*)\s+(левитации|огня|иллюзии|силы|слабости|копии|исцеления|плавания)\b",
            wording,
            re.IGNORECASE,
        )
        if spell:
            wording = f"Заклинание {spell.group(1).capitalize()}"
        suffix = f"\u00a0—\u00a0{target_paragraph}"
        wording_limit = ROUTE_BUTTON_MAX_LENGTH - len(suffix)
        if len(wording) > wording_limit:
            clipped = wording[:wording_limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
            if not clipped:
                clipped = wording[:wording_limit - 1]
            wording = f"{clipped.rstrip()}…"
        return f"{wording}{suffix}"

    @staticmethod
    def _resolve_luck_check(
        state: dict[str, Any], step: int, *, check: bool
    ) -> str | None:
        checks = state.setdefault("luck_checks", {})
        if not isinstance(checks, dict):
            checks = {}
            state["luck_checks"] = checks
        key = str(step)
        if key in checks:
            return None

        characteristics = state.get("characteristics")
        if not isinstance(characteristics, dict):
            characteristics = {"luck": 0}
            state["characteristics"] = characteristics
        try:
            luck = max(0, int(characteristics.get("luck", 0)))
        except (TypeError, ValueError):
            luck = 0
            characteristics["luck"] = luck
        roll = random.randint(1, 6) + random.randint(1, 6) if check and luck else None
        lucky = roll is not None and roll <= luck
        if luck:
            characteristics["luck"] = luck - 1
        checks[key] = {"luck_before": luck, "roll": roll, "lucky": lucky}

        if roll is None:
            reason = "проверка пропущена" if check else "проверка не проводилась"
            if check and not luck:
                reason = "удача равна нулю"
            return (
                f"Ваша удача: {luck}. Проверка удачи: {reason}. "
                "Результат: Вас настигла неудача."
            )
        outcome = "Удача улыбнулась вам." if lucky else "Вас настигла неудача."
        return (
            f"Ваша удача: {luck}. Проверка удачи выпала: {roll}. "
            f"Результат: {outcome}"
        )

    def _paged_screen(
        self, state: dict[str, Any], limit: int = 950
    ) -> tuple[str, list[list[dict[str, str]]], bool]:
        text, keyboard, _ = self._screen(state)
        part_key = "preface_part" if state.get("view") == "preface" else "page_part"
        parts = self._preface_parts(text, limit=limit)
        part = max(0, min(int(state.get(part_key, 0)), len(parts) - 1))
        state[part_key] = part
        if len(parts) == 1:
            return parts[0], keyboard, True

        navigation: list[dict[str, str]] = []
        if part > 0:
            navigation.append({"text": "Назад", "callback_data": "blackcastle:page_prev"})
        if part + 1 < len(parts):
            action = "blackcastle:preface_next" if state.get("view") == "preface" else "blackcastle:page_next"
            navigation.append({"text": "Читать продолжение", "callback_data": action})
        keyboard = [navigation] + (keyboard if part == len(parts) - 1 else [])
        return parts[part], keyboard, part == 0

    def _inline_screen(
        self, state: dict[str, Any]
    ) -> tuple[str, list[list[dict[str, str]]]]:
        text, keyboard, _ = self._paged_screen(state, limit=950)
        return self._format_telegram_text(text), keyboard

    async def _delete_message(self, chat_id: int, message_id: int) -> bool:
        if not message_id:
            return True
        try:
            await self._call("deleteMessage", {
                "chat_id": chat_id,
                "message_id": message_id,
            })
            return True
        except Exception as exc:
            error = str(exc).casefold()
            if "message to delete not found" in error or "message_id_invalid" in error:
                return True
            logger.info("Could not replace previous BlackCastle bot message (%s)", type(exc).__name__)
            return False

    async def _send_direct_screen(
        self, chat_id: int, player_id: int, state: dict[str, Any], previous_message_id: int = 0
    ) -> None:
        old_ids = state.get("direct_message_ids")
        if not isinstance(old_ids, list):
            old_ids = []
        old_ids = [message_id for message_id in old_ids if isinstance(message_id, int) and message_id > 0]
        fallback_id = state.get("direct_message_id")
        if isinstance(fallback_id, int) and fallback_id > 0:
            old_ids.append(fallback_id)
        if previous_message_id > 0:
            old_ids.append(previous_message_id)
        old_ids = list(dict.fromkeys(old_ids))

        text, keyboard, first_part = self._paged_screen(state, limit=950)
        _, _, has_photo = self._screen(state)
        photo_id = self._paragraph_photo(int(state.get("step", 1))) if has_photo and first_part else ""
        if has_photo and first_part and not photo_id:
            text = "Сцена сейчас недоступна. Попробуй написать позже."
            keyboard = [[{"text": "Обновить", "callback_data": "blackcastle:continue"}]]

        for old_id in old_ids:
            if not await self._delete_message(chat_id, old_id):
                logger.warning("Keeping existing BlackCastle screen because its message could not be deleted")
                return

        # Clear stale IDs before sending so a failed send cannot leave the state pointing
        # at messages that have already been removed.
        state["direct_message_ids"] = []
        state["direct_message_id"] = 0
        state["direct_message_has_photo"] = None
        self._save_state(player_id, state)

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": keyboard},
        }
        formatted = self._format_telegram_text(text)
        if photo_id:
            payload.update({"photo": photo_id, "caption": formatted})
            sent = await self._call("sendPhoto", payload)
            has_photo = True
        else:
            payload["text"] = formatted
            sent = await self._call("sendMessage", payload)
            has_photo = False

        sent_id = sent.get("message_id")
        state["direct_message_ids"] = [sent_id] if isinstance(sent_id, int) and sent_id > 0 else []
        state["direct_message_id"] = sent_id if isinstance(sent_id, int) and sent_id > 0 else 0
        state["direct_message_has_photo"] = has_photo
        self._save_state(player_id, state)

    async def _edit_inline_screen(self, inline_message_id: str, state: dict[str, Any]) -> None:
        text, keyboard = self._inline_screen(state)
        payload = {"inline_message_id": inline_message_id, "reply_markup": {"inline_keyboard": keyboard}}
        if state.get("view") == "step":
            photo_id = self._paragraph_photo(int(state.get("step", 1)))
            if not photo_id:
                raise RuntimeError("BlackCastle has no paragraph or default photo")
            payload["media"] = {
                "type": "photo",
                "media": photo_id,
                "caption": text,
                "parse_mode": "HTML",
            }
            await self._call("editMessageMedia", payload)
        else:
            payload["caption"] = text
            payload["parse_mode"] = "HTML"
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
            text, keyboard = self._inline_screen(state)
            results.append({
                "type": "photo",
                "id": f"black_castle_player_{player_id}",
                "photo_file_id": photo_id,
                "caption": text,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard},
            })
        await self._call("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": results,
            "cache_time": 0,
            "is_personal": True,
        })

    def _record_button_press(self, callback: dict[str, Any], player_id: int, state: dict[str, Any], action: str) -> None:
        callback_id = callback.get("id")
        if not isinstance(callback_id, str) or not callback_id:
            return

        paragraph_number = state.get("step")
        target_paragraph = None
        label = action.removeprefix("blackcastle:")
        label_from_message = False
        message = callback.get("message")
        if isinstance(message, dict):
            markup = message.get("reply_markup") or {}
            keyboard = markup.get("inline_keyboard") or []
            for row in keyboard:
                if not isinstance(row, list):
                    continue
                for button in row:
                    if isinstance(button, dict) and button.get("callback_data") == action:
                        label = str(button.get("text") or label)
                        label_from_message = True
                        break

        if action.startswith("blackcastle:route:"):
            try:
                _, _, source_text, choice_id = action.split(":", 3)
                paragraph_number = int(source_text)
                choice = self.game_store.get_paragraph_choice(paragraph_number, choice_id)
            except (ValueError, TypeError):
                choice = None
            if choice is not None:
                target_paragraph = int(choice["target_paragraph"])
                if not label_from_message:
                    label = self._route_button_text(
                        str(choice["button_text"]), target_paragraph
                    )
        elif action.startswith("blackcastle:step:"):
            try:
                target_paragraph = int(action.rsplit(":", 1)[1])
                if not label_from_message:
                    label = "К шагу 1" if target_paragraph == 1 else f"Шаг {target_paragraph}"
            except ValueError:
                pass
        else:
            label = {
                "blackcastle:preface": "Предисловие",
                "blackcastle:stats": "Характеристики",
                "blackcastle:inventory": "Инвентарь",
                "blackcastle:status": "Характеристики и инвентарь",
                "blackcastle:back": f"К шагу {state.get('step', 1)}",
                "blackcastle:continue": "Продолжить",
                "blackcastle:preface_next": "Читать продолжение",
                "blackcastle:page_next": "Читать продолжение",
                "blackcastle:page_prev": "Назад",
            }.get(action, label)

        self.game_store.record_button_press(
            callback_id,
            player_id,
            paragraph_number if isinstance(paragraph_number, int) else None,
            target_paragraph,
            label,
            action,
        )

    async def process_update(self, update: dict[str, Any]) -> None:
        query = update.get("inline_query")
        if isinstance(query, dict):
            await self._answer_inline_query(query)
            return

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            callback_id = callback.get("id")
            sender = callback.get("from") or {}
            player_id = sender.get("id")
            action = str(callback.get("data") or "")
            if not isinstance(player_id, int) or not action.startswith("blackcastle:"):
                if isinstance(callback_id, str):
                    await self._acknowledge_callback(callback_id)
                return
            state = self._get_or_create_state(player_id)
            callback_message = callback.get("message") or {}
            self._record_button_press(callback, player_id, state, action)
            callback_photo = callback_message.get("photo")
            if isinstance(callback_message, dict):
                state["direct_message_has_photo"] = isinstance(callback_photo, list) and bool(callback_photo)
            luck_alert = None
            luck_check_clicked = False
            route_choice = None
            route_source_step = None
            if action.startswith("blackcastle:route:"):
                try:
                    _, _, source_text, choice_id = action.split(":", 3)
                    route_source_step = int(source_text)
                    route_choice = self.game_store.get_paragraph_choice(
                        route_source_step, choice_id
                    )
                except (ValueError, TypeError):
                    route_choice = None
                if (
                    route_choice is not None
                    and state.get("view") == "step"
                    and state.get("step") == route_source_step
                    and (
                        not route_choice.get("required_item")
                        or self._has_item(state, str(route_choice["required_item"]))
                    )
                ):
                    paragraph = self.game_store.get_paragraph(route_source_step)
                    has_luck_prompt = bool(
                        paragraph
                        and paragraph.get("body")
                        and re.search(
                            r"ПРОВЕРЬТЕ СВОЮ УДАЧУ",
                            str(paragraph["body"]),
                            re.IGNORECASE,
                        )
                    )
                    button_label = ROUTE_BUTTON_SUFFIX.sub(
                        "", str(route_choice.get("button_text") or "")
                    ).strip()
                    is_luck_route = bool(
                        re.fullmatch(
                            r"Если(?: вы)? удачливы|Проверить удачу",
                            button_label,
                            re.IGNORECASE,
                        )
                    )
                    checks = state.get("luck_checks")
                    has_checked = isinstance(checks, dict) and str(route_source_step) in checks
                    if has_luck_prompt and is_luck_route and not has_checked:
                        luck_alert = self._resolve_luck_check(
                            state, route_source_step, check=True
                        )
                        luck_check_clicked = luck_alert is not None
                    elif has_luck_prompt and not is_luck_route and not has_checked:
                        luck_alert = self._resolve_luck_check(
                            state, route_source_step, check=False
                        )

            if luck_alert:
                self._save_state(player_id, state)
            if isinstance(callback_id, str):
                await self._acknowledge_callback(callback_id, luck_alert)
            if action == "blackcastle:preface":
                state["view"] = "preface"
                state["preface_part"] = 0
            elif action == "blackcastle:preface_next":
                state["preface_part"] = int(state.get("preface_part", 0)) + 1
            elif action == "blackcastle:page_next":
                part_key = "preface_part" if state.get("view") == "preface" else "page_part"
                state[part_key] = int(state.get(part_key, 0)) + 1
            elif action == "blackcastle:page_prev":
                part_key = "preface_part" if state.get("view") == "preface" else "page_part"
                state[part_key] = max(0, int(state.get(part_key, 0)) - 1)
            elif action == "blackcastle:stats":
                state["view"] = "stats"
            elif action == "blackcastle:inventory":
                state["view"] = "inventory"
            elif action == "blackcastle:status":
                state["view"] = "status"
                state["page_part"] = 0
            elif action == "blackcastle:back":
                state["view"] = "step"
                state["page_part"] = 0
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
                if route_choice is None or route_source_step is None:
                    return
                source_step = route_source_step
                if state.get("view") != "step" or state.get("step") != source_step:
                    return
                choice = route_choice
                if luck_check_clicked:
                    state["page_part"] = 0
                    state["view"] = "step"
                    state["step"] = source_step
                else:
                    source_paragraph = self.game_store.get_paragraph(source_step)
                    source_label = ROUTE_BUTTON_SUFFIX.sub(
                        "", str(choice.get("button_text") or "")
                    ).strip()
                    is_luck_success_route = bool(
                        re.fullmatch(
                            r"Если(?: вы)? удачливы|Проверить удачу",
                            source_label,
                            re.IGNORECASE,
                        )
                    ) and bool(
                        source_paragraph
                        and source_paragraph.get("body")
                        and re.search(
                            r"ПРОВЕРЬТЕ СВОЮ УДАЧУ",
                            str(source_paragraph["body"]),
                            re.IGNORECASE,
                        )
                    )
                    checks = state.get("luck_checks")
                    luck_result = (
                        checks.get(str(source_step))
                        if isinstance(checks, dict)
                        else None
                    )
                    if (
                        is_luck_success_route
                        and isinstance(luck_result, dict)
                        and not luck_result.get("lucky")
                    ):
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
            if state.get("view") in {"preface", "step"}:
                self._paged_screen(state)
            self._save_state(player_id, state)
            inline_message_id = callback.get("inline_message_id")
            if isinstance(inline_message_id, str):
                await self._edit_inline_screen(inline_message_id, state)
                return
            message = callback_message
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
        part_key = "preface_part" if state.get("view") == "preface" else "page_part"
        state[part_key] = 0
        previous_message_id = state.get("direct_message_id", 0)
        await self._send_direct_screen(
            chat_id,
            sender_id,
            state,
            previous_message_id if isinstance(previous_message_id, int) else 0,
        )

    async def _acknowledge_callback(
        self, callback_id: str, alert: str | None = None
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if alert:
            payload.update({"text": alert, "show_alert": True})
        try:
            await self._call("answerCallbackQuery", payload)
        except Exception as exc:
            # A failed acknowledgement must not block later game updates.
            logger.info(
                "Could not acknowledge BlackCastle button press (%s)",
                type(exc).__name__,
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
                    offset = int(update["update_id"]) + 1
                    try:
                        await self.process_update(update)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # A stale inline message or other per-update failure must
                        # not hold every later player message behind it.
                        logger.warning(
                            "BlackCastle update processing failed (update %s, %s)",
                            update.get("update_id"),
                            type(exc).__name__,
                        )
                    self.game_store.set_setting("kniga_igra_update_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("BlackCastle bot polling failed (%s)", type(exc).__name__)
                await asyncio.sleep(5)
