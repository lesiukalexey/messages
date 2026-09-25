import tempfile
import unittest
from pathlib import Path

import yaml

from messages.recruiter_answers import RecruiterAnswers


class RecruiterPersonaAnswersTest(unittest.TestCase):
    def test_recruiter_answers_merge_only_for_same_persona(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "profiles"
            primary = profiles / "primary" / "external-form-fields.yaml"
            same_persona = profiles / "same" / "external-form-fields.yaml"
            different_persona = profiles / "other" / "external-form-fields.yaml"
            for path in (primary, same_persona, different_persona):
                path.parent.mkdir(parents=True)

            primary.write_text(yaml.safe_dump({
                "persona_id": "alexey-lesiuk",
                "values": {"salary_expectation": 3200, "currency": "USD"},
                "learned_answers": {"What is my backend experience?": "More than 20 years."},
            }), encoding="utf-8")
            same_persona.write_text(yaml.safe_dump({
                "persona_id": "alexey-lesiuk",
                "values": {"salary_expectation": 2800, "currency": "USD"},
                "learned_answers": {
                    "How many years of AWS experience do I have?": "I used AWS in production.",
                    "What is my salary expectation?": "$2,800 gross.",
                },
                "owner_learned_answers": {
                    "What is my preferred AWS setup?": "I use AWS for cloud deployments."
                },
            }), encoding="utf-8")
            different_persona.write_text(yaml.safe_dump({
                "persona_id": "someone-else",
                "learned_answers": {"How many years of AWS experience do I have?": "Never used AWS."},
            }), encoding="utf-8")

            answers = RecruiterAnswers(primary)
            matched = answers.match("How many years of AWS experience?")
            self.assertIn(
                {"question": "How many years of AWS experience do I have?", "answer": "I used AWS in production."},
                matched,
            )
            context = answers.profile_context()
            self.assertIn("I used AWS in production.", context)
            self.assertIn("I use AWS for cloud deployments.", context)
            self.assertNotIn("Never used AWS.", context)
            self.assertNotIn("$2,800 gross.", context)
            self.assertNotIn("2800", context)
            self.assertIn("3200", context)


if __name__ == "__main__":
    unittest.main()
