"""Load the independently maintained ChatRole game engine."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_game(
    engine_path: Path,
    account_id: str,
    *,
    test_peer_ids: frozenset[int] = frozenset(),
    test_event_delay_minutes: int = 0,
):
    path = engine_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"ChatRole engine file is unavailable: {path}")
    module_name = "_messages_chatrole_game_engine"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("Could not load ChatRole game engine")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    game = module.ChatRoleGame(
        account_id,
        test_peer_ids=test_peer_ids,
        test_event_delay_minutes=test_event_delay_minutes,
    )
    game.initialize()
    return game
