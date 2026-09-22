from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                peer_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL CHECK (mode IN ('off', 'draft', 'auto')) DEFAULT 'off',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id INTEGER,
                event TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def contact_mode(self, peer_id: int) -> str:
        row = self.connection.execute(
            "SELECT mode FROM contacts WHERE peer_id = ?", (peer_id,)
        ).fetchone()
        return row["mode"] if row else "off"

    def audit(self, peer_id: int | None, event: str, details: str = "") -> None:
        self.connection.execute(
            "INSERT INTO audit_events(peer_id, event, details, created_at) VALUES (?, ?, ?, ?)",
            (peer_id, event, details, utc_now()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

