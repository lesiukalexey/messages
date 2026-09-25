from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    """Small protected state database for assistant settings, labels and audit."""

    def __init__(self, path: Path, account_id: str) -> None:
        self.account_id = account_id
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS assistant_contacts (
                account_id TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                category TEXT CHECK (category IN ('unknown', 'friends', 'recruiters', 'realtors')),
                category_source TEXT NOT NULL DEFAULT 'manual',
                username TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, peer_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assistant_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                peer_id INTEGER,
                event TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_messages (
                account_id TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, peer_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS calendar_events (
                source_account_id TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source_account_id, peer_id, source_message_id)
            );
            CREATE TABLE IF NOT EXISTS pending_calendar_durations (
                account_id TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                start_at TEXT NOT NULL,
                provisional_duration_minutes INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, peer_id)
            );
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                account_id TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                session_started_at TEXT NOT NULL,
                last_incoming_at TEXT NOT NULL,
                notification_state TEXT NOT NULL DEFAULT 'pending',
                control_mode TEXT NOT NULL DEFAULT 'ai',
                owner_started INTEGER NOT NULL DEFAULT 0,
                awaiting_contact INTEGER NOT NULL DEFAULT 0,
                last_activity_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, peer_id)
            );
            """
        )
        session_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(conversation_sessions)")
        }
        for name, declaration in (
            ("control_mode", "TEXT NOT NULL DEFAULT 'ai'"),
            ("owner_started", "INTEGER NOT NULL DEFAULT 0"),
            ("awaiting_contact", "INTEGER NOT NULL DEFAULT 0"),
            ("last_activity_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in session_columns:
                self.connection.execute(
                    f"ALTER TABLE conversation_sessions ADD COLUMN {name} {declaration}"
                )
        self.connection.execute(
            "UPDATE conversation_sessions SET last_activity_at = last_incoming_at "
            "WHERE last_activity_at = ''"
        )
        # An interrupted send can be retried on the next successful AI reply.
        self.connection.execute(
            "UPDATE conversation_sessions SET notification_state = 'pending' "
            "WHERE notification_state = 'sending'"
        )
        contact_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(assistant_contacts)")
        }
        if "category_source" not in contact_columns:
            self.connection.execute(
                "ALTER TABLE assistant_contacts ADD COLUMN category_source TEXT NOT NULL DEFAULT 'manual'"
            )
        contact_table = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assistant_contacts'"
        ).fetchone()["sql"]
        if "'unknown'" not in contact_table:
            self.connection.execute("ALTER TABLE assistant_contacts RENAME TO assistant_contacts_legacy")
            self.connection.execute(
                """CREATE TABLE assistant_contacts (
                    account_id TEXT NOT NULL,
                    peer_id INTEGER NOT NULL,
                    category TEXT CHECK (category IN ('unknown', 'friends', 'recruiters', 'realtors')),
                    category_source TEXT NOT NULL DEFAULT 'manual',
                    username TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, peer_id)
                )"""
            )
            self.connection.execute(
                """INSERT INTO assistant_contacts
                   (account_id, peer_id, category, category_source, username, display_name, updated_at)
                   SELECT account_id, peer_id, category, category_source, username, display_name, updated_at
                   FROM assistant_contacts_legacy"""
            )
            self.connection.execute("DROP TABLE assistant_contacts_legacy")
        self.connection.commit()

    def contact_category(self, peer_id: int) -> str | None:
        row = self.connection.execute(
            "SELECT category FROM assistant_contacts WHERE account_id = ? AND peer_id = ?",
            (self.account_id, peer_id),
        ).fetchone()
        return row["category"] if row else None

    def contact_category_source(self, peer_id: int) -> str | None:
        row = self.connection.execute(
            "SELECT category_source FROM assistant_contacts WHERE account_id = ? AND peer_id = ?",
            (self.account_id, peer_id),
        ).fetchone()
        return row["category_source"] if row else None

    def set_contact_category(
        self,
        peer_id: int,
        category: str | None,
        username: str = "",
        display_name: str = "",
        source: str = "manual",
    ) -> None:
        if category not in (None, "unknown", "friends", "recruiters", "realtors"):
            raise ValueError("category must be unknown, friends, recruiters, realtors, or None")
        self.connection.execute(
            """INSERT INTO assistant_contacts
                   (account_id, peer_id, category, category_source, username, display_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, peer_id) DO UPDATE SET category=excluded.category,
                 category_source=excluded.category_source,
                 username=excluded.username, display_name=excluded.display_name,
                 updated_at=excluded.updated_at""",
            (self.account_id, peer_id, category, source, username, display_name, utc_now()),
        )
        self.connection.commit()

    def set_existing_contact_friend(self, peer_id: int, username: str, display_name: str) -> None:
        """Give an existing dialog a friend label only when it has no label yet."""
        self.connection.execute(
            """INSERT INTO assistant_contacts
                   (account_id, peer_id, category, category_source, username, display_name, updated_at)
               VALUES (?, ?, 'friends', 'automatic', ?, ?, ?)
               ON CONFLICT(account_id, peer_id) DO UPDATE SET
                   category='friends', category_source='automatic',
                   updated_at=excluded.updated_at
               WHERE assistant_contacts.category IS NULL""",
            (self.account_id, peer_id, username, display_name, utc_now()),
        )
        self.connection.commit()

    def contacts(self, category: str | None = None) -> list[sqlite3.Row]:
        if category:
            return list(
                self.connection.execute(
                    "SELECT peer_id, category, username, display_name FROM assistant_contacts "
                    "WHERE account_id = ? AND category = ? "
                    "ORDER BY display_name COLLATE NOCASE, username COLLATE NOCASE",
                    (self.account_id, category),
                )
            )
        return list(
            self.connection.execute(
                "SELECT peer_id, category, username, display_name FROM assistant_contacts "
                "WHERE account_id = ? AND category IS NOT NULL "
                "ORDER BY category, display_name COLLATE NOCASE",
                (self.account_id,),
            )
        )

    def setting(self, key: str, default: str) -> str:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key = ?", (f"{self.account_id}:{key}",)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (f"{self.account_id}:{key}", value, utc_now()),
        )
        self.connection.commit()

    def recover_interrupted_messages(self, max_age: timedelta = timedelta(minutes=30)) -> list[tuple[int, int]]:
        """Requeue safe interrupted work and expire stale or ambiguous sends."""
        now = datetime.now(UTC)
        cutoff = (now - max_age).isoformat()
        rows = list(
            self.connection.execute(
                """SELECT peer_id, message_id, created_at FROM processed_messages
                   WHERE account_id = ? AND state IN ('processing', 'pending')""",
                (self.account_id,),
            )
        )
        generated: set[tuple[int, int]] = set()
        for row in self.connection.execute(
            "SELECT peer_id, details FROM assistant_audit_events "
            "WHERE account_id = ? AND event = 'generated' AND peer_id IS NOT NULL",
            (self.account_id,),
        ):
            try:
                details = json.loads(row["details"])
                message_id = int(details["incoming_message_id"])
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            generated.add((row["peer_id"], message_id))

        requeued: list[tuple[int, int]] = []
        for row in rows:
            peer_id, message_id = row["peer_id"], row["message_id"]
            key = (peer_id, message_id)
            if key in generated:
                state, audit = "failed", "interrupted_after_generation"
                details = f"message_id={message_id}; send outcome unknown; not retried"
            elif row["created_at"] < cutoff:
                state, audit = "skipped", "interrupted_message_expired"
                details = f"message_id={message_id}; older than {int(max_age.total_seconds() // 60)} minutes"
            else:
                state, audit = "pending", "interrupted_message_requeued"
                details = f"message_id={message_id}"
                requeued.append(key)
            self.connection.execute(
                "UPDATE processed_messages SET state = ?, updated_at = ? "
                "WHERE account_id = ? AND peer_id = ? AND message_id = ?",
                (state, now.isoformat(), self.account_id, peer_id, message_id),
            )
            self.connection.execute(
                """INSERT INTO assistant_audit_events
                   (account_id, peer_id, event, details, created_at) VALUES (?, ?, ?, ?, ?)""",
                (self.account_id, peer_id, audit, details, now.isoformat()),
            )
        self.connection.commit()
        return requeued

    def claim_message(self, account_id: str, peer_id: int, message_id: int) -> bool:
        now = utc_now()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO processed_messages(
                   account_id, peer_id, message_id, state, created_at, updated_at
               ) VALUES (?, ?, ?, 'processing', ?, ?)""",
            (account_id, peer_id, message_id, now, now),
        )
        claimed = cursor.rowcount == 1
        if not claimed:
            cursor = self.connection.execute(
                """UPDATE processed_messages SET state = 'processing', updated_at = ?
                   WHERE account_id = ? AND peer_id = ? AND message_id = ? AND state = 'pending'""",
                (now, account_id, peer_id, message_id),
            )
            claimed = cursor.rowcount == 1
        self.connection.commit()
        return claimed

    def message_state(self, account_id: str, peer_id: int, message_id: int, state: str) -> None:
        self.connection.execute(
            """UPDATE processed_messages SET state = ?, updated_at = ?
               WHERE account_id = ? AND peer_id = ? AND message_id = ?""",
            (state, utc_now(), account_id, peer_id, message_id),
        )
        self.connection.commit()

    def record_incoming_session(
        self, account_id: str, peer_id: int, received_at: datetime
    ) -> str:
        received = (
            received_at.astimezone(UTC)
            if received_at.tzinfo
            else received_at.replace(tzinfo=UTC)
        )
        incoming_at = received.isoformat()
        row = self.connection.execute(
            """SELECT session_started_at, last_incoming_at, notification_state,
                      control_mode, owner_started, awaiting_contact, last_activity_at
               FROM conversation_sessions WHERE account_id = ? AND peer_id = ?""",
            (account_id, peer_id),
        ).fetchone()
        if row is None:
            session_started_at = incoming_at
            last_incoming_at = incoming_at
            notification_state = "pending"
            control_mode, owner_started, awaiting_contact = "ai", 0, 0
            last_activity_at = incoming_at
        else:
            previous_activity = datetime.fromisoformat(
                row["last_activity_at"] or row["last_incoming_at"]
            )
            new_session = (received - previous_activity).total_seconds() > 30 * 60
            if new_session:
                session_started_at = incoming_at
                notification_state = "pending"
                control_mode, owner_started, awaiting_contact = "ai", 0, 0
            else:
                session_started_at = row["session_started_at"]
                notification_state = row["notification_state"]
                control_mode = row["control_mode"]
                owner_started = row["owner_started"]
                awaiting_contact = 0
            previous_incoming = datetime.fromisoformat(row["last_incoming_at"])
            last_incoming_at = incoming_at if received > previous_incoming else row["last_incoming_at"]
            last_activity_at = incoming_at if received > previous_activity else (
                row["last_activity_at"] or row["last_incoming_at"]
            )
        self.connection.execute(
            """INSERT INTO conversation_sessions
                   (account_id, peer_id, session_started_at, last_incoming_at, notification_state,
                    control_mode, owner_started, awaiting_contact, last_activity_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, peer_id) DO UPDATE SET
                 session_started_at=excluded.session_started_at,
                 last_incoming_at=excluded.last_incoming_at,
                 notification_state=excluded.notification_state,
                 control_mode=excluded.control_mode,
                 owner_started=excluded.owner_started,
                 awaiting_contact=excluded.awaiting_contact,
                 last_activity_at=excluded.last_activity_at""",
            (account_id, peer_id, session_started_at, last_incoming_at, notification_state,
             control_mode, owner_started, awaiting_contact, last_activity_at),
        )
        self.connection.commit()
        return session_started_at

    def record_owner_outgoing(
        self, account_id: str, peer_id: int, sent_at: datetime, ai_opt_in: bool
    ) -> tuple[str, bool]:
        sent = sent_at.astimezone(UTC) if sent_at.tzinfo else sent_at.replace(tzinfo=UTC)
        sent_iso = sent.isoformat()
        row = self.connection.execute(
            """SELECT session_started_at, last_incoming_at, notification_state,
                      control_mode, owner_started, awaiting_contact, last_activity_at
               FROM conversation_sessions WHERE account_id = ? AND peer_id = ?""",
            (account_id, peer_id),
        ).fetchone()
        stale = False
        if row is not None:
            old_activity = row["last_activity_at"] or row["last_incoming_at"]
            stale = (sent - datetime.fromisoformat(old_activity)).total_seconds() > 30 * 60
        if row is None or stale:
            session_started_at = sent_iso
            last_incoming_at = sent_iso
            notification_state = "pending"
        else:
            session_started_at = row["session_started_at"]
            last_incoming_at = row["last_incoming_at"]
            notification_state = row["notification_state"]
            previous_activity = row["last_activity_at"] or row["last_incoming_at"]
            if sent < datetime.fromisoformat(previous_activity):
                sent_iso = previous_activity
        control_mode = "ai" if ai_opt_in else "manual"
        owner_started = 1 if ai_opt_in else 0
        awaiting_contact = 1 if ai_opt_in else 0
        consume_opt_in_marker = ai_opt_in
        self.connection.execute(
            """INSERT INTO conversation_sessions
                   (account_id, peer_id, session_started_at, last_incoming_at,
                    notification_state, control_mode, owner_started, awaiting_contact,
                    last_activity_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, peer_id) DO UPDATE SET
                 session_started_at=excluded.session_started_at,
                 last_incoming_at=excluded.last_incoming_at,
                 notification_state=excluded.notification_state,
                 control_mode=excluded.control_mode,
                 owner_started=excluded.owner_started,
                 awaiting_contact=excluded.awaiting_contact,
                 last_activity_at=excluded.last_activity_at""",
            (account_id, peer_id, session_started_at, last_incoming_at, notification_state,
             control_mode, owner_started, awaiting_contact, sent_iso),
        )
        self.connection.commit()
        return control_mode, consume_opt_in_marker

    def conversation_control_mode(self, account_id: str, peer_id: int) -> str:
        row = self.connection.execute(
            "SELECT control_mode, last_activity_at, last_incoming_at FROM conversation_sessions "
            "WHERE account_id = ? AND peer_id = ?",
            (account_id, peer_id),
        ).fetchone()
        if not row:
            return "ai"
        last_activity = row["last_activity_at"] or row["last_incoming_at"]
        if datetime.now(UTC) - datetime.fromisoformat(last_activity) > timedelta(minutes=30):
            return "ai"
        return row["control_mode"]

    def conversation_owner_opt_in_active(self, account_id: str, peer_id: int) -> bool:
        row = self.connection.execute(
            """SELECT control_mode, owner_started, last_activity_at, last_incoming_at
               FROM conversation_sessions WHERE account_id = ? AND peer_id = ?""",
            (account_id, peer_id),
        ).fetchone()
        if not row or row["control_mode"] != "ai" or not row["owner_started"]:
            return False
        last_activity = row["last_activity_at"] or row["last_incoming_at"]
        return datetime.now(UTC) - datetime.fromisoformat(last_activity) <= timedelta(minutes=30)

    def claim_conversation_notification(
        self, account_id: str, peer_id: int, session_started_at: str
    ) -> bool:
        cursor = self.connection.execute(
            """UPDATE conversation_sessions SET notification_state = 'sending'
               WHERE account_id = ? AND peer_id = ? AND session_started_at = ?
                 AND notification_state = 'pending'""",
            (account_id, peer_id, session_started_at),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def finish_conversation_notification(
        self, account_id: str, peer_id: int, session_started_at: str, success: bool
    ) -> None:
        state = "sent" if success else "pending"
        self.connection.execute(
            """UPDATE conversation_sessions SET notification_state = ?
               WHERE account_id = ? AND peer_id = ? AND session_started_at = ?
                 AND notification_state = 'sending'""",
            (state, account_id, peer_id, session_started_at),
        )
        self.connection.commit()

    def calendar_event_exists(self, account_id: str, peer_id: int, message_id: int) -> str | None:
        row = self.connection.execute(
            """SELECT event_id FROM calendar_events
               WHERE source_account_id = ? AND peer_id = ? AND source_message_id = ?""",
            (account_id, peer_id, message_id),
        ).fetchone()
        return row["event_id"] if row else None

    def record_calendar_event(
        self, account_id: str, peer_id: int, message_id: int, event_id: str
    ) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO calendar_events
               (source_account_id, peer_id, source_message_id, event_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (account_id, peer_id, message_id, event_id, utc_now()),
        )
        self.connection.commit()

    def pending_calendar_duration(self, account_id: str, peer_id: int) -> sqlite3.Row | None:
        row = self.connection.execute(
            """SELECT event_id, start_at, provisional_duration_minutes
               FROM pending_calendar_durations WHERE account_id = ? AND peer_id = ?""",
            (account_id, peer_id),
        ).fetchone()
        if row is None:
            return None
        starts_at = datetime.fromisoformat(row["start_at"])
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=UTC)
        if starts_at <= datetime.now(UTC):
            self.clear_pending_calendar_duration(account_id, peer_id)
            return None
        return row

    def set_pending_calendar_duration(
        self,
        account_id: str,
        peer_id: int,
        event_id: str,
        start_at: str,
        duration_minutes: int,
    ) -> None:
        self.connection.execute(
            """INSERT INTO pending_calendar_durations
                   (account_id, peer_id, event_id, start_at, provisional_duration_minutes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, peer_id) DO UPDATE SET
                 event_id=excluded.event_id, start_at=excluded.start_at,
                 provisional_duration_minutes=excluded.provisional_duration_minutes,
                 updated_at=excluded.updated_at""",
            (account_id, peer_id, event_id, start_at, duration_minutes, utc_now()),
        )
        self.connection.commit()

    def clear_pending_calendar_duration(self, account_id: str, peer_id: int) -> None:
        self.connection.execute(
            "DELETE FROM pending_calendar_durations WHERE account_id = ? AND peer_id = ?",
            (account_id, peer_id),
        )
        self.connection.commit()

    def audit(self, peer_id: int | None, event: str, details: str = "") -> None:
        self.connection.execute(
            """INSERT INTO assistant_audit_events
               (account_id, peer_id, event, details, created_at) VALUES (?, ?, ?, ?, ?)""",
            (self.account_id, peer_id, event, details[:1000], utc_now()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
