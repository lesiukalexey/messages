import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from messages.black_castle_bot import BlackCastleBot
from messages.black_castle_battle_text import (
    ENEMY_BATTLE_TEXT,
    GENERIC_BATTLE_TEXT,
    canonical_enemy_key,
    iter_battle_text_rows,
)
from messages.black_castle_loot import PARAGRAPH_LOOT


def battle_quote_sentence_count(log):
    formatted = BlackCastleBot._format_telegram_text("Битва\n\n" + "\n\n".join(log))
    quote = re.search(r"<blockquote>(.*?)</blockquote>", formatted, re.DOTALL)
    return len(quote.group(1).split("\n\n")) if quote else 0


class FakeGameStore:
    def __init__(self, state, paragraph, choices):
        self.state = state
        self.paragraph = paragraph
        self.choices = choices
        self.paragraphs = {}
        self.choices_by_step = {}
        self.loot_options = {}
        self.choice_rewards = {}

    def get_player_state(self, player_id):
        return self.state

    def save_player_state(self, player_id, state):
        self.state = json.loads(json.dumps(state))

    def get_paragraph(self, number):
        return self.paragraphs.get(number, self.paragraph if number == 54 else {
            "paragraph_number": number,
            "body": "",
            "photo_file_id": "step-photo",
        })

    def get_paragraph_choices(self, number):
        return self.choices_by_step.get(number, self.choices if number == 54 else [])

    def get_paragraph_choice(self, number, choice_id):
        choices = self.choices_by_step.get(number, self.choices if number == 54 else [])
        return next((row for row in choices if row["choice_id"] == choice_id), None)

    def get_paragraph_loot_options(self, number):
        return self.loot_options.get(number, [])

    def get_paragraph_choice_reward(self, number, choice_id):
        return self.choice_rewards.get((number, choice_id))

    def get_book_page(self, key):
        return {"body": "Старое предисловие"} if key == "preface" else None

    def record_button_press(self, *args):
        pass

    def get_setting(self, key, default=""):
        return "default-photo"

    def get_battle_narrative_templates(self, enemy_name, phase):
        key = canonical_enemy_key(enemy_name)
        selected = [row[3] for row in iter_battle_text_rows() if row[0] == key and row[1] == phase]
        return selected or [row[3] for row in iter_battle_text_rows()
                            if row[0] == "*" and row[1] == phase]


class LuckBot(BlackCastleBot):
    async def _call(self, method, payload):
        self.calls.append((method, payload))
        return {}

    async def _send_direct_screen(self, chat_id, player_id, state, previous_message_id=0):
        self.visible_state = json.loads(json.dumps(state))
        self.last_previous_message_id = previous_message_id

    async def _edit_inline_screen(self, inline_message_id, state):
        self.visible_state = json.loads(json.dumps(state))


class PhotoBot(BlackCastleBot):
    async def _call(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": 10}


class SequentialMessageBot(BlackCastleBot):
    async def _call(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": len(self.calls)}


def make_bot(luck=8):
    paragraph = {
        "paragraph_number": 54,
        "body": "ПРОВЕРЬТЕ СВОЮ УДАЧУ. Если вы удачливы, то 558.",
        "photo_file_id": "step-photo",
    }
    choices = [
        {"choice_id": "route_01", "button_text": "Если вы удачливы — 558", "target_paragraph": 558, "required_item": None},
        {"choice_id": "route_02", "button_text": "Силы — 410", "target_paragraph": 410, "required_item": None},
        {"choice_id": "route_03", "button_text": "Слабости — 219", "target_paragraph": 219, "required_item": None},
        {"choice_id": "route_04", "button_text": "Если вы победили — 189", "target_paragraph": 189, "required_item": None},
    ]
    state = {
        "step": 54,
        "view": "step",
        "characteristics": {"mastery": 8, "stamina": 18, "luck": luck},
        "items": [], "gold": 0, "water_sips": 0, "bag_capacity": 7,
    }
    store = FakeGameStore(state, paragraph, choices)
    temp = tempfile.TemporaryDirectory()
    scene = Path(temp.name) / "scene.json"
    scene.write_text(json.dumps({"preface": ""}), encoding="utf-8")
    bot = LuckBot("token", None, store, scene)
    bot.calls = []
    bot.visible_state = None
    bot._test_tempdir = temp
    return bot, store


def make_callback(choice_id, callback_id="callback-1"):
    return {"callback_query": {
        "id": callback_id,
        "from": {"id": 42},
        "data": f"blackcastle:route:54:{choice_id}",
        "message": {"message_id": 9, "chat": {"id": 42}},
    }}


class BlackCastleLuckTest(unittest.TestCase):
    def test_battle_formatter_quotes_narrative_and_keeps_breakdown_plain(self):
        first_failed_wound = next(
            template for enemy, phase, variant, template in iter_battle_text_rows()
            if enemy == "*" and phase == "failed_wound" and variant == 1
        )
        self.assertTrue(first_failed_wound.startswith("Ваш удар не достигает цели."))

        descriptions = (
            "Гигантский Паук резко бросается вперёд, выбрасывая навстречу вас длинные когтистые лапы.",
            "Ваш удар не достигает цели. Гигантский Паук уклоняется и сохраняет равновесие.",
            "Паук всё же достаёт вас когтистой лапой.",
            "Гигантский Паук пригибается к земле, покачивая лапами, и готовится к следующему броску.",
            "Вы смещаетесь в сторону и готовите ответный выпад.",
            "Удары встречаются и расходятся; в этом обмене никто не ранен.",
        )
        for line in descriptions:
            with self.subTest(line=line):
                self.assertEqual(
                    BlackCastleBot._format_battle_line(line), f"<blockquote>{line}</blockquote>"
                )

        plain_lines = (
            "Гигантский Паук атакует с СИЛОЙ УДАРА 17 (9 + 8).",
            "Ваш бросок: 2 🎲 + 3 🎲 + 8 🎯 (база) = 13 ⚔️",
            "ВЫНОСЛИВОСТЬ после раунда:",
            "Вы — 18 ❤️",
            "Гигантский Паук — 4 ❤️",
            "ВЫНОСЛИВОСТЬ: 20 → 18",
            "Ваш выпад оказывается быстрее — 17 ⚔️ против 12 ⚔️.",
            "Гигантский Паук получает 2 урона:",
        )
        for line in plain_lines:
            with self.subTest(line=line):
                formatted = BlackCastleBot._format_battle_line(line)
                self.assertNotIn("<b>", formatted)
                self.assertNotIn("<i>", formatted)

        self.assertEqual(
            BlackCastleBot._format_battle_line("ВЫНОСЛИВОСТЬ Гигантский Паук: 20 → 18"),
            "Выносливость Гигантский Паук: 20 ❤️ → 18 ❤️",
        )
        self.assertEqual(
            BlackCastleBot._format_battle_line("Ваша ВЫНОСЛИВОСТЬ: 20 → 18"),
            "Выносливость: 20 ❤️ → 18 ❤️",
        )
        self.assertEqual(
            BlackCastleBot._format_battle_line("ВЫНОСЛИВОСТЬ: 20 → 18"),
            "Выносливость: 20 ❤️ → 18 ❤️",
        )
        self.assertEqual(
            BlackCastleBot._format_battle_line("ВЫНОСЛИВОСТЬ Копии: 20 → 18"),
            "Выносливость Копии: 20 ❤️ → 18 ❤️",
        )

    def test_battle_screen_groups_all_narration_before_plain_breakdown(self):
        rendered = BlackCastleBot._format_telegram_text(
            "Битва\n\n"
            "Паук бросается вперёд.\n"
            "Паук атакует с СИЛОЙ УДАРА 14.\n"
            "Ваш выпад оказывается быстрее — 17 против 14.\n"
            "Ваш удар не достигает цели. Паук уклоняется.\n"
            "Вы теряете 2 ВЫНОСЛИВОСТИ:\n"
            "Ваша ВЫНОСЛИВОСТЬ: 20 → 18\n"
            "Паук пригибается и готовится к новой атаке."
        )

        self.assertEqual(rendered.count("<blockquote>"), 1)
        self.assertEqual(rendered.count("</blockquote>"), 1)
        quote = rendered.split("<blockquote>", 1)[1].split("</blockquote>", 1)[0]
        self.assertIn("Паук бросается вперёд.", quote)
        self.assertIn("Ваш удар не достигает цели.", quote)
        self.assertIn("Паук пригибается", quote)
        self.assertIn("Паук бросается вперёд.\n\nВаш удар не достигает цели", quote)
        self.assertIn("Ваш удар не достигает цели.\n\nПаук уклоняется.", quote)
        self.assertIn("Паук уклоняется.\n\nПаук пригибается", quote)
        self.assertNotIn("Расшифровка битвы:", quote)
        breakdown = rendered.split("Расшифровка битвы:\n", 1)[1]
        self.assertIn("Паук атакует с СИЛОЙ УДАРА 14.", breakdown)
        self.assertIn("Вы теряете 2 ВЫНОСЛИВОСТИ:", breakdown)
        self.assertIn("Выносливость: 20 ❤️ → 18 ❤️", breakdown)
        self.assertIn("СИЛОЙ УДАРА 14.\n\nВаш выпад", breakdown)
        self.assertNotIn("<blockquote>", breakdown)
        self.assertLess(rendered.index("<blockquote>"), rendered.index("Расшифровка битвы:"))

        rendered_with_totals = BlackCastleBot._format_telegram_text(
            "Битва\n\nВЫНОСЛИВОСТЬ после раунда:\nВы — 18\nГигантский Паук — 8"
        )
        self.assertIn(
            "<pre>ВЫНОСЛИВОСТЬ после раунда:\nВы — 18 ❤️\nГигантский Паук — 8 ❤️</pre>",
            rendered_with_totals,
        )

    def test_battle_phrase_bank_covers_every_book_enemy_and_generic_phase(self):
        book_enemy_keys = {
            "дровосек", "летучая мышь", "орк", "гоблин", "дракон", "гигантский паук",
            "начальник стражи", "женщина-вампир", "зеленый рыцарь", "разбойник", "обезьяна",
            "призрак", "паук", "дух мертвых", "лев", "гиена", "барлад дэрт", "рыцарь",
            "капитан рыцарей", "водяной", "тролль", "человек", "торговец", "оборотень",
            "медведица", "гарпия", "лесовичок", "повар",
        }
        self.assertEqual(set(ENEMY_BATTLE_TEXT), book_enemy_keys)
        for enemy, phases in ENEMY_BATTLE_TEXT.items():
            for phase in ("opening", "wounded", "hit", "survives"):
                self.assertEqual(len(phases[phase]), 3, f"{enemy} {phase} must have 3 variants")
                self.assertEqual(len(set(phases[phase])), 3, f"{enemy} {phase} variants must differ")
        for phase, variants in GENERIC_BATTLE_TEXT.items():
            self.assertEqual(len(variants), 3, f"generic {phase} must have 3 variants")
            self.assertEqual(len(set(variants)), 3, f"generic {phase} variants must differ")
        rows = list(iter_battle_text_rows())
        self.assertTrue(all(row[0] == "*" or row[0] in book_enemy_keys for row in rows))
        banks = {}
        for enemy_key, phase, variant_no, _ in rows:
            banks.setdefault((enemy_key, phase), []).append(variant_no)
        for key, phases in ENEMY_BATTLE_TEXT.items():
            for phase in phases:
                self.assertEqual(banks[(key, phase)], [1, 2, 3])
        for phase in GENERIC_BATTLE_TEXT:
            self.assertEqual(banks[("*", phase)], [1, 2, 3])
        for _, _, _, template in rows:
            rendered = template.format(
                enemy="Гоблин", victim="вас", counterattack="Ваш удар",
                victim_dative="вам", actor="Путник", actor_genitive="путника",
                enemies="Гоблин, Орк",
            )
            self.assertNotIn("{", rendered)

    def test_battle_phrase_selects_randomly_from_three_variants(self):
        bot, store = make_bot()
        variants = store.get_battle_narrative_templates("ГИГАНТСКИЙ ПАУК", "opening")
        self.assertEqual(len(variants), 3)
        with patch("messages.black_castle_bot.random.choice", side_effect=lambda items: items[-1]) as choose:
            rendered = bot._battle_phrase(
                "ГИГАНТСКИЙ ПАУК", "opening", enemy="Гигантский Паук", victim="вас"
            )
        choose.assert_called_once_with(variants)
        self.assertEqual(rendered, variants[-1].format(
            enemy="Гигантский Паук", victim="вас", victim_dative="вам"
        ))
        bot._test_tempdir.cleanup()

    def test_merchant_battle_uses_book_three_stamina_wound(self):
        body = (
            "ТОРГОВЕЦ\nМастерство 7\nВыносливость 8\n"
            "Когда он ранит вас, вы теряете не 2, а 3 ВЫНОСЛИВОСТИ."
        )
        enemy = BlackCastleBot._battle_enemies(body)[0]
        self.assertEqual(enemy["damage_to_player"], 3)

    def test_spell_route_is_hidden_when_empty_and_cast_consumes_one_copy(self):
        bot, store = make_bot()
        store.state["spells"] = {"strength": 1, "weakness": 0}
        _, keyboard, _ = bot._screen(store.state)
        labels = [button["text"] for row in keyboard for button in row]
        self.assertTrue(any("Заклинание Силы" in label for label in labels))
        self.assertFalse(any("Заклинание Слабости" in label for label in labels))
        cast = {"callback_query": {
            "id": "spell-callback", "from": {"id": 42},
            "data": "blackcastle:cast:54:route_02:strength",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(cast))
            self.assertEqual(store.state["step"], 410)
            self.assertEqual(store.state["spells"]["strength"], 0)
        finally:
            bot._test_tempdir.cleanup()

    def test_step_558_shows_spell_buttons_without_route_numbers_and_prepares_spell(self):
        bot, store = make_bot()
        body = (
            "Вы будете драться, используя либо заклятие Силы, либо заклятие Слабости, "
            "либо заклятие Копии.\nГИГАНТСКИЙ ПАУК\nМастерство 8\n"
            "Выносливость 8\nЕсли вы победили, то 189."
        )
        choice = {"choice_id": "route_01", "button_text": "Если вы победили — 189",
                  "target_paragraph": 189, "required_item": None}
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [choice]
        store.state.update({"step": 558, "spells": {"strength": 1, "weakness": 1, "copy": 1}})
        _, keyboard, _ = bot._screen(store.state)
        labels = [button["text"].replace("\u00a0", " ") for row in keyboard for button in row]
        self.assertEqual(labels[:4], [
            "Заклинание Силы (усиление боя)",
            "Заклинание Слабости (усиление боя)",
            "Заклинание Копии (усиление боя)",
            "Вступить в бой",
        ])
        callback = {"callback_query": {
            "id": "cast-copy", "from": {"id": 42},
            "data": "blackcastle:cast:558:route_01:copy",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(callback))
            self.assertEqual(store.state["step"], 558)
            self.assertEqual(store.state["spells"]["copy"], 0)
            self.assertEqual(store.state["combat_magic_pending"], {"spell": "copy"})

            fight = {"callback_query": {
                "id": "fight-callback", "from": {"id": 42},
                "data": "blackcastle:battle:start:558:route_01",
                "message": {"message_id": 9, "chat": {"id": 42}},
            }}
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(fight))
            self.assertEqual(store.state["view"], "battle")
            self.assertEqual(store.state["battle"]["stage"], "copy")
            self.assertEqual(store.state["battle"]["copy"], {
                "name": "Копия ГИГАНТСКИЙ ПАУК", "mastery": 8, "stamina": 8,
            })
            self.assertIn(
                "Бросок Копии: 6 🎲 + 6 🎲 + 8 🎯 (база)",
                store.state["battle"]["log"][1],
            )
            self.assertEqual(store.state["battle"]["enemies"][0]["stamina"], 6)
            self.assertEqual(store.state["battle"]["status"], "awaiting_continue")
            self.assertEqual(len(store.state["battle"]["log"]), 7)
        finally:
            bot._test_tempdir.cleanup()

    def test_all_take_anything_pages_offer_each_loot_choice_and_keep_their_routes(self):
        bot, store = make_bot()
        expected_routes = {187: 47, 189: 19, 335: 46, 484: 308, 573: 561}
        for step, route in expected_routes.items():
            store.paragraphs[step] = {
                "paragraph_number": step, "body": "Возьмите все, что хотите.",
                "photo_file_id": "step-photo",
            }
            store.choices_by_step[step] = [{
                "choice_id": "route_01", "button_text": f"Продолжить — {route}",
                "target_paragraph": route, "required_item": None,
            }]
            store.loot_options[step] = [
                {
                    "loot_id": loot_id,
                    "button_text": button_text,
                    "item_name": item_name,
                    "gold_amount": gold,
                    "bag_slots": slots,
                }
                for loot_id, button_text, item_name, gold, slots in PARAGRAPH_LOOT[step]
            ]
            store.state.update({"step": step, "view": "step", "claimed_loot": {}})
            _, keyboard, _ = bot._screen(store.state)
            buttons = [button for row in keyboard for button in row]
            loot_buttons = [button for button in buttons if button["callback_data"].startswith("blackcastle:loot:")]
            self.assertEqual(len(loot_buttons), len(PARAGRAPH_LOOT[step]), step)
            self.assertTrue(any(button["callback_data"] == f"blackcastle:route:{step}:route_01"
                                for button in buttons), step)
        bot._test_tempdir.cleanup()

    def test_loot_button_adds_selected_item_or_gold_once_and_keeps_player_on_step(self):
        bot, store = make_bot()
        store.paragraphs[189] = {
            "paragraph_number": 189, "body": "Берите с собой всё.", "photo_file_id": "step-photo",
        }
        store.choices_by_step[189] = [{
            "choice_id": "route_01", "button_text": "Продолжайте путь — 19",
            "target_paragraph": 19, "required_item": None,
        }]
        store.loot_options[189] = [
            {"loot_id": loot_id, "button_text": label, "item_name": item,
             "gold_amount": gold, "bag_slots": slots}
            for loot_id, label, item, gold, slots in PARAGRAPH_LOOT[189]
        ]
        store.state.update({"step": 189, "view": "step", "gold": 15, "items": ["Меч", "Фляга"]})
        update = {"callback_query": {
            "id": "loot-diamond", "from": {"id": 42},
            "data": "blackcastle:loot:189:diamond",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        gold_update = {"callback_query": {
            "id": "loot-gold", "from": {"id": 42},
            "data": "blackcastle:loot:189:gold",
            "message": {"message_id": 10, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            self.assertEqual(store.state["items"], ["Меч", "Фляга", "Бриллиант"])
            self.assertEqual(store.state["step"], 189)
            self.assertEqual(store.state["gold"], 15)
            asyncio.run(bot.process_update(update))
            self.assertEqual(store.state["items"].count("Бриллиант"), 1)
            asyncio.run(bot.process_update(gold_update))
            self.assertEqual(store.state["gold"], 16)
            self.assertIn("diamond", store.state["claimed_loot"]["189"])
            self.assertIn("gold", store.state["claimed_loot"]["189"])
        finally:
            bot._test_tempdir.cleanup()

    def test_loot_button_refuses_items_when_the_backpack_is_full(self):
        bot, store = make_bot()
        store.paragraphs[189] = {"paragraph_number": 189, "body": "", "photo_file_id": "step-photo"}
        store.loot_options[189] = [{
            "loot_id": "diamond", "button_text": "Взять бриллиант",
            "item_name": "Бриллиант", "gold_amount": 0, "bag_slots": 1,
        }]
        store.state.update({"step": 189, "view": "step", "items": ["Меч", "Фляга", *[f"Вещь {i}" for i in range(7)]]})
        update = {"callback_query": {
            "id": "loot-full-bag", "from": {"id": 42},
            "data": "blackcastle:loot:189:diamond",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            self.assertNotIn("Бриллиант", store.state["items"])
            self.assertEqual(store.state.get("claimed_loot", {}).get("189", []), [])
        finally:
            bot._test_tempdir.cleanup()

    def test_repeatable_arrow_can_be_taken_until_the_bag_is_full(self):
        bot, store = make_bot()
        store.loot_options[471] = [{
            "loot_id": "black_arrow", "button_text": "Взять чёрную стрелу",
            "item_name": "Чёрная стрела", "gold_amount": 0, "bag_slots": 1,
        }]
        store.state.update({"step": 471, "view": "step", "items": ["Меч", "Фляга"]})
        update = {"callback_query": {
            "id": "black-arrow", "from": {"id": 42},
            "data": "blackcastle:loot:471:black_arrow",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            asyncio.run(bot.process_update(update))
            self.assertEqual(store.state["items"].count("Чёрная стрела"), 2)
            self.assertTrue(BlackCastleBot._has_item(store.state, "чёрная стрела"))
            self.assertTrue(any(
                button["callback_data"] == "blackcastle:loot:471:black_arrow"
                for row in bot._screen(store.state)[1] for button in row
            ))
        finally:
            bot._test_tempdir.cleanup()

    def test_three_slot_armor_uses_three_backpack_slots(self):
        bot, store = make_bot()
        store.loot_options[414] = [{
            "loot_id": "green_armor", "button_text": "Взять зелёные латы (3 места)",
            "item_name": "Зелёные латы", "gold_amount": 0, "bag_slots": 3,
        }]
        store.state.update({
            "step": 414, "view": "step",
            "items": ["Меч", "Фляга", "Кольцо", "Еда", "Стрела", "Перо"],
        })
        update = {"callback_query": {
            "id": "green-armor", "from": {"id": 42},
            "data": "blackcastle:loot:414:green_armor",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            self.assertIn("Зелёные латы", store.state["items"])
            self.assertEqual(store.state["item_slot_costs"]["Зелёные латы"], 3)
            self.assertEqual(BlackCastleBot._bag_item_count(store.state), 7)
        finally:
            bot._test_tempdir.cleanup()

    def test_green_knight_sword_adds_one_mastery_to_player_attack(self):
        bot, store = make_bot()
        store.state.update({
            "view": "battle", "items": ["Меч", "Фляга", "Меч Зеленого рыцаря"],
            "battle": {
                "source_step": 40, "status": "running", "stage": "hero",
                "target_index": 0, "round": 0, "log": [], "enemies": [{
                    "name": "ГОБЛИН", "mastery": 8, "mastery_base": 8,
                    "stamina": 8, "damage_to_player": 2,
                }],
            },
        })
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 1, 1]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot._advance_battle_round(42, store.state, inline_message_id=None, chat_id=None))
            self.assertIn("+ 1 (меч Зеленого рыцаря)", store.state["battle"]["log"][1])
            self.assertTrue(store.state["battle"]["log"][1].endswith("11 ⚔️."))
        finally:
            bot._test_tempdir.cleanup()

    def test_victory_screen_offers_conditional_loot_before_leaving_battle(self):
        bot, store = make_bot()
        store.loot_options[40] = [{
            "loot_id": "bronze_whistle", "button_text": "Взять бронзовый свисток",
            "item_name": "Бронзовый свисток", "gold_amount": 0, "bag_slots": 1,
        }]
        store.state.update({
            "view": "battle", "step": 40,
            "battle": {"source_step": 40, "status": "won", "log": ["Победа."],
                       "display_phase": "complete", "enemies": []},
        })
        _, keyboard, _ = bot._screen(store.state)
        buttons = [button for row in keyboard for button in row]
        self.assertEqual(buttons[0]["text"], "Взять бронзовый свисток")
        self.assertEqual(buttons[-1]["text"], "Продолжить")
        bot._test_tempdir.cleanup()

    def test_combat_loot_is_hidden_until_victory_and_then_can_be_claimed(self):
        bot, store = make_bot()
        store.paragraphs[40] = {
            "paragraph_number": 40,
            "body": "ГОБЛИН\nМастерство 6\nВыносливость 9",
            "photo_file_id": "step-photo",
        }
        store.loot_options[40] = [{
            "loot_id": "bronze_whistle", "button_text": "Взять бронзовый свисток",
            "item_name": "Бронзовый свисток", "gold_amount": 0, "bag_slots": 1,
        }]
        store.state.update({"step": 40, "view": "step", "items": ["Меч", "Фляга"]})
        _, keyboard, _ = bot._screen(store.state)
        self.assertFalse(any(
            button["callback_data"].startswith("blackcastle:loot:")
            for row in keyboard for button in row
        ))

        store.state.update({
            "view": "battle", "step": 40,
            "battle": {"source_step": 40, "status": "won", "log": ["Победа."],
                       "display_phase": "complete", "enemies": []},
        })
        update = {"callback_query": {
            "id": "combat-loot", "from": {"id": 42},
            "data": "blackcastle:loot:40:bronze_whistle",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            self.assertIn("Бронзовый свисток", store.state["items"])
            self.assertEqual(store.state["view"], "battle")
            self.assertEqual(store.state["battle"]["status"], "won")
        finally:
            bot._test_tempdir.cleanup()

    def test_route_selection_grants_the_selected_item_without_exceeding_bag_capacity(self):
        bot, store = make_bot()
        store.paragraphs[62] = {"paragraph_number": 62, "body": "Возьмите амулет.", "photo_file_id": "step-photo"}
        store.choices_by_step[62] = [{
            "choice_id": "route_01", "button_text": "Амулет — 457",
            "target_paragraph": 457, "required_item": None,
        }]
        store.choice_rewards[(62, "route_01")] = {
            "item_name": "Амулет с медвежьей шерстью", "gold_amount": 0, "bag_slots": 1,
        }
        store.state.update({"step": 62, "view": "step", "items": ["Меч", "Фляга"]})
        update = {"callback_query": {
            "id": "route-with-item", "from": {"id": 42},
            "data": "blackcastle:route:62:route_01",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            self.assertEqual(store.state["step"], 457)
            self.assertIn("Амулет с медвежьей шерстью", store.state["items"])
        finally:
            bot._test_tempdir.cleanup()

    def test_using_a_required_item_consumes_it_from_saved_inventory_and_frees_a_slot(self):
        bot, store = make_bot()
        store.paragraphs[427] = {
            "paragraph_number": 427, "body": "Покажите золотой свисток.", "photo_file_id": "step-photo",
        }
        store.choices_by_step[427] = [{
            "choice_id": "route_01", "button_text": "Показать свисток — 583",
            "target_paragraph": 583, "required_item": "золотой свисток",
        }]
        store.state.update({
            "step": 427, "view": "step",
            "items": ["Меч", "Фляга", "Золотой свисток", *[f"Вещь {i}" for i in range(7)]],
        })
        update = {"callback_query": {
            "id": "use-golden-whistle", "from": {"id": 42},
            "data": "blackcastle:route:427:route_01",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(update))
            self.assertEqual(store.state["step"], 583)
            self.assertNotIn("Золотой свисток", store.state["items"])
            self.assertEqual(len(store.state["items"]), 9)
        finally:
            bot._test_tempdir.cleanup()

    def test_battle_narrates_five_lines_then_finishes_round(self):
        bot, store = make_bot()
        body = (
            "Во время боя уменьшайте вашу СИЛУ УДАРА на 1.\nГИГАНТСКИЙ ПАУК\nМастерство 8\n"
            "Выносливость 2\nЕсли вы победили, то 189."
        )
        choice = {"choice_id": "route_01", "button_text": "Вступить в бой — 189",
                  "target_paragraph": 189, "required_item": None}
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [choice]
        store.state.update({"step": 558, "spells": {"strength": 0},
                            "combat_magic_pending": {"spell": "strength"}})
        start = {"callback_query": {
            "id": "start-fight", "from": {"id": 42},
            "data": "blackcastle:battle:start:558:route_01",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock) as pause:
                asyncio.run(bot.process_update(start))
            self.assertEqual(store.state["battle"]["status"], "won")
            self.assertEqual(len(store.state["battle"]["log"]), 7)
            narrated_count = battle_quote_sentence_count(store.state["battle"]["log"])
            self.assertEqual(pause.await_count, narrated_count)
            self.assertEqual(
                [call.args[0] for call in pause.await_args_list],
                [5] * narrated_count,
            )
            player_roll = store.state["battle"]["log"][1]
            self.assertIn("Ваш бросок: 6 🎲 + 6 🎲 + 8 🎯 (база)", player_roll)
            self.assertIn("+ 2 (бонус заклинания Силы) - 1 (штраф книги: бой на дереве)", player_roll)
            self.assertIn("= 21 ⚔️", player_roll)
            self.assertEqual(player_roll.count("Ваш бросок"), 1)
            self.assertRegex(store.state["battle"]["log"][0], r"(резко бросается|стремительно перебирает)")
            self.assertIn("21 ⚔️ против 10 ⚔️", store.state["battle"]["log"][2])
            self.assertIn("ВЫНОСЛИВОСТЬ Гигантский Паук: 2 → 0", store.state["battle"]["log"][3])
            self.assertIn("побед", store.state["battle"]["log"][-1].casefold())
            self.assertFalse(any(line.startswith(tuple(f"{n})" for n in range(1, 8)))
                                 for line in store.state["battle"]["log"]))
            self.assertFalse(any("Действие " in line for line in store.state["battle"]["log"]))
            finish = {"callback_query": {
                "id": "finish-fight", "from": {"id": 42},
                "data": "blackcastle:battle:finish",
                "message": {"message_id": 10, "chat": {"id": 42}},
            }}
            asyncio.run(bot.process_update(finish))
            self.assertEqual(store.state["step"], 189)
            self.assertEqual(bot.visible_state["step"], 189)
        finally:
            bot._test_tempdir.cleanup()

    def test_direct_battle_start_replaces_tracked_step_photos_with_one_battle_photo(self):
        template_bot, store = make_bot()
        bot = SequentialMessageBot(
            template_bot.token, template_bot.owner_store, store, template_bot.scene_path
        )
        bot._test_tempdir = template_bot._test_tempdir
        bot.calls = []
        body = "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 2\nЕсли вы победили, то 189."
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [{
            "choice_id": "route_01", "button_text": "Вступить в бой — 189",
            "target_paragraph": 189, "required_item": None,
        }]
        store.state.update({
            "step": 558, "direct_message_id": 20,
            "direct_message_ids": [19, 20], "direct_message_has_photo": False,
        })
        start = {"callback_query": {
            "id": "start-direct-battle", "from": {"id": 42},
            "data": "blackcastle:battle:start:558:route_01",
            # Some Telegram callback payloads omit the source message's photo field.
            "message": {"message_id": 20, "chat": {"id": 42}},
        }}

        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(start))

            deleted_ids = [payload["message_id"] for method, payload in bot.calls
                           if method == "deleteMessage"]
            sent_photos = [payload for method, payload in bot.calls if method == "sendPhoto"]
            self.assertCountEqual(deleted_ids, [19, 20])
            self.assertEqual(len(sent_photos), 1)
            self.assertEqual(sent_photos[0]["photo"], "step-photo")
            self.assertEqual(len(store.state["direct_message_ids"]), 2)
            self.assertEqual(len(set(store.state["direct_message_ids"])), 2)
            final_edit = [payload for method, payload in bot.calls if method == "editMessageText"][-1]
            self.assertEqual(
                final_edit["reply_markup"]["inline_keyboard"][0][0]["text"], "Продолжить"
            )
            self.assertNotIn("к вас", store.state["battle"]["log"][0])
            self.assertNotIn("навстречу вас", store.state["battle"]["log"][0])
        finally:
            bot._test_tempdir.cleanup()

    def test_spider_opening_uses_the_correct_dative_for_player_and_copy(self):
        templates = [row[3] for row in iter_battle_text_rows()
                     if row[0] == "гигантский паук" and row[1] == "opening"]
        for template in templates:
            player = template.format(
                enemy="Гигантский Паук", victim="вас", victim_dative="вам"
            )
            copy = template.format(
                enemy="Гигантский Паук", victim="Копию", victim_dative="Копии"
            )
            self.assertNotIn("к вас", player)
            self.assertNotIn("навстречу вас", player)
            self.assertTrue(copy)
        self.assertIn("выбрасывает к вам", templates[0].format(
            enemy="Гигантский Паук", victim="вас", victim_dative="вам"
        ))
        self.assertIn("бросается к Копии", templates[2].format(
            enemy="Гигантский Паук", victim="Копию", victim_dative="Копии"
        ))

    def test_inline_battle_edits_caption_progressively_with_narrative(self):
        bot, store = make_bot()
        body = "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 2\nЕсли вы победили, то 189."
        choice = {"choice_id": "route_01", "button_text": "Вступить в бой — 189",
                  "target_paragraph": 189, "required_item": None}
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [choice]
        store.state.update({"step": 558, "view": "step"})

        async def edit_inline(inline_message_id, state):
            await BlackCastleBot._edit_inline_screen(bot, inline_message_id, state)
            bot.visible_state = json.loads(json.dumps(state))

        bot._edit_inline_screen = edit_inline
        start = {"callback_query": {
            "id": "inline-fight", "from": {"id": 42},
            "inline_message_id": "inline-message-1",
            "data": "blackcastle:battle:start:558:route_01",
        }}
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(start))

            captions = [payload["caption"] for method, payload in bot.calls
                        if method == "editMessageCaption"]
            narrated_count = battle_quote_sentence_count(store.state["battle"]["log"])
            self.assertEqual(len(captions), narrated_count + 2)  # initial, each quote line, final details
            self.assertRegex(captions[1], r"<blockquote>Гигантский Паук (резко бросается|стремительно перебирает)")
            self.assertNotIn("<i>", "".join(captions))
            self.assertNotIn("<b>", "".join(captions))
            self.assertNotIn("<b>Битва", captions[0])
            self.assertTrue(all("Расшифровка битвы:" not in caption for caption in captions[1:-1]))
            self.assertNotIn("🎲", "".join(captions[1:-1]))
            self.assertNotIn("<b>Мастерство", captions[-1])
            self.assertNotIn("<b>СИЛА УДАРА", captions[-1])
            previous_quote_lines = 0
            for caption in captions[1:-1]:
                quote = re.search(r"<blockquote>(.*?)</blockquote>", caption, re.DOTALL)
                self.assertIsNotNone(quote)
                quote_lines = quote.group(1).split("\n\n")
                self.assertEqual(len(quote_lines), previous_quote_lines + 1)
                previous_quote_lines = len(quote_lines)
            self.assertIn(
                "Гигантский Паук: 1 🎲 + 1 🎲 + 8 🎯 (база) = 10 ⚔️.",
                captions[-1],
            )
            self.assertIn("20 ⚔️ против 10 ⚔️", captions[-1])
            self.assertRegex(captions[2], r"\nВы [^\n]+(?:\n\n|</blockquote>)")
            self.assertIn("<blockquote>", captions[-1])
            self.assertIn("Ваш бросок: 6 🎲 + 6 🎲 + 8 🎯 (база)", captions[-1])
            self.assertIn("Расшифровка битвы:", captions[-1])
            self.assertNotIn("<b>Ваш бросок", captions[-1])
            self.assertIn("Выносливость Гигантский Паук: 2 ❤️ → 0 ❤️", captions[-1])
            self.assertIn(
                "<pre>ВЫНОСЛИВОСТЬ после раунда:\nВы — 18 ❤️\nГигантский Паук — 0 ❤️</pre>",
                captions[-1],
            )
            self.assertIn("Вы — 18 ❤️", captions[-1])
            self.assertNotIn("<b>ВЫНОСЛИВОСТЬ после раунда:", captions[-1])
            self.assertNotIn("<b>Вы — 18", captions[-1])
            self.assertIn("побед", captions[-1].casefold())
            self.assertEqual(store.state["battle"]["status"], "won")
        finally:
            bot._test_tempdir.cleanup()

    def test_direct_continue_replaces_the_previous_round_text_in_the_same_message(self):
        bot, store = make_bot()
        body = "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 8\nЕсли вы победили, то 189."
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.state.update({
            "step": 558,
            "view": "battle",
            "direct_message_id": 20,
            "direct_message_ids": [19, 20],
            "direct_message_has_photo": False,
            "battle": {
                "source_step": 558,
                "victory_step": 189,
                "enemies": [{"name": "ГИГАНТСКИЙ ПАУК", "mastery": 8, "stamina": 8}],
                "stage": "hero",
                "status": "awaiting_continue",
                "round": 1,
                "log": ["Старый раунд больше не должен отображаться."],
                "magic": None,
                "escape_options": [],
                "target_index": 0,
            },
        })

        async def send_direct(chat_id, player_id, state, previous_message_id=0):
            await BlackCastleBot._send_direct_screen(
                bot, chat_id, player_id, state, previous_message_id
            )

        bot._send_direct_screen = send_direct
        continue_fight = {"callback_query": {
            "id": "continue-direct",
            "from": {"id": 42},
            "data": "blackcastle:battle:continue",
            "message": {"message_id": 20, "chat": {"id": 42}},
        }}
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(continue_fight))

            edits = [payload for method, payload in bot.calls if method == "editMessageText"]
            narrated_count = battle_quote_sentence_count(store.state["battle"]["log"])
            self.assertEqual(len(edits), narrated_count + 2)  # refresh, quote lines, final details
            self.assertFalse(any(method in {"deleteMessage", "sendMessage", "sendPhoto"}
                                 for method, _ in bot.calls))
            self.assertTrue(all(payload["message_id"] == 20 for payload in edits))
            self.assertTrue(all("Старый раунд больше не должен отображаться." not in payload["text"]
                                for payload in edits))
            self.assertEqual(store.state["direct_message_ids"], [19, 20])
            self.assertIn("Гигантский Паук", edits[-1]["text"])
            self.assertEqual(store.state["battle"]["round"], 2)
        finally:
            bot._test_tempdir.cleanup()

    def test_inline_read_continuation_replaces_the_previous_round_caption(self):
        bot, store = make_bot()
        body = "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 8\nЕсли вы победили, то 189."
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.state.update({
            "step": 558,
            "view": "battle",
            "battle": {
                "source_step": 558,
                "victory_step": 189,
                "enemies": [{"name": "ГИГАНТСКИЙ ПАУК", "mastery": 8, "stamina": 8}],
                "stage": "hero",
                "status": "awaiting_continue",
                "round": 1,
                "log": ["Текст завершившегося раунда удаляется из сообщения."],
                "magic": None,
                "escape_options": [],
                "target_index": 0,
                "inline_message": True,
            },
        })
        callback = {"callback_query": {
            "id": "continue-inline",
            "from": {"id": 42},
            "inline_message_id": "inline-battle",
            "data": "blackcastle:page_next",
        }}

        async def edit_inline(inline_message_id, state):
            await BlackCastleBot._edit_inline_screen(bot, inline_message_id, state)
            bot.visible_state = json.loads(json.dumps(state))

        bot._edit_inline_screen = edit_inline
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(callback))

            captions = [payload["caption"] for method, payload in bot.calls
                        if method == "editMessageCaption" and "caption" in payload]
            narrated_count = battle_quote_sentence_count(store.state["battle"]["log"])
            self.assertEqual(len(captions), narrated_count + 2)
            self.assertTrue(all("Текст завершившегося раунда" not in caption for caption in captions))
            self.assertIn("Гигантский Паук", captions[-1])
            self.assertEqual(store.state["battle"]["round"], 2)
        finally:
            bot._test_tempdir.cleanup()

    def test_interrupted_inline_battle_restores_continue_button_without_reroll(self):
        bot, store = make_bot()
        store.state.update({
            "step": 558,
            "view": "battle",
            "characteristics": {"mastery": 8, "stamina": 15, "luck": 8},
            "battle": {
                "source_step": 558,
                "victory_step": 189,
                "enemies": [{"name": "ГИГАНТСКИЙ ПАУК", "mastery": 8, "stamina": 6}],
                "stage": "hero",
                "status": "running",
                "round": 2,
                "log": [
                    "Паук атакует.",
                    "Вы готовите выпад.",
                    "Паук опережает вас.",
                    "Удар не достигает цели.",
                    "Вы теряете 2 ВЫНОСЛИВОСТИ: 17 → 15",
                ],
                "magic": None,
                "escape_options": [],
                "target_index": 0,
                "inline_message": True,
            },
        })
        callback = {"callback_query": {
            "id": "recover-inline",
            "from": {"id": 42},
            "inline_message_id": "inline-battle",
            "data": "blackcastle:page_next",
        }}

        try:
            with patch("messages.black_castle_bot.random.randint") as roll, \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(callback))

            roll.assert_not_called()
            battle = store.state["battle"]
            self.assertEqual(battle["status"], "awaiting_continue")
            self.assertEqual(len(battle["log"]), 7)
            self.assertEqual(battle["log"][4], "Вы теряете 2 ВЫНОСЛИВОСТИ: 17 → 15")
            self.assertIn("Вы — 15", battle["log"][5])
            self.assertTrue(any("Паук" in line for line in battle["log"][6:]))
            _, keyboard, _ = bot._screen(store.state)
            self.assertEqual(keyboard[0][0]["text"], "Продолжить битву")
        finally:
            bot._test_tempdir.cleanup()

    def test_player_message_recovers_interrupted_battle_for_direct_screen(self):
        bot, store = make_bot()
        store.state.update({
            "step": 558,
            "view": "battle",
            "characteristics": {"mastery": 8, "stamina": 15, "luck": 8},
            "battle": {
                "enemies": [{"name": "ГИГАНТСКИЙ ПАУК", "mastery": 8, "stamina": 6}],
                "stage": "hero",
                "status": "running",
                "round": 2,
                "log": ["Атака паука.", "Ваш выпад.", "Паук быстрее.", "Вы промахиваетесь.", "Вы теряете 2 ВЫНОСЛИВОСТИ: 17 → 15"],
                "magic": None,
                "escape_options": [],
                "target_index": 0,
            },
        })
        message = {"message": {
            "from": {"id": 42},
            "chat": {"id": 42, "type": "private"},
            "text": "Продолжить",
        }}

        try:
            asyncio.run(bot.process_update(message))

            self.assertEqual(bot.visible_state["battle"]["status"], "awaiting_continue")
            _, keyboard, _ = bot._screen(bot.visible_state)
            self.assertEqual(keyboard[0][0]["text"], "Продолжить битву")
        finally:
            bot._test_tempdir.cleanup()

    def test_weakness_reduces_enemy_mastery_for_the_battle(self):
        bot, store = make_bot()
        body = "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 2\nЕсли вы победили, то 189."
        choice = {"choice_id": "route_01", "button_text": "Вступить в бой — 189",
                  "target_paragraph": 189, "required_item": None}
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [choice]
        store.state.update({"step": 558, "combat_magic_pending": {"spell": "weakness"}})
        start = {"callback_query": {
            "id": "weakness-fight", "from": {"id": 42},
            "data": "blackcastle:battle:start:558:route_01",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(start))
            self.assertEqual(store.state["battle"]["enemies"][0]["mastery"], 6)
            self.assertEqual(store.state["battle"]["enemies"][0]["mastery_base"], 8)
            self.assertIn(
                "1 🎲 + 1 🎲 + 8 🎯 (база) - 2 (заклинание Слабости) = 8 ⚔️",
                store.state["battle"]["log"][0],
            )
        finally:
            bot._test_tempdir.cleanup()

    def test_losing_copy_is_followed_by_the_hero_fighting_the_enemy(self):
        bot, store = make_bot()
        body = "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 2\nЕсли вы победили, то 189."
        choice = {"choice_id": "route_01", "button_text": "Вступить в бой — 189",
                  "target_paragraph": 189, "required_item": None}
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [choice]
        store.state.update({"step": 558, "combat_magic_pending": {"spell": "copy"}})
        start = {"callback_query": {
            "id": "copy-fight", "from": {"id": 42},
            "data": "blackcastle:battle:start:558:route_01",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        continue_fight = {"callback_query": {
            "id": "continue-after-copy", "from": {"id": 42},
            "data": "blackcastle:battle:continue",
            "message": {"message_id": 10, "chat": {"id": 42}},
        }}
        try:
            with patch("messages.black_castle_bot.random.randint", side_effect=[6, 6, 1, 1]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(start))
            self.assertEqual(store.state["battle"]["stage"], "copy_lost")
            self.assertEqual(store.state["battle"]["copy"]["stamina"], 0)
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(continue_fight))
            self.assertEqual(store.state["battle"]["stage"], "hero")
            self.assertEqual(store.state["battle"]["status"], "won")
            self.assertEqual(store.state["characteristics"]["stamina"], 18)
        finally:
            bot._test_tempdir.cleanup()

    def test_battle_allows_book_escape_and_applies_automatic_two_stamina_loss(self):
        bot, store = make_bot()
        store.paragraphs[558] = {"paragraph_number": 558, "body": "", "photo_file_id": "step-photo"}
        store.paragraphs[86] = {"paragraph_number": 86, "body": "", "photo_file_id": "step-photo"}
        store.state.update({
            "step": 558, "view": "battle",
            "battle": {
                "status": "awaiting_continue", "stage": "hero", "log": ["Битва продолжается."],
                "enemies": [{"name": "Паук", "mastery": 8, "stamina": 4}],
                "escape_options": [{"button_text": "Убежать — 86", "target_paragraph": 86}],
            },
            "characteristics": {"mastery": 8, "stamina": 5, "luck": 4},
        })
        flee = {"callback_query": {
            "id": "flee-fight", "from": {"id": 42},
            "data": "blackcastle:battle:flee:0",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(flee))
            self.assertEqual(store.state["view"], "step")
            self.assertEqual(store.state["step"], 86)
            self.assertEqual(store.state["characteristics"]["stamina"], 3)
            self.assertNotIn("battle", store.state)
        finally:
            bot._test_tempdir.cleanup()

    def test_multiple_enemies_require_target_choice_and_can_both_damage_hero(self):
        bot, store = make_bot()
        body = (
            "ОРК-ПЕРВЫЙ\nМастерство 3\nВыносливость 2\n"
            "ОРК-ВТОРОЙ\nМастерство 9\nВыносливость 4\nЕсли вы победили, то 189."
        )
        choice = {"choice_id": "route_01", "button_text": "Вступить в бой — 189",
                  "target_paragraph": 189, "required_item": None}
        store.paragraphs[558] = {"paragraph_number": 558, "body": body, "photo_file_id": "step-photo"}
        store.choices_by_step[558] = [choice]
        store.state.update({"step": 558, "spells": {}})
        start = {"callback_query": {
            "id": "multi-start", "from": {"id": 42},
            "data": "blackcastle:battle:start:558:route_01",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        target = {"callback_query": {
            "id": "multi-target", "from": {"id": 42},
            "data": "blackcastle:battle:begin:0",
            "message": {"message_id": 10, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(start))
            self.assertEqual(store.state["battle"]["status"], "choose_target")
            _, keyboard, _ = bot._screen(store.state)
            self.assertEqual(len(keyboard), 2)
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 1, 1, 1, 1]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(target))
            self.assertEqual(store.state["battle"]["enemies"][0]["stamina"], 0)
            self.assertEqual(store.state["characteristics"]["stamina"], 16)
            self.assertEqual(store.state["battle"]["status"], "awaiting_continue")
        finally:
            bot._test_tempdir.cleanup()

    def test_step_54_spell_is_prepared_and_used_with_its_tree_fight_penalty(self):
        bot, store = make_bot(luck=5)
        store.paragraph = {
            "paragraph_number": 54,
            "body": (
                "ПРОВЕРЬТЕ СВОЮ УДАЧУ. Во время боя уменьшайте вашу СИЛУ УДАРА на 1.\n"
                "ГИГАНТСКИЙ ПАУК\nМастерство 8\nВыносливость 8\n"
                "Пользоваться заклятием Огня на дереве неразумно. Вы можете воспользоваться "
                "заклятиями либо Силы (410), либо Слабости (219). Если вы победили, то 189."
            ),
            "photo_file_id": "step-photo",
        }
        store.choices = [
            {"choice_id": "route_01", "button_text": "Если вы удачливы — 558", "target_paragraph": 558, "required_item": None},
            {"choice_id": "route_02", "button_text": "Силы — 410", "target_paragraph": 410, "required_item": None},
            {"choice_id": "route_03", "button_text": "Слабости — 219", "target_paragraph": 219, "required_item": None},
            {"choice_id": "route_04", "button_text": "Вступить в бой — 189", "target_paragraph": 189, "required_item": None},
        ]
        store.state["spells"] = {"strength": 1, "weakness": 1}
        cast = {"callback_query": {
            "id": "prepare-strength", "from": {"id": 42},
            "data": "blackcastle:cast:54:route_02:strength",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        fight = {"callback_query": {
            "id": "start-tree-fight", "from": {"id": 42},
            "data": "blackcastle:battle:start:54:route_04",
            "message": {"message_id": 10, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(cast))
            self.assertEqual(store.state["step"], 54)
            self.assertEqual(store.state["spells"]["strength"], 0)
            self.assertEqual(store.state["combat_magic_pending"], {"spell": "strength"})
            self.assertEqual(store.state["characteristics"]["luck"], 4)
            with patch("messages.black_castle_bot.random.randint", side_effect=[1, 1, 6, 6]), \
                    patch("messages.black_castle_bot.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(bot.process_update(fight))
            self.assertEqual(store.state["battle"]["player_attack_penalty"], 1)
            self.assertIn("Ваш бросок: 6 🎲 + 6 🎲 + 8 🎯 (база)", store.state["battle"]["log"][1])
            self.assertIn(
                "+ 2 (бонус заклинания Силы) - 1 (штраф книги: бой на дереве) = 21 ⚔️",
                store.state["battle"]["log"][1],
            )
        finally:
            bot._test_tempdir.cleanup()

    def test_new_profile_has_ten_spell_uses_and_preface_uses_default_photo(self):
        template_bot, store = make_bot()
        bot = PhotoBot("token", None, store, template_bot.scene_path)
        state = bot._new_state()
        self.assertEqual(sum(state["spells"].values()), 10)
        self.assertEqual(state["spells"]["levitation"], 2)
        self.assertEqual(state["spells"]["fire"], 2)
        state["view"] = "preface"
        bot.calls = []
        bot._test_tempdir = template_bot._test_tempdir
        try:
            asyncio.run(bot._send_direct_screen(42, 42, state))
            photo_call = next(payload for method, payload in bot.calls if method == "sendPhoto")
            self.assertEqual(photo_call["photo"], "default-photo")
            self.assertIn("Старое предисловие", photo_call["caption"])
            self.assertIn("ЗАКЛЯТИЕ ЛЕВИТАЦИИ", photo_call["caption"])
        finally:
            bot._test_tempdir.cleanup()

    def test_preface_cannot_continue_with_unallocated_spell_uses(self):
        bot, store = make_bot()
        store.state["view"] = "preface"
        store.state["spells"] = {
            "levitation": 2, "fire": 2, "illusion": 1, "strength": 1,
            "weakness": 1, "copy": 1, "healing": 1, "swimming": 0,
        }
        callback = {"callback_query": {
            "id": "continue-callback", "from": {"id": 42},
            "data": "blackcastle:continue",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        try:
            asyncio.run(bot.process_update(callback))
            self.assertEqual(store.state["view"], "preface")
            self.assertEqual(store.state["step"], 54)
            alert = next(payload for method, payload in bot.calls if method == "answerCallbackQuery")
            self.assertIn("Распределите все 10 заклинаний", alert["text"])
            self.assertIn("Осталось распределить: 1", alert["text"])
        finally:
            bot._test_tempdir.cleanup()

    def test_successful_check_consumes_one_luck_and_transitions_to_success_destination(self):
        bot, store = make_bot(luck=8)
        _, initial_keyboard, _ = bot._screen(store.state)
        initial_labels = [
            button["text"].replace("\u00a0", " ")
            for row in initial_keyboard
            for button in row
        ]
        self.assertEqual(initial_labels[:4], [
            "Проверить удачу — 558",
            "Заклинание Силы — 410",
            "Заклинание Слабости — 219",
            "Вступить в бой — 189",
        ])
        self.assertEqual(
            BlackCastleBot._route_button_text("Если он победил", 7).replace("\u00a0", " "),
            "Вступить в бой — 7",
        )
        with patch("messages.black_castle_bot.random.randint", side_effect=[2, 3]):
            asyncio.run(bot.process_update(make_callback("route_01")))
        try:
            self.assertEqual(store.state["step"], 558)
            self.assertEqual(store.state["characteristics"]["luck"], 7)
            self.assertEqual(store.state["luck_checks"]["54"], {
                "luck_before": 8, "roll": 5, "lucky": True,
            })
            alert = next(payload for method, payload in bot.calls if method == "answerCallbackQuery")
            self.assertIn("Ваша удача: 8", alert["text"])
            self.assertIn("Проверка удачи: 2 🎲 + 3 🎲 = 5", alert["text"])
            self.assertIn("Удача улыбнулась вам", alert["text"])
            self.assertEqual(bot.visible_state["step"], 558)
            self.assertEqual(bot.last_previous_message_id, 9)
            self.assertEqual(store.state["characteristics"]["luck"], 7)
        finally:
            bot._test_tempdir.cleanup()

    def test_combined_status_screen_uses_default_photo_in_direct_and_inline_delivery(self):
        template_bot, store = make_bot()
        bot = PhotoBot("token", None, store, template_bot.scene_path)
        bot._test_tempdir = template_bot._test_tempdir
        bot.calls = []
        state = store.state
        state["view"] = "status"
        try:
            _, _, has_photo = bot._screen(state)
            self.assertTrue(has_photo)
            asyncio.run(bot._send_direct_screen(42, 42, state))
            direct_call = next(call for call in bot.calls if call[0] == "sendPhoto")
            self.assertEqual(direct_call[1]["photo"], "default-photo")

            bot.calls.clear()
            asyncio.run(bot._edit_inline_screen("inline-1", state))
            inline_call = next(call for call in bot.calls if call[0] == "editMessageMedia")
            self.assertEqual(inline_call[1]["media"]["media"], "default-photo")
        finally:
            bot._test_tempdir.cleanup()

    def test_direct_battle_keeps_step_illustration_and_tracks_text_message(self):
        template_bot, store = make_bot()
        bot = SequentialMessageBot("token", None, store, template_bot.scene_path)
        bot._test_tempdir = template_bot._test_tempdir
        bot.calls = []
        state = store.state
        state.update({"view": "battle", "step": 558})
        state["battle"] = {"status": "awaiting_continue", "log": ["Раунд продолжается."]}
        try:
            asyncio.run(bot._send_direct_screen(42, 42, state))

            self.assertEqual([method for method, _ in bot.calls], ["sendPhoto", "sendMessage"])
            self.assertEqual(bot.calls[0][1]["photo"], "step-photo")
            self.assertNotIn("caption", bot.calls[0][1])
            self.assertIn("Раунд продолжается.", bot.calls[1][1]["text"])
            self.assertEqual(state["direct_message_ids"], [1, 2])
            self.assertEqual(state["direct_message_id"], 2)
            self.assertFalse(state["direct_message_has_photo"])
        finally:
            bot._test_tempdir.cleanup()

    def test_failed_check_hides_success_route_and_does_not_allow_a_second_roll(self):
        bot, store = make_bot(luck=5)
        with patch("messages.black_castle_bot.random.randint", side_effect=[6, 6]) as dice:
            asyncio.run(bot.process_update(make_callback("route_01")))
            asyncio.run(bot.process_update(make_callback("route_01", "callback-2")))
        try:
            self.assertEqual(store.state["step"], 54)
            self.assertEqual(store.state["characteristics"]["luck"], 4)
            self.assertEqual(dice.call_count, 2)
            _, keyboard, _ = bot._screen(store.state)
            labels = [button["text"] for row in keyboard for button in row]
            self.assertFalse(any("Проверить удачу" in label for label in labels))
            self.assertFalse(any("558" in label for label in labels))
        finally:
            bot._test_tempdir.cleanup()

    def test_other_route_declines_check_and_is_unlucky(self):
        bot, store = make_bot(luck=4)
        store.state["spells"] = {"strength": 1}
        cast = {"callback_query": {
            "id": "callback-1", "from": {"id": 42},
            "data": "blackcastle:cast:54:route_02:strength",
            "message": {"message_id": 9, "chat": {"id": 42}},
        }}
        with patch("messages.black_castle_bot.random.randint") as dice:
            asyncio.run(bot.process_update(cast))
        try:
            self.assertEqual(store.state["step"], 410)
            self.assertEqual(store.state["characteristics"]["luck"], 3)
            self.assertEqual(store.state["luck_checks"]["54"]["lucky"], False)
            dice.assert_not_called()
            alert = next(payload for method, payload in bot.calls if method == "answerCallbackQuery")
            self.assertIn("Вас настигла неудача", alert["text"])
        finally:
            bot._test_tempdir.cleanup()

    def test_zero_luck_fails_without_rolling_or_decrementing(self):
        bot, store = make_bot(luck=0)
        with patch("messages.black_castle_bot.random.randint") as dice:
            asyncio.run(bot.process_update(make_callback("route_01")))
        try:
            self.assertEqual(store.state["step"], 54)
            self.assertEqual(store.state["characteristics"]["luck"], 0)
            self.assertIsNone(store.state["luck_checks"]["54"]["roll"])
            self.assertFalse(store.state["luck_checks"]["54"]["lucky"])
            dice.assert_not_called()
        finally:
            bot._test_tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
