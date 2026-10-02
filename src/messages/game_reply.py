"""Adapter for the reply algorithm kept in the separate Game project."""

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType


class GameReplyError(RuntimeError):
    pass


class GameReplyAlgorithm:
    def __init__(self, algorithm_path: Path) -> None:
        self.algorithm_path = algorithm_path
        self._module: ModuleType | None = None
        self._mtime_ns: int | None = None

    def _load(self) -> ModuleType:
        try:
            modified = self.algorithm_path.stat().st_mtime_ns
        except OSError as exc:
            raise GameReplyError("Game reply algorithm is unavailable") from exc
        if self._module is not None and modified == self._mtime_ns:
            return self._module
        spec = importlib.util.spec_from_file_location(
            "telegram_game_reply_algorithm", self.algorithm_path
        )
        if spec is None or spec.loader is None:
            raise GameReplyError("Game reply algorithm cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise GameReplyError("Game reply algorithm failed to load") from exc
        if not callable(getattr(module, "reply_to_message", None)):
            raise GameReplyError("Game reply algorithm has no reply_to_message function")
        self._module = module
        self._mtime_ns = modified
        return module

    async def reply(self, account_id: str, peer_id: int, message: str) -> str:
        def invoke() -> str:
            return self._load().reply_to_message(account_id, peer_id, message)

        try:
            result = await asyncio.to_thread(invoke)
        except GameReplyError:
            raise
        except Exception as exc:
            raise GameReplyError("Game reply algorithm failed") from exc
        if not isinstance(result, str) or not result.strip() or len(result) > 4096:
            raise GameReplyError("Game reply algorithm returned an invalid reply")
        return result
