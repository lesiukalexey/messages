import unittest

from messages.language import check_reply_language, expected_reply_language


class ReplyLanguageCheckTest(unittest.TestCase):
    def test_ukrainian_incoming_requires_russian_reply(self) -> None:
        incoming = "Вітаю, підкажіть, будь ласка, щодо проєкту"
        self.assertEqual(expected_reply_language(incoming), "Russian")

    def test_language_choice_handles_technical_words_in_ukrainian_input(self) -> None:
        self.assertEqual(
            expected_reply_language("Який ваш досвід із AWS та MongoDB?") , "Russian"
        )

    def test_language_choice_handles_cyrillic_name_in_english_input(self) -> None:
        self.assertEqual(
            expected_reply_language("Hi Алексей, could you share your salary range?"), "English"
        )

    def test_russian_reply_passes_for_ukrainian_incoming(self) -> None:
        result = check_reply_language("Спасибо, я готов обсудить проект.", "Вітаю, підкажіть щодо проєкту")
        self.assertTrue(result.passed)
        self.assertEqual(result.checklist, "[x] Reply is in Russian; Ukrainian is not used")

    def test_english_reply_passes_for_english_incoming(self) -> None:
        result = check_reply_language("Thanks, I can start immediately.", "Could you tell me your availability?")
        self.assertTrue(result.passed)
        self.assertEqual(result.expected, "English")

    def test_short_ukrainian_reply_fails(self) -> None:
        result = check_reply_language("Ви вже працювали з AWS?", "Could you share your experience?")
        self.assertFalse(result.passed)

    def test_short_english_acknowledgment_passes(self) -> None:
        self.assertTrue(check_reply_language("Sounds good.", "Thanks for confirming.").passed)

    def test_ukrainian_sample_fails_even_when_source_is_ukrainian(self) -> None:
        incoming = "Які у вас очікування?"
        reply = (
            "Для AI-агентів використовував власні пайплайни та workflows. "
            "Працював із GPT, Claude і Grok через API. Конкретну модель обирали залежно від задачі. "
            "Використовував Markdown-файли та бази MySQL і MongoDB. "
            "Готовий розглянути тестове для неоплачуваного варіанту.")
        result = check_reply_language(reply, incoming)
        self.assertFalse(result.passed)
        self.assertTrue(result.checklist.startswith("[ ]"))

    def test_wrong_language_fails(self) -> None:
        self.assertFalse(check_reply_language("Спасибо, я готов.", "Can you start tomorrow?").passed)


if __name__ == "__main__":
    unittest.main()
