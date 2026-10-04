import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from messages.black_castle_bot import BlackCastleBot


class FakeGameStore:
    def __init__(self, state, paragraph, choices):
        self.state = state
        self.paragraph = paragraph
        self.choices = choices

    def get_player_state(self, player_id):
        return self.state

    def save_player_state(self, player_id, state):
        self.state = json.loads(json.dumps(state))

    def get_paragraph(self, number):
        return self.paragraph if number == 54 else {
            "paragraph_number": number,
            "body": "",
            "photo_file_id": "step-photo",
        }

    def get_paragraph_choices(self, number):
        return self.choices if number == 54 else []

    def get_paragraph_choice(self, number, choice_id):
        return next((row for row in self.choices if row["choice_id"] == choice_id), None)

    def get_book_page(self, key):
        return {"body": "Старое предисловие"} if key == "preface" else None

    def record_button_press(self, *args):
        pass

    def get_setting(self, key, default=""):
        return "default-photo"


class LuckBot(BlackCastleBot):
    async def _call(self, method, payload):
        self.calls.append((method, payload))
        return {}

    async def _send_direct_screen(self, chat_id, player_id, state, previous_message_id=0):
        self.visible_state = json.loads(json.dumps(state))

    async def _edit_inline_screen(self, inline_message_id, state):
        self.visible_state = json.loads(json.dumps(state))


class PhotoBot(BlackCastleBot):
    async def _call(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": 10}


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

    def test_successful_check_consumes_one_luck_and_returns_to_step_without_check_button(self):
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
            self.assertEqual(store.state["step"], 54)
            self.assertEqual(store.state["characteristics"]["luck"], 7)
            self.assertEqual(store.state["luck_checks"]["54"], {
                "luck_before": 8, "roll": 5, "lucky": True,
            })
            alert = next(payload for method, payload in bot.calls if method == "answerCallbackQuery")
            self.assertIn("Ваша удача: 8", alert["text"])
            self.assertIn("Проверка удачи выпала: 5", alert["text"])
            self.assertIn("Удача улыбнулась вам", alert["text"])
            _, keyboard, _ = bot._screen(store.state)
            labels = [button["text"] for row in keyboard for button in row]
            self.assertFalse(any("Проверить удачу" in label for label in labels))
            self.assertTrue(any("Удача улыбнулась" in label for label in labels))
            self.assertTrue(any("Вступить в бой" in label for label in labels))
            asyncio.run(bot.process_update(make_callback("route_01", "callback-2")))
            self.assertEqual(store.state["step"], 558)
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
