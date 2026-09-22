from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    session_path: Path
    database_path: Path
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> "Settings":
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if not api_id or not api_hash:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

        return cls(
            api_id=int(api_id),
            api_hash=api_hash,
            session_path=Path(os.getenv("TELEGRAM_SESSION_PATH", "runtime/telegram")),
            database_path=Path(os.getenv("DATABASE_PATH", "data/messages.sqlite3")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

