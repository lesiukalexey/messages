"""Persistent MySQL state for the BlackCastle game."""

from __future__ import annotations

import json
import os
import re
import copy
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
                """CREATE TABLE IF NOT EXISTS items (
                       item_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                       item_name VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                       UNIQUE KEY unique_item_name (item_name)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS player_inventory (
                       player_item_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                       player_id BIGINT NOT NULL,
                       item_id BIGINT UNSIGNED NOT NULL,
                       position SMALLINT UNSIGNED NOT NULL,
                       slot_cost SMALLINT UNSIGNED NOT NULL DEFAULT 1,
                       UNIQUE KEY unique_player_inventory_position (player_id, position),
                       KEY player_inventory_item (player_id, item_id),
                       CONSTRAINT player_inventory_player_fk FOREIGN KEY (player_id)
                           REFERENCES players (player_id) ON DELETE CASCADE,
                       CONSTRAINT player_inventory_item_fk FOREIGN KEY (item_id)
                           REFERENCES items (item_id)
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
                """CREATE TABLE IF NOT EXISTS paragraph_loot_options (
                       paragraph_number INT UNSIGNED NOT NULL,
                       loot_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       button_text VARCHAR(128) NOT NULL,
                       item_name VARCHAR(128) NULL,
                       gold_amount SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                       bag_slots SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                       sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                       PRIMARY KEY (paragraph_number, loot_id),
                       UNIQUE KEY paragraph_loot_order (paragraph_number, sort_order)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS paragraph_choice_rewards (
                       paragraph_number INT UNSIGNED NOT NULL,
                       choice_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                       item_name VARCHAR(128) NOT NULL,
                       gold_amount SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                       bag_slots SMALLINT UNSIGNED NOT NULL DEFAULT 1,
                       PRIMARY KEY (paragraph_number, choice_id)
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            from .black_castle_loot import PARAGRAPH_CHOICE_REWARDS, iter_paragraph_loot_rows

            cursor.executemany(
                """INSERT IGNORE INTO paragraph_loot_options
                   (paragraph_number, loot_id, button_text, item_name, gold_amount, bag_slots, sort_order)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                list(iter_paragraph_loot_rows()),
            )
            cursor.executemany(
                """INSERT IGNORE INTO paragraph_choice_rewards
                   (paragraph_number, choice_id, item_name, gold_amount, bag_slots)
                   VALUES (%s, %s, %s, %s, %s)""",
                PARAGRAPH_CHOICE_REWARDS,
            )
            cursor.execute(
                """INSERT IGNORE INTO paragraph_choices
                   (paragraph_number, choice_id, button_text, target_paragraph, sort_order)
                   VALUES (187, 'route_01', 'Теперь возвращайтесь на 47', 47, 0)"""
            )
            cursor.execute(
                """INSERT IGNORE INTO paragraph_choices
                   (paragraph_number, choice_id, button_text, target_paragraph, sort_order)
                   VALUES (47, 'knowledge_birches', 'Подняться к березам', 187, 1000)"""
            )
            cursor.execute(
                """DELETE FROM paragraph_choices
                   WHERE paragraph_number = 239 AND choice_id = 'route_01'
                     AND target_paragraph = 2
                     AND button_text LIKE 'Взять с собой:%'"""
            )
            cursor.execute(
                """DELETE FROM paragraph_choices
                   WHERE paragraph_number = 457
                     AND button_text LIKE 'Ее МАСТЕРСТВО%'"""
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
            cursor.execute(
                """UPDATE battle_narrative_templates
                   SET template_text = %s
                   WHERE enemy_key = '*' AND phase = 'failed_wound'
                     AND variant_no = 1 AND template_text IN (%s, %s)""",
                (
                    "Ваш удар не достигает цели. {enemy} уклоняется и сохраняет равновесие.",
                    "Удар не достигает цели: {enemy} уклоняется и сохраняет равновесие.",
                    "Ваш удар не достигает цели: {enemy} уклоняется и сохраняет равновесие.",
                ),
            )
            cursor.execute(
                """UPDATE battle_narrative_templates
                   SET template_text = %s
                   WHERE enemy_key = 'гигантский паук' AND phase = 'opening'
                     AND variant_no = 1 AND template_text = %s""",
                (
                    "{enemy} резко бросается вперёд и выбрасывает к {victim_dative} длинные когтистые лапы.",
                    "{enemy} резко бросается вперёд, выбрасывая навстречу {victim} длинные когтистые лапы.",
                ),
            )
            cursor.execute(
                """UPDATE battle_narrative_templates
                   SET template_text = %s
                   WHERE enemy_key = 'гигантский паук' AND phase = 'opening'
                     AND variant_no = 3
                     AND template_text = %s""",
                (
                    "Гигантский Паук резко бросается к {victim_dative}, выставив вперёд длинные лапы.",
                    "Гигантский Паук резко бросается к {victim}, выставив вперёд длинные лапы.",
                ),
            )
            self._battle_narrative_cache = None
        self._migrate_player_inventory()
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

    def get_paragraph_loot_options(self, paragraph_number: int) -> list[dict[str, Any]]:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT loot_id, button_text, item_name, gold_amount, bag_slots
                   FROM paragraph_loot_options
                   WHERE paragraph_number = %s
                   ORDER BY sort_order, loot_id""",
                (paragraph_number,),
            )
            return list(cursor.fetchall())

    def get_paragraph_choice_reward(self, paragraph_number: int, choice_id: str) -> dict[str, Any] | None:
        self.ensure_connected()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT item_name, gold_amount, bag_slots
                   FROM paragraph_choice_rewards
                   WHERE paragraph_number = %s AND choice_id = %s""",
                (paragraph_number, choice_id),
            )
            return cursor.fetchone()

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
        if not isinstance(state, dict):
            return None
        inventory = self._get_player_inventory(player_id)
        state["items"] = [row["item_name"] for row in inventory]
        state["item_ids"] = [int(row["player_item_id"]) for row in inventory]
        state["item_slot_costs"] = {
            str(row["item_name"]): int(row["slot_cost"])
            for row in inventory if int(row["slot_cost"]) > 1
        }
        return state

    def _get_player_inventory(self, player_id: int) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT pi.player_item_id, pi.item_id, pi.position, pi.slot_cost, i.item_name
                   FROM player_inventory pi
                   JOIN items i ON i.item_id = pi.item_id
                   WHERE pi.player_id = %s ORDER BY pi.position""",
                (player_id,),
            )
            return list(cursor.fetchall())

    @staticmethod
    def _state_without_inventory(state: dict[str, Any]) -> dict[str, Any]:
        saved = copy.deepcopy(state)
        for key in ("items", "item_ids", "item_slot_costs"):
            saved.pop(key, None)
        return saved

    def _sync_player_inventory(self, cursor: Any, player_id: int, state: dict[str, Any]) -> None:
        items = state.get("items", [])
        if not isinstance(items, list):
            items = []
        item_ids = state.get("item_ids", [])
        if not isinstance(item_ids, list):
            item_ids = []
        slot_costs = state.get("item_slot_costs", {})
        if not isinstance(slot_costs, dict):
            slot_costs = {}

        cursor.execute(
            """SELECT pi.player_item_id, pi.item_id, pi.position, pi.slot_cost, i.item_name
               FROM player_inventory pi JOIN items i ON i.item_id = pi.item_id
               WHERE pi.player_id = %s FOR UPDATE""",
            (player_id,),
        )
        existing = {int(row["player_item_id"]): row for row in cursor.fetchall()}
        retained: set[int] = set()
        resolved: list[tuple[int | None, int, int]] = []
        new_ids: list[int | None] = []
        for position, name in enumerate(items):
            if not isinstance(name, str) or not name.strip():
                continue
            item_name = name.strip()
            cursor.execute("INSERT IGNORE INTO items (item_name) VALUES (%s)", (item_name,))
            cursor.execute("SELECT item_id FROM items WHERE item_name = %s", (item_name,))
            catalog_row = cursor.fetchone()
            item_id = int(catalog_row["item_id"])
            try:
                slot_cost = max(1, int(slot_costs.get(item_name, 1)))
            except (TypeError, ValueError):
                slot_cost = 1
            supplied_id = item_ids[position] if position < len(item_ids) else None
            inventory_id = int(supplied_id) if supplied_id is not None else None
            current = existing.get(inventory_id) if inventory_id is not None else None
            if current is None or int(current["item_id"]) != item_id or inventory_id in retained:
                inventory_id = None
            else:
                retained.add(inventory_id)
            resolved.append((inventory_id, item_id, slot_cost))

        # Move retained rows out of the active position range before reordering them.
        for inventory_id in retained:
            cursor.execute(
                "UPDATE player_inventory SET position = position + 32768 WHERE player_item_id = %s",
                (inventory_id,),
            )
        if existing:
            obsolete = set(existing) - retained
            if obsolete:
                placeholders = ",".join(["%s"] * len(obsolete))
                cursor.execute(
                    f"DELETE FROM player_inventory WHERE player_id = %s AND player_item_id IN ({placeholders})",
                    (player_id, *sorted(obsolete)),
                )

        for position, (inventory_id, item_id, slot_cost) in enumerate(resolved):
            if inventory_id is None:
                cursor.execute(
                    """INSERT INTO player_inventory (player_id, item_id, position, slot_cost)
                       VALUES (%s, %s, %s, %s)""",
                    (player_id, item_id, position, slot_cost),
                )
                inventory_id = int(cursor.lastrowid)
            else:
                cursor.execute(
                    """UPDATE player_inventory SET item_id = %s, position = %s, slot_cost = %s
                       WHERE player_item_id = %s AND player_id = %s""",
                    (item_id, position, slot_cost, inventory_id, player_id),
                )
            new_ids.append(inventory_id)
        state["items"] = [name.strip() for name in items if isinstance(name, str) and name.strip()]
        state["item_ids"] = new_ids

    def _migrate_player_inventory(self) -> None:
        """Move any legacy JSON inventory into catalog rows and per-player references."""
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT player_id, state_json FROM players")
            player_ids = [int(row["player_id"]) for row in cursor.fetchall()]
        for player_id in player_ids:
            # Lock and re-read the latest state so concurrent worker startups cannot
            # overwrite a player's progress with a stale snapshot from the scan.
            self.connection.begin()
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT state_json FROM players WHERE player_id = %s FOR UPDATE",
                        (player_id,),
                    )
                    row = cursor.fetchone()
                    state = row["state_json"] if row else None
                    if isinstance(state, (bytes, bytearray)):
                        state = state.decode("utf-8")
                    if isinstance(state, str):
                        state = json.loads(state)
                    if isinstance(state, dict) and any(
                        key in state for key in ("items", "item_slot_costs", "item_ids")
                    ):
                        cursor.execute(
                            "UPDATE players SET state_json = %s WHERE player_id = %s",
                            (json.dumps(self._state_without_inventory(state), ensure_ascii=False), player_id),
                        )
                        self._sync_player_inventory(cursor, player_id, state)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def save_player_state(self, player_id: int, state: dict[str, Any]) -> None:
        self.ensure_connected()
        self.connection.begin()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO players (player_id, state_json) VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE state_json = VALUES(state_json)""",
                    (player_id, json.dumps(self._state_without_inventory(state), ensure_ascii=False)),
                )
                cursor.execute("SELECT player_id FROM players WHERE player_id = %s FOR UPDATE", (player_id,))
                self._sync_player_inventory(cursor, player_id, state)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def save_player_state_if_absent(self, player_id: int, state: dict[str, Any]) -> None:
        self.ensure_connected()
        self.connection.begin()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT IGNORE INTO players (player_id, state_json) VALUES (%s, %s)",
                    (player_id, json.dumps(self._state_without_inventory(state), ensure_ascii=False)),
                )
                if cursor.rowcount:
                    self._sync_player_inventory(cursor, player_id, state)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

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
