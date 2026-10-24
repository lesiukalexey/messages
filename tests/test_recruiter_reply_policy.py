import unittest
import tempfile
from pathlib import Path

from messages.app import recruiter_keyword_fallback
from messages.recruiter_answers import RecruiterAnswers


class RecruiterReplyPolicyTest(unittest.TestCase):
    def test_no_confirmed_details_still_gets_a_first_person_answer(self) -> None:
        result = recruiter_keyword_fallback("Есть опыт presales?", [])
        self.assertEqual(result["reply"], "Сейчас не могу точно ответить на этот вопрос.")

    def test_missing_aws_fact_does_not_create_an_experience_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.yaml"
            profile.write_text("values: {}\n", encoding="utf-8")
            self.assertEqual(RecruiterAnswers(profile).match("How many AWS years?"), [])

    def test_negative_aws_fact_is_not_replaced_with_positive_experience(self) -> None:
        result = recruiter_keyword_fallback(
            "Have you worked with AWS?",
            [{"question": "AWS commercial experience", "answer": "I have never used AWS."}],
        )
        self.assertEqual(result["reply"], "I can't give a precise answer right now.")

    def test_fallback_does_not_send_source_commentary_from_saved_answers(self) -> None:
        result = recruiter_keyword_fallback(
            "Какая зарплата и когда можете приступить?",
            [
                {"question": "salary", "answer": "В резюме не указана зарплата"},
                {"question": "date available", "answer": "с понедельника"},
            ],
        )
        self.assertEqual(result["reply"], "Приступить к проекту могу с понедельника.")

    def test_aws_fallback_keeps_known_fact_and_omits_unknown_details(self) -> None:
        result = recruiter_keyword_fallback(
            "How many years of AWS experience do you have, and what tasks did you do?",
            [{"question": "AWS commercial experience", "answer": "AWS is in the backend stack"}],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["reply"], "AWS is part of my backend stack.")
        self.assertTrue(result["reply"].startswith("AWS"))
        for unwanted in ("profile", "can't", "cannot", "don't know", "20 years"):
            self.assertNotIn(unwanted, result["reply"].casefold())


if __name__ == "__main__":
    unittest.main()
