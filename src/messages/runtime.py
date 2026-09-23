from __future__ import annotations

import os
import re
from pathlib import Path

RUNTIME_ROOT = Path("/home/admin/messages-runtime")


def _read_env(path: Path, override: bool = False) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if override or key not in os.environ or not os.environ[key]:
            os.environ[key] = value


def load_environment() -> None:
    _read_env(RUNTIME_ROOT / "messages.env")
    _read_env(Path(".env"))
    account_id = os.getenv("TELEGRAM_ACCOUNT_ID", "personal").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", account_id):
        raise ValueError("TELEGRAM_ACCOUNT_ID has invalid characters")
    _read_env(RUNTIME_ROOT / "accounts" / f"{account_id}.env", override=True)
