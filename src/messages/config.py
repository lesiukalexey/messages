from dataclasses import dataclass, field
import json
import ipaddress
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
    recruiter_answers_file: Path
    learning_bot_token: str
    job_apply_profiles: dict[str, Path] = field(default_factory=dict)
    reply_api_token: str = ""
    reply_api_host: str = "192.168.31.46"
    reply_api_port: int = 8095
    reply_api_persona_id: str = "alexey-lesiuk"
    log_level: str = "INFO"
    category_answers_dir: Path = Path("/home/admin/messages-runtime/category-answers")

    @classmethod
    def from_environment(cls, *, require_telegram: bool = True) -> "Settings":
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if require_telegram and (not api_id or not api_hash):
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        account_id = os.getenv("TELEGRAM_ACCOUNT_ID", "personal").strip()
        if not account_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in account_id):
            raise ValueError("TELEGRAM_ACCOUNT_ID has invalid characters")

        options = tuple(
            model.strip()
            for model in os.getenv(
                "CODEX_MODELS", "gpt-5.6-luna,gpt-5.6-terra,gpt-5.6-sol,gpt-6-luna"
            ).split(",")
            if model.strip()
        )
        if not options:
            raise ValueError("CODEX_MODELS must contain at least one model")
        default_model = os.getenv("CODEX_MODEL", options[0]).strip()
        if default_model not in options:
            options = (default_model, *options)

        recruiter_answers_file = Path(
            os.getenv(
                "RECRUITER_ANSWERS_FILE",
                "/var/www/job-apply/data/profiles/lesiuk.alexey@gmail.com/external-form-fields.yaml",
            )
        )
        profiles_raw = os.getenv("JOB_APPLY_PROFILES", "").strip()
        if profiles_raw:
            try:
                profile_values = json.loads(profiles_raw)
            except json.JSONDecodeError as exc:
                raise ValueError("JOB_APPLY_PROFILES must be a JSON object") from exc
            if not isinstance(profile_values, dict) or not profile_values:
                raise ValueError("JOB_APPLY_PROFILES must be a non-empty JSON object")
            job_apply_profiles = {
                str(profile_id): Path(path)
                for profile_id, path in profile_values.items()
                if isinstance(profile_id, str) and isinstance(path, str)
            }
            if len(job_apply_profiles) != len(profile_values):
                raise ValueError("JOB_APPLY_PROFILES entries must map string IDs to paths")
        else:
            job_apply_profiles = {
                "lesiuk.alexey@gmail.com": recruiter_answers_file,
                "lesuk.aleksey@gmail.com": recruiter_answers_file.parent.parent
                / "lesuk.aleksey@gmail.com"
                / recruiter_answers_file.name,
            }
        profiles_root = Path("/var/www/job-apply/data/profiles").resolve()
        for profile_id, profile_path in job_apply_profiles.items():
            resolved_path = profile_path.resolve()
            if (
                not profile_id
                or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.@-" for char in profile_id)
                or resolved_path.name != "external-form-fields.yaml"
                or resolved_path.parent.name != profile_id
                or profiles_root not in resolved_path.parents
            ):
                raise ValueError("JOB_APPLY_PROFILES contains an invalid profile mapping")
            job_apply_profiles[profile_id] = resolved_path
        if recruiter_answers_file.resolve() not in job_apply_profiles.values():
            raise ValueError("RECRUITER_ANSWERS_FILE must refer to an allowlisted profile")

        reply_api_host = os.getenv("REPLY_API_HOST", "192.168.31.46").strip()
        if reply_api_host != "localhost":
            try:
                address = ipaddress.ip_address(reply_api_host)
            except ValueError as exc:
                raise ValueError("REPLY_API_HOST must be a private IP or localhost") from exc
            if not (address.is_private or address.is_loopback):
                raise ValueError("REPLY_API_HOST must be a private IP or localhost")
        reply_api_port = int(os.getenv("REPLY_API_PORT", "8095"))
        if not 1 <= reply_api_port <= 65535:
            raise ValueError("REPLY_API_PORT must be between 1 and 65535")

        return cls(
            api_id=int(api_id) if api_id else 0,
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
            recruiter_answers_file=recruiter_answers_file.resolve(),
            job_apply_profiles=job_apply_profiles,
            learning_bot_token=os.getenv("LEARNING_BOT_TOKEN", "").strip(),
            reply_api_token=os.getenv("REPLY_API_TOKEN", "").strip(),
            reply_api_host=reply_api_host,
            reply_api_port=reply_api_port,
            reply_api_persona_id=os.getenv("REPLY_API_PERSONA_ID", "alexey-lesiuk").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            category_answers_dir=Path(
                os.getenv(
                    "CATEGORY_ANSWERS_DIR",
                    "/home/admin/messages-runtime/category-answers",
                )
            ),
        )
