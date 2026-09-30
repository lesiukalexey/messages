import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from messages.calendar import GoogleCalendar
from messages.store import Store


class FixedEventsCalendar(GoogleCalendar):
    def __init__(self, events):
        super().__init__(Path("/unused"), "Europe/Kyiv")
        self.events = events
        self.queried = None

    def events_starting_between(self, start, end):
        self.queried = (start, end)
        return self.events


class ExistingContactMeetingTest(unittest.TestCase):
    def setUp(self):
        self.start = "2026-09-30T17:00:00+03:00"
        self.event = {
            "summary": "Market visit",
            "description": "Contact: Тайный Огурец\nTelegram: @boobora",
            "start": {"dateTime": "2026-09-30T17:00:00+03:00"},
            "end": {"dateTime": "2026-09-30T18:00:00+03:00"},
        }

    def test_matches_same_telegram_username_at_exact_start(self):
        calendar = FixedEventsCalendar([self.event])

        self.assertTrue(calendar.has_existing_contact_meeting(self.start, "boobora", "Different Name"))
        self.assertEqual(calendar.queried[0], datetime.fromisoformat(self.start) - timedelta(minutes=1))

    def test_matches_full_display_name_when_username_is_pseudonymous(self):
        calendar = FixedEventsCalendar([self.event])

        self.assertTrue(calendar.has_existing_contact_meeting(self.start, "another_handle", "Тайный Огурец"))

    def test_matches_a_stored_same_contact_event_even_when_calendar_uses_alias(self):
        event = {**self.event, "id": "stored-event", "description": "Contact: @boobora"}
        calendar = FixedEventsCalendar([event])

        self.assertTrue(
            calendar.has_existing_contact_meeting(
                self.start, "different_telegram_handle", "Тайный Огурец", {"stored-event"}
            )
        )

    def test_does_not_match_event_id_stored_for_another_contact(self):
        event = {**self.event, "id": "different-event", "description": "Private plan"}
        calendar = FixedEventsCalendar([event])

        self.assertFalse(
            calendar.has_existing_contact_meeting(
                self.start, "different_telegram_handle", "Тайный Огурец", {"other-event"}
            )
        )

    def test_does_not_match_different_contact_or_partial_first_name(self):
        calendar = FixedEventsCalendar([self.event])

        self.assertFalse(calendar.has_existing_contact_meeting(self.start, "otheruser", "Огурец"))

    def test_does_not_match_a_different_start_time(self):
        calendar = FixedEventsCalendar([self.event])

        self.assertFalse(calendar.has_existing_contact_meeting("2026-09-30T17:05:00+03:00", "boobora", "Тайный Огурец"))

    def test_does_not_match_username_substring(self):
        event = {**self.event, "description": "Telegram: @booborax"}
        calendar = FixedEventsCalendar([event])

        self.assertFalse(calendar.has_existing_contact_meeting(self.start, "boobora", "Different Name"))

    def test_stored_event_identity_is_scoped_to_account_and_peer(self):
        with TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "assistant.sqlite3", "personal")
            store.initialize()
            store.record_calendar_event("personal", 100, 1, "personal-event")
            store.record_calendar_event("personal2", 100, 2, "other-account-event")
            store.record_calendar_event("personal", 200, 3, "other-peer-event")

            self.assertEqual(
                store.calendar_event_ids_for_contact("personal", 100), {"personal-event"}
            )
            store.connection.close()


if __name__ == "__main__":
    unittest.main()
