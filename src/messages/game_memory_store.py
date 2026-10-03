"""MySQL storage for private, per-player memories belonging to one game."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import pymysql

from .game_memory import MAX_FACT_LENGTH, MAX_MEMORY_FACTS


class GameMemoryStore:
    """Keep player facts isolated in the configured game's own database."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.database = os.getenv("GAME_MEMORY_DATABASE", "game_chat_with_role")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", self.database):
            raise ValueError("GAME_MEMORY_DATABASE must be a simple MySQL database name")
        self.connection = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            autocommit=True,
        )

    def initialize(self) -> None:
        self.connection.ping(reconnect=True)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS player_memories (
                       account_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       peer_id BIGINT NOT NULL,
                       facts_json JSON NOT NULL,
                       updated_at VARCHAR(40) NOT NULL,
                       PRIMARY KEY (account_id, peer_id)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
        self.connection.commit()

    @staticmethod
    def _facts(value: Any) -> list[str]:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        if not isinstance(value, list):
            return []
        return [
            item.strip()[:MAX_FACT_LENGTH]
            for item in value
            if isinstance(item, str) and item.strip()
        ][:MAX_MEMORY_FACTS]

    def game_player_memory(self, peer_id: int) -> list[str]:
        self.connection.ping(reconnect=True)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT facts_json FROM player_memories
                   WHERE account_id = %s AND peer_id = %s""",
                (self.account_id, peer_id),
            )
            row = cursor.fetchone()
        return [] if row is None else self._facts(row["facts_json"])

    def update_game_player_memory(
        self, peer_id: int, remember: list[str], forget: list[str]
    ) -> list[str]:
        """Apply a compact fact delta under a row lock, preserving other players."""
        self.connection.ping(reconnect=True)
        try:
            self.connection.begin()
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """SELECT facts_json FROM player_memories
                       WHERE account_id = %s AND peer_id = %s FOR UPDATE""",
                    (self.account_id, peer_id),
                )
                row = cursor.fetchone()
                facts = [] if row is None else self._facts(row["facts_json"])
                forgotten = {value.casefold() for value in forget if isinstance(value, str)}
                updated = [value for value in facts if value.casefold() not in forgotten]
                existing = {value.casefold() for value in updated}
                for value in remember:
                    if not isinstance(value, str):
                        continue
                    normalized = " ".join(value.split())[:MAX_FACT_LENGTH]
                    if normalized and normalized.casefold() not in existing:
                        updated.append(normalized)
                        existing.add(normalized.casefold())
                updated = updated[-MAX_MEMORY_FACTS:]
                if updated != facts:
                    cursor.execute(
                        """INSERT INTO player_memories
                               (account_id, peer_id, facts_json, updated_at)
                           VALUES (%s, %s, %s, %s)
                           AS incoming
                           ON DUPLICATE KEY UPDATE
                               facts_json = incoming.facts_json,
                               updated_at = incoming.updated_at""",
                        (
                            self.account_id,
                            peer_id,
                            json.dumps(updated, ensure_ascii=False),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
            self.connection.commit()
            return updated
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()
