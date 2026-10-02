"""Choose one independent game for a Telegram contact."""


def selected_game_folder(in_black_castle: bool, in_game: bool) -> str | None:
    if in_black_castle:
        return "BlackCastle"
    if in_game:
        return "Game"
    return None
