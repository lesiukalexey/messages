"""Adapter for the BlackCastle algorithm stored in the Game workspace."""

import importlib.util
from pathlib import Path
from types import ModuleType


class BlackCastleReplyError(RuntimeError):
    pass


class BlackCastleReplyAlgorithm:
    def __init__(self, algorithm_path: Path) -> None:
        self.algorithm_path = algorithm_path
        self._module: ModuleType | None = None
        self._mtime_ns: int | None = None

    def _load(self) -> ModuleType:
        try:
            modified = self.algorithm_path.stat().st_mtime_ns
        except OSError as exc:
            raise BlackCastleReplyError("BlackCastle algorithm is unavailable") from exc
        if self._module is not None and modified == self._mtime_ns:
            return self._module
        spec = importlib.util.spec_from_file_location(
            "telegram_black_castle_reply_algorithm", self.algorithm_path
        )
        if spec is None or spec.loader is None:
            raise BlackCastleReplyError("BlackCastle algorithm cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise BlackCastleReplyError("BlackCastle algorithm failed to load") from exc
        if not callable(getattr(module, "reply_to_message", None)):
            raise BlackCastleReplyError("BlackCastle algorithm has no reply_to_message function")
        self._module = module
        self._mtime_ns = modified
        return module

    def reply(self, account_id: str, peer_id: int, message: str) -> str:
        try:
            result = self._load().reply_to_message(account_id, peer_id, message)
        except BlackCastleReplyError:
            raise
        except Exception as exc:
            raise BlackCastleReplyError("BlackCastle algorithm failed") from exc
        if not isinstance(result, str) or not result.strip() or len(result) > 4096:
            raise BlackCastleReplyError("BlackCastle algorithm returned an invalid reply")
        return result
