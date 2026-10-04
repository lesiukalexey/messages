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

from .black_castle_battle_text import canonical_enemy_key

BOT_USERNAME = "KnigaIgraBot"
ROUTE_BUTTON_MAX_LENGTH = 50
ROUTE_BUTTON_SUFFIX = re.compile(r"\s+[—–-]\s*\d+\s*$")
INITIAL_SPELLS = {
    "levitation": 2,
    "fire": 2,
    "illusion": 1,
    "strength": 1,
    "weakness": 1,
    "copy": 1,
    "healing": 1,
    "swimming": 1,
}
SPELL_LABELS = {
    "levitation": "Левитации",
    "fire": "Огня",
    "illusion": "Иллюзии",
    "strength": "Силы",
    "weakness": "Слабости",
    "copy": "Копии",
    "healing": "Исцеления",
    "swimming": "Плавания",
}
SPELL_PATTERNS = {
    "levitation": re.compile(r"\bлевитац\w*", re.IGNORECASE),
    "fire": re.compile(r"\bогн\w*", re.IGNORECASE),
    "illusion": re.compile(r"\bиллюз\w*", re.IGNORECASE),
    "strength": re.compile(r"\bсилы\b(?!\s+удара)", re.IGNORECASE),
    "weakness": re.compile(r"\bслабост\w*", re.IGNORECASE),
    "copy": re.compile(r"\bкопи\w*", re.IGNORECASE),
    "healing": re.compile(r"\bисцел\w*", re.IGNORECASE),
    "swimming": re.compile(r"\bплаван\w*", re.IGNORECASE),
}
ENEMY_STATS = re.compile(
    r"(?P<name>^[А-ЯЁ][А-ЯЁ \t-]{1,79})[ \t]*\n[ \t]*Мастерство\s*:?[ \t]*(?P<mastery>\d+)[ \t]*\n[ \t]*Выносливость\s*:?[ \t]*(?P<stamina>\d+)",
    re.IGNORECASE | re.MULTILINE,
)
PREFACE_SPELL_TEXT = """Как и положено в сказках, путешествие начинается перед королевским дворцом. Узнав, зачем вы пришли, стражники провожают вас в Тронный зал, и вы предстаете перед Королем. Обрадованный тем, что есть еще в его королевстве герои, готовые рискнуть даже своей жизнью ради его дочери, он отправляет вас к придворному астрологу и волшебнику, лучшему в королевстве знатоку Белой магии — Майлину. Ведь вам придется сражаться не только с воинами, но и со злыми духами — без волшебства в дороге не обойтись.

Однако даже Майлин не может предвидеть всего могущества Барлада Дэрта, да и времени на учебу у вас совсем мало. Он лишь успевает научить вас самым необходимым заклятиям и дать несколько советов. Вот заклятия, которые вы изучили:

ЗАКЛЯТИЕ ЛЕВИТАЦИИ — с его помощью вы сможете подняться в воздух и перелететь то препятствие, которое вам встретится. Но будьте осторожны: заклятие действует не слишком долго, и если вы не рассчитаете свои силы, то можете опуститься на землю раньше, чем препятствие или опасность будут позади.

ЗАКЛЯТИЕ ОГНЯ — поможет вам в нужный момент создать в воздухе огненный шар и направить его на врагов. Но в закрытых помещениях им надо пользоваться осмотрительно, чтобы не устроить пожар.

ЗАКЛЯТИЕ ИЛЛЮЗИИ — вы создадите у вашего врага необходимую иллюзию и сможете спастись в тех ситуациях, из которых другого выхода не будет. Но заклятие иллюзии — опасное колдовство: ведь иллюзия рассеивается, и враг понимает, что его одурачили.

ЗАКЛЯТИЕ СИЛЫ — прибавит вам силу и увеличит вашу СИЛУ УДАРА.

ЗАКЛЯТИЕ СЛАБОСТИ — сделает вашего врага неуклюжим и неповоротливым, ослабит СИЛУ его УДАРА.

ЗАКЛЯТИЕ КОПИИ — с его помощью вы сможете при случае создать точную Копию вашего противника, которую вы будете контролировать. Тогда прежде чем добраться до вас, ему придется драться с собственной Копией, МАСТЕРСТВО и ВЫНОСЛИВОСТЬ которой будут равны его МАСТЕРСТВУ и ВЫНОСЛИВОСТИ. Если ваш враг победит свою Копию, то с ним придется драться вам самим. Если же Копия сразит противника, то заклятие теряет силу и Копия исчезает, а вы продолжаете свой путь. Но если противников было несколько, а Копию вы смогли или захотели создать только одну, то придется драться и с остальными.

ЗАКЛЯТИЕ ИСЦЕЛЕНИЯ — в любой момент (но не во время сражения) добавит вам 8 ВЫНОСЛИВОСТЕЙ.

ЗАКЛЯТИЕ ПЛАВАНИЯ — вы никогда не видели ни реки, ни озера, а в дороге может случиться всякое. У вас уже нет времени учиться плавать. Но с помощью этого заклятия вы сможете переплыть любую водную преграду, которая вам встретится. Но будьте внимательны: как только вы ступите на землю, заклятие утратит свою силу.

Астролог предупредил: уровень вашего МАСТЕРСТВА позволяет вам воспользоваться заклятиями только 10 раз. Поэтому вы можете выбрать любые заклятия и в любом количестве, но всего их должно быть не более десяти. Настройте запас заклятий кнопками ниже."""
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
            "spells": dict(INITIAL_SPELLS),
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
            state_changed = self._ensure_spell_profile(state)
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
                    state_changed = True
            if state_changed:
                self._save_state(player_id, state)
        return state

    @staticmethod
    def _ensure_spell_profile(state: dict[str, Any]) -> bool:
        spells = state.get("spells")
        if not isinstance(spells, dict):
            state["spells"] = dict(INITIAL_SPELLS)
            return True
        changed = False
        for spell, initial_count in INITIAL_SPELLS.items():
            if spell not in spells:
                spells[spell] = initial_count
                changed = True
            else:
                try:
                    count = max(0, int(spells[spell]))
                except (TypeError, ValueError):
                    count = 0
                if spells[spell] != count:
                    spells[spell] = count
                    changed = True
        return changed

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
            r"Шаг \d+|Характеристики|Инвентарь|Характеристики и инвентарь|Книга-игра|Битва",
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
            "Битва": "⚔️",
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
            if title == "Битва":
                formatted.append("\n".join(html.escape(line) for line in lines))
                continue
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
            if PREFACE_SPELL_TEXT not in preface_text:
                preface_text = f"{preface_text.rstrip()}\n\n{PREFACE_SPELL_TEXT}"
            spells = state.get("spells", INITIAL_SPELLS)
            allocated = sum(max(0, int(spells.get(key, 0))) for key in INITIAL_SPELLS)
            preface_text += f"\n\nРаспределено заклятий: {allocated} из 10."
            keyboard = []
            for key, label in SPELL_LABELS.items():
                keyboard.append([
                    {"text": "−", "callback_data": f"blackcastle:spell:{key}:-1"},
                    {"text": f"{label}: {spells.get(key, 0)}", "callback_data": "blackcastle:spell:noop"},
                    {"text": "+", "callback_data": f"blackcastle:spell:{key}:1"},
                ])
            keyboard.append([{"text": "Продолжить", "callback_data": "blackcastle:continue"}])
            return preface_text, keyboard, True

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
                "\nЗаклинания:\n"
                + "\n".join(
                    f"{SPELL_LABELS[key]}: {state.get('spells', {}).get(key, 0)}"
                    for key in INITIAL_SPELLS
                )
                + "\n"
                f"Золотые: {state['gold']}"
            )
            return text, [[{
                "text": f"К шагу {step}",
                "callback_data": "blackcastle:back",
            }]], view == "status"

        if view == "battle":
            battle = state.get("battle", {})
            log_limit = 850 if battle.get("inline_message") else 3600
            log = "\n\n".join(battle.get("log", []))[-log_limit:]
            text = "Битва"
            if log:
                text += f"\n\n{log}"
            else:
                text += "\n\nПодготовка к бою."
            keyboard = []
            if battle.get("status") == "choose_target":
                for index, enemy in enumerate(battle.get("enemies", [])):
                    keyboard.append([{
                        "text": f"Начать бой: {enemy['name']}",
                        "callback_data": f"blackcastle:battle:begin:{index}",
                    }])
            elif battle.get("status") == "awaiting_continue":
                if battle.get("stage") == "copy_lost":
                    label = "Продолжить бой за героя"
                    callback = "blackcastle:battle:continue"
                    keyboard.append([{"text": label, "callback_data": callback}])
                elif battle.get("stage") == "copy_won":
                    keyboard.append([{"text": "Продолжить бой", "callback_data": "blackcastle:battle:continue"}])
                elif battle.get("stage") == "hero" and len(battle.get("enemies", [])) > 1:
                    for index, enemy in enumerate(battle["enemies"]):
                        if enemy.get("stamina", 0) > 0:
                            keyboard.append([{
                                "text": f"Продолжить: {enemy['name']}",
                                "callback_data": f"blackcastle:battle:continue:{index}",
                            }])
                else:
                    label = "Продолжить битву"
                    callback = "blackcastle:battle:continue"
                    keyboard.append([{"text": label, "callback_data": callback}])
            elif battle.get("status") == "won":
                keyboard.append([{"text": "Продолжить", "callback_data": "blackcastle:battle:finish"}])
            elif battle.get("status") == "lost":
                keyboard.append([{"text": "Начать сначала", "callback_data": "blackcastle:battle:restart"}])
            if battle.get("status") == "awaiting_continue":
                for index, escape in enumerate(battle.get("escape_options", [])):
                    keyboard.append([{
                        "text": self._route_button_text(str(escape["button_text"]), int(escape["target_paragraph"])),
                        "callback_data": f"blackcastle:battle:flee:{index}",
                    }])
            return text, keyboard, False

        if view == "game_over":
            return "Путешествие окончено. Выносливость упала до нуля. Начните игру сначала.", [[{
                "text": "Начать сначала", "callback_data": "blackcastle:game:restart",
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
            enemies = self._battle_enemies(str(body or ""))
            prepared_magic = state.get("combat_magic_pending")
            if enemies and isinstance(prepared_magic, dict):
                text += f"\n\nПодготовлено заклинание: {SPELL_LABELS.get(prepared_magic.get('spell'), prepared_magic.get('spell'))}."
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
                spell_options = self._route_spell_options(source_label, str(body or ""))
                if enemies and spell_options:
                    if not isinstance(prepared_magic, dict):
                        for spell in spell_options:
                            if int(state.get("spells", INITIAL_SPELLS).get(spell, 0)) > 0:
                                keyboard.append([{
                                    "text": f"Заклинание {SPELL_LABELS[spell]}",
                                    "callback_data": f"blackcastle:cast:{step}:{choice['choice_id']}:{spell}",
                                }])
                    if self._is_battle_route(source_label):
                        keyboard.append([{
                            "text": "Вступить в бой",
                            "callback_data": f"blackcastle:battle:start:{step}:{choice['choice_id']}",
                        }])
                    continue
                if enemies and self._is_battle_route(source_label):
                    keyboard.append([{
                        "text": "Вступить в бой",
                        "callback_data": f"blackcastle:battle:start:{step}:{choice['choice_id']}",
                    }])
                    continue
                if spell_options:
                    available = [
                        spell for spell in spell_options
                        if int(state.get("spells", INITIAL_SPELLS).get(spell, 0)) > 0
                    ]
                    for spell in available:
                        keyboard.append([{
                            "text": self._route_button_text(
                                f"Заклинание {SPELL_LABELS[spell]}", target
                            ),
                            "callback_data": (
                                f"blackcastle:cast:{step}:{choice['choice_id']}:{spell}"
                            ),
                        }])
                    if self._is_battle_route(source_label):
                        keyboard.append([{
                            "text": self._route_button_text(str(choice["button_text"]), target),
                            "callback_data": f"blackcastle:route:{step}:{choice['choice_id']}",
                        }])
                    continue
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
        return self._default_photo()

    def _default_photo(self) -> str:
        return self.game_store.get_setting("kniga_igra_black_castle_photo_file_id")

    @staticmethod
    def _battle_enemies(body: str) -> list[dict[str, Any]]:
        stronger_merchant_blow = bool(re.search(
            r"когда он ранит вас.{0,100}не\s*2,\s*а\s*3\s+ВЫНОСЛИВОСТИ",
            body,
            re.IGNORECASE | re.DOTALL,
        ))
        return [{
            "name": re.sub(r"\s+", " ", match.group("name")).strip(),
            "mastery": int(match.group("mastery")),
            "stamina": int(match.group("stamina")),
            "damage_to_player": (
                3 if stronger_merchant_blow and "торгов" in match.group("name").casefold() else 2
            ),
        } for match in ENEMY_STATS.finditer(body)]

    def _battle_phrase(self, enemy_name: str, phase: str, **values: str) -> str:
        from .black_castle_battle_text import (
            ENEMY_BATTLE_TEXT,
            GENERIC_BATTLE_TEXT,
        )

        getter = getattr(self.game_store, "get_battle_narrative_templates", None)
        templates = getter(enemy_name, phase) if callable(getter) else []
        if not templates:
            key = canonical_enemy_key(enemy_name)
            templates = ENEMY_BATTLE_TEXT.get(key, {}).get(phase, [])
        if not templates:
            templates = GENERIC_BATTLE_TEXT.get(phase, [])
        if not templates:
            return ""
        return random.choice(templates).format(**values)

    async def _edit_battle_progress(
        self,
        player_id: int,
        state: dict[str, Any],
        *,
        inline_message_id: str | None,
        chat_id: int | None,
    ) -> None:
        self._save_state(player_id, state)
        if inline_message_id:
            await self._edit_inline_screen(inline_message_id, state)
            return
        message_id = state.get("direct_message_id")
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            return
        text, keyboard, _ = self._paged_screen(state, limit=950 if inline_message_id else 3900)
        await self._call("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": self._format_telegram_text(text),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": keyboard},
        })

    async def _advance_battle_round(
        self,
        player_id: int,
        state: dict[str, Any],
        *,
        inline_message_id: str | None,
        chat_id: int | None,
    ) -> None:
        battle = state.get("battle")
        if not isinstance(battle, dict) or battle.get("status") != "running":
            return
        enemies = battle.get("enemies", [])
        if not enemies:
            return
        stage = battle.get("stage", "hero")
        acting_copy = stage == "copy"
        actor = battle.get("copy") if acting_copy else state["characteristics"]
        target_index = 0 if acting_copy else int(battle.get("target_index", 0))
        if target_index >= len(enemies) or enemies[target_index].get("stamina", 0) <= 0:
            target_index = next((i for i, enemy in enumerate(enemies) if enemy.get("stamina", 0) > 0), 0)
        target = enemies[target_index]
        active_enemy_indexes = [0] if acting_copy else [
            i for i, enemy in enumerate(enemies) if int(enemy.get("stamina", 0)) > 0
        ]
        enemy_rolls: list[int | None] = [None] * len(enemies)
        enemy_attacks = [-1] * len(enemies)
        for i in active_enemy_indexes:
            enemy_rolls[i] = random.randint(1, 6) + random.randint(1, 6)
            enemy_attacks[i] = enemy_rolls[i] + enemies[i]["mastery"]
        player_roll = random.randint(1, 6) + random.randint(1, 6)
        player_mastery = actor["mastery"]
        strength_bonus = 2 if not acting_copy and (battle.get("magic") or {}).get("spell") == "strength" else 0
        attack_penalty = 0 if acting_copy else int(battle.get("player_attack_penalty", 0))
        player_attack = player_roll + player_mastery + strength_bonus - attack_penalty
        selected_attack = enemy_attacks[target_index]
        player_wins = player_attack > selected_attack
        player_stamina_before = int(actor["stamina"])
        target_stamina_before = int(target["stamina"])
        enemy_hits = [
            i for i, enemy_attack in enumerate(enemy_attacks)
            if enemy_attack > player_attack and enemies[i].get("stamina", 0) > 0
        ]
        battle["round"] = int(battle.get("round", 0)) + 1
        log = battle.setdefault("log", [])
        display_names = [re.sub(r"\s+", " ", enemy["name"]).strip().title() for enemy in enemies]
        target_name = display_names[target_index]
        victim = "Копию" if acting_copy else "вас"
        opening = "\n".join(
            self._battle_phrase(
                enemies[i]["name"], "opening", enemy=display_names[i], victim=victim
            )
            for i in active_enemy_indexes
        )
        round_intro = battle.pop("round_intro", None)
        if round_intro:
            opening = f"{round_intro}\n{opening}"
        enemy_attack_text = "; ".join(
            f"{display_names[i]} атакует с СИЛОЙ УДАРА {enemy_attacks[i]} "
            f"({enemy_rolls[i]} + {enemies[i]['mastery']})"
            for i in active_enemy_indexes
        )
        player_formula = f"{player_roll} + {player_mastery}"
        if strength_bonus:
            player_formula += " + 2"
        if attack_penalty:
            player_formula += f" - {attack_penalty}"
        hero_label = "Копии" if acting_copy else "игрока"
        if len(active_enemy_indexes) > 1:
            attack_description = f"Атаки противников: {enemy_attack_text}."
        else:
            attack_description = f"{enemy_attack_text}."
        if acting_copy:
            counter_start = "Копия повторяет движение противника и готовит ответный выпад."
        else:
            counter_start = "Вы успеваете отступить и готовите ответный выпад."
        event_lines = [
            f"{opening}\n{attack_description}",
            f"{counter_start} Бросок: {player_formula} = {player_attack}; "
            f"СИЛА УДАРА {hero_label} — {player_attack}.",
            (f"{('Удар Копии' if acting_copy else 'Ваш выпад')} оказывается быстрее — "
             f"{player_attack} против {selected_attack}."
             if player_wins else f"{target_name} успевает опередить {victim} — {selected_attack} против {player_attack}."
             if player_attack < selected_attack else
             self._battle_phrase(target["name"], "parry", enemy=target_name)),
        ]

        for action_number in range(1, 8):
            await asyncio.sleep(1)
            if action_number == 4 and player_wins:
                target["stamina"] = max(0, int(target["stamina"]) - 2)
                if acting_copy and target["stamina"] == 0:
                    battle["copy_won"] = True
            elif action_number == 5 and enemy_hits:
                damage = sum(
                    2 if acting_copy else int(enemies[i].get("damage_to_player", 2))
                    for i in enemy_hits
                )
                actor["stamina"] = max(0, int(actor["stamina"]) - damage)
            if action_number <= 5:
                if action_number <= 3:
                    line = event_lines[action_number - 1]
                elif action_number == 4 and player_wins:
                    counterattack = "Удар Копии" if acting_copy else "Ваш ответный удар"
                    if acting_copy:
                        wound = self._battle_phrase(
                            target["name"], "copy_wounded", enemy=target_name,
                            counterattack=counterattack,
                        )
                    else:
                        wound = self._battle_phrase(
                            target["name"], "wounded", enemy=target_name,
                            counterattack=counterattack, actor="Путник",
                            actor_genitive="путника",
                        )
                    opening_action = "Копия уклоняется от атаки и наносит ответный удар." if acting_copy else ""
                    if not acting_copy and canonical_enemy_key(target["name"]) == "гигантский паук":
                        opening_action = "Вы уклоняетесь от атаки и тут же наносите ответный удар."
                    line = (
                        f"{opening_action + ' ' if opening_action else ''}{wound}\n"
                        f"{target_name} получает 2 урона:\n"
                        f"ВЫНОСЛИВОСТЬ: {target_stamina_before} → {target['stamina']}"
                    )
                elif action_number == 4 and player_attack == selected_attack:
                    line = self._battle_phrase(target["name"], "parry", enemy=target_name)
                elif action_number == 4:
                    line = self._battle_phrase(target["name"], "failed_wound", enemy=target_name)
                elif enemy_hits:
                    attacks = [
                        self._battle_phrase(
                            enemies[i]["name"], "hit", enemy=display_names[i], victim=victim
                        )
                        for i in enemy_hits
                    ]
                    damage = sum(
                        2 if acting_copy else int(enemies[i].get("damage_to_player", 2))
                        for i in enemy_hits
                    )
                    injured_name = "Копия" if acting_copy else "Вы"
                    attack_narrative = "\n".join(attacks)
                    line = (
                        f"{attack_narrative}\n"
                        f"{injured_name} теря{'ет' if acting_copy else 'ете'} {damage} ВЫНОСЛИВОСТИ:\n"
                        f"ВЫНОСЛИВОСТЬ: {player_stamina_before} → {actor['stamina']}"
                    )
                elif player_wins:
                    line = "Вы успеваете уйти с линии атаки и не получаете повреждений."
                else:
                    line = "Противники расходятся после обмена ударами; новых ранений нет."
            elif action_number == 6:
                if acting_copy:
                    line = f"ВЫНОСЛИВОСТЬ после раунда:\nВы — {state['characteristics']['stamina']}\nКопия — {actor['stamina']}"
                else:
                    line = "ВЫНОСЛИВОСТЬ после раунда:\n" + "\n".join(
                        [f"Вы — {actor['stamina']}"]
                        + [f"{display_names[i]} — {enemy['stamina']}" for i, enemy in enumerate(enemies)]
                    )
            else:
                if not acting_copy and int(actor["stamina"]) <= 0:
                    battle["status"] = "lost"
                    line = "Вы падаете от полученных ран. ВЫНОСЛИВОСТЬ равна нулю — путешествие окончено."
                elif acting_copy and int(actor["stamina"]) <= 0:
                    battle["status"] = "awaiting_continue"
                    battle["stage"] = "copy_lost"
                    line = "Копия падает, и её очертания тают в воздухе. Теперь с противником предстоит драться вам."
                elif acting_copy and int(target["stamina"]) <= 0:
                    if all(int(enemy["stamina"]) <= 0 for enemy in enemies):
                        battle["status"] = "won"
                        line = self._battle_phrase(target["name"], "defeated", enemy=target_name) + " Копия исчезает, а победа остаётся за вами."
                    else:
                        battle["status"] = "awaiting_continue"
                        battle["stage"] = "copy_won"
                        line = self._battle_phrase(target["name"], "defeated", enemy=target_name) + " Копия исчезает; с остальными противниками предстоит драться вам."
                elif all(int(enemy["stamina"]) <= 0 for enemy in enemies):
                    battle["status"] = "won"
                    line = "Последний противник повержен. Вы переводите дух: битва окончена, победа за вами."
                else:
                    battle["status"] = "awaiting_continue"
                    remaining = [display_names[i] for i, enemy in enumerate(enemies) if enemy["stamina"] > 0]
                    if len(remaining) == 1:
                        line = self._battle_phrase(
                            enemies[next(i for i, enemy in enumerate(enemies) if enemy["stamina"] > 0)]["name"],
                            "survives",
                            enemy=remaining[0],
                        )
                    else:
                        line = f"Оставшиеся противники не отступают: {', '.join(remaining)}. Они готовятся к следующей атаке."
            log.append(line)
            if len(log) > 24:
                del log[:-24]
            await self._edit_battle_progress(
                player_id, state, inline_message_id=inline_message_id, chat_id=chat_id
            )

    @staticmethod
    def _route_spell_options(label: str, body: str) -> list[str]:
        normalized = label.casefold().replace("ё", "е")
        found = [key for key, pattern in SPELL_PATTERNS.items() if pattern.search(label)]
        if found:
            # Step 24 lets the player use either Swimming or Levitation for the same route.
            if ("левитац" in normalized and "плаван" in body.casefold().replace("ё", "е")
                    and re.search(r"плаван\w*\s+или\s+левитац", body, re.IGNORECASE)):
                return ["swimming", "levitation"]
            return found
        if (re.fullmatch(r"если(?: вы)? победили?|вступить в бой", normalized)
                and re.search(r"используя\b.*(?:заклятие|заклинание)", body, re.IGNORECASE | re.DOTALL)):
            battle_options = body.split("ГИГАНТСКИЙ", 1)[0]
            return [key for key, pattern in SPELL_PATTERNS.items() if pattern.search(battle_options)]
        if re.search(r"наложить какое-нибудь заклятие|попробуете наложить|накладываете заклятие", label, re.IGNORECASE):
            return list(INITIAL_SPELLS)
        if re.search(r"используете заклятие.*переплы|переплываете через реку", label, re.IGNORECASE):
            return ["swimming"]
        return []

    @staticmethod
    def _is_battle_route(label: str) -> bool:
        return bool(re.fullmatch(
            r"если(?: вы)? победили?|вступить в бой",
            label.casefold().replace("ё", "е"),
        ))

    @staticmethod
    def _is_escape_route(label: str) -> bool:
        return bool(re.search(r"убежать|бежать|сбежать|отступить|бегств", label, re.IGNORECASE))

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

        screen_limit = 3900 if state.get("view") == "battle" else 950
        text, keyboard, first_part = self._paged_screen(state, limit=screen_limit)
        _, _, has_photo = self._screen(state)
        if has_photo and first_part:
            photo_id = (
                self._default_photo()
                if state.get("view") in {"status", "preface"}
                else self._paragraph_photo(int(state.get("step", 1)))
            )
        else:
            photo_id = ""
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
        if state.get("view") in {"step", "status", "preface"}:
            photo_id = (
                self._default_photo()
                if state.get("view") in {"status", "preface"}
                else self._paragraph_photo(int(state.get("step", 1)))
            )
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
        photo_id = self._default_photo() if state.get("view") == "preface" else self._paragraph_photo(int(state.get("step", 1)))
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

        if action.startswith("blackcastle:route:") or action.startswith("blackcastle:cast:"):
            try:
                parts = action.split(":")
                source_text, choice_id = parts[2], parts[3]
                paragraph_number = int(source_text)
                choice = self.game_store.get_paragraph_choice(paragraph_number, choice_id)
            except (ValueError, TypeError):
                choice = None
            if choice is not None:
                target_paragraph = int(choice["target_paragraph"])
                if action.startswith("blackcastle:cast:"):
                    spell_key = parts[4]
                    source_paragraph = self.game_store.get_paragraph(paragraph_number)
                    in_battle = bool(
                        self._battle_enemies(str((source_paragraph or {}).get("body") or ""))
                    )
                    if in_battle:
                        target_paragraph = None
                        label = f"Заклинание {SPELL_LABELS.get(spell_key, spell_key)}"
                    else:
                        label = self._route_button_text(
                            f"Заклинание {SPELL_LABELS.get(spell_key, spell_key)}", target_paragraph
                        )
                if not label_from_message:
                    if not action.startswith("blackcastle:cast:"):
                        label = self._route_button_text(str(choice["button_text"]), target_paragraph)
        elif action.startswith("blackcastle:battle:start:"):
            try:
                _, _, _, source_text, choice_id = action.split(":", 4)
                paragraph_number = int(source_text)
                choice = self.game_store.get_paragraph_choice(paragraph_number, choice_id)
                if choice:
                    target_paragraph = int(choice["target_paragraph"])
                    label = "Вступить в бой"
            except (ValueError, TypeError):
                pass
        elif action.startswith("blackcastle:battle:"):
            label = {
                "blackcastle:battle:continue": "Продолжить битву",
                "blackcastle:battle:finish": "Продолжить",
                "blackcastle:battle:restart": "Начать сначала",
            }.get(action, label)
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
            logger.info(
                "BlackCastle callback received (action=%s, view=%s, step=%s)",
                action,
                state.get("view"),
                state.get("step"),
            )
            callback_message = callback.get("message") or {}
            self._record_button_press(callback, player_id, state, action)
            callback_photo = callback_message.get("photo")
            if isinstance(callback_message, dict):
                state["direct_message_has_photo"] = isinstance(callback_photo, list) and bool(callback_photo)
            luck_alert = None
            luck_check_clicked = False
            route_choice = None
            route_source_step = None
            cast_spell = None
            battle_advance = False
            battle_target_index = None
            battle = state.get("battle")
            if (action == "blackcastle:page_next" and state.get("view") == "battle"
                    and isinstance(battle, dict) and battle.get("status") == "awaiting_continue"):
                action = "blackcastle:battle:continue"
            if action.startswith("blackcastle:battle:start:"):
                try:
                    _, _, _, source_text, choice_id = action.split(":", 4)
                    route_source_step = int(source_text)
                    route_choice = self.game_store.get_paragraph_choice(route_source_step, choice_id)
                except (ValueError, TypeError):
                    route_choice = None
            elif action.startswith("blackcastle:battle:begin:"):
                try:
                    battle_target_index = int(action.rsplit(":", 1)[1])
                except ValueError:
                    battle_target_index = None
            elif action.startswith("blackcastle:battle:flee:"):
                try:
                    battle_target_index = int(action.rsplit(":", 1)[1])
                except ValueError:
                    battle_target_index = None
            elif action.startswith("blackcastle:battle:continue"):
                battle_advance = True
                try:
                    battle_target_index = int(action.rsplit(":", 1)[1]) if action.count(":") > 2 else None
                except ValueError:
                    battle_target_index = None
            if action.startswith("blackcastle:cast:"):
                try:
                    _, _, source_text, choice_id, cast_spell = action.split(":", 4)
                    route_source_step = int(source_text)
                    route_choice = self.game_store.get_paragraph_choice(route_source_step, choice_id)
                except (ValueError, TypeError):
                    route_choice = None
            elif action.startswith("blackcastle:route:"):
                try:
                    _, _, source_text, choice_id = action.split(":", 3)
                    route_source_step = int(source_text)
                    route_choice = self.game_store.get_paragraph_choice(
                        route_source_step, choice_id
                    )
                    if route_choice is not None:
                        source_paragraph = self.game_store.get_paragraph(route_source_step)
                        raw_label = ROUTE_BUTTON_SUFFIX.sub(
                            "", str(route_choice.get("button_text") or "")
                        ).strip()
                        if (self._route_spell_options(
                            raw_label,
                            str((source_paragraph or {}).get("body") or ""),
                        ) and not self._is_battle_route(raw_label)):
                            route_choice = None
                        if (self._battle_enemies(str((source_paragraph or {}).get("body") or ""))
                                and self._is_battle_route(raw_label)):
                            route_choice = None
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

            if (action.startswith("blackcastle:cast:") and route_choice is not None
                    and state.get("view") == "step" and state.get("step") == route_source_step):
                source_paragraph = self.game_store.get_paragraph(route_source_step)
                has_luck_prompt = bool(
                    source_paragraph
                    and re.search(r"ПРОВЕРЬТЕ СВОЮ УДАЧУ", str(source_paragraph.get("body") or ""), re.IGNORECASE)
                )
                checks = state.get("luck_checks")
                has_checked = isinstance(checks, dict) and str(route_source_step) in checks
                if has_luck_prompt and not has_checked:
                    luck_alert = self._resolve_luck_check(state, route_source_step, check=False)

            if action == "blackcastle:continue" and state.get("view") == "preface":
                spells = state.get("spells", INITIAL_SPELLS)
                allocated = sum(max(0, int(spells.get(key, 0))) for key in INITIAL_SPELLS)
                if allocated < 10:
                    if isinstance(callback_id, str):
                        await self._acknowledge_callback(
                            callback_id,
                            f"Распределите все 10 заклинаний. Осталось распределить: {10 - allocated}.",
                        )
                    return

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
            elif action.startswith("blackcastle:battle:start:"):
                if (route_choice is None or route_source_step is None
                        or state.get("view") != "step" or state.get("step") != route_source_step):
                    logger.warning(
                        "Rejected BlackCastle battle start (step=%s, view=%s, route_step=%s, route_found=%s)",
                        state.get("step"), state.get("view"), route_source_step,
                        route_choice is not None,
                    )
                    return
                if not self._is_battle_route(ROUTE_BUTTON_SUFFIX.sub(
                    "", str(route_choice.get("button_text") or "")
                ).strip()):
                    return
                paragraph = self.game_store.get_paragraph(route_source_step)
                enemies = self._battle_enemies(str((paragraph or {}).get("body") or ""))
                if not enemies:
                    logger.warning(
                        "Rejected BlackCastle battle start because no enemy stats were parsed (step=%s)",
                        route_source_step,
                    )
                    return
                required_item = route_choice.get("required_item")
                if required_item and not self._consume_item(state, str(required_item)):
                    return
                magic = state.pop("combat_magic_pending", None)
                battle = {
                    "source_step": route_source_step,
                    "victory_step": int(route_choice["target_paragraph"]),
                    "enemies": enemies,
                    "stage": "copy" if isinstance(magic, dict) and magic.get("spell") == "copy" else "hero",
                    "status": "running",
                    "inline_message": isinstance(callback.get("inline_message_id"), str),
                    "round": 0,
                    "log": [],
                    "magic": magic,
                    "escape_options": [
                        choice for choice in self.game_store.get_paragraph_choices(route_source_step)
                        if self._is_escape_route(str(choice.get("button_text") or ""))
                    ],
                    "player_attack_penalty": 1 if re.search(
                        r"уменьшайте вашу СИЛУ УДАРА на\s*1",
                        str((paragraph or {}).get("body") or ""),
                        re.IGNORECASE,
                    ) else 0,
                }
                if isinstance(magic, dict) and magic.get("spell") == "weakness":
                    for enemy in battle["enemies"]:
                        enemy["mastery"] = max(0, enemy["mastery"] - 2)
                if battle["stage"] == "copy":
                    original = battle["enemies"][0]
                    battle["copy"] = {
                        "name": f"Копия {original['name']}",
                        "mastery": original["mastery"],
                        "stamina": original["stamina"],
                    }
                if len(enemies) > 1 and battle["stage"] == "hero":
                    battle["status"] = "choose_target"
                state["battle"] = battle
                state["view"] = "battle"
                state["page_part"] = 0
                battle_advance = battle["status"] == "running"
            elif action.startswith("blackcastle:battle:begin:"):
                battle = state.get("battle")
                if (state.get("view") != "battle" or not isinstance(battle, dict)
                        or battle.get("status") != "choose_target" or battle_target_index is None
                        or battle_target_index < 0 or battle_target_index >= len(battle.get("enemies", []))):
                    return
                battle["target_index"] = battle_target_index
                battle["status"] = "running"
                battle_advance = True
            elif action.startswith("blackcastle:battle:flee:"):
                battle = state.get("battle")
                if (state.get("view") != "battle" or not isinstance(battle, dict)
                        or battle.get("status") != "awaiting_continue" or battle_target_index is None
                        or battle_target_index < 0 or battle_target_index >= len(battle.get("escape_options", []))):
                    return
                escape = battle["escape_options"][battle_target_index]
                target = int(escape["target_paragraph"])
                if self.game_store.get_paragraph(target) is None:
                    return
                characteristics = state["characteristics"]
                characteristics["stamina"] = max(0, int(characteristics.get("stamina", 0)) - 2)
                state.pop("battle", None)
                if characteristics["stamina"] == 0:
                    state["view"] = "game_over"
                else:
                    state["step"] = target
                    state["view"] = "step"
                state["page_part"] = 0
            elif action == "blackcastle:game:restart":
                if state.get("view") != "game_over":
                    return
                state.clear()
                state.update(self._new_state())
            elif action.startswith("blackcastle:battle:continue"):
                battle = state.get("battle")
                if (state.get("view") != "battle" or not isinstance(battle, dict)
                        or battle.get("status") != "awaiting_continue"):
                    return
                battle["log"] = []
                state["page_part"] = 0
                if battle.get("stage") == "copy_lost":
                    battle["stage"] = "hero"
                    battle["round_intro"] = "Очертания Копии тают в воздухе. Теперь противник снова перед вами."
                    battle["target_index"] = 0
                    battle["status"] = "running"
                    battle_advance = True
                elif battle.get("stage") == "copy_won":
                    battle["stage"] = "hero"
                    battle["round_intro"] = "Копия исчезает после победы. Выступаете против оставшихся врагов."
                    if len(battle.get("enemies", [])) > 1:
                        battle["status"] = "choose_target"
                        battle_advance = False
                if battle_target_index is not None:
                    if battle_target_index < 0 or battle_target_index >= len(battle.get("enemies", [])):
                        return
                    if battle["enemies"][battle_target_index].get("stamina", 0) <= 0:
                        return
                    battle["target_index"] = battle_target_index
                if battle.get("status") != "choose_target":
                    battle["status"] = "running"
                    battle_advance = True
            elif action == "blackcastle:battle:finish":
                battle = state.get("battle")
                if (state.get("view") != "battle" or not isinstance(battle, dict)
                        or battle.get("status") != "won"):
                    return
                target = int(battle["victory_step"])
                if self.game_store.get_paragraph(target) is None:
                    return
                state.pop("battle", None)
                state["view"] = "step"
                state["step"] = target
                state["page_part"] = 0
            elif action == "blackcastle:battle:restart":
                battle = state.get("battle")
                if (state.get("view") != "battle" or not isinstance(battle, dict)
                        or battle.get("status") != "lost"):
                    return
                state.clear()
                state.update(self._new_state())
            elif action.startswith("blackcastle:spell:"):
                try:
                    _, _, spell_key, delta_text = action.split(":", 3)
                    delta = int(delta_text)
                except ValueError:
                    return
                if spell_key == "noop":
                    return
                if state.get("view") != "preface" or spell_key not in INITIAL_SPELLS or delta not in {-1, 1}:
                    return
                spells = state.setdefault("spells", dict(INITIAL_SPELLS))
                current = int(spells.get(spell_key, 0))
                total = sum(max(0, int(value)) for value in spells.values())
                if delta < 0 and current > 0:
                    spells[spell_key] = current - 1
                elif delta > 0 and total < 10:
                    spells[spell_key] = current + 1
                else:
                    return
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
            elif action.startswith("blackcastle:route:") or action.startswith("blackcastle:cast:"):
                if route_choice is None or route_source_step is None:
                    return
                source_step = route_source_step
                if state.get("view") != "step" or state.get("step") != source_step:
                    return
                choice = route_choice
                if cast_spell:
                    source_paragraph = self.game_store.get_paragraph(source_step)
                    source_body = str((source_paragraph or {}).get("body") or "")
                    required_item = choice.get("required_item")
                    if required_item and not self._has_item(state, str(required_item)):
                        return
                    permitted = self._route_spell_options(
                        ROUTE_BUTTON_SUFFIX.sub("", str(choice.get("button_text") or "")).strip(),
                        source_body,
                    )
                    if (cast_spell not in permitted or cast_spell not in INITIAL_SPELLS
                            or int(state.get("spells", {}).get(cast_spell, 0)) <= 0):
                        return
                is_battle_spell = bool(
                    cast_spell
                    and self._battle_enemies(str((self.game_store.get_paragraph(source_step) or {}).get("body") or ""))
                )
                if is_battle_spell:
                    if state.get("combat_magic_pending"):
                        return
                    state["spells"][cast_spell] -= 1
                    state["combat_magic_pending"] = {"spell": cast_spell}
                    state["view"] = "step"
                    state["page_part"] = 0
                elif luck_check_clicked:
                    result = state.get("luck_checks", {}).get(str(source_step), {})
                    if result.get("lucky"):
                        target_step = int(choice["target_paragraph"])
                        if self.game_store.get_paragraph(target_step) is None:
                            return
                        state["step"] = target_step
                    else:
                        state["step"] = source_step
                    state["page_part"] = 0
                    state["view"] = "step"
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
                    if cast_spell:
                        state.setdefault("spells", dict(INITIAL_SPELLS))[cast_spell] -= 1
                    if self._is_escape_route(source_label):
                        characteristics = state["characteristics"]
                        characteristics["stamina"] = max(0, int(characteristics.get("stamina", 0)) - 2)
                        if characteristics["stamina"] == 0:
                            state["view"] = "game_over"
                        else:
                            state["step"] = target_step
                            state["view"] = "step"
                    else:
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
                if battle_advance:
                    await self._advance_battle_round(
                        player_id, state, inline_message_id=inline_message_id, chat_id=None
                    )
                return
            message = callback_message
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            previous_message_id = message.get("message_id")
            if isinstance(chat_id, int):
                if (
                    state.get("view") == "battle"
                    and not state.get("direct_message_has_photo")
                    and isinstance(previous_message_id, int)
                    and previous_message_id > 0
                ):
                    state["direct_message_ids"] = [previous_message_id]
                    state["direct_message_id"] = previous_message_id
                    state["direct_message_has_photo"] = False
                    await self._edit_battle_progress(
                        player_id, state, inline_message_id=None, chat_id=chat_id
                    )
                else:
                    await self._send_direct_screen(
                        chat_id,
                        player_id,
                        state,
                        previous_message_id if isinstance(previous_message_id, int) else 0,
                    )
                if battle_advance:
                    await self._advance_battle_round(
                        player_id,
                        state,
                        inline_message_id=None,
                        chat_id=chat_id,
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
                            "BlackCastle update processing failed (update %s, %s: %s)",
                            update.get("update_id"),
                            type(exc).__name__,
                            str(exc).replace(self.token, "<redacted>")[:240],
                        )
                    self.game_store.set_setting("kniga_igra_update_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("BlackCastle bot polling failed (%s)", type(exc).__name__)
                await asyncio.sleep(5)
