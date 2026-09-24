from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
SCOPES = [FREEBUSY_SCOPE, EVENTS_SCOPE]


class GoogleCalendar:
    def __init__(self, token_file: Path, timezone: str) -> None:
        self.token_file = token_file
        self.timezone = ZoneInfo(timezone)

    @property
    def configured(self) -> bool:
        return self.token_file.is_file()

    def _service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not self.token_file.is_file():
            raise RuntimeError("Google Calendar is not authorized; run messages-calendar-auth")
        credentials = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.token_file.write_text(credentials.to_json())
            self.token_file.chmod(0o600)
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    @staticmethod
    def parse_interval(start: str, duration_minutes: int, timezone: ZoneInfo):
        parsed = datetime.fromisoformat(start)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        parsed = parsed.astimezone(timezone)
        end = parsed + timedelta(minutes=duration_minutes)
        return parsed, end

    def check(self, start: str, duration_minutes: int) -> tuple[bool, str]:
        if not 5 <= duration_minutes <= 720:
            raise ValueError("meeting duration is outside the allowed range")
        begins, ends = self.parse_interval(start, duration_minutes, self.timezone)
        body = {
            "timeMin": begins.isoformat(),
            "timeMax": ends.isoformat(),
            "timeZone": str(self.timezone),
            "items": [{"id": "primary"}],
        }
        response = self._service().freebusy().query(body=body).execute()
        calendar = response.get("calendars", {}).get("primary", {})
        if calendar.get("errors"):
            raise RuntimeError("Google Calendar could not check availability")
        return not bool(calendar.get("busy")), f"{begins.isoformat()}/{ends.isoformat()}"

    def update_duration(
        self,
        event_id: str,
        start: str,
        requested_duration_minutes: int,
    ) -> tuple[bool, str | None]:
        if not 5 <= requested_duration_minutes <= 720:
            raise ValueError("meeting duration is outside the allowed range")
        begins, _ = self.parse_interval(start, requested_duration_minutes, self.timezone)
        requested_end = begins + timedelta(minutes=requested_duration_minutes)
        service = self._service()
        existing = service.events().get(calendarId="primary", eventId=event_id).execute()
        event_start = datetime.fromisoformat(existing["start"]["dateTime"]).astimezone(
            self.timezone
        )
        current_end = datetime.fromisoformat(existing["end"]["dateTime"]).astimezone(
            self.timezone
        )
        if event_start != begins:
            raise RuntimeError("the calendar event start changed before duration update")
        if current_end == requested_end:
            return True, None

        if requested_end > current_end:
            response = service.freebusy().query(
                body={
                    "timeMin": current_end.isoformat(),
                    "timeMax": requested_end.isoformat(),
                    "timeZone": str(self.timezone),
                    "items": [{"id": "primary"}],
                }
            ).execute()
            calendar = response.get("calendars", {}).get("primary", {})
            if calendar.get("errors"):
                raise RuntimeError("Google Calendar could not check the requested extension")
            busy = calendar.get("busy", [])
            if busy:
                conflict_start = min(
                    datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
                    for item in busy
                ).astimezone(self.timezone)
                return False, conflict_start.isoformat()

        service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body={"end": {"dateTime": requested_end.isoformat(), "timeZone": str(self.timezone)}},
        ).execute()
        return True, None

    def available_slots(
        self,
        day: date,
        duration_minutes: int,
        earliest: datetime,
        limit: int = 3,
    ) -> list[str]:
        if not 5 <= duration_minutes <= 720:
            raise ValueError("meeting duration is outside the allowed range")
        day_start = datetime.combine(day, time(9, 0), self.timezone)
        day_end = datetime.combine(day, time(22, 0), self.timezone)
        earliest = (
            earliest.astimezone(self.timezone)
            if earliest.tzinfo
            else earliest.replace(tzinfo=self.timezone)
        )
        cursor = max(day_start, earliest)
        minutes = cursor.hour * 60 + cursor.minute + bool(cursor.second or cursor.microsecond)
        cursor = datetime.combine(day, time.min, self.timezone) + timedelta(
            minutes=((minutes + 29) // 30) * 30
        )
        if cursor < day_start:
            cursor = day_start

        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "timeZone": str(self.timezone),
            "items": [{"id": "primary"}],
        }
        response = self._service().freebusy().query(body=body).execute()
        calendar = response.get("calendars", {}).get("primary", {})
        if calendar.get("errors"):
            raise RuntimeError("Google Calendar could not check availability")
        busy = [
            (
                datetime.fromisoformat(item["start"].replace("Z", "+00:00")).astimezone(
                    self.timezone
                ),
                datetime.fromisoformat(item["end"].replace("Z", "+00:00")).astimezone(
                    self.timezone
                ),
            )
            for item in calendar.get("busy", [])
        ]

        slots: list[str] = []
        while cursor + timedelta(minutes=duration_minutes) <= day_end and len(slots) < limit:
            end = cursor + timedelta(minutes=duration_minutes)
            if all(cursor >= busy_end or end <= busy_start for busy_start, busy_end in busy):
                slots.append(cursor.isoformat())
                cursor += timedelta(minutes=duration_minutes)
            else:
                cursor += timedelta(minutes=30)
        return slots

    def create(
        self,
        account_id: str,
        peer_id: int,
        source_message_id: int,
        start: str,
        duration_minutes: int,
        title: str,
        location: str | None = None,
        *,
        contact_name: str,
        telegram_username: str = "",
    ) -> str:
        if not 5 <= duration_minutes <= 720:
            raise ValueError("meeting duration is outside the allowed range")
        begins, ends = self.parse_interval(start, duration_minutes, self.timezone)
        raw_key = f"{account_id}:{peer_id}:{source_message_id}:{begins.isoformat()}".encode()
        event_id = "ev" + hashlib.sha256(raw_key).hexdigest()
        contact = " ".join(contact_name.split()) or f"Telegram user {peer_id}"
        username = telegram_username.strip().removeprefix("@")
        identity = contact[:90]
        if username:
            identity += f" (@{username})"
        summary_prefix = f"{identity} — "
        description = ["Agreed in Telegram.", f"Contact: {contact}"]
        if username:
            description.append(f"Telegram: @{username}")
        body: dict[str, Any] = {
            "id": event_id,
            "summary": f"{summary_prefix}{title}"[:160],
            "start": {"dateTime": begins.isoformat(), "timeZone": str(self.timezone)},
            "end": {"dateTime": ends.isoformat(), "timeZone": str(self.timezone)},
            "visibility": "private",
            "transparency": "opaque",
            "description": "\n".join(description),
        }
        if location:
            body["location"] = location[:500]
        service = self._service()
        try:
            result = service.events().insert(calendarId="primary", body=body).execute()
            return str(result["id"])
        except Exception as exc:
            # The deterministic ID makes a retry safe if Google accepted the
            # insert but the caller lost the response.
            if getattr(exc, "resp", None) is not None and getattr(exc.resp, "status", None) == 409:
                return event_id
            raise


def authorize(client_file: Path, token_file: Path, port: int = 8765) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_file.is_file():
        raise RuntimeError(f"OAuth client JSON not found: {client_file}")
    token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file.parent.chmod(0o700)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        port=port,
        open_browser=False,
        authorization_prompt_message=(
            "Open this URL in your browser to authorize calendar access:\n{url}\n"
            "If using Raspberry Pi over SSH, forward this port with: "
            "ssh -L 8765:localhost:8765 admin@192.168.31.46"
        ),
        success_message="Calendar authorization complete. You can close this tab.",
    )
    token_file.write_text(credentials.to_json())
    token_file.chmod(0o600)
    print("Google Calendar authorization saved in protected runtime storage.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Authorize the Telegram assistant to check and add Google Calendar events."
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    from .config import Settings
    from .runtime import load_environment

    load_environment()
    settings = Settings.from_environment()
    authorize(settings.google_client_file, settings.google_token_file, port=args.port)
