from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import random
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .black_castle_battle_text import ENEMY_BATTLE_TEXT, GENERIC_BATTLE_TEXT, canonical_enemy_key
from .black_castle_loot import (
    INVENTORY_CONSUMABLE_USE_TEXT,
    INVENTORY_STAMINA_CONSUMABLES,
    PARAGRAPH_PURCHASE_OPTIONS,
    PARAGRAPH_LOOT_STAMINA_EFFECTS,
    REPEATABLE_LOOT_IDS,
)

BOT_USERNAME = "KnigaIgraBot"
ROUTE_BUTTON_MAX_LENGTH = 50
ROUTE_BUTTON_SUFFIX = re.compile(r"\s+[—–-]\s*\d+\s*$")
STEP11_TREASURE_KNOWLEDGE = (
    "Между двумя берёзами на холме зарыт клад; на развилке идти налево."
)
OFFSET_KNOWLEDGE = {
    90: "В замке, надев зелёные латы, прибавлять 60 к номеру параграфа за дверью.",
    95: "Ответ на последнюю загадку Домика нужно проверить в параграфе с номером ответа +50.",
    108: "Если спросят о Золотом амулете, прибавить 217 к номеру параграфа.",
    126: "При входе в замок в зелёных латах прибавлять 60 к выбранному номеру параграфа.",
    130: "Перед входом в комнату в зелёных латах прибавлять 60 к выбранному номеру параграфа.",
    144: "Потайная лестница за зеркалом: вычесть 13 из номера параграфа.",
    187: "Если понадобится зажечь светильник, прибавить 10 к номеру параграфа.",
    196: "Если понадобится перстень с рубином, вычесть 49 из номера параграфа.",
    246: "Если понадобится предъявить пропуск, прибавить 20 к номеру параграфа.",
    251: "Перед дверью в замке в зелёных латах прибавлять 60 к выбранному номеру параграфа.",
    258: "Если понадобится перстень с изумрудом, вычесть 169 из номера параграфа.",
    318: "Если запертая дверь не открывается, попробовать вычесть 40 из номера параграфа.",
    330: "Перед дверью в замке в зелёных латах прибавлять 60 к номеру параграфа.",
    336: "Если понадобится золотой апельсин, прибавить 200 к номеру параграфа.",
    339: "Для помощи спасённого знакомого: сложить номера букв его имени и прибавить 30.",
    356: "Если понадобится золотое кольцо, прибавить 214 к номеру параграфа.",
    414: "При выборе пути в замке в зелёных латах прибавлять 60 к номеру параграфа.",
    495: "У зеркала, которым можно воспользоваться, вычесть 13 из номера параграфа.",
    520: "Если встретится друг старика, сказать «Трое из Эвенло» и вычесть 25 из номера параграфа.",
    527: "Перед дверью в замке в зелёных латах прибавлять 60 к номеру параграфа.",
    555: "На перекрёстках следовать за клубочком: прибавить 30 к номеру параграфа.",
    573: "Чтобы зажечь свечу, нужны свеча и огниво; прибавить 10 к номеру параграфа.",
    600: "Если неподатливая дверь не открывается, попробовать вычесть 40 из номера параграфа.",
}
OFFSET_ACTIONS = {
    "lamp": {"knowledge": (187,), "steps": (193, 277), "item": "Светильник", "delta": 10,
             "label": "Зажечь светильник"},
    "candle": {"knowledge": (573,), "steps": (193, 277), "item": "Свеча", "extra_item": "Огниво", "delta": 10,
               "label": "Зажечь свечу"},
    "amulet": {"knowledge": (108,), "steps": (388,), "item": "Золотой амулет", "delta": 217,
               "label": "Использовать Золотой амулет"},
    "gold_ring": {"knowledge": (356,), "steps": (388,), "item": "Золотое кольцо", "delta": 214,
                  "label": "Повернуть Золотое кольцо"},
    "ruby_ring": {"knowledge": (196,), "steps": (275,), "item": "Перстень с рубином", "delta": -49,
                  "label": "Использовать перстень с рубином"},
    "emerald_ring": {"knowledge": (258,), "steps": (275,), "item": "Перстень с изумрудом", "delta": -169,
                     "label": "Использовать перстень с изумрудом"},
    "orange": {"knowledge": (336,), "steps": (275,), "item": "Золотой апельсин", "delta": 200,
               "label": "Использовать золотой апельсин"},
    "mirror": {"knowledge": (144, 495), "steps": (279,), "item": None, "delta": -13,
               "label": "Открыть потайной ход за зеркалом"},
    "pass": {"knowledge": (246,), "steps": (252,), "item": "Пропуск", "delta": 20,
             "label": "Предъявить пропуск"},
    "everlo": {"knowledge": (520,), "steps": (455,), "item": None, "delta": -25,
               "label": "Сказать «Трое из Эвенло»"},
    "metal_key": {"knowledge": (318,), "steps": None, "item": "Большой металлический ключ", "delta": -40,
                  "label": "Попробовать большой ключ"},
    "ring_key": {"knowledge": (600,), "steps": None, "item": "Перстень-ключ", "delta": -40,
                 "label": "Попробовать перстень-ключ"},
    "magic_ball": {"knowledge": (555,), "steps": None, "item": None, "delta": 30,
                   "label": "Следовать за клубочком", "condition": "crossroad"},
}
KNOWLEDGE_GATED_ROUTES = {
    (47, "knowledge_birches"): STEP11_TREASURE_KNOWLEDGE,
}
CHARACTERISTIC_MAXIMUMS = {"mastery": 12, "stamina": 24, "luck": 12}
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
NON_DISCARDABLE_ITEMS = frozenset({"меч", "фляга", "заплечный мешок"})


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
            "item_ids": [None, None],
            "gold": 15,
            "water_sips": 2,
            "knowledge": [],
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
            state_changed = self._ensure_characteristic_maxima(state) or state_changed
            knowledge = state.setdefault("knowledge", [])
            if not isinstance(knowledge, list):
                knowledge = []
                state["knowledge"] = knowledge
                state_changed = True
            if "11" in state.get("applied_book_effects", []) and (
                STEP11_TREASURE_KNOWLEDGE not in knowledge
            ):
                knowledge.append(STEP11_TREASURE_KNOWLEDGE)
                state_changed = True
            current_step = state.get("step")
            current_step_knowledge = OFFSET_KNOWLEDGE.get(current_step)
            if current_step_knowledge and current_step_knowledge not in knowledge:
                knowledge.append(current_step_knowledge)
                state_changed = True
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
                    ids = state.get("item_ids")
                    if isinstance(ids, list):
                        state["item_ids"] = [
                            item_id for item, item_id in zip(items, ids)
                            if not (isinstance(item, str) and item.strip().casefold() == "заплечный мешок")
                        ]
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

    @staticmethod
    def _ensure_characteristic_maxima(state: dict[str, Any]) -> bool:
        characteristics = state.get("characteristics")
        if not isinstance(characteristics, dict):
            return False
        changed = False
        for key, theoretical_maximum in CHARACTERISTIC_MAXIMUMS.items():
            maximum_key = f"max_{key}"
            current = max(0, int(characteristics.get(key, 0)))
            try:
                personal_maximum = int(characteristics.get(maximum_key, current))
            except (TypeError, ValueError):
                personal_maximum = current
            personal_maximum = max(0, min(theoretical_maximum, personal_maximum))
            if characteristics.get(maximum_key) != personal_maximum:
                characteristics[maximum_key] = personal_maximum
                changed = True
            if current > personal_maximum:
                characteristics[key] = personal_maximum
                changed = True
        return changed

    @staticmethod
    def _personal_characteristic_maximum(characteristics: dict[str, Any], key: str) -> int:
        try:
            maximum = int(characteristics.get(f"max_{key}", characteristics.get(key, 0)))
        except (TypeError, ValueError):
            maximum = int(characteristics.get(key, 0))
        return max(0, min(CHARACTERISTIC_MAXIMUMS[key], maximum))

    def _save_state(self, player_id: int, state: dict[str, Any]) -> None:
        self.game_store.save_player_state(player_id, state)

    @staticmethod
    def _format_telegram_text(text: str) -> str:
        """Add readable Telegram HTML formatting while keeping book text intact."""
        title, separator, body = text.partition("\n\n")
        if not separator:
            return html.escape(text)

        title = title.strip()
        if title == "Битва":
            return BlackCastleBot._format_battle_screen(body)

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
        if is_heading and title == "Битва":
            formatted = [f"{icon} {html.escape(title)}"]
        else:
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
                formatted.append("\n".join(BlackCastleBot._format_battle_line(line) for line in lines))
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

    @staticmethod
    def _format_battle_line(line: str) -> str:
        line = line.strip()
        if re.match(r"Вы теряете \d+ ВЫНОСЛИВОСТИ:", line, re.IGNORECASE):
            return html.escape(line)
        stamina = re.fullmatch(r"ВЫНОСЛИВОСТЬ после раунда:", line, re.IGNORECASE)
        if stamina:
            return "ВЫНОСЛИВОСТЬ после раунда:"
        player_stamina = re.fullmatch(r"Вы — (\d+)(?: ❤️)?", line, re.IGNORECASE)
        if player_stamina:
            return f"Вы — {player_stamina.group(1)} ❤️"
        enemy_stamina = re.fullmatch(r"(.+?) — (\d+)(?: ❤️)?", line)
        if enemy_stamina:
            return f"{html.escape(enemy_stamina.group(1))} — {enemy_stamina.group(2)} ❤️"
        stamina_change = re.fullmatch(
            r"(Ваша ВЫНОСЛИВОСТЬ|ВЫНОСЛИВОСТЬ(?: (?:Копии|.+?))?): (\d+) → (\d+)",
            line,
        )
        if stamina_change:
            who = stamina_change.group(1)
            if who == "Ваша ВЫНОСЛИВОСТЬ":
                label = "Выносливость"
            elif who == "ВЫНОСЛИВОСТЬ":
                label = "Выносливость"
            else:
                label = f"Выносливость {who.removeprefix('ВЫНОСЛИВОСТЬ ')}"
            return (
                f"{html.escape(label)}: {stamina_change.group(2)} ❤️ → "
                f"{stamina_change.group(3)} ❤️"
            )

        escaped = html.escape(line)
        is_formula_or_stat = (
            "Мастерство:" in line or "УДАРА" in line
            or "🎲" in line or re.search(r"\d+\s+⚔️", line)
            or line.startswith(("ВЫНОСЛИВОСТЬ", "Выносливость", "Битва продолжается"))
            or "получает 2 урона" in line
            or re.search(r"\b\d+\s+против\s+\d+\b", line, re.IGNORECASE)
            or line.startswith("Выберите противника")
        )
        return escaped if is_formula_or_stat else f"<blockquote>{escaped}</blockquote>"

    @staticmethod
    def _split_battle_sentences(text: str) -> list[str]:
        sentence_pattern = re.compile(r".+?[.!?…]+(?:[»”\"’]+)?(?=\s|$)|.+$")
        return [sentence.strip() for sentence in sentence_pattern.findall(text) if sentence.strip()]

    @staticmethod
    def _battle_narration_lines(log: list[str]) -> list[str]:
        narration = []
        for entry in log:
            for line in str(entry).splitlines():
                formatted = BlackCastleBot._format_battle_line(line)
                if formatted.startswith("<blockquote>") and formatted.endswith("</blockquote>"):
                    prose = html.unescape(formatted[len("<blockquote>"):-len("</blockquote>")])
                    narration.extend(BlackCastleBot._split_battle_sentences(prose))
        return narration

    @staticmethod
    def _format_battle_screen(body: str) -> str:
        narration: list[str] = []
        breakdown: list[str] = []
        lines = body.splitlines()
        index = 0
        stamina_total = re.compile(r"(?:Вы|.+?) — \d+(?: ❤️)?")
        while index < len(lines):
            raw_line = lines[index]
            line = raw_line.strip()
            index += 1
            if not line:
                continue
            if re.fullmatch(r"ВЫНОСЛИВОСТЬ после раунда:", line, re.IGNORECASE):
                total_lines = [line]
                while index < len(lines) and stamina_total.fullmatch(lines[index].strip()):
                    total_lines.append(BlackCastleBot._format_battle_line(lines[index]))
                    index += 1
                breakdown.append("<pre>" + "\n".join(total_lines) + "</pre>")
                continue
            formatted = BlackCastleBot._format_battle_line(line)
            if formatted.startswith("<blockquote>") and formatted.endswith("</blockquote>"):
                prose = html.unescape(formatted[len("<blockquote>"):-len("</blockquote>")])
                narration.extend(BlackCastleBot._split_battle_sentences(prose))
            else:
                breakdown.append(formatted)

        sections = ["⚔️ Битва"]
        if narration:
            sections.append("<blockquote>" + "\n\n".join(narration) + "</blockquote>")
        if breakdown:
            sections.append("Расшифровка битвы:\n\n" + "\n\n".join(breakdown))
        return "\n\n".join(sections)

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

        if view == "discard":
            entries = [
                (index, item) for index, item in enumerate(state.get("items", []))
                if isinstance(item, str)
                and item.strip().casefold() not in NON_DISCARDABLE_ITEMS
            ]
            text = "Выберите предмет, который нужно выбросить:"
            if not entries:
                text = "В заплечном мешке нет предметов, которые можно выбросить."
            else:
                text += "\n\n" + "\n".join(
                    f"{ordinal}. {item}" for ordinal, (_, item) in enumerate(entries, start=1)
                )
            keyboard = []
            item_ids = state.get("item_ids", [])
            for ordinal, (index, item) in enumerate(entries, start=1):
                item_hash = hashlib.sha256(item.encode("utf-8")).hexdigest()[:8]
                item_id = item_ids[index] if isinstance(item_ids, list) and index < len(item_ids) else None
                item_ref = str(item_id) if item_id is not None else f"legacy-{index}"
                keyboard.append([{
                    "text": f"Выкинуть {item[:1].lower()}{item[1:]}",
                    "callback_data": f"blackcastle:discard:id:{item_ref}:{item_hash}",
                }])
            keyboard.append([{
                "text": "Характеристики и инвентарь",
                "callback_data": "blackcastle:status",
            }])
            return text, keyboard, True

        if view in {"stats", "inventory", "status"}:
            values = state["characteristics"]
            item_entries = [
                (index, item) for index, item in enumerate(state.get("items", []))
                if isinstance(item, str)
            ]
            carried_items = [item for _, item in item_entries]
            equipment_names = {"меч", "фляга", "заплечный мешок"}
            bag_items = [
                item for item in carried_items
                if item.strip().casefold() not in equipment_names
            ]
            bag_used = self._bag_item_count(state, bag_items)
            bag_listing = "\n".join(f"• {item}" for item in bag_items) or "Пусто"

            def characteristic_text(key: str) -> str:
                value = int(values[key])
                initial = self._personal_characteristic_maximum(values, key)
                current_text = str(value) if value >= initial else f"{value} из {initial}"
                theoretical_maximum = CHARACTERISTIC_MAXIMUMS[key]
                return f"{current_text} (Максимум {theoretical_maximum})"

            mastery_line = characteristic_text("mastery")
            if self._has_item(state, "Меч Зеленого рыцаря"):
                mastery_line += " (+1 меч Зеленого рыцаря)"
            flask_sips = max(0, min(2, int(state.get("water_sips", 0))))
            flask_status = {
                2: "полная (2 глотка)",
                1: "наполовину полная (1 глоток)",
                0: "пустая",
            }[flask_sips]
            has_flask = self._has_item(state, "Фляга")
            knowledge = state.get("knowledge", [])
            knowledge_section = (
                "\nЗнания:\n" + "\n".join(f"• {entry}" for entry in knowledge) + "\n"
                if isinstance(knowledge, list) and knowledge
                else ""
            )
            text = (
                "Характеристики и инвентарь\n\n"
                "Характеристики:\n"
                f"МАСТЕРСТВО: {mastery_line}\n"
                f"ВЫНОСЛИВОСТЬ: {characteristic_text('stamina')}\n"
                f"УДАЧА: {characteristic_text('luck')}\n\n"
                "Инвентарь:\n"
                f"Снаряжение: {'меч' if self._has_item(state, 'Меч') else 'нет'}\n"
                f"Фляга: {flask_status if has_flask else 'нет фляги'}; каждый глоток восстанавливает 2 ВЫНОСЛИВОСТИ.\n"
                f"Заплечный мешок: {bag_used}/{state['bag_capacity']} предметов:\n"
                f"{bag_listing}\n"
                + knowledge_section
                + "\nЗаклинания:\n"
                + "\n".join(
                    f"{SPELL_LABELS[key]}: {state.get('spells', {}).get(key, 0)}"
                    for key in INITIAL_SPELLS
                )
                + "\n"
                f"Золотые: {state['gold']}"
            )
            keyboard = []
            stamina = int(values.get("stamina", 0))
            personal_stamina_maximum = self._personal_characteristic_maximum(values, "stamina")
            if has_flask and flask_sips > 0 and stamina < personal_stamina_maximum:
                keyboard.append([{
                    "text": "Попить из фляги (+2 Выносливости)",
                    "callback_data": "blackcastle:flask:drink",
                }])
            healing_uses = max(0, int(state.get("spells", {}).get("healing", 0)))
            if (healing_uses > 0 and stamina < personal_stamina_maximum
                    and not isinstance(state.get("battle"), dict)):
                keyboard.append([{
                    "text": "Заклинание Исцеления (+8 Выносливости)",
                    "callback_data": "blackcastle:spell:healing",
                }])
            for consumable_key, (canonical_name, _, button_label) in INVENTORY_STAMINA_CONSUMABLES.items():
                if stamina < personal_stamina_maximum and any(
                    item.strip().casefold() == canonical_name.casefold()
                    for item in carried_items
                ):
                    keyboard.append([{
                        "text": button_label,
                        "callback_data": f"blackcastle:item_use:{consumable_key}",
                    }])
            if bag_items:
                keyboard.append([{
                    "text": "Выкинуть что-то из рюкзака",
                    "callback_data": "blackcastle:discard:open",
                }])
            keyboard.append([{
                "text": f"К шагу {step}",
                "callback_data": "blackcastle:back",
            }])
            return text, keyboard, view == "status"

        if view == "battle":
            battle = state.get("battle", {})
            log_limit = 850 if battle.get("inline_message") else 3600
            battle_log = battle.get("log", [])
            if battle.get("display_phase") == "narration":
                visible_count = max(0, int(battle.get("narration_visible_count", 0)))
                log = "\n\n".join(self._battle_narration_lines(battle_log)[:visible_count])[-log_limit:]
            else:
                log = "\n\n".join(battle_log)[-log_limit:]
            text = "Битва"
            if log:
                text += f"\n\n{log}"
            else:
                text += "\n\nПодготовка к бою."
            keyboard = []
            if battle.get("status") == "stage_won":
                return text, keyboard, False
            if battle.get("display_phase") == "narration":
                return text, keyboard, False
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
                get_loot = getattr(self.game_store, "get_paragraph_loot_options", None)
                loot_step = int(battle.get("source_step", step))
                loot_options = get_loot(loot_step) if callable(get_loot) else []
                claimed_by_step = state.get("claimed_loot", {})
                claimed_ids = set(
                    claimed_by_step.get(str(loot_step), [])
                ) if isinstance(claimed_by_step, dict) else set()
                for option in loot_options:
                    loot_id = str(option.get("loot_id") or "")
                    if loot_id and (loot_id not in claimed_ids or loot_id in REPEATABLE_LOOT_IDS):
                        keyboard.append([{
                            "text": str(option["button_text"]),
                            "callback_data": f"blackcastle:loot:{loot_step}:{loot_id}",
                        }])
                victory_options = battle.get("victory_options")
                if isinstance(victory_options, list) and len(victory_options) > 1:
                    for option in victory_options:
                        keyboard.append([{
                            "text": self._route_button_text(
                                str(option.get("button_text") or "Продолжить"),
                                int(option["target_paragraph"]),
                            ),
                            "callback_data": f"blackcastle:battle:finish:{option['choice_id']}",
                        }])
                else:
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
            full_body = str(body or "")
            sequence_chunks = self._battle_sequence_chunks(step, full_body)
            sequence_stage = int(state.get("battle_sequence_stage", 0)) if sequence_chunks else 0
            display_body = self._battle_stage_body(step, full_body, state)
            text = title
            if display_body:
                text += f"\n\n{display_body}"
            else:
                text += "\n\nТекст этого параграфа будет добавлен позже."
            question = paragraph.get("question")
            if question:
                text += f"\n\n{question}"

            choices = self.game_store.get_paragraph_choices(step)
            get_loot = getattr(self.game_store, "get_paragraph_loot_options", None)
            loot_options = get_loot(step) if callable(get_loot) else []
            claimed_by_step = state.get("claimed_loot", {})
            claimed_ids = set(claimed_by_step.get(str(step), [])) if isinstance(claimed_by_step, dict) else set()
            claimed_names = list(dict.fromkeys(
                str(option.get("button_text") or "").removeprefix("Взять ")
                for option in loot_options if option.get("loot_id") in claimed_ids
            ))
            if claimed_names:
                text += "\n\nВы уже использовали или взяли: " + ", ".join(claimed_names) + "."
            enemies = self._battle_enemies(display_body)
            prepared_magic = state.get("combat_magic_pending")
            prepared_spells = self._prepared_combat_spells(prepared_magic)
            if enemies and prepared_spells:
                labels = ", ".join(SPELL_LABELS[spell] for spell in prepared_spells)
                text += f"\n\nПодготовлены заклинания: {labels}."
            luck_checks = state.get("luck_checks")
            luck_result = (
                luck_checks.get(str(step))
                if isinstance(luck_checks, dict)
                else None
            )
            keyboard = []
            battle_button_label = "К бою" if sequence_chunks else "Вступить в бой"
            battle_button_added = False
            if not enemies:
                for option in loot_options:
                    loot_id = str(option.get("loot_id") or "")
                    if loot_id and (loot_id not in claimed_ids or loot_id in REPEATABLE_LOOT_IDS):
                        keyboard.append([{
                            "text": str(option["button_text"]),
                            "callback_data": f"blackcastle:loot:{step}:{loot_id}",
                        }])
                        use_effect = PARAGRAPH_LOOT_STAMINA_EFFECTS.get((step, loot_id))
                        current_stamina = int(state.get("characteristics", {}).get("stamina", 0))
                        stamina_maximum = self._personal_characteristic_maximum(
                            state.get("characteristics", {}), "stamina"
                        )
                        if use_effect and current_stamina < stamina_maximum:
                            keyboard.append([{
                                "text": use_effect[1],
                                "callback_data": f"blackcastle:loot_use:{step}:{loot_id}",
                            }])
                purchase_options = PARAGRAPH_PURCHASE_OPTIONS.get(step, [])
                if purchase_options:
                    receipt = state.get("last_shop_purchase")
                    if isinstance(receipt, dict) and receipt.get("step") == step:
                        text += f"\n\n{receipt.get('text', 'Покупка выполнена.')}. Осталось золотых: {int(state.get('gold', 0))}."
                    text += f"\n\nЗолотые: {int(state.get('gold', 0))}. Выберите покупку:"
                    has_flask = self._has_item(state, "Фляга")
                    for option in purchase_options:
                        if option.get("requires_flask") and not has_flask:
                            continue
                        if (option.get("water_sips") is not None
                                and int(state.get("water_sips", 0)) >= int(option["water_sips"])):
                            continue
                        if option.get("bag_capacity") and int(state.get("bag_capacity", 7)) >= int(option["bag_capacity"]):
                            continue
                        keyboard.append([{
                            "text": str(option["button_text"]),
                            "callback_data": f"blackcastle:buy:{step}:{option['purchase_id']}",
                        }])
            for choice in choices:
                required_item = choice.get("required_item")
                if required_item and not self._has_item(state, str(required_item)):
                    continue
                required_knowledge = KNOWLEDGE_GATED_ROUTES.get(
                    (step, str(choice.get("choice_id") or ""))
                )
                if required_knowledge and required_knowledge not in state.get("knowledge", []):
                    continue
                target = int(choice["target_paragraph"])
                source_label = ROUTE_BUTTON_SUFFIX.sub(
                    "", str(choice["button_text"])
                ).strip()
                choice_is_for_later_stage = bool(sequence_chunks) and any(
                    stage_index > sequence_stage
                    and re.search(rf"(?<!\d){target}(?!\d)", stage_body)
                    for stage_index, stage_body in enumerate(sequence_chunks)
                )
                if choice_is_for_later_stage and not self._is_battle_route(source_label):
                    continue
                choice_stage_body = next((
                    stage_body for stage_body in sequence_chunks
                    if re.search(rf"(?<!\d){target}(?!\d)", stage_body)
                ), display_body)
                choice_is_escape = self._is_escape_choice(
                    source_label, target, choice_stage_body
                )
                spell_options = self._route_spell_options(source_label, display_body)
                if enemies and spell_options:
                    for spell in spell_options:
                        if (spell not in prepared_spells
                                and int(state.get("spells", INITIAL_SPELLS).get(spell, 0)) > 0):
                            keyboard.append([{
                                "text": self._spell_button_label(spell, combat=True),
                                "callback_data": f"blackcastle:cast:{step}:{choice['choice_id']}:{spell}",
                            }])
                    if self._is_battle_route(source_label) and not battle_button_added:
                        keyboard.append([{
                            "text": battle_button_label,
                            "callback_data": f"blackcastle:battle:start:{step}:{choice['choice_id']}",
                        }])
                        battle_button_added = True
                    continue
                if enemies and self._is_battle_route(source_label):
                    if not battle_button_added:
                        keyboard.append([{
                            "text": battle_button_label,
                            "callback_data": f"blackcastle:battle:start:{step}:{choice['choice_id']}",
                        }])
                        battle_button_added = True
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
                    wording = (
                        "Попробовать убежать"
                        if sequence_chunks and sequence_stage > 0 and choice_is_escape
                        else str(choice["button_text"])
                    )
                    button_text = self._route_button_text(wording, target)
                keyboard.append([{
                    "text": button_text,
                    "callback_data": f"blackcastle:route:{step}:{choice['choice_id']}",
                }])
            keyboard.extend(self._offset_knowledge_buttons(state, step, choices, full_body))
            if enemies and not battle_button_added:
                keyboard.append([{
                    "text": battle_button_label,
                    "callback_data": f"blackcastle:battle:start:auto:{step}",
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
            if not enemies:
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

    def _offset_knowledge_buttons(
        self, state: dict[str, Any], paragraph_number: int,
        choices: list[dict[str, Any]], body: str,
    ) -> list[list[dict[str, Any]]]:
        knowledge = state.get("knowledge", [])
        if not isinstance(knowledge, list):
            return []
        paragraph = self.game_store.get_paragraph(paragraph_number)
        body = str((paragraph or {}).get("body") or "")
        folded_body = body.casefold().replace("ё", "е")
        buttons: list[list[dict[str, Any]]] = []
        for action_key, rule in OFFSET_ACTIONS.items():
            if rule["steps"] is not None and paragraph_number not in rule["steps"]:
                continue
            if rule["steps"] is None:
                if rule.get("condition") == "crossroad":
                    if paragraph_number in {450, 555} or not re.search(r"перекрест", folded_body):
                        continue
                elif (paragraph_number in {318, 600}
                      or not re.search(r"заперт|неподатлив|не.{0,20}откры|замок|закрыт", folded_body)):
                    continue
            if not any(OFFSET_KNOWLEDGE.get(source) in knowledge for source in rule["knowledge"]):
                continue
            if rule["item"] and not self._has_item(state, str(rule["item"])):
                continue
            extra_item = rule.get("extra_item")
            if extra_item and not self._has_item(state, str(extra_item)):
                continue
            target = paragraph_number + int(rule["delta"])
            if self.game_store.get_paragraph(target) is None:
                continue
            buttons.append([{
                "text": self._route_button_text(str(rule["label"]), target),
                "callback_data": f"blackcastle:knowledge:{action_key}:{paragraph_number}:{target}",
            }])
        armor_sources = (90, 126, 130, 251, 330, 414, 527)
        armor_known = any(OFFSET_KNOWLEDGE[source] in knowledge for source in armor_sources)
        if (armor_known and paragraph_number not in armor_sources
                and self._has_item(state, "Зелёные латы")
                and not self._battle_enemies(body)
                and re.search(r"двер|комнат|выбер", body, re.IGNORECASE)):
            for choice in choices:
                required_item = choice.get("required_item")
                if required_item and not self._has_item(state, str(required_item)):
                    continue
                base_target = int(choice["target_paragraph"])
                target = base_target + 60
                if self.game_store.get_paragraph(target) is None:
                    continue
                label = ROUTE_BUTTON_SUFFIX.sub("", str(choice["button_text"])).strip()
                buttons.append([{
                    "text": self._route_button_text(f"В латах: {label}", target),
                    "callback_data": (
                        f"blackcastle:knowledge_route:{paragraph_number}:"
                        f"{choice['choice_id']}:{base_target}:{target}"
                    ),
                }])
        return buttons

    @classmethod
    def _apply_step_supply_effects(cls, state: dict[str, Any], paragraph_number: int) -> None:
        applied = state.setdefault("applied_book_effects", [])
        knowledge = state.setdefault("knowledge", [])
        knowledge_text = OFFSET_KNOWLEDGE.get(paragraph_number)
        if knowledge_text and knowledge_text not in knowledge:
            knowledge.append(knowledge_text)
        if paragraph_number == 11:
            if STEP11_TREASURE_KNOWLEDGE not in knowledge:
                knowledge.append(STEP11_TREASURE_KNOWLEDGE)
        if paragraph_number not in {11, 21, 131, 307, 500}:
            return
        effect_key = str(paragraph_number)
        if effect_key in applied:
            return
        if paragraph_number == 11:
            if cls._has_item(state, "Фляга"):
                state["water_sips"] = 0
        elif paragraph_number == 307:
            if cls._has_item(state, "Фляга"):
                state["water_sips"] = 2
        else:
            stamina_gains = {21: 2, 131: 6, 500: 6}
            gain = stamina_gains.get(paragraph_number, 0)
            if gain:
                characteristics = state.get("characteristics", {})
                maximum = cls._personal_characteristic_maximum(characteristics, "stamina")
                characteristics["stamina"] = min(
                    maximum, int(characteristics.get("stamina", 0)) + gain
                )
        applied.append(effect_key)

    @staticmethod
    def _bag_item_count(state: dict[str, Any], bag_items: list[str] | None = None) -> int:
        if bag_items is None:
            equipment_names = {"меч", "фляга", "заплечный мешок"}
            bag_items = [
                item for item in state.get("items", [])
                if isinstance(item, str) and item.strip().casefold() not in equipment_names
            ]
        slot_costs = state.get("item_slot_costs", {})
        total = 0
        for item in bag_items:
            try:
                cost = max(1, int(slot_costs.get(item, 1))) if isinstance(slot_costs, dict) else 1
            except (TypeError, ValueError):
                cost = 1
            total += cost
        return total

    @staticmethod
    def _consume_item(state: dict[str, Any], required_item: str) -> bool:
        items = state.get("items", [])
        for index, item in enumerate(items):
            if not isinstance(item, str):
                continue
            if BlackCastleBot._has_item({"items": [item]}, required_item):
                del items[index]
                item_ids = state.get("item_ids")
                if isinstance(item_ids, list) and index < len(item_ids):
                    del item_ids[index]
                slot_costs = state.get("item_slot_costs")
                if isinstance(slot_costs, dict) and item not in items:
                    slot_costs.pop(item, None)
                return True
        return False

    @staticmethod
    def _discard_reference(state: dict[str, Any], action: str) -> tuple[int, str] | None:
        try:
            parts = action.split(":")
            items = state.get("items", [])
            if len(parts) == 5 and parts[2] == "id":
                item_ref, item_hash = parts[3], parts[4]
                if item_ref.startswith("legacy-"):
                    index = int(item_ref.removeprefix("legacy-"))
                else:
                    item_id = int(item_ref)
                    ids = state.get("item_ids", [])
                    index = ids.index(item_id) if isinstance(ids, list) else -1
            else:  # accept buttons sent before inventory IDs were introduced
                _, _, index_text, item_hash = action.split(":", 3)
                index = int(index_text)
            item = items[index] if isinstance(items, list) and 0 <= index < len(items) else None
            if (not isinstance(item, str) or item.strip().casefold() in NON_DISCARDABLE_ITEMS
                    or hashlib.sha256(item.encode("utf-8")).hexdigest()[:8] != item_hash):
                return None
            return index, item_hash
        except (ValueError, TypeError, IndexError):
            return None

    def _paragraph_photo(self, paragraph_number: int) -> str:
        paragraph = self.game_store.get_paragraph(paragraph_number)
        if paragraph and paragraph.get("photo_file_id"):
            return str(paragraph["photo_file_id"])
        return self._default_photo()

    def _default_photo(self) -> str:
        return self.game_store.get_setting("kniga_igra_black_castle_photo_file_id")

    def _screen_photo(self, state: dict[str, Any]) -> str:
        view = state.get("view")
        if view == "preface":
            return self.game_store.get_setting(
                "kniga_igra_black_castle_preface_photo_file_id"
            ) or self._default_photo()
        if view in {"status", "discard"}:
            return self.game_store.get_setting(
                "kniga_igra_black_castle_status_photo_file_id"
            ) or self._default_photo()
        return self._paragraph_photo(int(state.get("step", 1)))

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
            "mastery_base": int(match.group("mastery")),
            "stamina": int(match.group("stamina")),
            "damage_to_player": (
                3 if stronger_merchant_blow and "торгов" in match.group("name").casefold() else 2
            ),
        } for match in ENEMY_STATS.finditer(body)]

    @staticmethod
    def _battle_sequence_chunks(paragraph_number: int, body: str) -> list[str]:
        """Split prose only when a defeated enemy explicitly triggers the next one."""
        matches = list(ENEMY_STATS.finditer(body))
        if len(matches) < 2:
            return []
        transition = re.compile(
            r"если\s+(?:вы\s+)?(?:убили|победили)\s+(?:его|её|ее)"
            r".{0,180}?(?:появля|выходит|поднимается|вступает|бросает|хватается)",
            re.IGNORECASE | re.DOTALL,
        )
        cuts: list[int] = []
        for previous, following in zip(matches, matches[1:]):
            between = body[previous.end():following.start()]
            trigger = transition.search(between)
            if trigger:
                cuts.append(previous.end() + trigger.start())
        if not cuts:
            return []
        chunks: list[str] = []
        start = 0
        for cut in cuts:
            chunks.append(body[start:cut].strip())
            start = cut
        chunks.append(body[start:].strip())
        return chunks

    @classmethod
    def _battle_stage_body(
        cls, paragraph_number: int, body: str, state: dict[str, Any]
    ) -> str:
        chunks = cls._battle_sequence_chunks(paragraph_number, body)
        if not chunks:
            return body
        try:
            stage = int(state.get("battle_sequence_stage", 0))
        except (TypeError, ValueError):
            stage = 0
        return chunks[max(0, min(stage, len(chunks) - 1))]

    @staticmethod
    def _mark_battle_victory(battle: dict[str, Any]) -> None:
        next_stage = int(battle.get("sequence_stage_index", 0)) + 1
        if next_stage < int(battle.get("sequence_stage_count", 1)):
            battle["status"] = "stage_won"
            battle["next_sequence_stage"] = next_stage
        else:
            battle["status"] = "won"

    def _battle_phrase(self, enemy_name: str, phase: str, **values: str) -> str:
        getter = getattr(self.game_store, "get_battle_narrative_templates", None)
        templates = getter(enemy_name, phase) if callable(getter) else []
        if not templates:
            key = canonical_enemy_key(enemy_name)
            templates = ENEMY_BATTLE_TEXT.get(key, {}).get(phase, [])
        if not templates:
            templates = GENERIC_BATTLE_TEXT.get(phase, [])
        if not templates:
            return ""
        if "victim_dative" not in values:
            values["victim_dative"] = {"вас": "вам", "Копию": "Копии"}.get(
                values.get("victim", ""), values.get("victim", "")
            )
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
        if any("mastery_base" not in enemy for enemy in enemies):
            paragraph = self.game_store.get_paragraph(int(battle.get("source_step", state.get("step", 1))))
            original_enemies = self._battle_enemies(str((paragraph or {}).get("body") or ""))
            for index, enemy in enumerate(enemies):
                enemy.setdefault(
                    "mastery_base",
                    original_enemies[index]["mastery"]
                    if index < len(original_enemies) else int(enemy["mastery"]),
                )
        active_enemy_indexes = [0] if acting_copy else [
            i for i, enemy in enumerate(enemies) if int(enemy.get("stamina", 0)) > 0
        ]
        enemy_rolls: list[int | None] = [None] * len(enemies)
        enemy_dice: list[tuple[int, int] | None] = [None] * len(enemies)
        enemy_attacks = [-1] * len(enemies)
        for i in active_enemy_indexes:
            die_one, die_two = random.randint(1, 6), random.randint(1, 6)
            enemy_dice[i] = (die_one, die_two)
            enemy_rolls[i] = die_one + die_two
            enemy_attacks[i] = enemy_rolls[i] + enemies[i]["mastery"]
        player_die_one, player_die_two = random.randint(1, 6), random.randint(1, 6)
        player_roll = player_die_one + player_die_two
        player_mastery = actor["mastery"]
        weapon_bonus = (
            1 if not acting_copy and self._has_item(state, "Меч Зеленого рыцаря") else 0
        )
        strength_bonus = (
            2 if not acting_copy
            and "strength" in self._prepared_combat_spells(battle.get("magic"))
            else 0
        )
        attack_penalty = 0 if acting_copy else int(battle.get("player_attack_penalty", 0))
        player_attack = player_roll + player_mastery + weapon_bonus + strength_bonus - attack_penalty
        selected_attack = enemy_attacks[target_index]
        player_wins = player_attack > selected_attack
        player_stamina_before = int(actor["stamina"])
        target_stamina_before = int(target["stamina"])
        enemy_hits = [
            i for i, enemy_attack in enumerate(enemy_attacks)
            if enemy_attack > player_attack and enemies[i].get("stamina", 0) > 0
        ]
        battle["round"] = int(battle.get("round", 0)) + 1
        battle["display_phase"] = "narration"
        battle["narration_visible_count"] = 0
        log = battle.setdefault("log", [])
        display_names = [re.sub(r"\s+", " ", enemy["name"]).strip().title() for enemy in enemies]
        target_name = display_names[target_index]
        victim = "Копию" if acting_copy else "вас"
        victim_dative = "Копии" if acting_copy else "вам"
        opening = "\n".join(
            self._battle_phrase(
                enemies[i]["name"], "opening", enemy=display_names[i], victim=victim,
                victim_dative=victim_dative,
            )
            for i in active_enemy_indexes
        )
        round_intro = battle.pop("round_intro", None)
        if round_intro:
            opening = f"{round_intro}\n{opening}"
        enemy_attack_lines = []
        for i in active_enemy_indexes:
            enemy = enemies[i]
            mastery_base = int(enemy.get("mastery_base", enemy["mastery"]))
            mastery_reduction = max(0, mastery_base - int(enemy["mastery"]))
            die_one, die_two = enemy_dice[i] or (0, 0)
            formula = (
                f"{die_one} 🎲 + {die_two} 🎲 + "
                f"{mastery_base} 🎯 (база)"
            )
            if mastery_reduction:
                formula += f" - {mastery_reduction} (заклинание Слабости)"
            formula += f" = {enemy_attacks[i]} ⚔️"
            enemy_attack_lines.append(f"{display_names[i]}: {formula}.")
        enemy_attack_text = "\n".join(enemy_attack_lines)
        roll_owner = "Бросок Копии" if acting_copy else "Ваш бросок"
        player_formula = (
            f"{roll_owner}: {player_die_one} 🎲 + {player_die_two} 🎲 + "
            f"{player_mastery} 🎯 (база)"
        )
        if weapon_bonus:
            player_formula += " + 1 (меч Зеленого рыцаря)"
        if strength_bonus:
            player_formula += " + 2 (бонус заклинания Силы)"
        if attack_penalty:
            player_formula += f" - {attack_penalty} (штраф книги: бой на дереве)"
        if len(active_enemy_indexes) > 1:
            attack_description = f"Атаки противников:\n{enemy_attack_text}"
        else:
            attack_description = enemy_attack_text
        counter_start = self._battle_phrase(
            "*", "copy_intro" if acting_copy else "player_intro"
        )
        event_lines = [
            f"{opening}\n{attack_description}",
            f"{counter_start}\n{player_formula} = {player_attack} ⚔️.",
            (f"{('Удар Копии' if acting_copy else 'Ваш выпад')} оказывается быстрее — "
             f"{player_attack} ⚔️ против {selected_attack} ⚔️."
             if player_wins else f"{target_name} успевает опередить {victim} — "
             f"{selected_attack} ⚔️ против {player_attack} ⚔️."
             if player_attack < selected_attack else
             self._battle_phrase(target["name"], "parry", enemy=target_name)),
        ]

        for action_number in range(1, 6):
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
                    if int(target["stamina"]) == 0:
                        finisher = "Удар Копии" if acting_copy else "Ваш удар"
                        wound = self._battle_phrase(
                            target["name"], "fatal_blow", enemy=target_name,
                            finisher=finisher,
                        )
                    elif acting_copy:
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
                        f"ВЫНОСЛИВОСТЬ {target_name}: {target_stamina_before} → {target['stamina']}"
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
                        f"{'ВЫНОСЛИВОСТЬ Копии' if acting_copy else 'Ваша ВЫНОСЛИВОСТЬ'}: "
                        f"{player_stamina_before} → {actor['stamina']}"
                    )
                elif player_wins:
                    line = self._battle_phrase("*", "avoids_hit")
                else:
                    line = self._battle_phrase("*", "tie_no_damage")
            log.append(line)
            if len(log) > 24:
                del log[:-24]
            narration_lines = self._battle_narration_lines(log)
            while battle["narration_visible_count"] < len(narration_lines):
                if battle["narration_visible_count"]:
                    await asyncio.sleep(5)
                battle["narration_visible_count"] += 1
                await self._edit_battle_progress(
                    player_id, state, inline_message_id=inline_message_id, chat_id=chat_id
                )

        if not acting_copy and int(actor["stamina"]) <= 0:
            battle["status"] = "lost"
            ending = self._battle_phrase("*", "player_defeated")
        elif acting_copy and int(actor["stamina"]) <= 0:
            battle["status"] = "awaiting_continue"
            battle["stage"] = "copy_lost"
            ending = self._battle_phrase("*", "copy_falls")
        elif acting_copy and int(target["stamina"]) <= 0:
            if all(int(enemy["stamina"]) <= 0 for enemy in enemies):
                self._mark_battle_victory(battle)
                ending = (
                    self._battle_phrase(target["name"], "defeated", enemy=target_name)
                    + " " + self._battle_phrase("*", "copy_victory_end")
                )
            else:
                battle["status"] = "awaiting_continue"
                battle["stage"] = "copy_won"
                ending = (
                    self._battle_phrase(target["name"], "defeated", enemy=target_name)
                    + " " + self._battle_phrase("*", "copy_victory_continue")
                )
        elif all(int(enemy["stamina"]) <= 0 for enemy in enemies):
            self._mark_battle_victory(battle)
            ending = self._battle_phrase("*", "battle_victory")
        else:
            battle["status"] = "awaiting_continue"
            remaining = [display_names[i] for i, enemy in enumerate(enemies) if enemy["stamina"] > 0]
            if len(remaining) == 1:
                ending = self._battle_phrase(
                    enemies[next(i for i, enemy in enumerate(enemies) if enemy["stamina"] > 0)]["name"],
                    "survives",
                    enemy=remaining[0],
                )
            else:
                ending = self._battle_phrase(
                    "*", "multiple_survives", enemies=", ".join(remaining)
                )
        log.append(ending)

        narration_lines = self._battle_narration_lines(log)
        while battle["narration_visible_count"] < len(narration_lines):
            if battle["narration_visible_count"]:
                await asyncio.sleep(5)
            battle["narration_visible_count"] += 1
            await self._edit_battle_progress(
                player_id, state, inline_message_id=inline_message_id, chat_id=chat_id
            )
        if battle["narration_visible_count"]:
            await asyncio.sleep(5)

        if acting_copy:
            summary = (
                f"ВЫНОСЛИВОСТЬ после раунда:\nВы — {state['characteristics']['stamina']}\n"
                f"Копия — {actor['stamina']}"
            )
        else:
            summary = "ВЫНОСЛИВОСТЬ после раунда:\n" + "\n".join(
                [f"Вы — {actor['stamina']}"]
                + [f"{display_names[i]} — {enemy['stamina']}" for i, enemy in enumerate(enemies)]
            )
        log.insert(len(log) - 1, summary)
        battle["display_phase"] = "complete"
        battle.pop("narration_visible_count", None)
        if len(log) > 24:
            del log[:-24]
        await self._edit_battle_progress(
            player_id, state, inline_message_id=inline_message_id, chat_id=chat_id
        )
        if battle.get("status") == "stage_won":
            state["battle_sequence_stage"] = int(battle["next_sequence_stage"])
            state.pop("battle", None)
            state["view"] = "step"
            state["page_part"] = 0
            await self._edit_battle_progress(
                player_id, state, inline_message_id=inline_message_id, chat_id=chat_id
            )

    def _recover_interrupted_battle(self, state: dict[str, Any]) -> None:
        """Restore a continue button when Telegram redelivers an interrupted round callback."""
        battle = state.get("battle")
        if not isinstance(battle, dict):
            return
        if battle.get("display_phase") == "narration" and battle.get("status") != "running":
            battle["display_phase"] = "complete"
            battle.pop("narration_visible_count", None)
            return
        if battle.get("status") != "running":
            return
        battle["display_phase"] = "complete"
        log = battle.setdefault("log", [])
        if len(log) < 4:
            log.clear()
            battle["round_intro"] = self._battle_phrase("*", "recovery")
            battle["status"] = "awaiting_continue"
            return

        enemies = battle.get("enemies", [])
        if not enemies:
            battle["status"] = "awaiting_continue"
            log.clear()
            log.append(self._battle_phrase("*", "recovery"))
            return
        display_names = [re.sub(r"\s+", " ", enemy["name"]).strip().title() for enemy in enemies]
        acting_copy = battle.get("stage") == "copy"
        actor = battle.get("copy") if acting_copy else state["characteristics"]
        if len(log) < 6:
            if acting_copy:
                log.append(
                    f"ВЫНОСЛИВОСТЬ после раунда:\nВы — {state['characteristics']['stamina']}\n"
                    f"Копия — {actor['stamina']}"
                )
            else:
                log.append("ВЫНОСЛИВОСТЬ после раунда:\n" + "\n".join(
                    [f"Вы — {actor['stamina']}"]
                    + [f"{display_names[i]} — {enemy['stamina']}" for i, enemy in enumerate(enemies)]
                ))

        if not acting_copy and int(actor["stamina"]) <= 0:
            battle["status"] = "lost"
            log.append(self._battle_phrase("*", "player_defeated"))
        elif acting_copy and int(actor["stamina"]) <= 0:
            battle["status"] = "awaiting_continue"
            battle["stage"] = "copy_lost"
            log.append(self._battle_phrase("*", "copy_falls"))
        elif acting_copy and int(enemies[0]["stamina"]) <= 0:
            if all(int(enemy["stamina"]) <= 0 for enemy in enemies):
                battle["status"] = "won"
                log.append(self._battle_phrase("*", "copy_victory_end"))
            else:
                battle["status"] = "awaiting_continue"
                battle["stage"] = "copy_won"
                log.append(self._battle_phrase("*", "copy_victory_continue"))
        elif all(int(enemy["stamina"]) <= 0 for enemy in enemies):
            battle["status"] = "won"
            log.append(self._battle_phrase("*", "battle_victory"))
        else:
            battle["status"] = "awaiting_continue"
            remaining = [display_names[i] for i, enemy in enumerate(enemies) if enemy["stamina"] > 0]
            if len(remaining) == 1:
                enemy = next(enemy for enemy in enemies if int(enemy["stamina"]) > 0)
                log.append(self._battle_phrase(enemy["name"], "survives", enemy=remaining[0]))
            else:
                log.append(self._battle_phrase(
                    "*", "multiple_survives", enemies=", ".join(remaining)
                ))

    @staticmethod
    def _spell_button_label(spell: str, *, combat: bool = False) -> str:
        label = f"Заклинание {SPELL_LABELS.get(spell, spell)}"
        if combat and spell in {"strength", "weakness", "copy"}:
            label += " (усиление боя)"
        return label

    @staticmethod
    def _prepared_combat_spells(magic: Any) -> list[str]:
        """Normalize current and legacy spell-preparation state."""
        if isinstance(magic, dict):
            spells = magic.get("spells")
            if not isinstance(spells, list):
                spells = [magic.get("spell")]
        elif isinstance(magic, list):
            spells = magic
        else:
            spells = []
        return list(dict.fromkeys(
            spell for spell in spells
            if isinstance(spell, str) and spell in INITIAL_SPELLS
        ))

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
        normalized = label.casefold().replace("ё", "е")
        return bool(
            re.fullmatch(r"(?:если(?: вы)? )?победили?|вступить в бой", normalized)
            or re.match(
                r"^(?:если(?: же)?\s+)?(?:(?:вы|вам)\s+)?"
                r"(?:(?:удалось|удается|удастся)\s+)?"
                r"(?:победить|победили|победил|победите|убить|убили|убив|ранить)\b",
                normalized,
            )
        )

    @classmethod
    def _post_battle_choices(cls, choices: list[dict[str, Any]], body: str) -> list[dict[str, Any]]:
        """Return choices that are available after the current fight."""
        result = []
        for choice in choices:
            label = ROUTE_BUTTON_SUFFIX.sub("", str(choice.get("button_text") or "")).strip()
            folded = label.casefold().replace("ё", "е")
            try:
                target = int(choice["target_paragraph"])
            except (KeyError, TypeError, ValueError):
                continue
            if (cls._is_escape_choice(label, target, body)
                    or re.match(r"если\s+(?:нет|не удалось|не получилось)\b", folded)
                    or re.match(r"если\s+(?:есть|у вас есть|знаете пароль)\b", folded)
                    or re.search(r"проверить удачу|если вы удачливы|если вам повезло", folded)
                    or cls._route_spell_options(label, body)):
                continue
            result.append(choice)
        return result

    @staticmethod
    def _is_escape_route(label: str) -> bool:
        return bool(re.search(r"убежать|бежать|сбежать|отступить|бегств", label, re.IGNORECASE))

    @classmethod
    def _is_escape_choice(cls, label: str, target: int, body: str) -> bool:
        if cls._is_escape_route(label):
            return True
        destination = rf"(?:\({target}\)|[—–-]\s*{target}\b)"
        escape = r"(?:убежать|бежать|сбежать|отступить|бегств)"
        return bool(re.search(
            rf"(?:{escape}[^.!?\n]{{0,80}}{destination}|{destination}[^.!?\n]{{0,80}}{escape})",
            body,
            re.IGNORECASE,
        ))

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
        luck_dice = (random.randint(1, 6), random.randint(1, 6)) if check and luck else None
        roll = sum(luck_dice) if luck_dice is not None else None
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
            f"Ваша удача: {luck}. Проверка удачи: "
            f"{luck_dice[0]} 🎲 + {luck_dice[1]} 🎲 = {roll}. "
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
        # Telegram photo captions allow up to 1024 characters. Step 239 is
        # 953 characters including its heading, and fits as a single caption;
        # splitting it at 950 hides every item and route action on page one.
        limit = 1024
        text, keyboard, _ = self._paged_screen(state, limit=limit)
        formatted = self._format_telegram_text(text)
        # Formatting adds HTML tags and a page heading icon. Use the full
        # caption allowance when possible, then reserve only the markup overhead.
        while len(formatted) > 1024 and limit > 900:
            limit -= 16
            text, keyboard, _ = self._paged_screen(state, limit=limit)
            formatted = self._format_telegram_text(text)
        return formatted, keyboard

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

        screen_limit = 3900 if state.get("view") in {"battle", "step"} else 1024
        text, keyboard, first_part = self._paged_screen(state, limit=screen_limit)
        is_direct_battle = state.get("view") == "battle"
        _, _, has_photo = self._screen(state)
        if is_direct_battle:
            # Battle logs can exceed Telegram's photo-caption limit. Keep the
            # step illustration as its own message and render the interactive
            # battle log in a separate text message below it.
            photo_id = self._paragraph_photo(int(state.get("step", 1)))
        elif has_photo and first_part:
            photo_id = self._screen_photo(state)
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
        # Keep battle logs separate from their illustration. A regular step
        # also uses separate photo/text messages when its caption would exceed
        # Telegram's 1024-character caption limit.
        if is_direct_battle or (photo_id and len(formatted) > 1024):
            photo_message_id = None
            if photo_id:
                photo = await self._call("sendPhoto", {
                    "chat_id": chat_id,
                    "photo": photo_id,
                })
                candidate_id = photo.get("message_id")
                photo_message_id = candidate_id if isinstance(candidate_id, int) else None
            sent = await self._call("sendMessage", {**payload, "text": formatted})
            sent_id = sent.get("message_id")
            state["direct_message_ids"] = [
                message_id for message_id in (photo_message_id, sent_id)
                if isinstance(message_id, int) and message_id > 0
            ]
            state["direct_message_id"] = sent_id if isinstance(sent_id, int) else 0
            state["direct_message_has_photo"] = False
            self._save_state(player_id, state)
            return
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
        if state.get("view") in {"step", "status", "discard", "preface"}:
            photo_id = self._screen_photo(state)
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
        battle = state.get("battle")
        if (state.get("view") == "battle" and isinstance(battle, dict)
                and battle.get("status") == "running"):
            self._recover_interrupted_battle(state)
            self._save_state(player_id, state)
        photo_id = self._screen_photo(state)
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
                        label = self._spell_button_label(spell_key, combat=True)
                    else:
                        label = self._route_button_text(
                            self._spell_button_label(spell_key), target_paragraph
                        )
                if not label_from_message:
                    if not action.startswith("blackcastle:cast:"):
                        label = self._route_button_text(str(choice["button_text"]), target_paragraph)
        elif action.startswith("blackcastle:loot:"):
            try:
                _, _, source_text, loot_id = action.split(":", 3)
                paragraph_number = int(source_text)
                get_loot = getattr(self.game_store, "get_paragraph_loot_options", None)
                options = get_loot(paragraph_number) if callable(get_loot) else []
                option = next((row for row in options if str(row.get("loot_id")) == loot_id), None)
                if option is not None and not label_from_message:
                    label = str(option.get("button_text") or label)
            except (ValueError, TypeError):
                pass
        elif action.startswith("blackcastle:loot_use:"):
            try:
                _, _, source_text, loot_id = action.split(":", 3)
                paragraph_number = int(source_text)
                use_effect = PARAGRAPH_LOOT_STAMINA_EFFECTS.get((paragraph_number, loot_id))
                if use_effect and not label_from_message:
                    label = use_effect[1]
            except (ValueError, TypeError):
                pass
        elif action.startswith("blackcastle:item_use:"):
            consumable_key = action.rsplit(":", 1)[-1]
            consumable = INVENTORY_STAMINA_CONSUMABLES.get(consumable_key)
            if consumable and not label_from_message:
                label = consumable[2]
        elif action == "blackcastle:flask:drink":
            label = "Попить из фляги (+2 Выносливости)"
        elif action == "blackcastle:discard:open":
            label = "Выкинуть что-то из рюкзака"
        elif action.startswith("blackcastle:discard:"):
            reference = self._discard_reference(state, action)
            if reference is not None:
                index, _ = reference
                item = state["items"][index]
                label = f"Выкинуть {item[:1].lower()}{item[1:]}"
        elif action.startswith("blackcastle:battle:start:"):
            try:
                parts = action.split(":")
                if len(parts) == 5 and parts[3] == "auto":
                    paragraph_number = int(parts[4])
                    choice = None
                else:
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
        elif action.startswith("blackcastle:knowledge:"):
            try:
                _, _, action_key, source_text, target_text = action.split(":", 4)
                paragraph_number = int(source_text)
                target_paragraph = int(target_text)
                label = str(OFFSET_ACTIONS[action_key]["label"])
            except (ValueError, KeyError):
                pass
        elif action.startswith("blackcastle:knowledge_route:"):
            try:
                _, _, source_text, choice_id, base_text, target_text = action.split(":", 5)
                paragraph_number = int(source_text)
                target_paragraph = int(target_text)
                choice = self.game_store.get_paragraph_choice(paragraph_number, choice_id)
                if choice and int(choice["target_paragraph"]) == int(base_text):
                    label = f"В латах: {choice['button_text']}"
            except (ValueError, TypeError):
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
            if action.startswith("blackcastle:buy:") and isinstance(callback_id, str):
                processed = state.get("processed_shop_purchase_callbacks", [])
                if isinstance(processed, list) and callback_id in processed:
                    await self._acknowledge_callback(callback_id, "Эта покупка уже обработана.")
                    return
            previous_step = state.get("step")
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
            loot_alert = None
            inventory_alert = None
            luck_check_clicked = False
            route_choice = None
            route_source_step = None
            auto_battle_start = False
            loot_source_step = None
            loot_choice = None
            loot_from_battle = False
            loot_use_gain = 0
            purchase_offer = None
            purchase_source_step = None
            purchase_error = None
            purchase_success = None
            inventory_consumable_index = None
            inventory_consumable_key = None
            inventory_consumable_gain = 0
            choice_reward = None
            drink_from_flask = False
            use_healing_spell = False
            discard_index = None
            discard_item = None
            cast_spell = None
            battle_advance = False
            battle_started = False
            battle_target_index = None
            battle = state.get("battle")
            personal_stamina_maximum = self._personal_characteristic_maximum(
                state.get("characteristics", {}), "stamina"
            )
            if (action == "blackcastle:page_next" and state.get("view") == "battle"
                    and isinstance(battle, dict)):
                if battle.get("status") == "awaiting_continue":
                    action = "blackcastle:battle:continue"
                elif battle.get("status") == "running":
                    action = "blackcastle:battle:recover_interrupted"
            elif (action.startswith("blackcastle:battle:continue")
                    and isinstance(battle, dict) and battle.get("status") == "running"):
                action = "blackcastle:battle:recover_interrupted"
            if action in {"blackcastle:status", "blackcastle:stats", "blackcastle:inventory"}:
                if state.get("view") == "battle":
                    return
                if state.get("view") == "step" and isinstance(state.get("step"), int):
                    paragraph = self.game_store.get_paragraph(int(state["step"]))
                    body = str((paragraph or {}).get("body") or "")
                    if self._battle_enemies(self._battle_stage_body(int(state["step"]), body, state)):
                        return
            if action.startswith("blackcastle:battle:start:"):
                try:
                    parts = action.split(":")
                    if len(parts) == 5 and parts[3] == "auto":
                        route_source_step = int(parts[4])
                        auto_battle_start = True
                    else:
                        _, _, _, source_text, choice_id = action.split(":", 4)
                        route_source_step = int(source_text)
                        route_choice = self.game_store.get_paragraph_choice(route_source_step, choice_id)
                except (ValueError, TypeError):
                    route_choice = None
                battle_started = True
            elif action.startswith("blackcastle:loot:"):
                try:
                    _, _, source_text, loot_id = action.split(":", 3)
                    loot_source_step = int(source_text)
                    get_loot = getattr(self.game_store, "get_paragraph_loot_options", None)
                    options = get_loot(loot_source_step) if callable(get_loot) else []
                    loot_choice = next(
                        (row for row in options if str(row.get("loot_id")) == loot_id), None
                    )
                except (ValueError, TypeError):
                    loot_choice = None
                claimed = state.get("claimed_loot", {})
                claimed_ids = claimed.get(str(loot_source_step), []) if isinstance(claimed, dict) else []
                battle_state = state.get("battle")
                loot_from_battle = bool(
                    state.get("view") == "battle" and isinstance(battle_state, dict)
                    and battle_state.get("status") == "won"
                    and int(battle_state.get("source_step", -1)) == loot_source_step
                )
                loot_paragraph = self.game_store.get_paragraph(loot_source_step)
                on_loot_step = (
                    state.get("view") == "step" and state.get("step") == loot_source_step
                    and not self._battle_enemies(str((loot_paragraph or {}).get("body") or ""))
                )
                if (loot_choice is None or not (on_loot_step or loot_from_battle)
                        or (loot_id in claimed_ids and loot_id not in REPEATABLE_LOOT_IDS)):
                    loot_choice = None
                elif loot_choice.get("item_name") and int(loot_choice.get("bag_slots") or 0):
                    equipment_names = {"меч", "фляга", "заплечный мешок"}
                    bag_items = [
                        item for item in state.get("items", [])
                        if isinstance(item, str) and item.strip().casefold() not in equipment_names
                    ]
                    if self._bag_item_count(state, bag_items) + int(loot_choice["bag_slots"]) > int(state.get("bag_capacity", 7)):
                        loot_alert = "В заплечном мешке недостаточно места для этой вещи."
            elif action.startswith("blackcastle:buy:"):
                try:
                    _, _, source_text, purchase_id = action.split(":", 3)
                    purchase_source_step = int(source_text)
                    purchase_offer = next((
                        row for row in PARAGRAPH_PURCHASE_OPTIONS.get(purchase_source_step, [])
                        if str(row.get("purchase_id")) == purchase_id
                    ), None)
                except (ValueError, TypeError):
                    purchase_offer = None
                if (purchase_offer is None or state.get("view") != "step"
                        or state.get("step") != purchase_source_step):
                    purchase_error = "Эта покупка сейчас недоступна."
                elif int(state.get("gold", 0)) < int(purchase_offer["gold_cost"]):
                    purchase_error = "Недостаточно золотых для этой покупки."
                elif purchase_offer.get("requires_flask") and not self._has_item(state, "Фляга"):
                    purchase_error = "Чтобы купить воду, нужна фляга."
                elif (purchase_offer.get("water_sips") is not None
                        and int(state.get("water_sips", 0)) >= int(purchase_offer["water_sips"])):
                    purchase_error = "Во фляге уже достаточно воды для этой покупки."
                elif (purchase_offer.get("bag_capacity") is not None
                        and int(state.get("bag_capacity", 7)) >= int(purchase_offer["bag_capacity"])):
                    purchase_error = "У вас уже есть заплечный мешок на 9 предметов."
                elif purchase_offer.get("item_name"):
                    equipment_names = {"меч", "фляга", "заплечный мешок"}
                    bag_items = [
                        item for item in state.get("items", [])
                        if isinstance(item, str) and item.strip().casefold() not in equipment_names
                    ]
                    slots = int(purchase_offer.get("bag_slots") or 1)
                    if self._bag_item_count(state, bag_items) + slots > int(state.get("bag_capacity", 7)):
                        purchase_error = "В заплечном мешке недостаточно места для покупки."
                if purchase_offer is not None and purchase_error is None:
                    remaining_gold = int(state.get("gold", 0)) - int(purchase_offer["gold_cost"])
                    receipt_text = str(
                        purchase_offer.get("item_name")
                        or purchase_offer.get("receipt")
                        or "Покупка выполнена"
                    )
                    purchase_success = f"Покупка: {receipt_text}. Золотых останется: {remaining_gold}."
            elif action.startswith("blackcastle:loot_use:"):
                loot_id = ""
                try:
                    _, _, source_text, loot_id = action.split(":", 3)
                    loot_source_step = int(source_text)
                    use_effect = PARAGRAPH_LOOT_STAMINA_EFFECTS.get((loot_source_step, loot_id))
                    get_loot = getattr(self.game_store, "get_paragraph_loot_options", None)
                    options = get_loot(loot_source_step) if callable(get_loot) else []
                    loot_choice = next(
                        (row for row in options if str(row.get("loot_id")) == loot_id), None
                    )
                except (ValueError, TypeError):
                    use_effect = None
                    loot_choice = None
                claimed = state.get("claimed_loot", {})
                claimed_ids = claimed.get(str(loot_source_step), []) if isinstance(claimed, dict) else []
                source_paragraph = self.game_store.get_paragraph(loot_source_step)
                valid_source = (
                    loot_source_step is not None
                    and state.get("view") == "step"
                    and state.get("step") == loot_source_step
                    and not self._battle_enemies(str((source_paragraph or {}).get("body") or ""))
                )
                current_stamina = int(state.get("characteristics", {}).get("stamina", 0))
                if (not valid_source or not loot_choice or not use_effect
                        or loot_id in claimed_ids):
                    loot_alert = "Это действие с предметом уже недоступно."
                elif current_stamina >= personal_stamina_maximum:
                    loot_alert = "Выносливость уже на начальном максимуме; предмет не потрачен."
                else:
                    loot_use_gain = int(use_effect[0])
                    loot_alert = (
                        f"Выносливость: {current_stamina} → "
                        f"{min(personal_stamina_maximum, current_stamina + loot_use_gain)}."
                    )
            elif action.startswith("blackcastle:item_use:"):
                inventory_consumable_key = action.rsplit(":", 1)[-1]
                consumable = INVENTORY_STAMINA_CONSUMABLES.get(inventory_consumable_key)
                current_stamina = int(state.get("characteristics", {}).get("stamina", 0))
                if state.get("view") not in {"stats", "inventory", "status"}:
                    inventory_alert = "Использовать еду и вино можно на экране характеристик и инвентаря."
                elif consumable is None:
                    inventory_alert = "Этот предмет нельзя использовать."
                elif current_stamina >= personal_stamina_maximum:
                    inventory_alert = "Выносливость уже на начальном максимуме; предмет не потрачен."
                else:
                    canonical_name = consumable[0].casefold()
                    inventory_consumable_index = next((
                        index for index, item in enumerate(state.get("items", []))
                        if isinstance(item, str) and item.strip().casefold() == canonical_name
                    ), None)
                    if inventory_consumable_index is None:
                        inventory_alert = "Этого предмета больше нет в инвентаре."
                    else:
                        inventory_consumable_gain = min(
                            int(consumable[1]), personal_stamina_maximum - current_stamina
                        )
                        consumed_description = INVENTORY_CONSUMABLE_USE_TEXT[inventory_consumable_key]
                        inventory_alert = (
                            f"{consumed_description}. Выносливость: {current_stamina} → "
                            f"{current_stamina + inventory_consumable_gain}."
                        )
            elif action == "blackcastle:flask:drink":
                if state.get("view") not in {"stats", "inventory", "status"} or not self._has_item(state, "Фляга"):
                    inventory_alert = "У вас нет фляги."
                elif int(state.get("water_sips", 0)) <= 0:
                    inventory_alert = "Во фляге не осталось воды."
                elif int(state.get("characteristics", {}).get("stamina", 0)) >= personal_stamina_maximum:
                    inventory_alert = "Выносливость уже на начальном максимуме; глоток не потрачен."
                else:
                    drink_from_flask = True
                    stamina_before = int(state["characteristics"].get("stamina", 0))
                    inventory_alert = (
                        f"Выносливость: {stamina_before} → "
                        f"{min(personal_stamina_maximum, stamina_before + 2)}. "
                        f"Во фляге останется глотков: {int(state.get('water_sips', 0)) - 1}."
                    )
            elif action == "blackcastle:spell:healing":
                if state.get("view") not in {"stats", "inventory", "status"}:
                    inventory_alert = "Заклинание можно применить на экране характеристик и инвентаря."
                elif isinstance(state.get("battle"), dict):
                    inventory_alert = "Во время сражения заклинание Исцеления использовать нельзя."
                elif int(state.get("spells", {}).get("healing", 0)) <= 0:
                    inventory_alert = "У вас не осталось применений заклинания Исцеления."
                elif int(state.get("characteristics", {}).get("stamina", 0)) >= personal_stamina_maximum:
                    inventory_alert = "Выносливость уже на начальном максимуме; заклинание не потрачено."
                else:
                    use_healing_spell = True
                    stamina_before = int(state["characteristics"].get("stamina", 0))
                    stamina_after = min(
                        personal_stamina_maximum,
                        stamina_before + 8,
                    )
                    inventory_alert = (
                        f"Заклинание Исцеления восстановило {stamina_after - stamina_before} "
                        f"ВЫНОСЛИВОСТИ: {stamina_before} → {stamina_after}."
                    )
            elif action == "blackcastle:discard:open":
                eligible = any(
                    isinstance(item, str)
                    and item.strip().casefold() not in NON_DISCARDABLE_ITEMS
                    for item in state.get("items", [])
                )
                if state.get("view") not in {"stats", "inventory", "status"} or not eligible:
                    inventory_alert = "В заплечном мешке нет предметов, которые можно выбросить."
            elif action.startswith("blackcastle:discard:"):
                reference = self._discard_reference(state, action)
                if state.get("view") == "discard" and reference is not None:
                    discard_index = reference[0]
                    discard_item = state["items"][discard_index]
                else:
                    inventory_alert = "Этот предмет уже отсутствует в инвентаре."
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
            elif action == "blackcastle:battle:recover_interrupted":
                self._recover_interrupted_battle(state)
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
                    required_knowledge = KNOWLEDGE_GATED_ROUTES.get(
                        (route_source_step, choice_id)
                    )
                    if (route_choice is not None and required_knowledge
                            and required_knowledge not in state.get("knowledge", [])):
                        route_choice = None
                    reward_getter = getattr(self.game_store, "get_paragraph_choice_reward", None)
                    choice_reward = (
                        reward_getter(route_source_step, choice_id)
                        if callable(reward_getter) else None
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
                if (choice_reward and route_choice is not None
                        and state.get("view") == "step"
                        and state.get("step") == route_source_step
                        and int(choice_reward.get("bag_slots") or 0) > 0):
                    equipment_names = {"меч", "фляга", "заплечный мешок"}
                    bag_items = [
                        item for item in state.get("items", [])
                        if isinstance(item, str) and item.strip().casefold() not in equipment_names
                    ]
                    if self._bag_item_count(state, bag_items) + int(choice_reward["bag_slots"]) > int(state.get("bag_capacity", 7)):
                        loot_alert = "В заплечном мешке недостаточно места для этой вещи."
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
                await self._acknowledge_callback(
                    callback_id,
                    luck_alert or loot_alert or inventory_alert or purchase_error or purchase_success,
                )
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
            elif action == "blackcastle:discard:open":
                if inventory_alert:
                    return
                state["view"] = "discard"
                state["page_part"] = 0
            elif action == "blackcastle:back":
                state["view"] = "step"
                state["page_part"] = 0
            elif action.startswith("blackcastle:buy:"):
                if purchase_offer is None or purchase_source_step is None or purchase_error:
                    return
                state["gold"] = int(state.get("gold", 0)) - int(purchase_offer["gold_cost"])
                if purchase_offer.get("item_name"):
                    item_name = str(purchase_offer["item_name"])
                    state.setdefault("items", []).append(item_name)
                    state.setdefault("item_ids", []).append(None)
                    slot_cost = int(purchase_offer.get("bag_slots") or 1)
                    if slot_cost > 1:
                        state.setdefault("item_slot_costs", {})[item_name] = slot_cost
                if purchase_offer.get("water_sips") is not None:
                    state["water_sips"] = int(purchase_offer["water_sips"])
                if purchase_offer.get("bag_capacity") is not None:
                    state["bag_capacity"] = int(purchase_offer["bag_capacity"])
                receipt_text = str(
                    purchase_offer.get("item_name")
                    or purchase_offer.get("receipt")
                    or "Покупка выполнена"
                )
                state["last_shop_purchase"] = {
                    "step": purchase_source_step,
                    "text": f"Покупка завершена: {receipt_text}",
                }
                if isinstance(callback_id, str):
                    processed = state.setdefault("processed_shop_purchase_callbacks", [])
                    if not isinstance(processed, list):
                        processed = []
                        state["processed_shop_purchase_callbacks"] = processed
                    processed.append(callback_id)
                    del processed[:-64]
                state["view"] = "step"
                state["step"] = purchase_source_step
            elif action.startswith("blackcastle:loot:"):
                if loot_choice is None or loot_source_step is None or loot_alert:
                    return
                if loot_choice.get("item_name"):
                    item_name = str(loot_choice["item_name"])
                    state.setdefault("items", []).append(item_name)
                    state.setdefault("item_ids", []).append(None)
                    slot_cost = int(loot_choice.get("bag_slots") or 1)
                    if slot_cost > 1:
                        state.setdefault("item_slot_costs", {})[item_name] = slot_cost
                state["gold"] = int(state.get("gold", 0)) + int(loot_choice.get("gold_amount") or 0)
                claimed = state.setdefault("claimed_loot", {})
                claimed.setdefault(str(loot_source_step), []).append(str(loot_choice["loot_id"]))
                if loot_from_battle:
                    state["view"] = "battle"
                else:
                    state["view"] = "step"
                    state["step"] = loot_source_step
                    state["page_part"] = 0
            elif action.startswith("blackcastle:loot_use:"):
                if loot_use_gain <= 0 or loot_source_step is None:
                    return
                characteristics = state["characteristics"]
                characteristics["stamina"] = min(
                    personal_stamina_maximum,
                    int(characteristics.get("stamina", 0)) + loot_use_gain,
                )
                claimed = state.setdefault("claimed_loot", {})
                claimed.setdefault(str(loot_source_step), []).append(str(loot_choice["loot_id"]))
                state["view"] = "step"
                state["step"] = loot_source_step
                state["page_part"] = 0
            elif action.startswith("blackcastle:item_use:"):
                if inventory_consumable_index is None or inventory_consumable_gain <= 0:
                    return
                characteristics = state["characteristics"]
                characteristics["stamina"] = min(
                    personal_stamina_maximum,
                    int(characteristics.get("stamina", 0)) + inventory_consumable_gain,
                )
                items = state["items"]
                removed_item = items.pop(inventory_consumable_index)
                item_ids = state.get("item_ids")
                if isinstance(item_ids, list) and inventory_consumable_index < len(item_ids):
                    item_ids.pop(inventory_consumable_index)
                slot_costs = state.get("item_slot_costs")
                if isinstance(slot_costs, dict) and removed_item not in items:
                    slot_costs.pop(removed_item, None)
            elif action == "blackcastle:flask:drink":
                if not drink_from_flask:
                    return
                characteristics = state["characteristics"]
                stamina_before = int(characteristics.get("stamina", 0))
                stamina_after = min(
                    personal_stamina_maximum,
                    stamina_before + 2,
                )
                characteristics["stamina"] = stamina_after
                state["water_sips"] = max(0, int(state.get("water_sips", 0)) - 1)
            elif action == "blackcastle:spell:healing":
                if not use_healing_spell:
                    return
                characteristics = state["characteristics"]
                stamina_before = int(characteristics.get("stamina", 0))
                characteristics["stamina"] = min(
                    personal_stamina_maximum,
                    stamina_before + 8,
                )
                state.setdefault("spells", dict(INITIAL_SPELLS))["healing"] -= 1
            elif action.startswith("blackcastle:discard:id:") or re.match(
                r"^blackcastle:discard:\d+:", action
            ):
                if discard_index is None or discard_item is None:
                    return
                items = state["items"]
                del items[discard_index]
                item_ids = state.get("item_ids")
                if isinstance(item_ids, list) and discard_index < len(item_ids):
                    del item_ids[discard_index]
                slot_costs = state.get("item_slot_costs")
                if isinstance(slot_costs, dict) and discard_item not in items:
                    slot_costs.pop(discard_item, None)
                if discard_item.strip().casefold() == "фляга":
                    state["water_sips"] = 0
            elif action == "blackcastle:continue":
                state["view"] = "step"
                state["step"] = 1
                state["page_part"] = 0
            elif action.startswith("blackcastle:battle:start:"):
                if (route_source_step is None or (route_choice is None and not auto_battle_start)
                        or state.get("view") != "step" or state.get("step") != route_source_step):
                    logger.warning(
                        "Rejected BlackCastle battle start (step=%s, view=%s, route_step=%s, route_found=%s)",
                        state.get("step"), state.get("view"), route_source_step,
                        route_choice is not None,
                    )
                    return
                if route_choice is not None and not self._is_battle_route(ROUTE_BUTTON_SUFFIX.sub(
                    "", str(route_choice.get("button_text") or "")
                ).strip()):
                    return
                paragraph = self.game_store.get_paragraph(route_source_step)
                full_body = str((paragraph or {}).get("body") or "")
                all_choices = self.game_store.get_paragraph_choices(route_source_step)
                victory_options = [
                    option for option in self._post_battle_choices(all_choices, full_body)
                    if (not option.get("required_item")
                        or self._has_item(state, str(option["required_item"])))
                ]
                if route_choice is not None:
                    selected = next((option for option in victory_options
                                     if str(option.get("choice_id")) == str(route_choice.get("choice_id"))), None)
                    if selected is None:
                        victory_options.insert(0, route_choice)
                    else:
                        victory_options.remove(selected)
                        victory_options.insert(0, selected)
                if not victory_options:
                    logger.warning("Rejected BlackCastle battle start without a victory route (step=%s)", route_source_step)
                    return
                sequence_chunks = self._battle_sequence_chunks(route_source_step, full_body)
                sequence_stage_index = int(state.get("battle_sequence_stage", 0)) if sequence_chunks else 0
                battle_body = self._battle_stage_body(route_source_step, full_body, state)
                enemies = self._battle_enemies(battle_body)
                if not enemies:
                    logger.warning(
                        "Rejected BlackCastle battle start because no enemy stats were parsed (step=%s)",
                        route_source_step,
                    )
                    return
                required_item = route_choice.get("required_item") if route_choice is not None else None
                if required_item and not self._consume_item(state, str(required_item)):
                    return
                magic = state.pop("combat_magic_pending", None)
                magic_spells = self._prepared_combat_spells(magic)
                battle = {
                    "source_step": route_source_step,
                    "victory_step": int(victory_options[0]["target_paragraph"]),
                    "victory_options": victory_options,
                    "enemies": enemies,
                    "stage": "copy" if "copy" in magic_spells else "hero",
                    "status": "running",
                    "inline_message": isinstance(callback.get("inline_message_id"), str),
                    "round": 0,
                    "log": [],
                    "magic": {"spells": magic_spells} if magic_spells else None,
                    "sequence_stage_index": sequence_stage_index,
                    "sequence_stage_count": len(sequence_chunks) if sequence_chunks else 1,
                    "escape_options": [
                        choice for choice in self.game_store.get_paragraph_choices(route_source_step)
                        if self._is_escape_choice(
                            ROUTE_BUTTON_SUFFIX.sub("", str(choice.get("button_text") or "")).strip(),
                            int(choice["target_paragraph"]),
                            full_body,
                        )
                        and (not sequence_chunks or sequence_stage_index > 0)
                    ],
                    "player_attack_penalty": 1 if re.search(
                        r"уменьшайте вашу СИЛУ УДАРА на\s*1",
                        str((paragraph or {}).get("body") or ""),
                        re.IGNORECASE,
                    ) else 0,
                }
                if "weakness" in magic_spells:
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
            elif action == "blackcastle:battle:recover_interrupted":
                # Recovery above already finalized the persisted partial round.
                pass
            elif action == "blackcastle:battle:finish" or action.startswith("blackcastle:battle:finish:"):
                battle = state.get("battle")
                if (state.get("view") != "battle" or not isinstance(battle, dict)
                        or battle.get("status") != "won"):
                    return
                target = int(battle["victory_step"])
                if action.startswith("blackcastle:battle:finish:"):
                    choice_id = action.rsplit(":", 1)[-1]
                    options = battle.get("victory_options", [])
                    selected = next((option for option in options
                                     if str(option.get("choice_id")) == choice_id), None)
                    if selected is None:
                        return
                    target = int(selected["target_paragraph"])
                if self.game_store.get_paragraph(target) is None:
                    return
                state.pop("battle", None)
                state.pop("battle_sequence_stage", None)
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
            elif action.startswith("blackcastle:knowledge_route:"):
                try:
                    _, _, source_text, choice_id, base_text, target_text = action.split(":", 5)
                    source_step = int(source_text)
                    base_target = int(base_text)
                    target_step = int(target_text)
                    choice = self.game_store.get_paragraph_choice(source_step, choice_id)
                except (ValueError, TypeError):
                    return
                knowledge = state.get("knowledge", [])
                armor_sources = (90, 126, 130, 251, 330, 414, 527)
                if (state.get("view") != "step" or state.get("step") != source_step
                        or source_step in armor_sources
                        or not isinstance(knowledge, list)
                        or not any(OFFSET_KNOWLEDGE[source] in knowledge for source in armor_sources)
                        or not self._has_item(state, "Зелёные латы")
                        or choice is None
                        or (choice.get("required_item") and not self._has_item(state, str(choice["required_item"])))
                        or int(choice["target_paragraph"]) != base_target
                        or target_step != base_target + 60
                        or self.game_store.get_paragraph(target_step) is None):
                    return
                source_paragraph = self.game_store.get_paragraph(source_step)
                source_body = str((source_paragraph or {}).get("body") or "")
                if (self._battle_enemies(source_body)
                        or not re.search(r"двер|комнат|выбер", source_body, re.IGNORECASE)):
                    return
                state["step"] = target_step
                state["view"] = "step"
                state["page_part"] = 0
                state.pop("battle_sequence_stage", None)
            elif action.startswith("blackcastle:knowledge:"):
                try:
                    _, _, action_key, source_text, target_text = action.split(":", 4)
                    source_step = int(source_text)
                    target_step = int(target_text)
                    rule = OFFSET_ACTIONS[action_key]
                except (ValueError, KeyError):
                    return
                knowledge = state.get("knowledge", [])
                if (state.get("view") != "step" or state.get("step") != source_step
                        or (rule["steps"] is not None and source_step not in rule["steps"])
                        or not isinstance(knowledge, list)
                        or not any(OFFSET_KNOWLEDGE.get(source) in knowledge for source in rule["knowledge"])
                        or target_step != source_step + int(rule["delta"])
                        or self.game_store.get_paragraph(target_step) is None):
                    return
                if rule["item"] and not self._has_item(state, str(rule["item"])):
                    return
                extra_item = rule.get("extra_item")
                if extra_item and not self._has_item(state, str(extra_item)):
                    return
                if rule["steps"] is None:
                    source_paragraph = self.game_store.get_paragraph(source_step)
                    source_body = str((source_paragraph or {}).get("body") or "").casefold().replace("ё", "е")
                    if rule.get("condition") == "crossroad":
                        if source_step in {450, 555} or not re.search(r"перекрест", source_body):
                            return
                    elif (source_step in {318, 600}
                          or not re.search(r"заперт|неподатлив|не.{0,20}откры|замок|закрыт", source_body)):
                        return
                state["step"] = target_step
                state["view"] = "step"
                state["page_part"] = 0
                state.pop("battle_sequence_stage", None)
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
                    prepared_spells = self._prepared_combat_spells(
                        state.get("combat_magic_pending")
                    )
                    if cast_spell in prepared_spells:
                        return
                    state["spells"][cast_spell] -= 1
                    state["combat_magic_pending"] = {
                        "spells": prepared_spells + [cast_spell],
                    }
                    state["view"] = "step"
                    state["page_part"] = 0
                elif luck_check_clicked:
                    result = state.get("luck_checks", {}).get(str(source_step), {})
                    if result.get("lucky"):
                        target_step = int(choice["target_paragraph"])
                        if self.game_store.get_paragraph(target_step) is None:
                            return
                        state["step"] = target_step
                        state.pop("battle_sequence_stage", None)
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
                    required_knowledge = KNOWLEDGE_GATED_ROUTES.get(
                        (source_step, str(choice.get("choice_id") or ""))
                    )
                    knowledge = state.get("knowledge", [])
                    if required_knowledge:
                        if not isinstance(knowledge, list) or required_knowledge not in knowledge:
                            return
                    required_item = choice.get("required_item")
                    if required_item and not self._consume_item(state, str(required_item)):
                        return
                    target_step = int(choice["target_paragraph"])
                    if self.game_store.get_paragraph(target_step) is None:
                        return
                    if choice_reward:
                        if loot_alert:
                            return
                        if choice_reward.get("item_name"):
                            item_name = str(choice_reward["item_name"])
                            state.setdefault("items", []).append(item_name)
                            state.setdefault("item_ids", []).append(None)
                            slot_cost = int(choice_reward.get("bag_slots") or 1)
                            if slot_cost > 1:
                                state.setdefault("item_slot_costs", {})[item_name] = slot_cost
                        state["gold"] = int(state.get("gold", 0)) + int(
                            choice_reward.get("gold_amount") or 0
                        )
                    if cast_spell:
                        state.setdefault("spells", dict(INITIAL_SPELLS))[cast_spell] -= 1
                    if required_knowledge:
                        state["knowledge"] = [
                            entry for entry in knowledge if entry != required_knowledge
                        ]
                    if self._is_escape_route(source_label):
                        characteristics = state["characteristics"]
                        characteristics["stamina"] = max(0, int(characteristics.get("stamina", 0)) - 2)
                        state.pop("battle_sequence_stage", None)
                        if characteristics["stamina"] == 0:
                            state["view"] = "game_over"
                        else:
                            state["step"] = target_step
                            state["view"] = "step"
                    else:
                        state["step"] = target_step
                        state["view"] = "step"
                        state.pop("battle_sequence_stage", None)
                    state["page_part"] = 0
            else:
                return
            if state.get("view") in {"preface", "step"}:
                current_step = state.get("step")
                if isinstance(current_step, int) and current_step != previous_step:
                    self._apply_step_supply_effects(state, current_step)
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
                    and not battle_started
                    and not state.get("direct_message_has_photo")
                    and isinstance(previous_message_id, int)
                    and previous_message_id > 0
                ):
                    tracked_ids = state.get("direct_message_ids")
                    if not isinstance(tracked_ids, list):
                        tracked_ids = []
                    state["direct_message_ids"] = list(dict.fromkeys(
                        [message_id for message_id in tracked_ids
                         if isinstance(message_id, int) and message_id > 0]
                        + [previous_message_id]
                    ))
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
        photo_target: int | str | None = None
        if command == "/blackcastle_photo" and len(text.split()) == 2:
            raw_target = text.split()[1].casefold()
            if raw_target in {"preface", "status"}:
                photo_target = raw_target
            else:
                try:
                    photo_target = int(raw_target)
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
            elif photo_target == "preface":
                self.game_store.set_setting("kniga_igra_black_castle_preface_photo_file_id", photo_id)
                await self._send_message(chat_id, "Фото предисловия BlackCastle сохранено.")
                logger.info("Registered the BlackCastle preface photo")
            elif photo_target == "status":
                self.game_store.set_setting("kniga_igra_black_castle_status_photo_file_id", photo_id)
                await self._send_message(chat_id, "Фото характеристик и инвентаря BlackCastle сохранено.")
                logger.info("Registered the BlackCastle status and inventory photo")
            elif isinstance(photo_target, int) and photo_target > 0 and self.game_store.set_paragraph_photo(photo_target, photo_id):
                await self._send_message(chat_id, f"Фото параграфа {photo_target} сохранено.")
                logger.info("Registered a BlackCastle paragraph photo")
            else:
                await self._send_message(chat_id, "Параграф не найден. Укажи номер существующей страницы.")
            return

        state = self._get_or_create_state(sender_id)
        battle = state.get("battle")
        if (state.get("view") == "battle" and isinstance(battle, dict)
                and battle.get("status") == "running"):
            self._recover_interrupted_battle(state)
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
