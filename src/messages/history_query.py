from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import pymysql


def load_database_environment(account: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", account):
        raise ValueError("account must contain only letters, numbers, dots, hyphens, or underscores")
    for env_path in (Path(".env"), Path("/home/admin/messages-runtime/messages.env")):
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


def positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= limit <= 500:
        raise argparse.ArgumentTypeError("limit must be between 1 and 500")
    return limit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print recent exported Telegram messages for one account and username"
    )
    parser.add_argument("--account", required=True, help="account id, e.g. personal or personal2")
    parser.add_argument("--username", required=True, help="Telegram username, with or without @")
    parser.add_argument("--limit", type=positive_limit, default=30, help="messages to print (1–500; default: 30)")
    return parser


def normalized_username(value: str) -> str:
    username = value.strip().removeprefix("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        raise ValueError("username must be a Telegram username, with or without @")
    return username


def connect() -> Any:
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.getenv("MYSQL_DATABASE", "messages"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def print_history(account_id: str, username: str, limit: int) -> int:
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT dialog_id, name, username FROM dialogs
                   WHERE account_id = %s AND kind = 'user' AND LOWER(username) = LOWER(%s)
                   LIMIT 2""",
                (account_id, username),
            )
            dialogs = cursor.fetchall()
            if not dialogs:
                print(f"No exported dialog found for @{username} in account {account_id}.", file=sys.stderr)
                return 1
            if len(dialogs) > 1:
                print(f"More than one exported dialog matched @{username}; refusing to choose.", file=sys.stderr)
                return 2

            dialog = dialogs[0]
            cursor.execute(
                """SELECT date, outgoing, text, media_type FROM messages
                   WHERE account_id = %s AND dialog_id = %s
                   ORDER BY date DESC, message_id DESC LIMIT %s""",
                (account_id, dialog["dialog_id"], limit),
            )
            messages = cursor.fetchall()
    finally:
        connection.close()

    print(f"{dialog['name']} (@{dialog['username']}) — account {account_id}")
    for message in reversed(messages):
        timestamp = message["date"] or "unknown time"
        direction = "me" if message["outgoing"] else "contact"
        body = str(message["text"] or "").strip()
        if not body:
            body = f"[{message['media_type'] or 'non-text message'}]"
        print(f"\n[{timestamp}] {direction}:\n{body}")
    if not messages:
        print("No exported messages found.")
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        username = normalized_username(args.username)
        load_database_environment(args.account)
        exit_code = print_history(args.account, username, args.limit)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
