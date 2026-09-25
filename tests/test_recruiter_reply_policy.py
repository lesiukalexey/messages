import unittest

from messages.app import recruiter_keyword_fallback


class RecruiterReplyPolicyTest(unittest.TestCase):
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
