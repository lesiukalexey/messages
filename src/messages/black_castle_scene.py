import json
from pathlib import Path
from typing import Any


def black_castle_caption_and_keyboard(
    scene_path: Path,
) -> tuple[str, list[list[dict[str, Any]]]]:
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
    choice_rows: list[list[dict[str, Any]]] = []
    for choice in choices:
        target_step = choice.get("target_step") if isinstance(choice, dict) else None
        if (
            not isinstance(choice, dict)
            or not isinstance(choice.get("text"), str)
            or not isinstance(target_step, int)
        ):
            raise ValueError("BlackCastle choice is invalid")
        choice_rows.append([{
            "text": choice["text"],
            "callback_data": f"blackcastle:step:{target_step}",
        }])
    keyboard = choice_rows + [
        [
            {"text": "Предисловие", "callback_data": "blackcastle:preface"},
            {"text": "Характеристики", "callback_data": "blackcastle:stats"},
        ],
        [{"text": "Инвентарь", "callback_data": "blackcastle:inventory"}],
    ]
    return f"Шаг 1\n\n{caption}\n\n{question}", keyboard
