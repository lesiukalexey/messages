from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .calendar import GoogleCalendar
from .config import Settings
from .language import check_reply_language
from .llm import Responder
from .recruiter_answers import RecruiterAnswers
from .runtime import load_environment
from .store import Store

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 96_000
MAX_HISTORY_TURNS = 24
MAX_TURN_CHARS = 4_000


def _style_profile() -> str:
    runtime = Path("/home/admin/messages-runtime/profiles")
    paths = (
        Path(os.getenv("PERSONALITY_PROFILE_FILE", runtime / "personality-profile.md")),
        Path(os.getenv("STYLE_PROFILE_FILE", runtime / "communication-style.md")),
    )
    profiles = [path.read_text(encoding="utf-8") for path in paths if path.is_file()]
    return "\n\n".join(profiles)[:12_000] or "Write concisely, practically, and naturally."


def _safe_learning_question(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    question = re.sub(
        r"https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|@[A-Za-z0-9_]{3,}",
        "",
        value,
    )
    question = " ".join(question.split()).strip(" \t\r\n-–—")
    if re.search(
        r"password|passcode|secret|security\s+code|2fa|otp|bank(?:ing)?\s+(?:account|card)",
        question,
        re.IGNORECASE,
    ):
        return ""
    return question[:500]


def _bounded_text(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > limit:
        raise ValueError(f"{name} is too long")
    return value


def _identifier(value: Any, name: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _bounded_text(value, name, 200, required=True)


def _event_payload(payload: Any, settings: Settings) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    profile_id = _bounded_text(payload.get("profile_id"), "profile_id", 200, required=True)
    profile_path = settings.job_apply_profiles.get(profile_id)
    if profile_path is None:
        raise PermissionError("profile is not allowed")
    persona_id = _bounded_text(payload.get("persona_id"), "persona_id", 200, required=True)
    if persona_id.casefold() != settings.reply_api_persona_id.casefold():
        raise PermissionError("persona is not allowed")
    thread_id = _identifier(payload.get("thread_id"), "thread_id")
    message_id = _identifier(payload.get("message_id"), "message_id")
    incoming = _bounded_text(payload.get("incoming_message"), "incoming_message", 8_000, required=True)
    vacancy_context = _bounded_text(payload.get("vacancy_context", ""), "vacancy_context", 12_000)
    recruiter_name = _bounded_text(payload.get("recruiter_name", ""), "recruiter_name", 200)
    raw_history = payload.get("history", [])
    if not isinstance(raw_history, list) or len(raw_history) > MAX_HISTORY_TURNS:
        raise ValueError("history must contain at most 24 turns")
    history: list[dict[str, str]] = []
    recent_replies: list[str] = []
    for turn in raw_history:
        if (
            not isinstance(turn, dict)
            or not isinstance(turn.get("speaker"), str)
            or turn["speaker"] not in {"recruiter", "candidate"}
        ):
            raise ValueError("history speakers must be recruiter or candidate")
        text = _bounded_text(turn.get("text"), "history text", MAX_TURN_CHARS, required=True)
        speaker = turn["speaker"]
        history.append({"role": "user" if speaker == "recruiter" else "assistant", "text": text})
        if speaker == "candidate":
            recent_replies.append(text)
    return {
        "profile_id": profile_id,
        "profile_path": profile_path,
        "persona_id": persona_id,
        "thread_id": thread_id,
        "message_id": message_id,
        "incoming_message": incoming,
        "vacancy_context": vacancy_context,
        "recruiter_name": recruiter_name,
        "history": history,
        "recent_replies": recent_replies[-20:],
    }


class ReplyAPI:
    def __init__(self, settings: Settings) -> None:
        if len(settings.reply_api_token) < 32:
            raise RuntimeError("REPLY_API_TOKEN must be at least 32 characters")
        if not settings.reply_api_persona_id:
            raise RuntimeError("REPLY_API_PERSONA_ID must be configured")
        self.settings = settings
        self.responder = Responder(settings)
        self.calendar = GoogleCalendar(settings.google_token_file, settings.timezone)
        self.allowed_profiles = tuple(settings.job_apply_profiles.values())
        store = self._new_store()
        try:
            store.initialize()
        finally:
            store.close()

    def _new_store(self) -> Store:
        return Store(self.settings.database_path, "djinni-api")

    async def handle(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            event = _event_payload(request, self.settings)
        except PermissionError as exc:
            return 403, {"error": str(exc), "retryable": False}
        except ValueError as exc:
            return 400, {"error": str(exc), "retryable": False}

        profile_path: Path = event["profile_path"]
        answers = RecruiterAnswers(profile_path, self.allowed_profiles)
        normalized = {
            key: event[key]
            for key in (
                "profile_id", "persona_id", "thread_id", "message_id", "incoming_message",
                "vacancy_context", "recruiter_name", "history",
            )
        }
        request_hash = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        store = self._new_store()
        profile_id = event["profile_id"]
        account_id = profile_id
        thread_id = event["thread_id"]
        message_id = event["message_id"]
        try:
            state, prior = store.claim_integration_request(
                "djinni", account_id, profile_id, thread_id, message_id, request_hash
            )
            if state == "replay" and prior:
                return 200, json.loads(prior)
            if state == "conflict":
                return 409, {"error": "idempotency key was reused with different content", "retryable": False}
            if state == "processing":
                return 409, {"error": "request with this idempotency key is processing", "retryable": True}

            try:
                try:
                    persona_id = answers.profile_persona_id()
                except Exception as exc:
                    store.finish_integration_request(
                        "djinni", account_id, profile_id, thread_id, message_id, "", failed=True
                    )
                    store.audit(None, "external_profile_read_failed", type(exc).__name__)
                    logger.warning("Could not read an allowlisted profile (%s)", type(exc).__name__)
                    return 503, {"error": "profile is temporarily unavailable", "retryable": True}
                if persona_id != self.settings.reply_api_persona_id.casefold():
                    store.finish_integration_request(
                        "djinni", account_id, profile_id, thread_id, message_id, "", failed=True
                    )
                    store.audit(None, "external_profile_rejected", "persona mismatch")
                    return 403, {"error": "profile persona is not allowed", "retryable": False}
                response = await self._prepare_reply(event, answers, store)
            except Exception as exc:
                store.finish_integration_request(
                    "djinni", account_id, profile_id, thread_id, message_id, "", failed=True
                )
                store.audit(None, "external_reply_failed", type(exc).__name__)
                logger.warning("Recruiter reply request failed (%s)", type(exc).__name__)
                return 503, {"error": "reply preparation failed", "retryable": True}

            response_json = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            store.finish_integration_request(
                "djinni", account_id, profile_id, thread_id, message_id, response_json
            )
            store.audit(
                None,
                "external_reply_prepared",
                f"profile={profile_id}; outcome={response['outcome']}; calendar={response['calendar_status']}",
            )
            return 200, response
        finally:
            store.close()

    async def _prepare_reply(
        self,
        event: dict[str, Any],
        answers: RecruiterAnswers,
        store: Store,
    ) -> dict[str, Any]:
        incoming = event["incoming_message"]
        prepared_answers = answers.for_recruiter_message(incoming)
        if not prepared_answers or not prepared_answers[0]["question"].startswith(
            "Complete candidate profile context"
        ):
            raise RuntimeError("the selected recruiter profile could not be prepared")
        now = datetime.now(ZoneInfo(self.settings.timezone))
        plan = await self.responder.plan(
            model=self.settings.default_model,
            category="recruiters",
            history=event["history"],
            current_message=incoming,
            now=now,
            style_profile=_style_profile(),
            prepared_answers=prepared_answers,
            recent_outgoing_replies=event["recent_replies"],
            vacancy_context=event["vacancy_context"],
            platform="djinni",
        )
        learning_question = _safe_learning_question(plan.get("learn_question"))
        learning_requested = bool(learning_question)
        source_event_key = hashlib.sha256(
            f"djinni:{event['profile_id']}:{event['thread_id']}:{event['message_id']}".encode()
        ).hexdigest()
        learning_queued = bool(
            learning_requested
            and store.enqueue_learning_question(
                learning_question,
                category="recruiters",
                profile_id=event["profile_id"],
                source_platform="djinni",
                source_account_id=event["profile_id"],
                source_event_key=source_event_key,
            )
        )
        calendar_status = "none"
        calendar_result = "No calendar action is needed."
        action = plan.get("calendar_action", "none")
        start = plan.get("start")
        if action in {"check", "create"}:
            if not isinstance(start, str) or not start.strip():
                calendar_status = "time_unresolved"
                calendar_result = "TIME_UNRESOLVED; no time was confirmed."
            elif not self.calendar.configured:
                calendar_status = "unavailable"
                calendar_result = "CALENDAR_UNAVAILABLE; no time was confirmed."
            else:
                duration = plan.get("duration_minutes", 30)
                if not isinstance(duration, int) or not 5 <= duration <= 720:
                    raise ValueError("meeting duration is outside the allowed range")
                begins, ends = self.calendar.parse_interval(start, duration, self.calendar.timezone)
                if begins.hour < 9 or ends.date() != begins.date():
                    calendar_status = "unavailable"
                    calendar_result = "QUIET_HOURS_BLOCKED; no time was confirmed."
                else:
                    is_free, interval = await asyncio.to_thread(self.calendar.check, start, duration)
                    if not is_free:
                        calendar_status = "busy"
                        calendar_result = "BUSY; no event was created."
                    elif (
                        action == "create"
                        and plan.get("confirmed_agreement")
                        and plan.get("assistant_accepts_meeting")
                    ):
                        source_key = hashlib.sha256(
                            f"{event['profile_id']}:{event['thread_id']}:{event['message_id']}".encode()
                        ).hexdigest()
                        await asyncio.to_thread(
                            self.calendar.create_external,
                            "djinni",
                            source_key,
                            start,
                            duration,
                            str(plan.get("title") or "Interview"),
                            event["recruiter_name"],
                        )
                        calendar_status = "created"
                        calendar_result = "FREE; calendar event successfully created."
                    else:
                        calendar_status = "free"
                        calendar_result = f"FREE at {interval}; no event created yet."

        if action in {"check", "create"}:
            reply = await self.responder.compose_with_calendar_result(
                model=self.settings.default_model,
                category="recruiters",
                history=event["history"],
                current_message=incoming,
                plan=plan,
                calendar_result=calendar_result,
                now=now,
                style_profile=_style_profile(),
                prepared_answers=prepared_answers,
                vacancy_context=event["vacancy_context"],
                platform="djinni",
            )
        else:
            reply = str(plan.get("reply") or "").strip()

        reply = reply.strip()
        if reply and plan.get("should_reply", True):
            language = check_reply_language(reply, incoming)
            if not language.passed:
                reply = await self.responder.rewrite_reply_language(
                    self.settings.default_model,
                    incoming,
                    reply,
                    language.expected,
                )
            if not check_reply_language(reply, incoming).passed:
                reply = ""
        else:
            reply = ""

        outcome = "reply" if reply else "owner_attention" if learning_requested else "no_reply"
        return {
            "version": "v1",
            "outcome": outcome,
            "reply_text": reply,
            "learning_question_queued": learning_queued,
            "calendar_status": calendar_status,
        }


class ReplyRequestHandler(BaseHTTPRequestHandler):
    server: "ReplyHTTPServer"

    def do_POST(self) -> None:
        if self.path != "/v1/djinni/replies":
            self._send_json(404, {"error": "not found", "retryable": False})
            return
        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.api.settings.reply_api_token}"
        if not hmac.compare_digest(authorization.encode("utf-8"), expected.encode("utf-8")):
            self._send_json(401, {"error": "unauthorized", "retryable": False})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid content length", "retryable": False})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "request body size is invalid", "retryable": False})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            status, result = asyncio.run(self.server.api.handle(payload))
        except (UnicodeDecodeError, json.JSONDecodeError):
            status, result = 400, {"error": "invalid JSON body", "retryable": False}
        except Exception as exc:
            logger.warning("Reply API request failed (%s)", type(exc).__name__)
            status, result = 503, {"error": "reply preparation failed", "retryable": True}
        self._send_json(status, result)

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("Reply API client=%s", self.client_address[0])


class ReplyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api: ReplyAPI) -> None:
        self.api = api
        super().__init__(address, ReplyRequestHandler)


def main() -> None:
    load_environment(include_account=False)
    settings = Settings.from_environment(require_telegram=False)
    for key in ("REPLY_API_TOKEN", "LEARNING_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        os.environ.pop(key, None)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    api = ReplyAPI(settings)
    server = ReplyHTTPServer((settings.reply_api_host, settings.reply_api_port), api)
    logger.info("Private recruiter reply API listening on %s:%s", settings.reply_api_host, settings.reply_api_port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
