import tempfile
import unittest
import sqlite3
from pathlib import Path
import asyncio
from datetime import datetime, timezone

from messages.config import Settings
from messages.llm import Responder
from messages.learning import LearningBot, save_learned_answer
from messages.recruiter_answers import RecruiterAnswers
from messages.store import Store


class LearningAnswersTest(unittest.TestCase):
    def test_existing_learning_queue_migrates_to_recruiter_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "assistant.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """CREATE TABLE learning_questions (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       question TEXT NOT NULL,
                       normalized_question TEXT NOT NULL UNIQUE,
                       status TEXT NOT NULL,
                       channel_message_id INTEGER,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            connection.execute(
                """INSERT INTO learning_questions
                   (question, normalized_question, status, created_at, updated_at)
                   VALUES (?, ?, 'answered', 'old', 'old')""",
                ("How is the recruiter profile used?", "how is the recruiter profile used?"),
            )
            connection.commit()
            connection.close()

            store = Store(database, "test")
            try:
                store.initialize()
                migrated = store.connection.execute(
                    "SELECT category, normalized_question FROM learning_questions"
                ).fetchone()
                self.assertEqual(migrated["category"], "recruiters")
                self.assertTrue(migrated["normalized_question"].startswith("recruiters:"))
                self.assertTrue(store.enqueue_learning_question(
                    "How is the recruiter profile used?", category="friends"
                ))
            finally:
                store.close()

    def test_substantive_position_is_queued_even_when_assistant_replies(self) -> None:
        class PromptResponder(Responder):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.prompt = ""

            async def _run(
                self, model: str, prompt: str, schema: dict[str, object] | None = None,
                timeout_seconds: int = 240,
            ) -> str:
                self.prompt = prompt
                return '{"reply":"Спасибо, но такой формат мне не подходит.","learn_question":"Готов ли я рассматривать обратный аутстаф за 25 долларов в час вместо обычного рейта 20 долларов и на каких условиях?","should_reply":true}'

        settings = Settings(
            api_id=1, api_hash="", account_id="personal", session_path=Path("/tmp/session"),
            database_path=Path("/tmp/db"), codex_binary=Path("/tmp/codex"),
            codex_home=Path("/tmp/codex-home"), model_options=("test",), default_model="test",
            timezone="Europe/Kyiv", google_client_file=Path("/tmp/client"),
            google_token_file=Path("/tmp/token"), recruiter_answers_file=Path("/tmp/answers"),
            learning_bot_token="",
        )
        responder = PromptResponder(settings)
        plan = asyncio.run(responder.plan(
            "test", "recruiters", [],
            "Клиент предлагает обратный аутстаф за $25/ч вместо моего обычного рейта $20/ч.",
            datetime.now(timezone.utc), "", prepared_answers=[],
        ))
        self.assertTrue(plan["should_reply"])
        self.assertIn("learn_question", plan)
        self.assertIn("substantive choice, preference, boundary", responder.prompt)
        self.assertIn("even when you can", responder.prompt)
        self.assertIn("test assignments, interview exercises, trial projects", responder.prompt)
        self.assertIn("Even if you decide to answer yes or no yourself", responder.prompt)
        self.assertIn("omit that part completely", responder.prompt)
        self.assertIn("first-person voice", responder.prompt)

    def test_answer_is_saved_and_available_to_future_model_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "answers.yaml"
            profile.write_text("values:\n  skills: Python\n", encoding="utf-8")
            save_learned_answer(
                profile,
                "How many years of AWS experience?",
                "I have used AWS in production for four years.",
                Path(directory) / "lock",
            )
            context = RecruiterAnswers(profile).for_recruiter_message(
                "What commercial AWS experience do you have?"
            )
            self.assertTrue(any("four years" in item["answer"] for item in context))
            friends_context = RecruiterAnswers(profile).learned_answers_context()
            self.assertEqual(len(friends_context), 1)
            self.assertIn("four years", friends_context[0]["answer"])

    def test_queue_serializes_questions_and_accepts_one_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "assistant.sqlite3", "test")
            try:
                store.initialize()
                store.register_learning_owner(17)
                self.assertEqual(store.learning_owner_ids(), {17})
                self.assertTrue(store.enqueue_learning_question("What is my AWS experience?"))
                self.assertFalse(store.enqueue_learning_question(" what is my aws experience? "))
                self.assertTrue(store.enqueue_learning_question(
                    "What is my AWS experience?", category="friends"
                ))
                self.assertFalse(store.enqueue_learning_question("   ", category="friends"))
                question = store.claim_next_learning_question()
                self.assertIsNotNone(question)
                assert question is not None
                self.assertEqual(question["category"], "recruiters")
                store.mark_learning_question_awaiting(question["id"], 99)
                self.assertTrue(store.is_learning_question_message(99))
                self.assertTrue(store.claim_learning_answer(question["id"]))
                self.assertFalse(store.claim_learning_answer(question["id"]))
                store.finish_learning_question(question["id"])
                friend_question = store.claim_next_learning_question()
                self.assertIsNotNone(friend_question)
                assert friend_question is not None
                self.assertEqual(friend_question["category"], "friends")
            finally:
                store.close()

    def test_bot_sends_question_and_saves_owner_reply(self) -> None:
        class FakeLearningBot(LearningBot):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.sent: list[dict[str, object]] = []

            async def _call(self, method: str, payload: dict[str, object]) -> object:
                self.sent.append(payload)
                return {"message_id": 100 + len(self.sent)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "answers.yaml"
            friends_profile = root / "friends.yaml"
            profile.write_text("values: {}\n", encoding="utf-8")
            friends_profile.write_text("learned_answers: {}\n", encoding="utf-8")
            store = Store(root / "assistant.sqlite3", "personal")
            try:
                store.initialize()
                store.register_learning_owner(123)
                store.enqueue_learning_question(
                    "How many AWS years do I have?", category="friends"
                )
                bot = FakeLearningBot(
                    "test-token", store, profile, root / "lock",
                    category_profile_paths={"friends": friends_profile},
                )
                asyncio.run(bot.process_update({"message": {
                    "from": {"id": 456},
                    "chat": {"id": 456, "type": "private"},
                    "text": "/start",
                }}))
                self.assertEqual(store.setting("learn_bot_owner_chat_id", ""), "")
                asyncio.run(bot.process_update({"message": {
                    "from": {"id": 123},
                    "chat": {"id": 123, "type": "private"},
                    "text": "/start",
                }}))
                pending = store.awaiting_learning_question()
                self.assertIsNotNone(pending)
                assert pending is not None
                question_message_id = int(pending["channel_message_id"])
                self.assertEqual(bot.sent[-1]["text"], "How many AWS years do I have?")
                asyncio.run(bot.process_update({"message": {
                    "from": {"id": 123},
                    "chat": {"id": 123, "type": "private"},
                    "text": "I have used AWS for four years.",
                    "reply_to_message": {"message_id": question_message_id},
                }}))
                saved = RecruiterAnswers(friends_profile).learned_answers_context()
                self.assertEqual(saved[0]["answer"], "I have used AWS for four years.")
                self.assertEqual(RecruiterAnswers(profile).learned_answers_context(), [])
                self.assertIsNone(store.claim_next_learning_question())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
