#!/usr/bin/env python3
"""Copy legacy Game memory rows from assistant SQLite into the game's MySQL DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from messages.game_memory_store import GameMemoryStore


def canonical_json(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def migrate(database_path: Path, drop_source: bool) -> tuple[int, bool]:
    if not database_path.is_file():
        raise FileNotFoundError("The protected assistant SQLite database was not found")
    sqlite = sqlite3.connect(database_path)
    sqlite.row_factory = sqlite3.Row
    target = GameMemoryStore(account_id="migration")
    try:
        target.initialize()
        table = sqlite.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_player_memories'"
        ).fetchone()
        if table is None:
            rows: list[sqlite3.Row] = []
        else:
            rows = sqlite.execute(
                "SELECT account_id, peer_id, facts_json, updated_at FROM game_player_memories"
            ).fetchall()

        target.connection.ping(reconnect=True)
        target.connection.begin()
        with target.connection.cursor() as cursor:
            for row in rows:
                # Validate before touching the destination; never log or print fact contents.
                facts_json = canonical_json(row["facts_json"])
                cursor.execute(
                    """INSERT IGNORE INTO player_memories
                           (account_id, peer_id, facts_json, updated_at)
                       VALUES (%s, %s, %s, %s)""",
                    (row["account_id"], row["peer_id"], facts_json, row["updated_at"]),
                )
            target.connection.commit()

            for row in rows:
                cursor.execute(
                    """SELECT facts_json, updated_at FROM player_memories
                       WHERE account_id = %s AND peer_id = %s""",
                    (row["account_id"], row["peer_id"]),
                )
                copied = cursor.fetchone()
                if (
                    copied is None
                    or canonical_json(copied["facts_json"]) != canonical_json(row["facts_json"])
                    or copied["updated_at"] != row["updated_at"]
                ):
                    raise RuntimeError("A legacy Game memory row did not verify in MySQL")

        if drop_source and table is not None:
            sqlite.execute("DROP TABLE game_player_memories")
            sqlite.commit()
        return len(rows), table is not None and not drop_source
    except Exception:
        target.connection.rollback()
        raise
    finally:
        target.close()
        sqlite.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-path",
        type=Path,
        default=Path(os.getenv("DATABASE_PATH", "/home/admin/messages-runtime/assistant.sqlite3")),
    )
    parser.add_argument(
        "--drop-sqlite-source",
        action="store_true",
        help="remove the old SQLite table after all copied rows have been verified; stop workers first",
    )
    args = parser.parse_args()
    count, source_retained = migrate(args.database_path, args.drop_sqlite_source)
    print(
        f"verified_rows={count}; source_table_retained={str(source_retained).lower()}; "
        f"database={os.getenv('GAME_MEMORY_DATABASE', 'game_chat_with_role')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
