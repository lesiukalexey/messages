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
        self.connection = self._connect()

    def _connect(self) -> pymysql.connections.Connection:
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
        return self.connection

    def ensure_connected(self) -> None:
        try:
            self.connection.ping()
        except pymysql.err.MySQLError:
            try:
                self.connection.close()
            except pymysql.err.MySQLError:
                pass
            self._connect()

    def initialize(self) -> None:
        self.ensure_connected()
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
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS player_followups (
                       account_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       peer_id BIGINT NOT NULL,
                       player_message_id BIGINT NOT NULL,
                       bot_message_id BIGINT NOT NULL,
                       due_at VARCHAR(40) NOT NULL,
                       state VARCHAR(20) NOT NULL,
                       updated_at VARCHAR(40) NOT NULL,
                       PRIMARY KEY (account_id, peer_id),
                       INDEX player_followups_due (account_id, state, due_at)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            # A generation interrupted by a process restart has not reached Telegram's send call.
            cursor.execute(
                """UPDATE player_followups SET state = 'pending', updated_at = %s
                   WHERE account_id = %s AND state = 'generating'""",
                (datetime.now(UTC).isoformat(), self.account_id),
            )
        self.connection.commit()

    def cancel_player_followup(self, peer_id: int, player_message_id: int) -> bool:
        """Cancel an unsent reminder when a newer player message arrives."""
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE player_followups SET state = 'cancelled', updated_at = %s
                   WHERE account_id = %s AND peer_id = %s AND bot_message_id < %s
                     AND state IN ('pending', 'generating', 'sending')""",
                (
                    datetime.now(UTC).isoformat(),
                    self.account_id,
                    peer_id,
                    player_message_id,
                ),
            )
        self.connection.commit()
        return cursor.rowcount == 1

    def schedule_player_followup(
        self,
        peer_id: int,
        player_message_id: int,
        bot_message_id: int,
        due_at: datetime,
    ) -> None:
        """Start one pending follow-up cycle for the latest successful Game reply."""
        self.ensure_connected()
        now = datetime.now(UTC).isoformat()
        due = due_at.astimezone(UTC) if due_at.tzinfo else due_at.replace(tzinfo=UTC)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO player_followups
                       (account_id, peer_id, player_message_id, bot_message_id,
                        due_at, state, updated_at)
                   VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                   AS incoming
                   ON DUPLICATE KEY UPDATE
                       player_message_id = incoming.player_message_id,
                       bot_message_id = incoming.bot_message_id,
                       due_at = incoming.due_at,
                       state = 'pending',
                       updated_at = incoming.updated_at""",
                (
                    self.account_id,
                    peer_id,
                    player_message_id,
                    bot_message_id,
                    due.isoformat(),
                    now,
                ),
            )
        self.connection.commit()

    def claim_due_player_followup(self, now: datetime | None = None) -> dict[str, Any] | None:
        """Claim one due reminder; claimed generations can safely resume after restart."""
        self.ensure_connected()
        current = now or datetime.now(UTC)
        current = current.astimezone(UTC) if current.tzinfo else current.replace(tzinfo=UTC)
        try:
            self.connection.begin()
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """SELECT peer_id, player_message_id, bot_message_id, due_at
                       FROM player_followups
                       WHERE account_id = %s AND state = 'pending' AND due_at <= %s
                       ORDER BY due_at, peer_id LIMIT 1 FOR UPDATE""",
                    (self.account_id, current.isoformat()),
                )
                row = cursor.fetchone()
                if row is None:
                    self.connection.commit()
                    return None
                cursor.execute(
                    """UPDATE player_followups SET state = 'generating', updated_at = %s
                       WHERE account_id = %s AND peer_id = %s AND state = 'pending'""",
                    (current.isoformat(), self.account_id, row["peer_id"]),
                )
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    return None
            self.connection.commit()
            return row
        except Exception:
            self.connection.rollback()
            raise

    def set_player_followup_state(
        self,
        peer_id: int,
        player_message_id: int,
        bot_message_id: int,
        expected_state: str,
        new_state: str,
    ) -> bool:
        """Transition one exact follow-up cycle without overwriting newer activity."""
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE player_followups SET state = %s, updated_at = %s
                   WHERE account_id = %s AND peer_id = %s
                     AND player_message_id = %s AND bot_message_id = %s AND state = %s""",
                (
                    new_state,
                    datetime.now(UTC).isoformat(),
                    self.account_id,
                    peer_id,
                    player_message_id,
                    bot_message_id,
                    expected_state,
                ),
            )
        self.connection.commit()
        return cursor.rowcount == 1

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
        self.ensure_connected()
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
        self.ensure_connected()
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
