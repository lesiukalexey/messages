import tempfile
import unittest
from pathlib import Path

from messages.app import answers_for_category
from messages.recruiter_answers import CategoryAnswers


class CategoryAnswersTest(unittest.TestCase):
    def test_friends_and_unknown_load_only_their_category_files(self) -> None:
        class ForbiddenRecruiterAnswers:
            def for_recruiter_message(self, message: str) -> list[dict[str, str]]:
                raise AssertionError("non-recruiter category read the Job Apply profile")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            friends_unknown = CategoryAnswers(root / "friends-unknown.yaml")
            realtors = CategoryAnswers(root / "realtors.yaml")
            for source in (friends_unknown, realtors):
                source.ensure_file()
            self.assertEqual((root / "friends-unknown.yaml").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root.stat().st_mode & 0o777), 0o700)

            friends_unknown.path.write_text(
                "learned_answers:\n  What do I like?: Hiking\n", encoding="utf-8"
            )
            sources = {
                "friends": friends_unknown,
                "unknown": friends_unknown,
                "realtors": realtors,
            }

            self.assertEqual(
                answers_for_category("friends", "What do I like?", ForbiddenRecruiterAnswers(), sources),
                [{"question": "What do I like?", "answer": "Hiking"}],
            )
            self.assertEqual(
                answers_for_category("unknown", "What do I like?", ForbiddenRecruiterAnswers(), sources),
                [{"question": "What do I like?", "answer": "Hiking"}],
            )
            self.assertEqual(
                answers_for_category("realtors", "question", ForbiddenRecruiterAnswers(), sources),
                [],
            )


if __name__ == "__main__":
    unittest.main()
