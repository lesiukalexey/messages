import tempfile
import unittest
from pathlib import Path
import asyncio

from messages.learning import LearningBot, save_learned_answer
from messages.recruiter_answers import RecruiterAnswers
from messages.store import Store


class LearningAnswersTest(unittest.TestCase):
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
                question = store.claim_next_learning_question()
                self.assertIsNotNone(question)
                assert question is not None
                store.mark_learning_question_awaiting(question["id"], 99)
                self.assertTrue(store.is_learning_question_message(99))
                self.assertTrue(store.claim_learning_answer(question["id"]))
                self.assertFalse(store.claim_learning_answer(question["id"]))
                store.finish_learning_question(question["id"])
                self.assertIsNone(store.claim_next_learning_question())
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
            profile.write_text("values: {}\n", encoding="utf-8")
            store = Store(root / "assistant.sqlite3", "personal")
            try:
                store.initialize()
                store.register_learning_owner(123)
                store.enqueue_learning_question("How many AWS years do I have?")
                bot = FakeLearningBot("test-token", store, profile, root / "lock")
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
                saved = RecruiterAnswers(profile).learned_answers_context()
                self.assertEqual(saved[0]["answer"], "I have used AWS for four years.")
                self.assertIsNone(store.claim_next_learning_question())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
