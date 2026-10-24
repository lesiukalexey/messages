import asyncio
import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from messages.llm import Responder
from messages.reply_api import ReplyAPI
from messages.reply_voice import REPLY_VOICE_POLICY, needs_reply_voice_repair


class ScriptedResponder(Responder):
    def __init__(self, responses):
        self.timezone = ZoneInfo("Europe/Kyiv")
        self.responses = iter(responses)
        self.prompts = []

    async def _run(self, model, prompt, schema=None, timeout_seconds=240, effort="medium"):
        self.prompts.append(prompt)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class ReplyVoiceTest(unittest.TestCase):
    def test_detects_original_djinni_response_and_source_variants(self):
        for reply in (
            "Так, мав суміжний досвід. Формальної ролі в presales у резюме не зазначено.",
            "Формальная роль presales в моём резюме не указана.",
            "My profile does not specify AWS-specific years or responsibilities.",
            "AWS-specific years are not recorded in my CV.",
            "Согласно моему резюме, я работал с AWS.",
            "According to my resume, I worked with AWS.",
            "В моём профиле указан опыт работы с AWS.",
            "Алексей имеет опыт разработки.",
            "Alexey has backend experience.",
            "The candidate has relevant experience.",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(needs_reply_voice_repair(reply))

    def test_allows_cv_links_real_uncertainty_and_honest_negative_answers(self):
        for reply in (
            "Вот ссылка на моё резюме: https://example.com/cv",
            "Here is my CV: https://example.com/cv",
            "Я работал с AWS и презентовал технические решения.",
            "Я не работал в presales.",
            "I have not worked in presales.",
            "Не помню точную дату.",
            "Я обновлю резюме.",
            "Да, этот ответ подготовлен с помощью ИИ.",
        ):
            with self.subTest(reply=reply):
                self.assertFalse(needs_reply_voice_repair(reply))

    def test_every_reply_generation_path_repairs_source_commentary(self):
        bad = "Я презентовал технические решения. Формальная роль в резюме не указана."
        good = "Я презентовал технические решения."
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        common = dict(
            model="test", category="recruiters", history=[],
            current_message="Есть опыт presales?", now=now, style_profile="",
        )
        for operation in ("plan", "compose", "language", "rephrase"):
            platforms = ("telegram", "djinni", "linkedin") if operation in ("plan", "compose") else ("telegram",)
            for platform in platforms:
                with self.subTest(operation=operation, platform=platform):
                    first = json.dumps({"reply": bad, "should_reply": True}) if operation == "plan" else bad
                    responder = ScriptedResponder([first, good])
                    if operation == "plan":
                        result = asyncio.run(responder.plan(**common, platform=platform))["reply"]
                    elif operation == "compose":
                        result = asyncio.run(responder.compose_with_calendar_result(
                            **common, platform=platform, plan={}, calendar_result="none",
                        ))
                    elif operation == "language":
                        result = asyncio.run(responder.rewrite_reply_language(
                            "test", common["current_message"], bad, "Russian",
                        ))
                    else:
                        result = asyncio.run(responder.rephrase_repeated_reply(
                            **common, candidate_reply=bad, recent_outgoing_replies=[],
                            calendar_result="none",
                        ))
                    self.assertEqual(result, good)
                    self.assertEqual(len(responder.prompts), 2)
                    for prompt in responder.prompts:
                        self.assertIn(REPLY_VOICE_POLICY, prompt)

    def test_invalid_or_failed_repair_still_answers_in_first_person(self):
        bad = "В резюме не указан опыт AWS."
        for repair in (bad, "", RuntimeError("unavailable")):
            with self.subTest(repair=repr(repair)):
                responder = ScriptedResponder([repair])
                result = asyncio.run(responder._check_reply_voice("test", "Есть опыт AWS?", bad, "medium"))
                self.assertEqual(result, "Сейчас не могу точно ответить на этот вопрос.")
                self.assertFalse(needs_reply_voice_repair(result))

    def test_valid_reply_requires_no_extra_model_call(self):
        responder = ScriptedResponder([])
        reply = "Я согласовывал требования и презентовал решения."
        result = asyncio.run(responder._check_reply_voice("test", "Есть опыт presales?", reply, "medium"))
        self.assertEqual(result, reply)
        self.assertEqual(responder.prompts, [])

    def test_plan_answers_missing_fact_while_queuing_learning_question(self):
        responder = ScriptedResponder([json.dumps({
            "reply": "", "should_reply": False,
            "learn_question": "Какой у меня опыт presales?",
        })])
        result = asyncio.run(responder.plan(
            "test", "recruiters", [], "Есть опыт presales?",
            datetime.now(ZoneInfo("Europe/Kyiv")), "",
        ))
        self.assertTrue(result["should_reply"])
        self.assertEqual(result["reply"], "Сейчас не могу точно ответить на этот вопрос.")
        self.assertEqual(result["learn_question"], "Какой у меня опыт presales?")

    def test_external_api_answers_even_if_model_leaves_missing_fact_unanswered(self):
        for platform in ("djinni", "linkedin"):
            with self.subTest(platform=platform):
                api = object.__new__(ReplyAPI)
                api.settings = SimpleNamespace(timezone="Europe/Kyiv")
                api.responder = SimpleNamespace(plan=AsyncMock(return_value={
                    "reply": "", "should_reply": False,
                    "learn_question": "Какой у меня опыт presales?",
                }))
                store = SimpleNamespace(enqueue_learning_question=Mock(return_value=True))
                answers = SimpleNamespace(for_recruiter_message=lambda message: [{
                    "question": "Complete candidate profile context", "answer": "{}",
                }])
                event = dict(
                    incoming_message="Есть опыт presales?", model="test", history=[],
                    recent_replies=[], vacancy_context="", effort="medium", profile_id="owner",
                    thread_id="thread", message_id="message",
                )
                result = asyncio.run(api._prepare_reply(event, answers, store, platform=platform))
                self.assertEqual(result["outcome"], "reply")
                self.assertEqual(result["reply_text"], "Сейчас не могу точно ответить на этот вопрос.")
                self.assertTrue(result["learning_question_queued"])
                store.enqueue_learning_question.assert_called_once()


if __name__ == "__main__":
    unittest.main()
