from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    account_id: str
    session_path: Path
    database_path: Path
    codex_binary: Path
    codex_home: Path
    model_options: tuple[str, ...]
    default_model: str
    timezone: str
    google_client_file: Path
    google_token_file: Path
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> "Settings":
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if not api_id or not api_hash:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        account_id = os.getenv("TELEGRAM_ACCOUNT_ID", "personal").strip()
        if not account_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in account_id):
            raise ValueError("TELEGRAM_ACCOUNT_ID has invalid characters")

        options = tuple(
            model.strip()
            for model in os.getenv(
                "CODEX_MODELS", "gpt-5.6-luna,gpt-5.6-terra,gpt-5.6-sol"
            ).split(",")
            if model.strip()
        )
        if not options:
            raise ValueError("CODEX_MODELS must contain at least one model")
        default_model = os.getenv("CODEX_MODEL", options[0]).strip()
        if default_model not in options:
            options = (default_model, *options)

        return cls(
            api_id=int(api_id),
            api_hash=api_hash,
            account_id=account_id,
            session_path=Path(
                os.getenv(
                    "TELEGRAM_SESSION_PATH",
                    f"/home/admin/messages-runtime/accounts/{account_id}/telegram",
                )
            ),
            database_path=Path(
                os.getenv(
                    "DATABASE_PATH", "/home/admin/messages-runtime/assistant.sqlite3"
                )
            ),
            codex_binary=Path(
                os.getenv(
                    "CODEX_BINARY",
                    "/home/admin/.codex/packages/standalone/current/bin/codex",
                )
            ),
            codex_home=Path(os.getenv("CODEX_HOME", "/home/admin/.codex")),
            model_options=options,
            default_model=default_model,
            timezone=os.getenv("TIME_ZONE", "Europe/Kyiv").strip(),
            google_client_file=Path(
                os.getenv(
                    "GOOGLE_CALENDAR_CLIENT_FILE",
                    "/home/admin/messages-runtime/google-calendar/client_secret.json",
                )
            ),
            google_token_file=Path(
                os.getenv(
                    "GOOGLE_CALENDAR_TOKEN_FILE",
                    "/home/admin/messages-runtime/google-calendar/token.json",
                )
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
