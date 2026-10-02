import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from messages.game_reply import GameReplyAlgorithm, game_session_history


GAME_SOURCE = next(
    (
        path for path in (
            Path("/game/games/chat_with_role/src/reply_algorithm.py"),
            Path("/var/www/game/games/chat_with_role/src/reply_algorithm.py"),
        ) if path.exists()
    ),
    Path("/game/games/chat_with_role/src/reply_algorithm.py"),
)


class FakeEvent:
    def __init__(self, message_id: int) -> None:
        self.message = SimpleNamespace(id=message_id)

    async def get_input_chat(self) -> str:
        return "test-chat"


class FakeClient:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self.messages = messages

    async def iter_messages(self, chat: str):
        assert chat == "test-chat"
        for message in self.messages:
            yield message


class GameReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_entire_session_is_sent_in_order_and_older_turns_are_excluded(self) -> None:
        start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        messages = [
            SimpleNamespace(id=105, date=start + timedelta(minutes=4), message="future", out=False),
            SimpleNamespace(id=104, date=start + timedelta(minutes=3), message="current", out=False),
            *(
                SimpleNamespace(
                    id=number,
                    date=start + timedelta(seconds=number - 1),
                    message=f"turn {number}",
                    out=number % 2 == 0,
                )
                for number in range(103, 0, -1)
            ),
            SimpleNamespace(id=0, date=start - timedelta(seconds=1), message="prior session", out=False),
        ]
        history = await game_session_history(
            FakeClient(messages), FakeEvent(104), start.isoformat()
        )
        self.assertEqual(len(history), 103)
        self.assertEqual(history[0], {"role": "contact", "text": "turn 1"})
        self.assertEqual(history[-1], {"role": "contact", "text": "turn 103"})
        self.assertNotIn("prior session", [turn["text"] for turn in history])
        self.assertNotIn("future", [turn["text"] for turn in history])

    @unittest.skipUnless(
        GAME_SOURCE.exists(),
        "Game source is mounted only in the active workspace",
    )
    async def test_game_uses_history_and_latest_message_for_generation(self) -> None:
        algorithm = GameReplyAlgorithm(GAME_SOURCE)
        prompts: list[str] = []

        async def generate(prompt: str) -> str:
            prompts.append(prompt)
            return "Конечно, давай попробуем!"

        reply = await algorithm.reply(
            "personal2", 123, "А теперь пошути",
            [{"role": "contact", "text": "Люблю каламбуры"},
             {"role": "assistant", "text": "Запомню"}],
            generate,
        )
        self.assertEqual(reply, "Конечно, давай попробуем!")
        transcript = json.loads(prompts[0].split("Current session transcript (oldest first):\n", 1)[1])
        self.assertEqual(
            transcript,
            [
                {"speaker": "contact", "text": "Люблю каламбуры"},
                {"speaker": "assistant", "text": "Запомню"},
                {"speaker": "contact", "text": "А теперь пошути"},
            ],
        )

    @unittest.skipUnless(
        GAME_SOURCE.exists(),
        "Game source is mounted only in the active workspace",
    )
    async def test_separate_follow_up_question_uses_ten_percent_threshold(self) -> None:
        algorithm = GameReplyAlgorithm(GAME_SOURCE)
        module = algorithm._load()
        prompts: list[str] = []

        async def generate(prompt: str) -> str:
            prompts.append(prompt)
            return "А какой чай ты любишь?"

        original_random = module.random.random
        try:
            module.random.random = lambda: 0.10
            self.assertIsNone(
                await algorithm.follow_up_question("Хочу чай", [], "Заварим)", generate)
            )
            self.assertEqual(prompts, [])
            module.random.random = lambda: 0.09
            question = await algorithm.follow_up_question(
                "Хочу чай", [], "Заварим)", generate
            )
            self.assertEqual(question, "А какой чай ты любишь?")
            self.assertEqual(len(prompts), 1)
        finally:
            module.random.random = original_random


if __name__ == "__main__":
    unittest.main()
