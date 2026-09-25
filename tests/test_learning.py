import tempfile
import unittest
from pathlib import Path

from messages.learning import save_learned_answer
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


if __name__ == "__main__":
    unittest.main()
