"""Persistent MySQL state for the BlackCastle game."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pymysql


class BlackCastleStore:
    def __init__(self) -> None:
        self.database = os.getenv("BLACK_CASTLE_DATABASE", "black_castle").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", self.database):
            raise ValueError("BLACK_CASTLE_DATABASE must be a simple MySQL database name")
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
            self.connection.ping(reconnect=True)
        except pymysql.err.MySQLError:
            self._connect()

    def initialize(self) -> None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS players (
                       player_id BIGINT NOT NULL PRIMARY KEY,
                       state_json JSON NOT NULL,
                       updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS settings (
                       setting_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
                       setting_value TEXT NOT NULL,
                       updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )

    def get_player_state(self, player_id: int) -> dict[str, Any] | None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state_json FROM players WHERE player_id = %s",
                (player_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        state = row["state_json"]
        if isinstance(state, (bytes, bytearray)):
            state = state.decode("utf-8")
        if isinstance(state, str):
            state = json.loads(state)
        return state if isinstance(state, dict) else None

    def save_player_state(self, player_id: int, state: dict[str, Any]) -> None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO players (player_id, state_json)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE state_json = VALUES(state_json)""",
                (player_id, json.dumps(state, ensure_ascii=False)),
            )

    def save_player_state_if_absent(self, player_id: int, state: dict[str, Any]) -> None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT IGNORE INTO players (player_id, state_json) VALUES (%s, %s)",
                (player_id, json.dumps(state, ensure_ascii=False)),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM settings WHERE setting_key = %s",
                (key,),
            )
            row = cursor.fetchone()
        return default if row is None else str(row["setting_value"])

    def set_setting(self, key: str, value: str) -> None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO settings (setting_key, setting_value)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)""",
                (key, value),
            )

    def set_setting_if_absent(self, key: str, value: str) -> None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)",
                (key, value),
            )

    def migrate_legacy_sqlite(self, legacy_store: Any) -> tuple[int, int]:
        """Move BlackCastle state from assistant SQLite once, preserving MySQL values."""
        connection = legacy_store.connection
        account_prefix = f"{legacy_store.account_id}:"
        player_key_prefix = "kniga_igra_black_castle_player_"
        game_prefix = f"{account_prefix}{player_key_prefix}"
        legacy_rows = connection.execute(
            """SELECT key, value FROM settings
               WHERE substr(key, 1, ?) = ? OR key IN (?, ?)""",
            (
                len(game_prefix),
                game_prefix,
                f"{account_prefix}kniga_igra_black_castle_photo_file_id",
                f"{account_prefix}kniga_igra_update_offset",
            ),
        ).fetchall()

        migrated_players = 0
        migrated_settings = 0
        delete_keys: list[str] = []
        for row in legacy_rows:
            key = str(row["key"])[len(account_prefix):]
            value = str(row["value"])
            if key.startswith(player_key_prefix):
                suffix = key[len(player_key_prefix):]
                if not suffix.isdigit():
                    continue
                try:
                    state = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if not isinstance(state, dict):
                    continue
                self.save_player_state_if_absent(int(suffix), state)
                migrated_players += 1
                delete_keys.append(str(row["key"]))
            elif key in {
                "kniga_igra_black_castle_photo_file_id",
                "kniga_igra_update_offset",
            }:
                self.set_setting_if_absent(key, value)
                migrated_settings += 1
                delete_keys.append(str(row["key"]))

        if delete_keys:
            connection.executemany(
                "DELETE FROM settings WHERE key = ?",
                [(key,) for key in delete_keys],
            )
            connection.commit()
        return migrated_players, migrated_settings

    def close(self) -> None:
        self.connection.close()
