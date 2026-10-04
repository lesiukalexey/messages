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
        self._battle_narrative_cache: dict[tuple[str, str], list[str]] | None = None

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

    def initialize(self, scene_path: Any | None = None) -> None:
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
                """CREATE TABLE IF NOT EXISTS paragraphs (
                       paragraph_number INT UNSIGNED NOT NULL PRIMARY KEY,
                       title VARCHAR(255) NOT NULL,
                       body MEDIUMTEXT NULL,
                       question VARCHAR(1000) NULL,
                       photo_file_id VARCHAR(255) NULL,
                       updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS book_pages (
                       page_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
                       title VARCHAR(255) NOT NULL,
                       body MEDIUMTEXT NOT NULL,
                       updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS paragraph_choices (
                       paragraph_number INT UNSIGNED NOT NULL,
                       choice_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       button_text VARCHAR(128) NOT NULL,
                       target_paragraph INT UNSIGNED NOT NULL,
                       required_item VARCHAR(128) NULL,
                       sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                       PRIMARY KEY (paragraph_number, choice_id),
                       UNIQUE KEY paragraph_choice_order (paragraph_number, sort_order)
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
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS player_button_presses (
                       press_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                       callback_query_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       player_id BIGINT NOT NULL,
                       paragraph_number INT UNSIGNED NULL,
                       target_paragraph INT UNSIGNED NULL,
                       button_text VARCHAR(128) NOT NULL,
                       callback_data VARCHAR(256) NOT NULL,
                       pressed_at_utc DATETIME(6) NOT NULL,
                       UNIQUE KEY unique_callback_query (callback_query_id),
                       KEY player_pressed_at (player_id, pressed_at_utc)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS battle_narrative_templates (
                       enemy_key VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                       phase VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       variant_no SMALLINT UNSIGNED NOT NULL,
                       template_text TEXT NOT NULL,
                       updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP,
                       PRIMARY KEY (enemy_key, phase, variant_no),
                       KEY battle_template_phase (phase, enemy_key)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            from .black_castle_battle_text import iter_battle_text_rows

            cursor.executemany(
                """INSERT IGNORE INTO battle_narrative_templates
                   (enemy_key, phase, variant_no, template_text)
                   VALUES (%s, %s, %s, %s)""",
                list(iter_battle_text_rows()),
            )
            self._battle_narrative_cache = None
        if scene_path is not None:
            self.seed_opening_scene(scene_path)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE paragraphs
                   SET title = CONCAT('Шаг ', paragraph_number)
                   WHERE title <> CONCAT('Шаг ', paragraph_number)"""
            )

    def seed_opening_scene(self, scene_path: Any) -> None:
        """Create initial book pages once; MySQL remains the source of truth afterward."""
        from pathlib import Path

        scene = json.loads(Path(scene_path).read_text(encoding="utf-8"))
        choices = scene.get("choices")
        if not isinstance(choices, list):
            raise ValueError("BlackCastle opening scene has invalid choices")
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT IGNORE INTO paragraphs
                   (paragraph_number, title, body, question)
                   VALUES (%s, %s, %s, %s)""",
                (
                    1,
                    "Шаг 1",
                    str(scene.get("caption") or ""),
                    str(scene.get("question") or ""),
                ),
            )
            cursor.execute(
                """INSERT IGNORE INTO book_pages (page_key, title, body)
                   VALUES ('preface', %s, %s)""",
                ("Книга-игра", str(scene.get("preface") or "")),
            )
            cursor.executemany(
                """INSERT IGNORE INTO paragraphs (paragraph_number, title, body)
                   VALUES (%s, %s, NULL)""",
                [(number, f"Шаг {number}") for number in (86, 110)],
            )
            cursor.executemany(
                """INSERT IGNORE INTO paragraph_choices
                   (paragraph_number, choice_id, button_text, target_paragraph, sort_order)
                   VALUES (%s, %s, %s, %s, %s)""",
                [
                    (
                        1,
                        str(choice.get("id") or f"route_{index}"),
                        str(choice.get("text") or ""),
                        int(choice["target_step"]),
                        index,
                    )
                    for index, choice in enumerate(choices)
                    if isinstance(choice, dict)
                    and isinstance(choice.get("target_step"), int)
                    and isinstance(choice.get("text"), str)
                ],
            )

    def get_book_page(self, page_key: str) -> dict[str, Any] | None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT page_key, title, body FROM book_pages WHERE page_key = %s",
                (page_key,),
            )
            return cursor.fetchone()

    def get_paragraph(self, paragraph_number: int) -> dict[str, Any] | None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT paragraph_number, title, body, question, photo_file_id
                   FROM paragraphs WHERE paragraph_number = %s""",
                (paragraph_number,),
            )
            return cursor.fetchone()

    def get_paragraph_choices(self, paragraph_number: int) -> list[dict[str, Any]]:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT choice_id, button_text, target_paragraph, required_item
                   FROM paragraph_choices
                   WHERE paragraph_number = %s
                   ORDER BY sort_order, choice_id""",
                (paragraph_number,),
            )
            return list(cursor.fetchall())

    def get_paragraph_choice(self, paragraph_number: int, choice_id: str) -> dict[str, Any] | None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT choice_id, button_text, target_paragraph, required_item
                   FROM paragraph_choices
                   WHERE paragraph_number = %s AND choice_id = %s""",
                (paragraph_number, choice_id),
            )
            return cursor.fetchone()

    def get_battle_narrative_templates(self, enemy_name: str, phase: str) -> list[str]:
        """Return the enemy-specific phrase bank or the generic fallback bank."""
        from .black_castle_battle_text import canonical_enemy_key

        self.ensure_connected()
        if self._battle_narrative_cache is None:
            cache: dict[tuple[str, str], list[str]] = {}
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """SELECT enemy_key, phase, template_text
                       FROM battle_narrative_templates
                       ORDER BY enemy_key, phase, variant_no"""
                )
                for row in cursor.fetchall():
                    cache.setdefault((row["enemy_key"], row["phase"]), []).append(
                        str(row["template_text"])
                    )
            self._battle_narrative_cache = cache
        key = canonical_enemy_key(enemy_name)
        return (
            self._battle_narrative_cache.get((key, phase))
            or self._battle_narrative_cache.get(("*", phase), [])
        )

    def record_button_press(
        self,
        callback_query_id: str,
        player_id: int,
        paragraph_number: int | None,
        target_paragraph: int | None,
        button_text: str,
        callback_data: str,
    ) -> None:
        """Record one Telegram button callback, idempotently, in UTC."""
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT IGNORE INTO player_button_presses
                   (callback_query_id, player_id, paragraph_number, target_paragraph,
                    button_text, callback_data, pressed_at_utc)
                   VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6))""",
                (
                    callback_query_id[:64],
                    player_id,
                    paragraph_number,
                    target_paragraph,
                    button_text[:128],
                    callback_data[:256],
                ),
            )

    def set_paragraph_photo(self, paragraph_number: int, photo_file_id: str) -> bool:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE paragraphs SET photo_file_id = %s WHERE paragraph_number = %s",
                (photo_file_id, paragraph_number),
            )
            return cursor.rowcount > 0

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
