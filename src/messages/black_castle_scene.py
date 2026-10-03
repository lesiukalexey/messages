import json
from pathlib import Path
from typing import Any


def black_castle_caption_and_keyboard(scene_path: Path) -> tuple[str, list[dict[str, Any]]]:
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    caption = scene["caption"]
    question = scene["question"]
    choices = scene["choices"]
    if (
        not isinstance(caption, str)
        or not isinstance(question, str)
        or not isinstance(choices, list)
        or len(choices) != 2
    ):
        raise ValueError("BlackCastle opening scene is invalid")
    buttons: list[dict[str, Any]] = []
    for choice in choices:
        if not isinstance(choice, dict) or not isinstance(choice.get("text"), str):
            raise ValueError("BlackCastle choice is invalid")
        buttons.append({"text": choice["text"], "callback_data": "black_castle_noop"})
    return f"{caption}\n\n{question}", buttons
