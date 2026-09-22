from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymysql
from telethon import TelegramClient, utils
from telethon.tl import types

from .config import Settings


def load_dotenv(path: Path = Path(".env")) -> None:
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
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dialogs (
                dialog_id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                username TEXT,
                exported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
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
                PRIMARY KEY (dialog_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_dialog_date
                ON messages(dialog_id, date);
            """
        )
        self.connection.commit()
        path.chmod(0o600)

    def save_dialog(self, dialog_id: int, kind: str, name: str, username: str | None) -> None:
        self.connection.execute(
            """
            INSERT INTO dialogs(dialog_id, kind, name, username, exported_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(dialog_id) DO UPDATE SET
                kind = excluded.kind,
                name = excluded.name,
                username = excluded.username,
                exported_at = excluded.exported_at
            """,
            (dialog_id, kind, name, username, datetime.now(UTC).isoformat()),
        )

    def save_message(self, dialog_id: int, record: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO messages(
                dialog_id, message_id, date, sender_id, text, reply_to_msg_id,
                media_type, outgoing, post, grouped_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class MysqlExportStore:
    def __init__(self) -> None:
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
                    dialog_id BIGINT PRIMARY KEY,
                    kind VARCHAR(32) NOT NULL,
                    name TEXT NOT NULL,
                    username VARCHAR(255),
                    exported_at DATETIME(6) NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
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
                    PRIMARY KEY (dialog_id, message_id),
                    INDEX messages_dialog_date (dialog_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
        self.connection.commit()

    def save_dialog(self, dialog_id: int, kind: str, name: str, username: str | None) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dialogs(dialog_id, kind, name, username, exported_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    kind = VALUES(kind), name = VALUES(name),
                    username = VALUES(username), exported_at = VALUES(exported_at)
                """,
                (dialog_id, kind, name, username, datetime.now(UTC).replace(tzinfo=None)),
            )

    def save_message(self, dialog_id: int, record: dict[str, Any]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT IGNORE INTO messages(
                    dialog_id, message_id, date, sender_id, text, reply_to_msg_id,
                    media_type, outgoing, post, grouped_id, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
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

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


async def export_history(limit: int | None, since: datetime | None) -> None:
    settings = Settings.from_environment()
    backend = os.getenv("STORAGE_BACKEND", "mysql").lower()
    if backend == "mysql":
        store = MysqlExportStore()
    elif backend == "sqlite":
        export_path = Path(os.getenv("TELEGRAM_EXPORT_PATH", "/home/admin/messages-data"))
        export_path.mkdir(parents=True, exist_ok=True)
        export_path.chmod(0o700)
        store = ExportStore(export_path / "telegram.sqlite3")
    else:
        raise ValueError("STORAGE_BACKEND must be mysql or sqlite")
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
    logger = logging.getLogger(__name__)
    dialogs = 0
    messages = 0

    try:
        await client.start()
        async for dialog in client.iter_dialogs(ignore_migrated=True):
            entity = dialog.entity
            kind = dialog_kind(entity)
            name = utils.get_display_name(entity) or str(dialog.id)
            username = getattr(entity, "username", None)
            store.save_dialog(dialog.id, kind, name, username)
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
            store.commit()
            logger.info("Exported %s messages from %s (%s)", dialog_messages, name, kind)
    finally:
        store.close()
        await client.disconnect()

    logger.info("Export complete: %s dialogs, %s messages, backend=%s", dialogs, messages, backend)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Telegram history to protected SQLite storage")
    parser.add_argument("--limit", type=int, help="maximum messages per dialog; useful for a test run")
    parser.add_argument("--since", help="only export messages on or after ISO date, e.g. 2026-01-01")
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(export_history(args.limit, parse_date(args.since)))
