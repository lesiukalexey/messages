from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymysql
from telethon import TelegramClient, utils
from telethon.tl import types

from .config import Settings


def load_dotenv(account: str, path: Path = Path(".env")) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", account):
        raise ValueError("account must contain only letters, numbers, dots, hyphens, or underscores")
    paths = [path, Path("/home/admin/messages-runtime/messages.env")]
    for env_path in paths:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
    account_path = Path("/home/admin/messages-runtime/accounts") / f"{account}.env"
    if account_path.exists():
        for line in account_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def dialog_kind(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "user"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        return "channel" if entity.broadcast else "group"
    return "unknown"


def is_real_person_dialog(entity: Any) -> bool:
    """Keep only one-to-one dialogs with non-bot Telegram users."""
    return (
        isinstance(entity, types.User)
        and not entity.bot
        and not entity.deleted
        and not entity.is_self
    )


def message_record(message: Any) -> dict[str, Any]:
    media_type = type(message.media).__name__ if message.media else None
    return {
        "message_id": message.id,
        "date": message.date.astimezone(UTC).isoformat() if message.date else None,
        "sender_id": message.sender_id,
        "text": message.message or "",
        "reply_to_msg_id": getattr(message.reply_to, "reply_to_msg_id", None),
        "media_type": media_type,
        "outgoing": bool(message.out),
        "post": bool(message.post),
        "grouped_id": message.grouped_id,
    }


class ExportStore:
    def __init__(self, path: Path, account_id: str) -> None:
        self.account_id = account_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dialogs (
                account_id TEXT NOT NULL,
                dialog_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                username TEXT,
                phone TEXT,
                first_message_at TEXT,
                last_message_at TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                message_counted_at TEXT,
                exported_at TEXT NOT NULL,
                PRIMARY KEY (account_id, dialog_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                account_id TEXT NOT NULL,
                dialog_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                date TEXT,
                sender_id INTEGER,
                text TEXT NOT NULL,
                reply_to_msg_id INTEGER,
                media_type TEXT,
                outgoing INTEGER NOT NULL,
                post INTEGER NOT NULL,
                grouped_id INTEGER,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (account_id, dialog_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_account_dialog_date
                ON messages(account_id, dialog_id, date);
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(dialogs)")}
        if "phone" not in columns:
            self.connection.execute("ALTER TABLE dialogs ADD COLUMN phone TEXT")
        for column, definition in (
            ("first_message_at", "TEXT"),
            ("last_message_at", "TEXT"),
            ("message_count", "INTEGER NOT NULL DEFAULT 0"),
            ("message_counted_at", "TEXT"),
        ):
            if column not in columns:
                self.connection.execute(f"ALTER TABLE dialogs ADD COLUMN {column} {definition}")
        self.connection.commit()
        path.chmod(0o600)

    def save_dialog(
        self, dialog_id: int, kind: str, name: str, username: str | None, phone: str | None
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO dialogs(account_id, dialog_id, kind, name, username, phone, exported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, dialog_id) DO UPDATE SET
                kind = excluded.kind,
                name = excluded.name,
                username = excluded.username,
                phone = excluded.phone,
                exported_at = excluded.exported_at
            """,
            (self.account_id, dialog_id, kind, name, username, phone, datetime.now(UTC).isoformat()),
        )

    def save_message(self, dialog_id: int, record: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO messages(
                account_id, dialog_id, message_id, date, sender_id, text, reply_to_msg_id,
                media_type, outgoing, post, grouped_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.account_id,
                dialog_id,
                record["message_id"],
                record["date"],
                record["sender_id"],
                record["text"],
                record["reply_to_msg_id"],
                record["media_type"],
                int(record["outgoing"]),
                int(record["post"]),
                record["grouped_id"],
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def refresh_dialog_stats(self, dialog_id: int) -> None:
        first, last, count = self.connection.execute(
            """
            SELECT MIN(date), MAX(date), COUNT(*)
            FROM messages
            WHERE account_id = ? AND dialog_id = ?
            """,
            (self.account_id, dialog_id),
        ).fetchone()
        self.connection.execute(
            """
            UPDATE dialogs
            SET first_message_at = ?, last_message_at = ?, message_count = ?,
                message_counted_at = ?
            WHERE account_id = ? AND dialog_id = ?
            """,
            (
                first,
                last,
                count,
                datetime.now(UTC).isoformat(),
                self.account_id,
                dialog_id,
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class MysqlExportStore:
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.connection = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.getenv("MYSQL_DATABASE", "messages"),
            charset="utf8mb4",
            autocommit=False,
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS dialogs (
                    account_id VARCHAR(128) NOT NULL,
                    dialog_id BIGINT NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    name TEXT NOT NULL,
                    username VARCHAR(255),
                    phone VARCHAR(32),
                    first_message_at VARCHAR(40),
                    last_message_at VARCHAR(40),
                    message_count BIGINT NOT NULL DEFAULT 0,
                    message_counted_at DATETIME(6),
                    exported_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (account_id, dialog_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    account_id VARCHAR(128) NOT NULL,
                    dialog_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    date VARCHAR(40),
                    sender_id BIGINT,
                    text LONGTEXT NOT NULL,
                    reply_to_msg_id BIGINT,
                    media_type VARCHAR(128),
                    outgoing BOOLEAN NOT NULL,
                    post BOOLEAN NOT NULL,
                    grouped_id BIGINT,
                    raw_json LONGTEXT NOT NULL,
                    PRIMARY KEY (account_id, dialog_id, message_id),
                    INDEX messages_account_dialog_date (account_id, dialog_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            self._migrate_existing_schema(cursor)
        self.connection.commit()

    @staticmethod
    def _migrate_existing_schema(cursor: Any) -> None:
        cursor.execute("SHOW COLUMNS FROM dialogs LIKE 'account_id'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE dialogs ADD COLUMN account_id VARCHAR(128) NOT NULL DEFAULT 'default' FIRST")
            cursor.execute("ALTER TABLE dialogs DROP PRIMARY KEY, ADD PRIMARY KEY (account_id, dialog_id)")
        cursor.execute("SHOW COLUMNS FROM messages LIKE 'account_id'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE messages ADD COLUMN account_id VARCHAR(128) NOT NULL DEFAULT 'default' FIRST")
            cursor.execute("ALTER TABLE messages DROP PRIMARY KEY, ADD PRIMARY KEY (account_id, dialog_id, message_id)")
        cursor.execute("SHOW COLUMNS FROM dialogs LIKE 'phone'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE dialogs ADD COLUMN phone VARCHAR(32) NULL AFTER username")
        for column, definition in (
            ("first_message_at", "VARCHAR(40) NULL"),
            ("last_message_at", "VARCHAR(40) NULL"),
            ("message_count", "BIGINT NOT NULL DEFAULT 0"),
            ("message_counted_at", "DATETIME(6) NULL"),
        ):
            cursor.execute(f"SHOW COLUMNS FROM dialogs LIKE '{column}'")
            if not cursor.fetchone():
                cursor.execute(f"ALTER TABLE dialogs ADD COLUMN {column} {definition}")
        cursor.execute("SHOW INDEX FROM messages WHERE Key_name = 'messages_account_dialog_date'")
        if not cursor.fetchone():
            cursor.execute(
                "CREATE INDEX messages_account_dialog_date ON messages(account_id, dialog_id, date)"
            )

    def save_dialog(
        self, dialog_id: int, kind: str, name: str, username: str | None, phone: str | None
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dialogs(account_id, dialog_id, kind, name, username, phone, exported_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    kind = VALUES(kind), name = VALUES(name),
                    username = VALUES(username), phone = VALUES(phone),
                    exported_at = VALUES(exported_at)
                """,
                (self.account_id, dialog_id, kind, name, username, phone, datetime.now(UTC).replace(tzinfo=None)),
            )

    def save_message(self, dialog_id: int, record: dict[str, Any]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT IGNORE INTO messages(
                    account_id, dialog_id, message_id, date, sender_id, text, reply_to_msg_id,
                    media_type, outgoing, post, grouped_id, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.account_id,
                    dialog_id,
                    record["message_id"],
                    record["date"],
                    record["sender_id"],
                    record["text"],
                    record["reply_to_msg_id"],
                    record["media_type"],
                    int(record["outgoing"]),
                    int(record["post"]),
                    record["grouped_id"],
                    json.dumps(record, ensure_ascii=False),
                ),
            )

    def refresh_dialog_stats(self, dialog_id: int) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT MIN(date), MAX(date), COUNT(*)
                FROM messages
                WHERE account_id = %s AND dialog_id = %s
                """,
                (self.account_id, dialog_id),
            )
            first, last, count = cursor.fetchone()
            cursor.execute(
                """
                UPDATE dialogs
                SET first_message_at = %s, last_message_at = %s,
                    message_count = %s, message_counted_at = %s
                WHERE account_id = %s AND dialog_id = %s
                """,
                (
                    first,
                    last,
                    count,
                    datetime.now(UTC).replace(tzinfo=None),
                    self.account_id,
                    dialog_id,
                ),
            )

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


async def export_history(account_id: str, limit: int | None, since: datetime | None) -> None:
    os.environ.setdefault(
        "TELEGRAM_SESSION_PATH",
        f"/home/admin/messages-runtime/accounts/{account_id}/telegram",
    )
    settings = Settings.from_environment()
    backend = os.getenv("STORAGE_BACKEND", "mysql").lower()
    if backend == "mysql":
        store = MysqlExportStore(account_id)
    elif backend == "sqlite":
        export_path = Path(os.getenv("TELEGRAM_EXPORT_PATH", "/home/admin/messages-data"))
        export_path.mkdir(parents=True, exist_ok=True)
        export_path.chmod(0o700)
        store = ExportStore(export_path / "telegram.sqlite3", account_id)
    else:
        raise ValueError("STORAGE_BACKEND must be mysql or sqlite")
    settings.session_path.parent.mkdir(parents=True, exist_ok=True)
    settings.session_path.parent.chmod(0o700)
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
    logger = logging.getLogger(__name__)
    dialogs = 0
    messages = 0

    try:
        await client.start()
        async for dialog in client.iter_dialogs(ignore_migrated=True):
            entity = dialog.entity
            if not is_real_person_dialog(entity):
                continue
            kind = dialog_kind(entity)
            name = utils.get_display_name(entity) or str(dialog.id)
            username = getattr(entity, "username", None)
            phone = getattr(entity, "phone", None)
            store.save_dialog(dialog.id, kind, name, username, phone)
            dialogs += 1
            dialog_messages = 0

            async for message in client.iter_messages(entity, limit=limit, reverse=True):
                if since and message.date and message.date < since:
                    continue
                store.save_message(dialog.id, message_record(message))
                dialog_messages += 1
                messages += 1
                if dialog_messages % 100 == 0:
                    store.commit()
            store.refresh_dialog_stats(dialog.id)
            store.commit()
            logger.info("Exported %s messages from %s (%s)", dialog_messages, name, kind)
    finally:
        store.close()
        await client.disconnect()

    logger.info("Export complete: account=%s, %s dialogs, %s messages, backend=%s", account_id, dialogs, messages, backend)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Telegram history to protected account-separated storage")
    parser.add_argument("--account", required=True, help="stable account name, e.g. personal or work")
    parser.add_argument("--limit", type=int, help="maximum messages per dialog; useful for a test run")
    parser.add_argument("--since", help="only export messages on or after ISO date, e.g. 2026-01-01")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_dotenv(args.account)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(export_history(args.account, args.limit, parse_date(args.since)))
