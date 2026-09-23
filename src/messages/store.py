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
                category TEXT CHECK (category IN ('friends', 'recruiters')),
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
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                account_id TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                session_started_at TEXT NOT NULL,
                last_incoming_at TEXT NOT NULL,
                notification_state TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (account_id, peer_id)
            );
            """
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
        if category not in (None, "friends", "recruiters"):
            raise ValueError("category must be friends, recruiters, or None")
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
            """SELECT session_started_at, last_incoming_at, notification_state
               FROM conversation_sessions WHERE account_id = ? AND peer_id = ?""",
            (account_id, peer_id),
        ).fetchone()
        if row is None:
            session_started_at = incoming_at
            last_incoming_at = incoming_at
            notification_state = "pending"
        else:
            previous = datetime.fromisoformat(row["last_incoming_at"])
            new_session = (received - previous).total_seconds() > 30 * 60
            if new_session:
                session_started_at = incoming_at
                notification_state = "pending"
            else:
                session_started_at = row["session_started_at"]
                notification_state = row["notification_state"]
            last_incoming_at = incoming_at if received > previous else row["last_incoming_at"]
        self.connection.execute(
            """INSERT INTO conversation_sessions
                   (account_id, peer_id, session_started_at, last_incoming_at, notification_state)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, peer_id) DO UPDATE SET
                 session_started_at=excluded.session_started_at,
                 last_incoming_at=excluded.last_incoming_at,
                 notification_state=excluded.notification_state""",
            (account_id, peer_id, session_started_at, last_incoming_at, notification_state),
        )
        self.connection.commit()
        return session_started_at

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

    def audit(self, peer_id: int | None, event: str, details: str = "") -> None:
        self.connection.execute(
            """INSERT INTO assistant_audit_events
               (account_id, peer_id, event, details, created_at) VALUES (?, ?, ?, ?, ?)""",
            (self.account_id, peer_id, event, details[:1000], utc_now()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
